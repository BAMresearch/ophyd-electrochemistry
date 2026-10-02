"""Host knowledge, ambiguous mutations, disconnect and chunk failure checks."""

import pytest

from ophyd_electrochemistry import AcquisitionRequest, GalvanostaticHold, StartMode
from ophyd_electrochemistry.exceptions import (
    AcquisitionAborted,
    ElectrochemistryError,
    ShutdownUnconfirmed,
)
from ophyd_electrochemistry.simulation import FakeTransport, FaultInjection, SimulatedRuntime
from ophyd_electrochemistry.state import DeviceState


def request(mode=StartMode.IMMEDIATE):
    return AcquisitionRequest(
        experiment_id="m2-link-test",
        start_mode=mode,
        program=GalvanostaticHold(
            current_a=0.01, duration_s=0.04, sample_period_s=0.01, voltage_limit_v=5
        ),
    )


@pytest.mark.parametrize("mode", list(StartMode))
def test_disconnect_does_not_stop_local_finite_execution_or_timeout(link, runtime, mode):
    link.prepare(request(mode), acquisition_id="acq-1")
    link.kickoff()
    link.disconnect()
    assert link.observed_output is None and link.observed_state == DeviceState.ERROR
    with pytest.raises(ShutdownUnconfirmed):
        link.abort()
    runtime.advance_ticks(100)
    assert runtime.output is False
    assert runtime.state == (
        DeviceState.COMPLETE if mode == StartMode.IMMEDIATE else DeviceState.ABORTED
    )
    retained = runtime.records
    link.connect()
    assert link.observed_output is None  # Connection success is not state confirmation.
    assert link.inspect() == (runtime.state, False)
    assert runtime.records == retained
    assert link.collect_chunk(max_records=100) == retained


@pytest.mark.parametrize("operation", ["prepare", "kickoff", "abort"])
def test_lost_reply_after_mutation_requires_reconciliation_no_replay(link, runtime, operation):
    if operation != "prepare":
        link.prepare(request(), acquisition_id="acq-1")
    if operation == "abort":
        link.kickoff()
        runtime.advance_ticks(5)
    link.fail_next_transaction()
    with pytest.raises(ShutdownUnconfirmed):
        if operation == "prepare":
            link.prepare(request(), acquisition_id="acq-1")
        else:
            getattr(link, operation)()
    expected = {
        "prepare": DeviceState.PREPARED,
        "kickoff": DeviceState.RUNNING,
        "abort": DeviceState.ABORTED,
    }[operation]
    assert runtime.state == expected and link.observed_output is None
    trace = runtime.source_trace
    link.connect()
    assert link.observed_output is None
    assert link.inspect()[0] == expected
    assert runtime.source_trace == trace  # No automatic replay/source action on reconnect.


def test_snapshot_query_after_disconnect_never_returns_fresh_fake_zero(link, runtime):
    link.prepare(request(), acquisition_id="acq-1")
    link.kickoff()
    runtime.advance_ticks(5)
    snapshot = link.latch_snapshot()
    link.disconnect()
    with pytest.raises(ShutdownUnconfirmed):
        link.read_snapshot()
    link.connect()
    assert link.read_snapshot() is snapshot


def test_lost_chunk_reply_keeps_raw_data_for_diagnostic_replay(link, runtime, tmp_path):
    link.prepare(request(), acquisition_id="acq-1")
    link.kickoff()
    runtime.advance_ticks(40)
    link.fail_next_transaction()
    with pytest.raises(ShutdownUnconfirmed):
        link.collect_chunk(max_records=2)
    assert len(runtime.records) == 4
    link.connect()
    # No exactly-once promise: cursor advanced, but export always has all raw records.
    assert [r.sample_index for r in link.collect_chunk(max_records=2)] == [2, 3]
    link.export_retained_data(str(tmp_path / "all-records.json"))
    assert len(runtime.records) == 4


def test_chunk_boundaries_allow_abort_between_transactions(link, runtime):
    link.prepare(request(), acquisition_id="acq-1")
    link.kickoff()
    runtime.advance_ticks(15)
    link.abort(reason="manual")
    first = link.collect_chunk(max_records=1)
    assert first[0].sample_index == 0
    outcome = link.abort(reason="repeat-between-chunks")
    assert outcome.cause == "manual"
    assert link.collect_chunk(max_records=1)[0].sample_index == 1
    with pytest.raises(AcquisitionAborted):
        link.completion()


def test_bounded_polling_helper_and_close(link, runtime):
    link.prepare(request(), acquisition_id="acq-1")
    link.kickoff()
    with pytest.raises(ElectrochemistryError):
        link.close()
    with pytest.raises(TimeoutError):
        link.poll_until_terminal(max_ticks=10, poll_ticks=3)
    assert runtime.state == DeviceState.RUNNING
    assert link.poll_until_terminal(max_ticks=30, poll_ticks=7).success
    link.close()
    assert not link.connected and link.observed_output is None


def test_shutdown_failure_host_observation_unknown(runtime, capabilities, config):
    sim = SimulatedRuntime(
        capabilities=capabilities,
        config=config,
        cell=runtime.cell,
        faults=FaultInjection(shutdown_confirmation_lost=True),
    )
    link = FakeTransport(sim)
    link.connect()
    link.prepare(request(), acquisition_id="acq-1")
    link.kickoff()
    with pytest.raises(ShutdownUnconfirmed):
        link.abort()
    assert link.observed_output is None and link.observed_state == DeviceState.ERROR


def test_external_abort_is_local_after_host_disconnect(link, runtime):
    link.prepare(request(StartMode.EXTERNAL_TRIGGER), acquisition_id="acq-1")
    link.kickoff()
    runtime.set_inputs(start=True, abort=False)
    runtime.advance_ticks(5)
    link.disconnect()
    runtime.set_inputs(start=False, abort=True)
    assert runtime.state == DeviceState.ABORTED and runtime.output is False
    assert runtime.outcome.cause == "external_abort" and len(runtime.records) == 1
    link.connect()
    assert link.inspect() == (DeviceState.ABORTED, False)
