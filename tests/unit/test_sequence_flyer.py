"""Sequence lifecycle and streaming under a real RunEngine (no hardware)."""

import json
import math
import threading
from dataclasses import replace

import numpy as np
import pytest
from bluesky import RunEngine
from bluesky.utils import FailedStatus, RunEngineInterrupted

from ophyd_electrochemistry.exceptions import (
    AcquisitionAborted,
    ElectrochemistryError,
    ShutdownUnconfirmed,
    UnsupportedCapabilityError,
    ValidationError,
)
from ophyd_electrochemistry.sequences import (
    SafetyLimits,
    Sampling,
    Sequence,
    SequenceFlyer,
    SequenceRequest,
    SimulatedSequenceBackend,
    bipolar_pulse_sequence,
    load_run_sheet,
    make_template,
    rest_step,
    run_sequence,
)
from ophyd_electrochemistry.sequences.run_file import READOUT_FIELDS, read_run_file
from ophyd_electrochemistry.sequences.simulator import CellModel, simulate_sequence
from ophyd_electrochemistry.state import DeviceState


@pytest.fixture
def limits():
    return SafetyLimits(0, 4, 0.01, 10000, 100000)


@pytest.fixture
def seq_request(limits):
    return SequenceRequest("pulse / 001", bipolar_pulse_sequence(0.001, 0.1, 2), limits)


def engine():
    re = RunEngine({}, call_returns_result=True)
    docs = []
    re.subscribe(lambda name, doc: docs.append((name, doc)))
    return re, docs


def pages(docs, stream):
    descriptors = {d["uid"] for n, d in docs if n == "descriptor" and d["name"] == stream}
    return [d for n, d in docs if n == "event_page" and d["descriptor"] in descriptors]


def test_full_bipolar_stream_matches_pure_simulation(seq_request):
    cell = CellModel(noise_v=1e-4)
    backend = SimulatedSequenceBackend(cell, speed=math.inf, seed=42)
    flyer = SequenceFlyer(backend, name="sequence", poll_period_s=0.01, chunk_records=13)
    re, docs = engine()
    result = re(run_sequence(flyer, seq_request, md={"operator": "test"}))
    assert result.plan_result == "completed"
    expected = simulate_sequence(seq_request.sequence, seq_request.limits, cell, seed=42)
    readout = pages(docs, "readout")
    assert sum(len(page["time"]) for page in readout) == docs[0][1]["expected_records"]
    assert set(readout[0]["data"]) == set(READOUT_FIELDS)
    origin = backend.time_origin_epoch_s()
    for key in READOUT_FIELDS:
        np.testing.assert_array_equal(
            np.concatenate([page["data"][key] for page in readout]),
            expected.readout[key],
        )
    for page in readout:
        np.testing.assert_allclose(
            page["timestamps"]["t_s"],
            origin + np.asarray(page["data"]["t_s"]),
            rtol=0,
            atol=0,
        )
    event_pages = pages(docs, "sequence_events")
    actual_events = [
        row for page in event_pages for row in zip(*page["data"].values(), strict=True)
    ]
    assert actual_events == expected.events
    assert len(set(readout[0]["data"]) & set(event_pages[0]["data"])) == 0
    descriptors = [d for n, d in docs if n == "descriptor"]
    assert {d["name"] for d in descriptors} == {"readout", "sequence_events"}
    assert descriptors[0]["data_keys"]["voltage_v"]["units"] == "V"
    assert descriptors[0]["data_keys"]["t_s"]["source"] == "sequence:t_s"
    assert backend.inspect() == (DeviceState.COMPLETE, False)
    assert flyer.complete().success


def test_live_pages_arrive_before_completion(limits, tmp_path):
    from ophyd_electrochemistry.sequences.callbacks import RunFileWriter

    seq_request = SequenceRequest(
        "live", Sequence((rest_step(30, sampling=Sampling(0.1)),)), limits
    )
    backend = SimulatedSequenceBackend(speed=10)
    flyer = SequenceFlyer(backend, name="sequence", poll_period_s=0.03)
    re, docs = engine()
    states = []
    writer = RunFileWriter(tmp_path)
    re.subscribe(writer)
    snapshots = []
    re.subscribe(
        lambda n, d: (
            snapshots.append(read_run_file(writer.path))
            if n == "event_page" and "voltage_v" in d["data"]
            else None
        )
    )
    re.subscribe(lambda n, d: states.append(backend.inspect()[0]) if n == "event_page" else None)
    assert re(run_sequence(flyer, seq_request, flush_period_s=0.2)).plan_result == "completed"
    assert len(pages(docs, "readout")) > 1
    assert DeviceState.RUNNING in states
    assert snapshots[0]["run"]["outcome"] == "incomplete"
    assert 0 < len(snapshots[0]["readout"]["t_s"]) < 300
    assert len(read_run_file(writer.path)["readout"]["t_s"]) == 300
    assert read_run_file(writer.path)["run"]["outcome"] == "completed"
    assert sum(len(p["time"]) for p in pages(docs, "readout")) == 300


def test_external_kickoff_waits_off_and_thread_can_fire(seq_request):
    backend = SimulatedSequenceBackend(speed=math.inf)
    flyer = SequenceFlyer(backend, name="sequence", poll_period_s=0.002)
    flyer.stage()
    flyer.prepare(replace(seq_request, start_mode="external")).wait(1)
    flyer.kickoff().wait(1)
    assert backend.inspect() == (DeviceState.WAITING_START, False)
    assert backend.time_origin_epoch_s() is None
    assert list(flyer.collect()) == []
    complete = flyer.complete()
    assert flyer.complete() is complete
    thread = threading.Thread(target=backend.fire_start)
    thread.start()
    thread.join(1)
    complete.wait(1)
    assert complete.success
    assert backend.time_origin_epoch_s() is not None
    assert len(list(flyer.collect())) > 0
    flyer.unstage()


def test_external_start_timeout_fails_and_drains_abort(seq_request):
    backend = SimulatedSequenceBackend(speed=math.inf)
    flyer = SequenceFlyer(backend, name="sequence", poll_period_s=0.002, start_timeout_s=0.02)
    re, docs = engine()
    with pytest.raises(FailedStatus):
        re(run_sequence(flyer, replace(seq_request, start_mode="external")))
    assert backend.inspect() == (DeviceState.ABORTED, False)
    assert flyer.outcome == "aborted"
    assert flyer.outcome_detail == "host_completion_timeout"
    assert not flyer.complete().success
    events = pages(docs, "sequence_events")
    assert events[-1]["data"]["event_kind"][-1] == "abort"
    assert events[-1]["data"]["event_detail"][-1] == "host_completion_timeout"
    assert not pages(docs, "readout")


def test_completion_timeout_aborts_stalled_backend(limits):
    backend = SimulatedSequenceBackend(clock=lambda: 0.0)
    seq_request = SequenceRequest(
        "stalled", Sequence((rest_step(0.02, sampling=Sampling(0.01)),)), limits
    )
    flyer = SequenceFlyer(backend, name="sequence", poll_period_s=0.002, completion_margin_s=0)
    flyer.stage()
    flyer.prepare(seq_request).wait(1)
    flyer.kickoff().wait(1)
    with pytest.raises(AcquisitionAborted, match="host_completion_timeout"):
        flyer.complete().wait(1)
    assert backend.inspect() == (DeviceState.ABORTED, False)
    assert list(flyer.collect())[-1]["data"]["event_kind"] == "abort"
    flyer.unstage()


def test_runengine_abort_archives_partial_data_and_recovers(tmp_path, limits):
    from ophyd_electrochemistry.sequences.callbacks import RunFileWriter

    seq_request = SequenceRequest(
        "abort", Sequence((rest_step(30, sampling=Sampling(0.01)),)), limits
    )
    backend = SimulatedSequenceBackend(speed=10)
    flyer = SequenceFlyer(backend, name="sequence", poll_period_s=0.005)
    re, docs = engine()
    writer = RunFileWriter(tmp_path)
    re.subscribe(writer)
    timer = None

    def abort_after_first_page(name, doc):
        nonlocal timer
        if name == "event_page" and "voltage_v" in doc["data"] and timer is None:
            timer = threading.Timer(0.02, re.abort)
            timer.start()

    token = re.subscribe(abort_after_first_page)
    with pytest.raises(RunEngineInterrupted):
        re(run_sequence(flyer, seq_request, flush_period_s=0.03))
    assert timer is not None
    timer.join(1)
    assert not timer.is_alive()
    assert backend.inspect() == (DeviceState.ABORTED, False)
    assert not flyer.complete().success
    saved = read_run_file(writer.path)
    assert saved["run"]["outcome"] == "aborted"
    assert saved["events"]["kind"][-1] == "abort"
    abort_t = saved["events"]["t_s"][-1]
    retained = saved["readout"]["t_s"]
    np.testing.assert_allclose(retained, np.arange(1, int(abort_t / 0.01) + 1) * 0.01)
    assert 0 < len(retained) < 3000
    assert sum(len(p["time"]) for p in pages(docs, "readout")) == len(retained)
    flyer.recover().wait(1)
    re.unsubscribe(token)
    re.unsubscribe(writer)
    # A distinct run ID also avoids intentional exclusive-file name collisions.
    backend.speed = math.inf
    assert (
        re(run_sequence(flyer, replace(seq_request, measurement_id="recovered"))).plan_result
        == "completed"
    )


def test_invalid_prepare_opens_no_run(seq_request):
    # Revalidation protects against a seq_request altered after construction.
    object.__setattr__(seq_request, "limits", replace(seq_request.limits, i_max_abs=1e-5))
    backend = SimulatedSequenceBackend(speed=math.inf)
    flyer = SequenceFlyer(backend, name="sequence")
    re, docs = engine()
    with pytest.raises(FailedStatus):
        re(run_sequence(flyer, seq_request))
    assert not docs
    assert backend.inspect() == (DeviceState.IDLE, False)


@pytest.mark.parametrize("key", ["equipment", "measurement_id", "sequence", "plan_name"])
def test_reserved_metadata_fails_before_stage(seq_request, key):
    backend = SimulatedSequenceBackend(speed=math.inf)
    flyer = SequenceFlyer(backend, name="sequence")
    re, docs = engine()
    with pytest.raises(ValidationError, match="Reserved"):
        re(run_sequence(flyer, seq_request, md={key: "override"}))
    assert not docs
    assert backend.inspect() == (DeviceState.IDLE, False)


def test_request_from_template_row(tmp_path, limits):
    path = tmp_path / "runs.xlsx"
    make_template(path)
    spec = next(iter(load_run_sheet(path, limits=limits).values()))
    seq_request = SequenceRequest.from_run_spec(spec, limits)
    assert seq_request.sequence == spec.sequence
    assert seq_request.measurement_id == spec.measurement_id
    assert seq_request.run_sheet_row == spec.row
    assert seq_request.sample_id == spec.sample_id
    assert seq_request.notes == spec.notes


@pytest.mark.parametrize("speed", [0, -1, 0.5, math.nan, -math.inf, True])
def test_invalid_simulation_speed(speed):
    with pytest.raises(ValidationError, match="speed"):
        SimulatedSequenceBackend(speed=speed)


def test_fake_clock_release_abort_and_recovery(seq_request):
    now = [100.0]
    backend = SimulatedSequenceBackend(
        speed=2,
        clock=lambda: now[0],
        wall_clock=lambda: now[0] + 1000,
        external_start_delay_s=1,
    )
    backend.connect()
    backend.prepare(replace(seq_request, start_mode="external"))
    backend.kickoff()
    assert backend.poll(1).events == []
    now[0] = 101.5
    # The scheduled start was 101, independent of the first inspection/poll.
    assert backend.inspect() == (DeviceState.RUNNING, True)
    assert backend.time_origin_epoch_s() == 1101
    first = backend.poll(3)
    assert len(first.readout["t_s"]) == 3
    assert np.all(first.readout["t_s"] <= 1)
    backend.abort("test_abort")
    chunks, events = [first.readout["t_s"]], first.events
    while True:
        poll = backend.poll(7)
        chunks.append(poll.readout["t_s"])
        events.extend(poll.events)
        if poll.outcome:
            break
    expected = simulate_sequence(seq_request.sequence, seq_request.limits)
    np.testing.assert_array_equal(
        np.concatenate(chunks), expected.readout["t_s"][: len(np.concatenate(chunks))]
    )
    assert events[-1] == (1.0, 0, 0, "abort", "test_abort")
    assert len(np.concatenate(chunks)) > 3
    assert backend.inspect() == (DeviceState.ABORTED, False)
    backend.recover()
    assert backend.inspect() == (DeviceState.IDLE, False)
    backend.abort("idle no-op")
    assert backend.inspect() == (DeviceState.IDLE, False)
    equipment = backend.equipment()
    json.dumps(equipment, allow_nan=False)
    assert json.loads(equipment["ocv_soc"]) == list(backend.cell.ocv_soc)
    assert all(isinstance(v, (str, int, float, bool)) for v in equipment.values())
    backend.close()


@pytest.mark.parametrize("output", [True, None])
def test_stage_requires_confirmed_off(output):
    class UnsafeBackend(SimulatedSequenceBackend):
        def inspect(self):
            return DeviceState.IDLE, output

    flyer = SequenceFlyer(UnsafeBackend(), name="sequence")
    with pytest.raises(ShutdownUnconfirmed):
        flyer.stage()


def test_lifecycle_guards(seq_request):
    backend = SimulatedSequenceBackend(speed=math.inf)
    flyer = SequenceFlyer(backend, name="sequence", poll_period_s=0.002)
    with pytest.raises(ElectrochemistryError):
        flyer.prepare(seq_request).wait(1)
    with pytest.raises(UnsupportedCapabilityError):
        flyer.resume()
    with pytest.raises(ElectrochemistryError):
        list(flyer.collect())
    flyer.stage()
    flyer.prepare(replace(seq_request, start_mode="external")).wait(1)
    flyer.kickoff().wait(1)
    status = flyer.complete()
    flyer.stop(success=True)
    assert status.done and not status.success
    assert backend.inspect() == (DeviceState.ABORTED, False)
    flyer.unstage()


def test_no_backend_calls_overlap(seq_request):
    class GuardedBackend(SimulatedSequenceBackend):
        def __init__(self):
            super().__init__(speed=math.inf)
            self.busy = threading.Lock()

        def poll(self, max_records):
            assert self.busy.acquire(blocking=False)
            try:
                return super().poll(max_records)
            finally:
                self.busy.release()

        def inspect(self):
            assert self.busy.acquire(blocking=False)
            try:
                return super().inspect()
            finally:
                self.busy.release()

    backend = GuardedBackend()
    flyer = SequenceFlyer(backend, name="sequence", chunk_records=1)
    re, _ = engine()
    assert re(run_sequence(flyer, seq_request, flush_period_s=0.001)).plan_result == "completed"


def test_abort_before_complete_still_has_failed_completion(seq_request):
    backend = SimulatedSequenceBackend(speed=math.inf)
    flyer = SequenceFlyer(backend, name="sequence")
    flyer.stage()
    flyer.prepare(replace(seq_request, start_mode="external")).wait(1)
    flyer.kickoff().wait(1)
    flyer.abort("cancel_before_start").wait(1)
    with pytest.raises(AcquisitionAborted, match="cancel_before_start"):
        flyer.complete().wait(1)
    events = list(flyer.collect())
    assert [event["data"]["event_kind"] for event in events] == ["abort"]
    assert events[0]["data"]["event_t_s"] == 0
    flyer.unstage()


def test_collect_envelope_uses_acquisition_origin(seq_request):
    backend = SimulatedSequenceBackend(speed=math.inf, wall_clock=lambda: 1700000000.0)
    flyer = SequenceFlyer(backend, name="sequence", chunk_records=100)
    flyer.stage()
    flyer.prepare(seq_request).wait(1)
    flyer.kickoff().wait(1)
    flyer.complete().wait(1)
    events = list(flyer.collect())
    origin = backend.time_origin_epoch_s()
    for event in events:
        data = event["data"]
        t_s = data.get("t_s", data.get("event_t_s"))
        assert event["time"] == origin + t_s
        assert all(t == event["time"] for t in event["timestamps"].values())
    assert list(flyer.collect()) == []
    flyer.unstage()


def test_aborted_drain_error_cannot_leave_completion_pending(seq_request):
    class BrokenDrain(SimulatedSequenceBackend):
        def poll(self, max_records):
            if self.inspect()[0] == DeviceState.ABORTED:
                raise RuntimeError("read failed after shutdown")
            return super().poll(max_records)

    backend = BrokenDrain()
    flyer = SequenceFlyer(backend, name="sequence")
    flyer.stage()
    flyer.prepare(replace(seq_request, start_mode="external")).wait(1)
    flyer.kickoff().wait(1)
    with pytest.raises(RuntimeError, match="read failed"):
        flyer.abort("cancel").wait(1)
    with pytest.raises(AcquisitionAborted, match="cancel"):
        flyer.complete().wait(1)
    assert backend.inspect() == (DeviceState.ABORTED, False)
    flyer.unstage()
