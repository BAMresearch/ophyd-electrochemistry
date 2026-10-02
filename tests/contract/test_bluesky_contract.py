"""Real Bluesky API/event checks using a deliberately minimal test witness.

This is not a 2460 simulator. It verifies that the proposed lifecycle and
partial-event schema can be consumed by the selected Bluesky release.
"""

import threading
import time

import event_model
import pytest
from bluesky import RunEngine
from bluesky import plan_stubs as bps
from ophyd.status import Status

from examples.follower import follower_acquisition
from ophyd_electrochemistry import (
    AcquisitionRequest,
    DeviceState,
    PotentiostaticHold,
    StartMode,
)
from ophyd_electrochemistry.exceptions import AcquisitionAborted, ShutdownUnconfirmed


def request(mode):
    return AcquisitionRequest(
        experiment_id="contract-witness",
        start_mode=mode,
        program=PotentiostaticHold(
            voltage=0.1, duration_s=1, sample_period_s=1, current_limit_a=0.001
        ),
    )


def finished_status():
    result = Status()
    result.set_finished()
    return result


class LifecycleWitness:
    """Only exercises the selected interface semantics; no physics/TSP/network."""

    name = "ec"
    parent = None

    def __init__(self):
        self.state = DeviceState.IDLE
        self.output = False
        self.ready = False
        self.busy = False
        self.done_status = Status()
        self.records = []
        self.emitted = 0
        self.completion_requested = threading.Event()

    def prepare(self, value):
        self.request = value
        self.state = DeviceState.PREPARED
        return finished_status()

    def stage(self):
        return [self]

    def unstage(self):
        assert self.output is False
        return [self]

    def stop(self, *, success=False):
        if self.state in (DeviceState.COMPLETE, DeviceState.ABORTED):
            return
        self.abort().wait(timeout=1)

    def kickoff(self):
        if self.request.start_mode == StartMode.EXTERNAL_TRIGGER:
            self.state = DeviceState.WAITING_START
            self.ready = True
        else:
            self.external_start()
        return finished_status()

    def external_start(self):
        self.state = DeviceState.RUNNING
        self.ready = False
        self.busy = True
        self.output = True
        timestamp = time.time()
        self.records.append(
            {
                "time": timestamp,
                "data": {"ec_voltage": 0.1},
                "timestamps": {"ec_voltage": timestamp},
            }
        )

    def complete(self):
        self.completion_requested.set()
        return self.done_status

    def finish(self):
        self.output = False
        self.busy = False
        self.state = DeviceState.COMPLETE
        self.done_status.set_finished()

    def abort(self, *, confirm_shutdown=True):
        abort_status = Status()
        if confirm_shutdown:
            self.output = False
            self.ready = self.busy = False
            self.state = DeviceState.ABORTED
            self.done_status.set_exception(AcquisitionAborted("witness abort"))
            abort_status.set_finished()
        else:
            self.output = None
            self.state = DeviceState.ERROR
            self.done_status.set_exception(ShutdownUnconfirmed("witness disconnect"))
            abort_status.set_exception(ShutdownUnconfirmed("witness disconnect"))
        return abort_status

    def recover(self):
        assert self.output is False
        self.state = DeviceState.IDLE
        return finished_status()

    def describe_collect(self):
        return {
            "electrochemistry": {
                "ec_voltage": {
                    "source": "SIM:contract-witness",
                    "dtype": "number",
                    "shape": [],
                    "units": "V",
                }
            }
        }

    def collect(self):
        while self.emitted < len(self.records):
            record = self.records[self.emitted]
            self.emitted += 1
            yield record


@pytest.mark.parametrize("mode", list(StartMode))
def test_real_runengine_prepare_kickoff_complete_collect(mode):
    witness = LifecycleWitness()
    documents = []
    re = RunEngine({})
    re.subscribe(lambda name, doc: documents.append((name, doc)))
    failures = []

    def finish_after_complete_is_requested():
        try:
            assert witness.completion_requested.wait(timeout=5)
            if mode == StartMode.EXTERNAL_TRIGGER:
                assert witness.state == DeviceState.WAITING_START
                assert witness.ready and not witness.output
                witness.external_start()
            witness.finish()
        except BaseException as exc:
            failures.append(exc)
            if not witness.done_status.done:
                witness.done_status.set_exception(exc)

    worker = threading.Thread(target=finish_after_complete_is_requested, daemon=True)
    worker.start()

    def plan():
        yield from bps.open_run(md={"experiment_id": "contract-witness"})
        yield from bps.prepare(witness, request(mode), wait=True)
        assert witness.state == DeviceState.PREPARED
        assert not witness.ready and not witness.output
        yield from bps.kickoff(witness, wait=True)
        if mode == StartMode.EXTERNAL_TRIGGER:
            assert witness.ready and not witness.output
        else:
            assert witness.state == DeviceState.RUNNING and witness.output
        yield from bps.complete(witness, wait=True)
        yield from bps.collect(witness, return_payload=False)
        yield from bps.close_run()

    try:
        re(plan())
    finally:
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert not failures
    assert witness.state == DeviceState.COMPLETE
    assert not witness.output and not witness.busy
    for name, doc in documents:
        event_model.schema_validators[event_model.DocumentNames(name)].validate(doc)
    descriptors = [doc for name, doc in documents if name == "descriptor"]
    assert descriptors[0]["name"] == "electrochemistry"
    assert [name for name, _ in documents].count("event_page") == 1
    assert list(witness.collect()) == []


def test_abort_success_and_acquisition_failure_are_distinct_and_data_survives():
    witness = LifecycleWitness()
    witness.prepare(request(StartMode.IMMEDIATE))
    witness.kickoff()
    completion = witness.complete()
    assert witness.abort().success
    assert witness.state == DeviceState.ABORTED
    with pytest.raises(AcquisitionAborted):
        completion.wait(timeout=1)
    assert not witness.output
    assert witness.recover().success
    assert witness.state == DeviceState.IDLE
    assert len(list(witness.collect())) == 1


def test_shutdown_uncertainty_does_not_report_success_or_safe_output():
    witness = LifecycleWitness()
    witness.prepare(request(StartMode.IMMEDIATE))
    witness.kickoff()
    result = witness.abort(confirm_shutdown=False)
    with pytest.raises(ShutdownUnconfirmed):
        result.wait(timeout=1)
    assert witness.output is None
    assert witness.state == DeviceState.ERROR
    with pytest.raises(ShutdownUnconfirmed):
        witness.complete().wait(timeout=1)


@pytest.mark.parametrize("aborted", [False, True])
def test_documented_follower_plan_runs_cleanup_and_retains_partial_data(aborted):
    witness = LifecycleWitness()
    documents = []
    re = RunEngine({})
    re.subscribe(lambda name, doc: documents.append((name, doc)))

    def end_acquisition():
        if witness.completion_requested.wait(timeout=5):
            if aborted:
                witness.abort()
            else:
                witness.finish()

    worker = threading.Thread(target=end_acquisition, daemon=True)
    worker.start()
    try:
        if aborted:
            from bluesky.utils import FailedStatus

            with pytest.raises(FailedStatus):
                re(follower_acquisition(witness, request(StartMode.IMMEDIATE)))
        else:
            re(follower_acquisition(witness, request(StartMode.IMMEDIATE)))
    finally:
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert witness.output is False
    expected = DeviceState.ABORTED if aborted else DeviceState.COMPLETE
    assert witness.state == expected
    assert witness.records and witness.emitted == 1
    for name, doc in documents:
        event_model.schema_validators[event_model.DocumentNames(name)].validate(doc)
    start = next(doc for name, doc in documents if name == "start")
    assert start["experiment_id"] == "contract-witness"
    stop = next(doc for name, doc in documents if name == "stop")
    assert stop["exit_status"] == ("fail" if aborted else "success")
