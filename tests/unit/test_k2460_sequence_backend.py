"""Hardware sequence backend against the real packaged runtime under Lua.

The fake transport executes every line the backend sends in the runtime-test
mock of the 2460, extended with an event log, a reading buffer and a Python
stand-in for the trigger model's execution (records from the simulator).
"""

import math
from dataclasses import replace

import numpy as np
import pytest

lupa = pytest.importorskip("lupa.lua51")

from bluesky import RunEngine  # noqa: E402
from test_k2460_sequencer_runtime import MOCK  # noqa: E402

from ophyd_electrochemistry.exceptions import (  # noqa: E402
    ElectrochemistryError,
    IncompatibleInstrumentError,
    RetainedDataError,
    ShutdownUnconfirmed,
    TransportProtocolError,
    TransportTimeoutError,
    ValidationError,
)
from ophyd_electrochemistry.keithley.k2460.sequence_backend import (  # noqa: E402
    Keithley2460SequenceBackend,
    guarded,
    parse_event,
    parse_status,
)
from ophyd_electrochemistry.keithley.k2460.sequencer import compile_program  # noqa: E402
from ophyd_electrochemistry.keithley.k2460.sequencer_runtime import (  # noqa: E402
    sequencer_packaged_runtime,
)
from ophyd_electrochemistry.keithley.k2460.transport import InstrumentIdentity  # noqa: E402
from ophyd_electrochemistry.sequences import (  # noqa: E402
    SequenceFlyer,
    SequenceRequest,
    run_sequence,
)
from ophyd_electrochemistry.sequences.backend import READOUT_FIELDS  # noqa: E402
from ophyd_electrochemistry.sequences.builders import (  # noqa: E402
    bipolar_pulse_sequence,
    cc_step,
    rest_step,
)
from ophyd_electrochemistry.sequences.models import (  # noqa: E402
    CutoffAction,
    SafetyLimits,
    Sampling,
    Sequence,
    StepMode,
)
from ophyd_electrochemistry.sequences.simulator import CellModel, simulate_sequence  # noqa: E402
from ophyd_electrochemistry.state import DeviceState  # noqa: E402

CLOCK_OFFSET = 4167.75  # instrument minus host UTC, as on 3 Nov
T0 = 1_793_715_558.25

EXTRA = r"""
localnode = { linefreq = 50 }
clock_s = 0
events = {}
function push_event(number, message, severity, t)
    local s = math.floor(t)
    table.insert(events, { number, message, severity, 2, s, math.floor((t - s) * 1e9 + 0.5) })
end
eventlog.SEV_ALL = "ALL"
eventlog.getcount = function(sev) return #events end
eventlog.next = function(sev)
    local e = table.remove(events, 1)
    return e[1], e[2], e[3], e[4], e[5], e[6]
end
eventlog.post = function(m, s) push_event(1003, "User: " .. m, 4, clock_s) end
trigger.STATE_ABORTED = "trigger.STATE_ABORTED"
trigger.STATE_FAILED = "trigger.STATE_FAILED"
model_block = 0
trigger.model.state = function() return model_state, model_state, model_block end
trigger.model.abort = function()
    table.insert(log, { "abort" })
    if model_state == "trigger.STATE_RUNNING" or model_state == "trigger.STATE_WAITING" then
        model_state = "trigger.STATE_ABORTED"
    end
end
trigger.model.initiate = function()
    table.insert(log, { "initiate" })
    model_state = "trigger.STATE_RUNNING"
    on_initiate()
end
printbuffer = function(first, last, ...) print(py_printbuffer(first, last)) end
"""


class FakeClock:
    def __init__(self):
        self.now = 1_793_711_390.0

    def __call__(self):
        self.now += 0.001
        return self.now


class FakeInstrument:
    """Transport whose instrument is the packaged runtime in a mocked 2460."""

    def __init__(self, clock, *, firmware="1.7.16a", language="TSP"):
        self.clock = clock
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(MOCK)
        self.g = self.lua.globals()
        self.g.py_printbuffer = self._printbuffer
        self.g.on_initiate = self._on_initiate
        self.lua.execute(EXTRA)
        self.identity = InstrumentIdentity(
            "KEITHLEY INSTRUMENTS",
            "2460",
            "04567",
            firmware,
            f"KEITHLEY INSTRUMENTS,MODEL 2460,04567,{firmware}",
        )
        self.command_language = language
        self.connected = False
        self.records: list[tuple[float, ...]] = []
        self.sent: list[str] = []
        self.instant = None  # (records, logged) produced as soon as the model starts
        self.fail_on: str | None = None

    # Transport

    def connect(self):
        self.connected = True

    def close(self):
        self.connected = False

    def query(self, command):
        if not self.connected:
            raise TransportTimeoutError("not connected")
        if self.fail_on is not None and self.fail_on in command:
            self.connected = False
            raise TransportTimeoutError(f"timed out during {command!r}; session invalidated")
        self.sent.append(command)
        self.g.clock_s = self.clock() + CLOCK_OFFSET
        self.g.outputs = self.lua.table()
        self.lua.execute(command)
        lines = list(self.g.outputs.values())
        if not lines:
            self.connected = False
            raise TransportTimeoutError(f"no reply to {command!r}; session invalidated")
        if len(lines) > 1:
            raise TransportProtocolError(f"{len(lines)} reply lines to {command!r}")
        return lines[0]

    def load_runtime(self, source, *, abi, sha256):
        artifact = sequencer_packaged_runtime()
        assert (source, abi, sha256) == (artifact.source, artifact.abi, artifact.sha256)
        assert self.g.src.output == "smu.OFF"
        if self.g.oe_seq_runtime_abi is None:
            self.lua.execute(source)
        self.g.oe_seq_initialize()

    # Instrument side

    def _printbuffer(self, first, last):
        rows = self.records[int(first) - 1 : int(last)]
        return ", ".join(repr(float(v)) for row in rows for v in row)

    def _on_initiate(self):
        if self.instant is not None:
            records, logged = self.instant
            self.start()
            self.add(records)
            self.finish(logged)

    def log(self, name, t, block=3):
        self.g.push_event(2734, f"TM path #1 block #{block} logged: {name}", 4, t)

    def start(self, t0=T0):
        self.log("OE_T0", t0, 1)
        self.g.src.output = "smu.ON"

    def add(self, rows):
        self.records.extend(rows)
        self.g.oe_seq_buf.n = len(self.records)

    def finish(self, logged="end", t=None):
        t = (self.records[-1][0] + self.records[-1][1] if self.records else T0) if t is None else t
        self.log("OE_END" if logged == "end" else "OE_STOP", t + 0.001)
        self.log("OE_DONE", t + 0.001)
        self.g.model_state = "trigger.STATE_IDLE"
        self.g.src.output = "smu.OFF"


def instrument_records(sequence, limits, *, seed=0, t0=T0):
    """Simulated readings as the instrument stores them (time, reading, source value)."""
    sim = simulate_sequence(sequence, limits, CellModel(noise_v=1e-6), seed=seed)
    r = sim.readout
    voltage_step = np.array([s.mode == StepMode.VOLTAGE for s in sequence.steps])[r["step_index"]]
    reading = np.where(voltage_step, r["current_a"], r["voltage_v"])
    source = np.where(voltage_step, r["voltage_v"], r["current_a"])
    rows = []
    for i, t_s in enumerate(r["t_s"]):
        t = t0 + float(t_s)
        seconds = math.floor(t)
        rows.append(
            (
                seconds,
                t - seconds,
                reading[i],
                source[i],
                r["measure_status"][i],
                r["source_status"][i],
            )
        )
    return sim, rows


@pytest.fixture
def limits():
    return SafetyLimits(0, 4, 0.01, 10000, 100000)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def instrument(clock):
    return FakeInstrument(clock)


def make_backend(instrument, clock, **kwargs):
    return Keithley2460SequenceBackend(instrument, terminals="front", wall_clock=clock, **kwargs)


@pytest.fixture
def backend(instrument, clock):
    b = make_backend(instrument, clock)
    b.connect()
    return b


def pulse_request(limits, **kwargs):
    return SequenceRequest("pulse 001", bipolar_pulse_sequence(0.001, 0.1, 2), limits, **kwargs)


def drain(backend, max_records=100):
    readouts, events, outcome, detail = [], [], None, ""
    for _ in range(1000):
        poll = backend.poll(max_records)
        readouts.append(poll.readout)
        events.extend(poll.events)
        if poll.outcome is not None and len(poll.readout["t_s"]) < max_records:
            outcome, detail = poll.outcome, poll.detail
            break
    readout = {k: np.concatenate([r[k] for r in readouts]) for k in READOUT_FIELDS}
    return readout, events, outcome, detail


# Helpers


def test_guarded_wraps_numeric_calls_only():
    assert guarded("oe_seq_ack(5)") == (
        'local ok, e = pcall(oe_seq_ack, 5) if not ok then print("ERR " .. tostring(e)) end'
    )
    assert guarded("oe_seq_initialize()", prints=False).endswith(
        'if ok then print("OK") else print("ERR " .. tostring(e)) end'
    )
    for bad in (
        'oe_seq_ack("x")',
        "os.exit()",
        "oe_seq_ack(1) print(2)",
        "oe_seq_ack(" + "1" * 900 + ")",
    ):
        with pytest.raises(ValidationError):
            guarded(bad)


def test_parsers_reject_malformed_replies():
    assert parse_status("armed\ttrigger.STATE_RUNNING\t9\tsmu.ON\t12\t0").active
    assert parse_event(
        "2734\tTM path #1 block #1 logged: OE_T0\t4\t2\t1793715558\t381898180"
    ).time == (pytest.approx(1793715558.381898180))
    for reply in (
        "armed\tx",
        "armed\ttrigger.STATE_IDLE\t1.5\tsmu.OFF\t0\t0",
        "a\tb\t1\tsmu.MAYBE\t0\t0",
    ):
        with pytest.raises(TransportProtocolError):
            parse_status(reply)
    with pytest.raises(TransportProtocolError):
        parse_event("1003\tUser: OE_PING")


# Connection


def test_connect_installs_runtime_and_verifies_identity(instrument, clock):
    backend = make_backend(instrument, clock)
    backend.connect()
    assert instrument.g.oe_seq_runtime_build == "seq-v1"
    assert backend.inspect() == (DeviceState.IDLE, False)
    assert backend.equipment()["firmware"] == "1.7.16a"


@pytest.mark.parametrize(
    "change",
    [
        lambda i: setattr(i, "identity", replace(i.identity, firmware_version="1.7.12b")),
        lambda i: setattr(i, "command_language", "SCPI"),
        lambda i: setattr(i.g.localnode, "linefreq", 60),
    ],
)
def test_connect_refuses_unreviewed_instrument(instrument, clock, change):
    change(instrument)
    backend = make_backend(instrument, clock)
    with pytest.raises(IncompatibleInstrumentError):
        backend.connect()
    assert not instrument.connected


def test_connect_never_installs_under_a_running_model(instrument, clock):
    instrument.g.model_state = "trigger.STATE_RUNNING"
    with pytest.raises(IncompatibleInstrumentError, match="runtime build"):
        make_backend(instrument, clock).connect()
    assert instrument.g.oe_seq_runtime_abi is None


# Prepare


def test_prepare_uploads_program_and_maps_clock(backend, instrument, limits):
    request = pulse_request(limits)
    backend.prepare(request)
    g = instrument.g
    assert backend.inspect() == (DeviceState.PREPARED, False)
    assert g.oe_seq_state == "ready"
    assert (g.oe_seq_cfg.ready, g.oe_seq_cfg.busy, g.oe_seq_cfg.marker, g.oe_seq_cfg.start) == (
        1,
        2,
        4,
        6,
    )
    assert g.oe_seq_cfg.vlimit_max == pytest.approx(4.1)
    assert (
        g.offmodes["smu.FUNC_DC_CURRENT"]
        == g.offmodes["smu.FUNC_DC_VOLTAGE"]
        == "smu.OFFMODE_HIGHZ"
    )
    program = compile_program(request.sequence, limits)
    assert len(list(g.blocks.keys())) == len(program.blocks)
    equipment = backend.equipment()
    assert equipment["clock_offset_s"] == pytest.approx(CLOCK_OFFSET, abs=1e-3)
    assert 0 < equipment["clock_uncertainty_s"] <= 0.001
    assert equipment["block_count"] == len(program.blocks)
    assert equipment["marker_line"] == 4
    # Every line sent was a fixed query or a guarded packaged call.
    assert all(
        line.startswith(("print(", "local ok, e = pcall(oe_seq_")) for line in instrument.sent
    )


def test_runtime_refusal_reports_the_message_and_leaves_output_off(backend, instrument, limits):
    instrument.lua.execute('oe_seq_measure = function() error("boom") end')
    with pytest.raises(ElectrochemistryError, match="oe_seq_measure.*boom"):
        backend.prepare(pulse_request(limits))
    assert backend.inspect() == (DeviceState.IDLE, False)


def test_upload_warnings_fail_prepare(backend, instrument, limits):
    instrument.lua.execute(
        "local commit = oe_seq_commit "
        "oe_seq_commit = function() "
        'push_event(5076, "Source Limit changed to 0.21 V due to new measure Range", 2, 0) '
        "commit() end"
    )
    with pytest.raises(ElectrochemistryError, match="5076"):
        backend.prepare(pulse_request(limits))


def test_earlier_instrument_events_are_recorded_not_fatal(backend, instrument, limits):
    instrument.g.push_event(-363, "Input buffer overrun", 1, 0)
    backend.prepare(pulse_request(limits))
    assert backend.equipment()["pre_run_instrument_events"] == "-363 Input buffer overrun"


# Runs


def test_completed_run_matches_simulation(backend, instrument, limits, clock):
    request = pulse_request(limits)
    sim, rows = instrument_records(request.sequence, limits)
    backend.prepare(request)
    backend.kickoff()
    assert backend.inspect() == (DeviceState.RUNNING, False)
    assert backend.time_origin_epoch_s() is None
    instrument.start()
    instrument.add(rows[:150])
    first = backend.poll(100)
    assert len(first.readout["t_s"]) == 100 and first.outcome is None
    assert backend.inspect() == (DeviceState.RUNNING, True)
    instrument.add(rows[150:])
    instrument.finish("end")
    readout, events, outcome, detail = drain(backend)
    readout = {k: np.concatenate([first.readout[k], readout[k]]) for k in READOUT_FIELDS}
    assert (outcome, detail) == ("completed", "")
    for key in ("step_index", "repeat_index", "measure_status", "source_status"):
        np.testing.assert_array_equal(readout[key], sim.readout[key])
    np.testing.assert_allclose(readout["t_s"], sim.readout["t_s"], atol=1e-6)
    np.testing.assert_allclose(readout["voltage_v"], sim.readout["voltage_v"])
    events = first.events + events
    assert [e[1:] for e in events] == [e[1:] for e in sim.events]
    assert backend.inspect() == (DeviceState.COMPLETE, False)
    assert backend.time_origin_epoch_s() == pytest.approx(T0 - CLOCK_OFFSET, abs=1e-3)


def test_reads_are_chunked_at_250_records(backend, instrument, limits):
    request = pulse_request(limits)
    _, rows = instrument_records(request.sequence, limits)
    backend.prepare(request)
    backend.kickoff()
    instrument.start()
    instrument.add(rows)
    instrument.sent.clear()
    poll = backend.poll(600)
    assert len(poll.readout["t_s"]) == min(600, len(rows))
    reads = [line for line in instrument.sent if "oe_seq_read" in line]
    assert [r.split("pcall(oe_seq_read, ")[1].split(")")[0] for r in reads] == [
        "1, 250",
        "251, 250",
        "501, 100",
    ]


def test_stop_cutoff_run(backend, instrument):
    limits = SafetyLimits(-2, 2.5, 0.005, 1e6, 999984)
    sequence = Sequence(
        (
            rest_step(0.5, Sampling(0.1)),
            cc_step(0.001, 1.04, 30, Sampling(0.1), CutoffAction.STOP),
            rest_step(1, Sampling(0.1)),
        )
    )
    request = SequenceRequest("stop 001", sequence, limits)
    cell = CellModel(ocv_soc=(0.0, 1.0), ocv_v=(1.0, 1.0), noise_v=1e-6)
    sim = simulate_sequence(sequence, limits, cell, seed=1)
    assert sim.outcome == "stopped_cutoff"
    rows = [
        (math.floor(T0 + t), T0 + t - math.floor(T0 + t), v, i, 0, 0)
        for t, v, i in zip(
            sim.readout["t_s"], sim.readout["voltage_v"], sim.readout["current_a"], strict=True
        )
    ]
    backend.prepare(request)
    backend.kickoff()
    instrument.start()
    instrument.add(rows)
    instrument.finish("stop")
    readout, events, outcome, _ = drain(backend)
    assert outcome == "stopped_cutoff"
    assert [e[3] for e in events][-2:] == ["cutoff", "stop"]
    np.testing.assert_array_equal(readout["step_index"], sim.readout["step_index"])


def test_logged_end_disagreeing_with_replay_is_an_error(backend, instrument, limits):
    request = pulse_request(limits)
    _, rows = instrument_records(request.sequence, limits)
    backend.prepare(request)
    backend.kickoff()
    instrument.start()
    instrument.add(rows[:-5])
    instrument.finish("end")
    *_, outcome, detail = drain(backend)
    assert outcome == "error"
    assert "predicted running, logged end" in detail


def test_instrument_error_during_run_fails_outcome(backend, instrument, limits):
    request = pulse_request(limits)
    _, rows = instrument_records(request.sequence, limits)
    backend.prepare(request)
    backend.kickoff()
    instrument.start()
    instrument.add(rows)
    instrument.g.push_event(5077, "Cannot change source Limit", 1, T0 + 0.05)
    instrument.finish("end")
    _, events, outcome, detail = drain(backend)
    assert outcome == "error"
    assert "instrument 5077: Cannot change source Limit" in detail
    errors = [e for e in events if e[3] == "error"]
    assert errors and errors[0][0] == pytest.approx(0.05, abs=1e-6)


def test_stale_on_after_model_end_is_forced_off_and_fails(backend, instrument, limits):
    request = pulse_request(limits)
    _, rows = instrument_records(request.sequence, limits)
    backend.prepare(request)
    backend.kickoff()
    instrument.start()
    instrument.add(rows)
    instrument.finish("end")
    instrument.g.src.output = "smu.ON"
    instrument.g.stale_on = True
    *_, outcome, detail = drain(backend)
    assert outcome == "error" and "forced OFF" in detail
    assert backend.inspect() == (DeviceState.COMPLETE, False)


def test_external_start_waits_then_runs(backend, instrument, limits):
    backend.prepare(pulse_request(limits, start_mode="external"))
    backend.kickoff()
    instrument.g.model_state = "trigger.STATE_WAITING"
    instrument.g.model_block = 2
    assert backend.inspect() == (DeviceState.WAITING_START, False)
    poll = backend.poll(100)
    assert len(poll.readout["t_s"]) == 0 and poll.outcome is None
    assert backend.time_origin_epoch_s() is None
    instrument.g.model_block = 9  # a later WAITING (not the START wait) is running
    assert backend.inspect()[0] == DeviceState.RUNNING


def test_abort_mid_run_drains_and_recovers(backend, instrument, limits):
    request = pulse_request(limits)
    _, rows = instrument_records(request.sequence, limits)
    backend.prepare(request)
    backend.kickoff()
    instrument.start()
    instrument.add(rows[:40])
    backend.poll(10)
    backend.abort("user")
    assert backend.inspect() == (DeviceState.ABORTED, False)
    assert instrument.g.digio.line[4].state == "LOW"
    readout, events, outcome, detail = drain(backend)
    assert (outcome, detail) == ("aborted", "user")
    assert len(readout["t_s"]) == 30
    assert events[-1][3:] == ("abort", "user")
    backend.recover()
    assert backend.inspect() == (DeviceState.IDLE, False)
    backend.prepare(request)  # all 40 records were read: acknowledged, not refused
    assert backend.inspect() == (DeviceState.PREPARED, False)


def test_abort_while_waiting_for_start(backend, instrument, limits):
    backend.prepare(pulse_request(limits, start_mode="external"))
    backend.kickoff()
    instrument.g.model_state = "trigger.STATE_WAITING"
    backend.abort("start timeout")
    _, events, outcome, _ = drain(backend)
    assert outcome == "aborted" and events == [(0.0, 0, 0, "abort", "start timeout")]


def test_abort_refuses_unconfirmed_off(backend, instrument, limits):
    backend.prepare(pulse_request(limits))
    backend.kickoff()
    instrument.g.src.output = "smu.ON"
    instrument.lua.execute('oe_seq_off = function() print("ON") end')
    with pytest.raises(ShutdownUnconfirmed):
        backend.abort("user")


def test_transport_timeout_needs_recover(backend, instrument, limits):
    backend.prepare(pulse_request(limits))
    backend.kickoff()
    instrument.fail_on = "oe_seq_status"
    with pytest.raises(TransportTimeoutError):
        backend.poll(10)
    assert backend.inspect() == (DeviceState.ERROR, None)
    with pytest.raises(ElectrochemistryError, match="recover"):
        backend.poll(10)
    instrument.fail_on = None
    backend.recover()
    assert backend.inspect() == (DeviceState.IDLE, False)
    assert instrument.g.model_state == "trigger.STATE_ABORTED"


# Retained data


def test_unread_records_of_another_session_must_be_archived(instrument, clock, limits, tmp_path):
    first = make_backend(instrument, clock)
    first.connect()
    request = pulse_request(limits)
    _, rows = instrument_records(request.sequence, limits)
    first.prepare(request)
    first.kickoff()
    instrument.start()
    instrument.add(rows)
    instrument.finish("end")
    # The host process dies here; a new one connects to the same instrument.
    second = make_backend(instrument, clock)
    second.connect()
    assert second.inspect() == (DeviceState.IDLE, False)
    with pytest.raises(RetainedDataError, match=f"{len(rows)} unread records"):
        second.prepare(request)
    path = second.archive_retained(tmp_path / "retained.npz")
    with np.load(path) as saved:
        np.testing.assert_allclose(saved["reading"], [r[2] for r in rows])
        assert '"first_record": 1' in str(saved["metadata"])
    with pytest.raises(FileExistsError):
        second.archive_retained(path)
    second.prepare(request)
    assert second.inspect() == (DeviceState.PREPARED, False)


def test_consecutive_runs_acknowledge_read_records(backend, instrument, limits):
    request = pulse_request(limits)
    _, rows = instrument_records(request.sequence, limits)
    for _ in range(2):
        instrument.records = []
        backend.prepare(request)
        backend.kickoff()
        instrument.start()
        instrument.add(rows)
        instrument.finish("end")
        assert drain(backend)[2] == "completed"
    assert instrument.g.oe_seq_acked == 0  # the second run's records are not yet acknowledged


# Through the Flyer and the RunEngine


def test_run_sequence_end_to_end(backend, instrument, limits, tmp_path):
    from ophyd_electrochemistry.sequences.callbacks import RunFileWriter
    from ophyd_electrochemistry.sequences.run_file import read_run_file

    request = pulse_request(limits)
    sim, rows = instrument_records(request.sequence, limits)
    instrument.instant = (rows, "end")
    flyer = SequenceFlyer(backend, name="k2460", poll_period_s=0.01, chunk_records=97)
    re = RunEngine({}, call_returns_result=True)
    docs = []
    re.subscribe(lambda name, doc: docs.append((name, doc)))
    writer = RunFileWriter(tmp_path)
    re.subscribe(writer)
    assert re(run_sequence(flyer, request)).plan_result == "completed"
    saved = read_run_file(writer.path)
    assert saved["run"]["outcome"] == "completed"
    assert saved["equipment"]["clock_method"].startswith("eventlog.post")
    np.testing.assert_array_equal(saved["readout"]["step_index"], sim.readout["step_index"])
    descriptor = next(d["uid"] for n, d in docs if n == "descriptor" and d["name"] == "readout")
    pages = [d for n, d in docs if n == "event_page" and d["descriptor"] == descriptor]
    np.testing.assert_array_equal(
        np.concatenate([p["data"]["step_index"] for p in pages]), sim.readout["step_index"]
    )
    start = next(d for n, d in docs if n == "start")
    assert start["equipment"]["backend"] == "k2460-sequencer"
    assert start["equipment"]["marker_line"] == 4
    # Unstaging closed the session with the output OFF.
    assert not instrument.connected and instrument.g.src.output == "smu.OFF"


def test_new_session_after_a_closed_run_prepares_without_archive(instrument, clock, limits):
    # The notebook builds a new backend per run: closing (unstage) acknowledges.
    request = pulse_request(limits)
    _, rows = instrument_records(request.sequence, limits)
    first = make_backend(instrument, clock)
    first.connect()
    first.prepare(request)
    first.kickoff()
    instrument.start()
    instrument.add(rows)
    instrument.finish("end")
    assert drain(first)[2] == "completed"
    first.close()
    assert instrument.g.oe_seq_acked == len(rows)
    second = make_backend(instrument, clock)
    second.connect()
    second.prepare(request)
    assert second.inspect() == (DeviceState.PREPARED, False)


def test_close_does_not_acknowledge_an_unfinished_read(backend, instrument, limits):
    request = pulse_request(limits)
    _, rows = instrument_records(request.sequence, limits)
    backend.prepare(request)
    backend.kickoff()
    instrument.start()
    instrument.add(rows)
    instrument.finish("end")
    backend.poll(10)
    backend.close()
    assert instrument.g.oe_seq_acked == 0
    backend.connect()
    with pytest.raises(RetainedDataError):
        backend.prepare(request)
