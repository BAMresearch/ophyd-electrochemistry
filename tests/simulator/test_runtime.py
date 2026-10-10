"""Independent expected traces/integrals, lifecycle races and retained evidence."""

import hashlib
import json
import math
from dataclasses import FrozenInstanceError, replace

import pytest

from ophyd_electrochemistry import (
    AcquisitionRequest,
    ArbitraryWaveform,
    CurrentPulseSequence,
    CyclicVoltammetry,
    GalvanostaticHold,
    MultisineSpec,
    PotentiostaticHold,
    PRBSWaveform,
    StartMode,
    generate_multisine,
)
from ophyd_electrochemistry.exceptions import (
    AcquisitionAborted,
    ElectrochemistryError,
    RetainedDataError,
    ShutdownUnconfirmed,
    ValidationError,
)
from ophyd_electrochemistry.keithley.k2460.compiler import CompiledProgram, SourceSchedule
from ophyd_electrochemistry.simulation import FakeCell, FaultInjection, SimulatedRuntime
from ophyd_electrochemistry.state import DeviceState


def request(mode=StartMode.IMMEDIATE, program=None):
    return AcquisitionRequest(
        experiment_id="m2-independent-test",
        start_mode=mode,
        program=program
        if program is not None
        else GalvanostaticHold(
            current_a=0.01,
            duration_s=0.04,
            sample_period_s=0.01,
            voltage_limit_v=5,
        ),
    )


def arbitrary(levels=(0.01, -0.01, 0), *, repeats=2, sample_period_s=0.006):
    return ArbitraryWaveform(
        source_function="current",
        levels=levels,
        repeats=repeats,
        point_period_s=0.01,
        sample_period_s=sample_period_s,
        voltage_limit_v=5,
    )


def start(runtime, value=None):
    program = runtime.prepare(value or request(), acquisition_id="acq-1")
    runtime.kickoff()
    if runtime.state == DeviceState.WAITING_START:
        runtime.set_inputs(start=True, abort=False)
    return program


@pytest.mark.parametrize("mode", list(StartMode))
def test_prepare_kickoff_completion_local_clock(runtime, mode):
    runtime.advance_ticks(100)
    p = runtime.prepare(request(mode), acquisition_id="acq-1")
    assert runtime.state == DeviceState.PREPARED
    assert runtime.output is False and not runtime.ready and not runtime.busy
    assert not runtime.records and not runtime.source_trace
    assert runtime.completion() is None
    runtime.kickoff()
    if mode == StartMode.EXTERNAL_TRIGGER:
        assert runtime.state == DeviceState.WAITING_START and runtime.ready
        assert runtime.output is False and not runtime.source_trace
        runtime.advance_ticks(15)
        runtime.set_inputs(start=True, abort=False)
        actual_start = 115
    else:
        actual_start = 100
    assert runtime.state == DeviceState.RUNNING and runtime.output is True
    assert runtime.busy and not runtime.ready
    runtime.advance_ticks(p.source.duration_ticks - 1)
    assert runtime.completion() is None and runtime.output is True
    runtime.advance_ticks(1)
    assert runtime.state == DeviceState.COMPLETE and runtime.output is False
    assert not runtime.busy and not runtime.ready
    assert runtime.completion().start_tick == actual_start
    assert runtime.outcome.terminal_tick == actual_start + 40
    assert len(runtime.records) == 4
    assert [r.aperture_start_tick for r in runtime.records] == [2, 12, 22, 32]
    assert [r.available_tick for r in runtime.records] == [5, 15, 25, 35]
    assert all(r.voltage_v == pytest.approx(0.2) for r in runtime.records)
    assert all(r.current_a == pytest.approx(0.01) for r in runtime.records)


@pytest.mark.parametrize("split", [1, 7, 60, 1000])
def test_arbitrary_trace_independent_of_host_polling(runtime, split, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Simulator must not reuse production schedule mapping")

    # Source expected directly from user levels/dwells; no generator/mapping helper.
    monkeypatch.setattr(CompiledProgram, "sample_mapping", forbidden)
    monkeypatch.setattr(SourceSchedule, "point_at_tick", forbidden)
    p = start(runtime, request(program=arbitrary()))
    remaining = p.source.duration_ticks
    while remaining:
        delta = min(remaining, split)
        runtime.advance_ticks(delta)
        remaining -= delta
    assert [
        (s.relative_tick, s.level, s.logical_point, s.repeat_index, s.point_index)
        for s in runtime.source_trace
    ] == [
        (0, 0.01, 0, 0, 0),
        (10, -0.01, 1, 0, 1),
        (20, 0, 2, 0, 2),
        (30, 0.01, 3, 1, 0),
        (40, -0.01, 4, 1, 1),
        (50, 0, 5, 1, 2),
    ]
    assert runtime.outcome.terminal_tick == 60
    assert runtime.outcome.waveform_complete
    # Independently integrate tick-by-tick from the original levels.
    for r in runtime.records:
        window = list(range(r.aperture_start_tick, r.aperture_end_tick))
        expected_i = math.fsum((0.01, -0.01, 0)[(t // 10) % 3] for t in window) / len(window)
        assert r.current_a == pytest.approx(expected_i, abs=1e-15)
        assert r.voltage_v == pytest.approx(0.1 + 10 * expected_i)
        assert r.first_logical_point == window[0] // 10
        assert r.last_logical_point == window[-1] // 10
        assert r.wholly_settled_in_one_dwell == (
            window[0] // 10 == window[-1] // 10 and window[0] % 10 >= 2
        )
    # Zero is still sourced throughout its final full dwell.
    assert runtime.source_trace[-1].level == 0
    assert runtime.outcome.source_points_executed == 6


def test_aperture_crosses_edge_without_invented_single_point(runtime, capabilities, config):
    runtime = SimulatedRuntime(
        capabilities=replace(
            capabilities, measurement_aperture_s=0.008, measurement_overhead_s=0.001
        ),
        config=replace(config, timing=replace(config.timing, required_cutoff_latency_s=0.2)),
        cell=runtime.cell,
    )
    start(runtime, request(program=arbitrary(sample_period_s=0.012)))
    runtime.advance_ticks(60)
    # [14,22) crosses 20: six ticks at -10mA and two at zero.
    r = runtime.records[1]
    assert (r.aperture_start_tick, r.aperture_end_tick) == (14, 22)
    assert (r.first_logical_point, r.last_logical_point) == (1, 2)
    assert r.current_a == pytest.approx(-0.0075)
    assert r.source_setpoint == -0.01
    assert not r.wholly_settled_in_one_dwell
    # [2,10) ends exactly on edge and belongs entirely to point zero.
    first = runtime.records[0]
    assert first.first_logical_point == first.last_logical_point == 0
    assert first.wholly_settled_in_one_dwell


def test_prbs_equal_bits_keep_independent_logical_dwell(runtime):
    program = PRBSWaveform(
        source_function="current",
        voltage_limit_v=5,
        low_level=-0.01,
        high_level=0.01,
        bit_period_s=0.01,
        sample_period_s=0.006,
        order=3,
        seed=1,
        repeats=2,
    )
    start(runtime, request(program=program))
    runtime.advance_ticks(140)
    bits = "1001011" * 2  # Independent known vector, never generated by the implementation.
    assert [s.level for s in runtime.source_trace] == [0.01 if b == "1" else -0.01 for b in bits]
    assert [s.relative_tick for s in runtime.source_trace] == list(range(0, 140, 10))
    assert [s.logical_point for s in runtime.source_trace] == list(range(14))
    assert runtime.source_trace[5].level == runtime.source_trace[6].level
    assert runtime.outcome.source_points_executed == 14


def test_multisine_uses_same_arbitrary_execution(runtime):
    spec = MultisineSpec(
        source_function="current",
        voltage_limit_v=5,
        bias=0.005,
        frequencies_hz=(6.25,),
        amplitudes=(0.002,),
        phases_rad=(0.3,),
        point_period_s=0.01,
        point_count=16,
        sample_period_s=0.006,
        repeats=2,
    )
    start(runtime, request(program=generate_multisine(spec)))
    runtime.advance_ticks(320)
    assert [s.level for s in runtime.source_trace] == pytest.approx(
        [0.005 + 0.002 * math.sin(2 * math.pi * k / 16 + 0.3) for k in range(16)] * 2
    )
    assert [s.relative_tick for s in runtime.source_trace] == list(range(0, 320, 10))
    assert runtime.completion().success


def test_pulses_baseline_and_full_last_dwell(runtime):
    p = CurrentPulseSequence(
        baseline_current_a=0,
        pulse_current_a=0.01,
        pulse_width_s=0.01,
        period_s=0.03,
        count=2,
        sample_period_s=0.006,
        voltage_limit_v=5,
    )
    start(runtime, request(program=p))
    runtime.advance_ticks(59)
    assert runtime.output is True
    assert [(s.relative_tick, s.level) for s in runtime.source_trace] == [
        (0, 0.01),
        (10, 0),
        (30, 0.01),
        (40, 0),
    ]
    runtime.advance_ticks(1)
    assert runtime.completion().terminal_tick == 60


def test_cv_endpoints_and_cycles_survive_execution(runtime):
    p = CyclicVoltammetry(
        start_voltage_v=0,
        first_vertex_v=0.1,
        second_vertex_v=-0.1,
        scan_rate_v_per_s=10,
        cycles=2,
        sample_period_s=0.01,
        current_limit_a=0.1,
    )
    start(runtime, request(program=p))
    runtime.advance_ticks(90)
    assert [s.level for s in runtime.source_trace] == pytest.approx(
        [
            0,
            0.1,
            0,
            -0.1,
            0,
            0.1,
            0,
            -0.1,
            0,
        ]
    )
    assert [s.relative_tick for s in runtime.source_trace] == list(range(0, 90, 10))
    assert runtime.records[-1].source_setpoint == 0
    assert runtime.outcome.waveform_complete


@pytest.mark.parametrize(
    "stage,tick,records",
    [
        ("prepared", 0, 0),
        ("wait", 10, 0),
        ("run", 0, 0),
        ("run", 3, 0),
        ("run", 5, 1),
        ("run", 14, 1),
    ],
)
def test_abort_idempotent_pending_measurement_not_fabricated(runtime, stage, tick, records):
    mode = StartMode.EXTERNAL_TRIGGER if stage == "wait" else StartMode.IMMEDIATE
    runtime.prepare(request(mode), acquisition_id="acq-1")
    if stage != "prepared":
        runtime.kickoff()
    runtime.advance_ticks(tick)
    outcome = runtime.abort(reason="operator")
    assert runtime.state == DeviceState.ABORTED and runtime.output is False
    assert outcome.cause == "operator" and not outcome.success
    assert len(runtime.records) == records
    assert runtime.abort(reason="second") is outcome
    with pytest.raises(AcquisitionAborted, match="operator"):
        runtime.completion()
    trace = runtime.source_trace
    runtime.advance_ticks(100)
    assert runtime.source_trace == trace
    assert len(runtime.records) == records
    runtime.recover()
    runtime.recover()
    assert runtime.state == DeviceState.IDLE and runtime.output is False
    assert runtime.outcome is outcome and len(runtime.records) == records
    with pytest.raises(RetainedDataError):
        runtime.prepare(request(), acquisition_id="next")


@pytest.mark.parametrize("bad_operation", ["kickoff", "prepare", "recover"])
def test_active_changes_rejected_without_effect(runtime, bad_operation):
    start(runtime)
    before = runtime.source_trace
    with pytest.raises(ElectrochemistryError):
        if bad_operation == "prepare":
            runtime.prepare(request(), acquisition_id="next")
        else:
            getattr(runtime, bad_operation)()
    assert runtime.state == DeviceState.RUNNING and runtime.output is True
    assert runtime.source_trace == before


@pytest.mark.parametrize("start_input,abort_input", [(True, False), (False, True), (True, True)])
def test_asserted_inputs_block_arming(runtime, start_input, abort_input):
    runtime.set_inputs(start=start_input, abort=abort_input)
    runtime.prepare(request(StartMode.EXTERNAL_TRIGGER), acquisition_id="acq-1")
    with pytest.raises(ElectrochemistryError):
        runtime.kickoff()
    assert runtime.output is False and not runtime.ready


def test_stale_start_not_queued_and_ready_race_no_event_clearing(runtime):
    runtime.set_inputs(start=True, abort=False)
    runtime.set_inputs(start=False, abort=False)
    runtime.prepare(request(StartMode.EXTERNAL_TRIGGER), acquisition_id="acq-1")
    runtime.set_inputs(start=True, abort=False)  # PREPARED edge must not execute.
    assert not runtime.source_trace and runtime.output is False
    runtime.set_inputs(start=False, abort=False)
    runtime.kickoff()
    assert runtime.ready
    runtime.set_inputs(start=True, abort=False)  # Immediately after READY, same virtual tick.
    assert runtime.state == DeviceState.RUNNING
    assert len(runtime.source_trace) == 1
    runtime.set_inputs(start=False, abort=False)
    runtime.set_inputs(start=True, abort=False)  # RUNNING edge ignored.
    assert len(runtime.source_trace) == 1


@pytest.mark.parametrize(
    "edge,idle_level,trigger_level",
    [
        ("rising", False, True),
        ("falling", True, False),
        ("either", False, True),
        ("either", True, False),
    ],
)
def test_configured_start_edge_and_return_transition_are_one_shot(
    capabilities, config, runtime, edge, idle_level, trigger_level
):
    edge_runtime = SimulatedRuntime(
        capabilities=capabilities,
        config=replace(config, io=replace(config.io, start_edge=edge)),
        cell=runtime.cell,
    )
    edge_runtime.set_inputs(start=idle_level, abort=False)
    edge_runtime.prepare(request(StartMode.EXTERNAL_TRIGGER), acquisition_id="edge-acq")
    edge_runtime.kickoff()
    assert edge_runtime.state == DeviceState.WAITING_START

    edge_runtime.set_inputs(start=idle_level, abort=False)
    assert edge_runtime.state == DeviceState.WAITING_START
    edge_runtime.set_inputs(start=trigger_level, abort=False)
    assert edge_runtime.state == DeviceState.RUNNING
    assert len(edge_runtime.source_trace) == 1

    edge_runtime.set_inputs(start=idle_level, abort=False)
    assert edge_runtime.state == DeviceState.RUNNING
    assert len(edge_runtime.source_trace) == 1


@pytest.mark.parametrize("edge,active_level", [("rising", True), ("falling", False)])
def test_directional_start_edge_rejects_active_level_before_arm(
    capabilities, config, runtime, edge, active_level
):
    edge_runtime = SimulatedRuntime(
        capabilities=capabilities,
        config=replace(config, io=replace(config.io, start_edge=edge)),
        cell=runtime.cell,
    )
    edge_runtime.set_inputs(start=active_level, abort=False)
    edge_runtime.prepare(request(StartMode.EXTERNAL_TRIGGER), acquisition_id="edge-acq")
    with pytest.raises(ElectrochemistryError, match="Cannot arm"):
        edge_runtime.kickoff()
    assert edge_runtime.state == DeviceState.PREPARED
    assert edge_runtime.output is False and not edge_runtime.ready


def test_falling_start_idle_level_is_irrelevant_to_immediate_mode_recovery(
    capabilities, config, runtime
):
    edge_runtime = SimulatedRuntime(
        capabilities=capabilities,
        config=replace(config, io=replace(config.io, start_edge="falling")),
        cell=runtime.cell,
    )
    edge_runtime.prepare(request(StartMode.IMMEDIATE), acquisition_id="edge-acq")
    edge_runtime.kickoff()
    edge_runtime.abort(reason="immediate-mode-test")
    edge_runtime.recover()
    assert edge_runtime.state == DeviceState.IDLE and edge_runtime.output is False


def test_coincident_abort_wins_before_first_source(runtime):
    runtime.prepare(request(StartMode.EXTERNAL_TRIGGER), acquisition_id="acq-1")
    runtime.kickoff()
    runtime.set_inputs(start=True, abort=True)
    assert runtime.state == DeviceState.ABORTED and not runtime.source_trace
    assert runtime.outcome.cause == "external_abort"
    with pytest.raises(ElectrochemistryError):
        runtime.recover()
    runtime.set_inputs(start=False, abort=False)
    runtime.recover()
    assert runtime.state == DeviceState.IDLE and runtime.output is False


def test_external_abort_running_and_disabled_input_policy(runtime, capabilities, config):
    start(runtime)
    runtime.advance_ticks(5)
    runtime.set_inputs(start=False, abort=True)
    assert runtime.state == DeviceState.ABORTED and len(runtime.records) == 1
    disabled = SimulatedRuntime(
        capabilities=replace(capabilities, supports_external_abort=False),
        config=replace(config, io=replace(config.io, external_abort_enabled=False)),
        cell=runtime.cell,
    )
    start(disabled)
    disabled.set_inputs(start=False, abort=True)
    assert disabled.state == DeviceState.RUNNING
    disabled.abort()
    assert disabled.output is False


def test_start_timeout_local_and_start_cannot_restart(runtime):
    runtime.prepare(request(StartMode.EXTERNAL_TRIGGER), acquisition_id="acq-1")
    runtime.kickoff()
    runtime.advance_ticks(29)
    assert runtime.ready and runtime.output is False
    runtime.advance_ticks(1)
    assert runtime.state == DeviceState.ABORTED
    assert runtime.outcome.cause == "start_timeout" and not runtime.source_trace
    runtime.set_inputs(start=True, abort=False)
    assert runtime.output is False and not runtime.source_trace


@pytest.mark.parametrize(
    "which,cause,count",
    [
        (FaultInjection(source_deadline_point=2), "source_deadline", 3),
        (FaultInjection(measurement_deadline_sample=2), "measurement_deadline", 2),
        (FaultInjection(sensor_failure_tick=15), "sensor_failure", 2),
        (FaultInjection(buffer_capacity_records=3), "buffer_exhausted", 1),
    ],
)
def test_injected_faults_preserve_partial_buffer(
    runtime, capabilities, config, which, cause, count
):
    sim = SimulatedRuntime(
        capabilities=capabilities, config=config, cell=runtime.cell, faults=which
    )
    start(sim, request(program=arbitrary()))
    sim.advance_ticks(100)
    assert sim.state == DeviceState.ABORTED and sim.output is False
    assert sim.outcome.cause == cause and not sim.outcome.waveform_complete
    assert len(sim.records) == count
    assert [r.sample_index for r in sim.records] == list(range(count))
    assert sim.outcome.records_retained == count
    assert sim.collect_chunk(max_records=100) == sim.records
    assert sim.collect_chunk(max_records=100) == ()


@pytest.mark.parametrize("normal", [False, True])
def test_shutdown_unknown_requires_explicit_recovery(runtime, capabilities, config, normal):
    sim = SimulatedRuntime(
        capabilities=capabilities,
        config=config,
        cell=runtime.cell,
        faults=FaultInjection(shutdown_confirmation_lost=True),
    )
    start(sim)
    sim.advance_ticks(40 if normal else 5)
    if not normal:
        with pytest.raises(ShutdownUnconfirmed):
            sim.abort()
    assert sim.state == DeviceState.ERROR and sim.output is None
    outcome = sim.outcome
    with pytest.raises(ShutdownUnconfirmed):
        sim.completion()
    with pytest.raises(ShutdownUnconfirmed):
        sim.abort()
    with pytest.raises(ShutdownUnconfirmed):
        sim.recover()
    with pytest.raises(ElectrochemistryError):
        sim.prepare(request(), acquisition_id="next")
    sim.restore_shutdown_confirmation()
    sim.recover()
    assert sim.output is False and sim.state == DeviceState.IDLE
    assert sim.outcome is outcome and not outcome.output_off_confirmed
    assert len(sim.records) == (4 if normal else 1)


@pytest.mark.parametrize(
    "current,ocv,cause", [(0.01, 1, "voltage_cutoff"), (-0.01, -1, "voltage_cutoff")]
)
def test_cell_cutoffs_use_synthetic_voltage_not_command(
    runtime, capabilities, config, current, ocv, cause
):
    sim = SimulatedRuntime(
        capabilities=capabilities,
        config=config,
        cell=FakeCell(resistance_ohm=10, open_circuit_voltage_v=ocv),
    )
    p = GalvanostaticHold(
        current_a=current,
        duration_s=0.04,
        sample_period_s=0.01,
        voltage_limit_v=5,
        lower_cutoff_v=-0.5,
        upper_cutoff_v=0.5,
    )
    start(sim, request(program=p))
    sim.advance_ticks(40)
    assert sim.outcome.cause == cause and sim.outcome.terminal_tick == 5
    assert len(sim.records) == 1 and sim.records[0].voltage_v == pytest.approx(ocv + 10 * current)
    assert sim.output is False


def test_independent_monitor_cutoff_on_first_source(runtime, capabilities, config):
    sim = SimulatedRuntime(
        capabilities=replace(capabilities, local_cutoffs_use_measurements=False),
        config=config,
        cell=FakeCell(resistance_ohm=10, open_circuit_voltage_v=6),
    )
    start(sim)
    assert sim.outcome.cause == "voltage_cutoff" and sim.outcome.terminal_tick == 0
    assert sim.records == () and sim.output is False


def test_compliance_flag_and_value_origin(runtime, capabilities, config):
    sim = SimulatedRuntime(
        capabilities=capabilities,
        config=config,
        cell=FakeCell(resistance_ohm=1000, open_circuit_voltage_v=0.1),
    )
    p = PotentiostaticHold(voltage=1, duration_s=0.04, sample_period_s=0.01, current_limit_a=0.001)
    start(sim, request(program=p))
    sim.advance_ticks(40)
    assert sim.records[0].current_a == pytest.approx(0.0009)
    assert sim.records[0].simulation_status_bits == 0
    # Low resistance hits synthetic current compliance, differing from commanded V.
    other = SimulatedRuntime(capabilities=capabilities, config=config, cell=runtime.cell)
    start(other, request(program=p))
    other.advance_ticks(40)
    r = other.records[0]
    assert r.source_setpoint == 1 and r.voltage_v == pytest.approx(0.11)
    assert r.current_a == pytest.approx(0.001) and r.simulation_status_bits == 1


def test_snapshot_coherence_no_fresh_timestamp_or_competing_measurement(runtime):
    start(runtime)
    with pytest.raises(ElectrochemistryError):
        runtime.latch_snapshot()
    with pytest.raises(ElectrochemistryError):
        runtime.read_snapshot()
    runtime.advance_ticks(5)
    snapshot = runtime.latch_snapshot()
    assert snapshot.acquired_tick == 2 and snapshot.age_ticks == 3
    assert snapshot.record.sample_index == 0
    runtime.advance_ticks(10)
    assert runtime.read_snapshot() is snapshot  # Coherent latched tuple, not mixed latest fields.
    assert len(runtime.records) == 2
    second = runtime.latch_snapshot()
    assert second.record.sample_index == 1 and second.acquired_tick == 12
    with pytest.raises(FrozenInstanceError):
        second.record.voltage_v = 9


def test_terminal_boundary_publishes_full_record(runtime, capabilities, config):
    # [2,4) aperture with one overhead tick, source ends at 5.
    start(
        runtime,
        request(
            program=GalvanostaticHold(
                current_a=0.01,
                duration_s=0.005,
                sample_period_s=0.006,
                voltage_limit_v=5,
            )
        ),
    )
    runtime.advance_ticks(5)
    assert len(runtime.records) == 1 and runtime.records[0].available_tick == 5
    assert runtime.completion().success


def test_frozen_chunks_disposition_export_digest_and_recovery(runtime, tmp_path):
    start(runtime)
    runtime.advance_ticks(15)
    with pytest.raises(ElectrochemistryError):
        runtime.collect_chunk(max_records=2)
    runtime.abort(reason="beamline-stop")
    assert len(runtime.collect_chunk(max_records=1)) == 1
    assert len(runtime.collect_chunk(max_records=1)) == 1
    assert runtime.collect_chunk(max_records=1) == ()
    destination = tmp_path / "partial.json"
    digest = runtime.export_retained_data(str(destination))
    saved = json.loads(destination.read_text())
    canonical = json.dumps(
        saved["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert saved["sha256"] == digest == hashlib.sha256(canonical.encode()).hexdigest()
    assert len(saved["payload"]["records"]) == 2
    assert saved["payload"]["program"]["terminal_action"] == "output_off"
    assert saved["payload"]["outcome"]["fields"]["cause"] == "beamline-stop"
    with pytest.raises(RetainedDataError, match="already exists"):
        runtime.export_retained_data(str(destination))
    assert len(runtime.records) == 2
    runtime.recover()
    with pytest.raises(RetainedDataError):
        runtime.prepare(request(), acquisition_id="acq-2")
    with pytest.raises(RetainedDataError):
        runtime.discard_retained_data(acquisition_id="wrong", reason="archived")
    runtime.discard_retained_data(acquisition_id="acq-1", reason="archived-and-reviewed")
    runtime.discard_retained_data(acquisition_id="acq-1", reason="duplicate")
    assert len(runtime.records) == 2 and len(runtime.dispositions) == 1
    runtime.prepare(request(), acquisition_id="acq-2")
    assert runtime.records == () and runtime.state == DeviceState.PREPARED


def test_normal_terminal_abort_does_not_change_success(runtime):
    start(runtime)
    runtime.advance_ticks(40)
    outcome = runtime.outcome
    assert runtime.abort() is outcome and runtime.completion() is outcome
    with pytest.raises(RetainedDataError):
        runtime.prepare(request(), acquisition_id="next")
    runtime.discard_retained_data(acquisition_id="acq-1", reason="operator-reviewed")
    with pytest.raises(ValidationError):
        runtime.prepare(request(), acquisition_id="acq-1")
    runtime.prepare(request(), acquisition_id="next")


@pytest.mark.parametrize(
    "fault",
    [
        FaultInjection(source_deadline_point=0),
        FaultInjection(sensor_failure_tick=0),
        FaultInjection(buffer_capacity_records=0),
    ],
)
def test_fault_at_start_never_claims_normal_or_leaves_output_on(
    runtime, capabilities, config, fault
):
    sim = SimulatedRuntime(
        capabilities=capabilities, config=config, cell=runtime.cell, faults=fault
    )
    start(sim)
    assert sim.state == DeviceState.ABORTED and sim.output is False
    assert not sim.records and not sim.source_trace
    assert not sim.outcome.success


def test_invalid_request_and_profile_do_not_mutate_output(runtime, capabilities, config):
    with pytest.raises(ValidationError):
        runtime.prepare(request(program=arbitrary(levels=(0.3,))), acquisition_id="bad")
    assert runtime.state == DeviceState.IDLE and runtime.output is False
    with pytest.raises(ValidationError):
        SimulatedRuntime(
            capabilities=replace(capabilities, evidence_kind="bench"),
            config=config,
            cell=runtime.cell,
        )


@pytest.mark.parametrize("sign", [-1, 1])
def test_cutoff_equality_terminates(runtime, capabilities, config, sign):
    sim = SimulatedRuntime(
        capabilities=capabilities,
        config=config,
        cell=FakeCell(resistance_ohm=10, open_circuit_voltage_v=0),
    )
    start(
        sim,
        request(
            program=GalvanostaticHold(
                current_a=sign * 0.01,
                duration_s=0.04,
                sample_period_s=0.01,
                voltage_limit_v=5,
                lower_cutoff_v=-0.1,
                upper_cutoff_v=0.1,
            )
        ),
    )
    sim.advance_ticks(40)
    assert sim.outcome.cause == "voltage_cutoff" and sim.outcome.terminal_tick == 5
    assert sim.records[0].voltage_v == sign * 0.1


def test_ready_race_repeated_acquisitions_no_stale_events(runtime):
    for cycle in range(32):
        acquisition_id = f"race-{cycle}"
        runtime.prepare(request(StartMode.EXTERNAL_TRIGGER), acquisition_id=acquisition_id)
        runtime.kickoff()
        runtime.set_inputs(start=True, abort=False)
        runtime.advance_ticks(5)
        assert len(runtime.records) == 1
        runtime.set_inputs(start=False, abort=True)
        assert runtime.outcome.cause == "external_abort" and runtime.output is False
        runtime.set_inputs(start=False, abort=False)
        runtime.recover()
        runtime.discard_retained_data(acquisition_id=acquisition_id, reason="race-test-reviewed")
    assert len(runtime.dispositions) == 32


def test_numeric_sensor_failure_is_a_retained_fault(runtime, capabilities, config):
    sim = SimulatedRuntime(
        capabilities=capabilities,
        config=config,
        cell=FakeCell(resistance_ohm=1e-320, open_circuit_voltage_v=0.1),
    )
    start(
        sim,
        request(
            program=PotentiostaticHold(
                voltage=1,
                duration_s=0.04,
                sample_period_s=0.01,
                current_limit_a=0.001,
            )
        ),
    )
    assert sim.outcome.cause == "sensor_failure" and sim.output is False
    assert not sim.records


def test_recovery_export_preserves_original_fault_injection(
    runtime, capabilities, config, tmp_path
):
    sim = SimulatedRuntime(
        capabilities=capabilities,
        config=config,
        cell=runtime.cell,
        faults=FaultInjection(shutdown_confirmation_lost=True),
    )
    start(sim)
    sim.advance_ticks(5)
    with pytest.raises(ShutdownUnconfirmed):
        sim.abort()
    sim.restore_shutdown_confirmation()
    sim.recover()
    path = tmp_path / "recovered.json"
    sim.export_retained_data(str(path))
    saved = json.loads(path.read_text())["payload"]
    assert saved["faults_at_prepare"]["fields"]["shutdown_confirmation_lost"] is True
    assert saved["faults_at_export"]["fields"]["shutdown_confirmation_lost"] is False
    assert saved["outcome"]["fields"]["output_off_confirmed"] is False


@pytest.mark.parametrize("value", [-1, True, 0.1])
def test_invalid_tick_input(runtime, value):
    with pytest.raises(ValidationError):
        runtime.advance_ticks(value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source_deadline_point": -1},
        {"buffer_capacity_records": True},
        {"shutdown_confirmation_lost": 1},
    ],
)
def test_invalid_fault_parameters(kwargs):
    with pytest.raises(ValidationError):
        FaultInjection(**kwargs)
