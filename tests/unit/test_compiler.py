import math
import socket
from dataclasses import replace

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
    VoltagePulseSequence,
    generate_multisine,
)
from ophyd_electrochemistry.exceptions import UnsupportedCapabilityError, ValidationError
from ophyd_electrochemistry.keithley.k2460 import DigitalIOConfig, Keithley2460Compiler


def arbitrary(**changes):
    values = dict(
        source_function="current",
        voltage_limit_v=5,
        sample_period_s=0.01,
        point_period_s=0.01,
        levels=(0.0, 0.05, -0.05),
        repeats=2,
    )
    return ArbitraryWaveform(**(values | changes))


def compile_program(program, capabilities, config, mode=StartMode.IMMEDIATE):
    return Keithley2460Compiler(capabilities).compile(
        AcquisitionRequest(program=program, experiment_id="m1", start_mode=mode), config
    )


@pytest.mark.parametrize("mode", list(StartMode))
def test_compiler_has_no_network_side_effects_and_retains_full_final_dwell(
    capabilities, config, mode, monkeypatch
):
    def forbidden(*args, **kwargs):
        raise AssertionError("M1 compiler attempted network communication")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    result = compile_program(arbitrary(), capabilities, config, mode)
    assert not result.hardware_ready
    assert result.achieved_duration_s == 0.06
    assert result.source.boundaries_ticks == (0, 10, 20, 30)
    assert result.source.repeats == 2 and result.source.logical_points == 6
    assert result.budget.measurement_records == 6 and result.budget.source_records == 6
    assert result.budget.buffer_records == 12
    assert result.source.point_at_tick(29) == (2, 0, 2)
    assert result.source.point_at_tick(30) == (3, 1, 0)
    assert result.source.point_at_tick(59) == (5, 1, 2)
    with pytest.raises(ValidationError):
        result.source.point_at_tick(60)
    assert '"terminal_action":"output_off"' in result.canonical_program_json


def test_independent_measurement_aperture_mapping_and_half_open_boundaries(capabilities, config):
    profile = replace(capabilities, measurement_aperture_s=0.003, measurement_overhead_s=0)
    result = compile_program(arbitrary(sample_period_s=0.007), profile, config)
    crossing = result.sample_mapping(1)  # [9,12) crosses the update at tick 10.
    assert (crossing.aperture_start_tick, crossing.aperture_end_tick) == (9, 12)
    assert (crossing.first_logical_point, crossing.last_logical_point) == (0, 1)
    assert not crossing.wholly_settled_in_one_dwell
    profile = replace(profile, aperture_may_cross_updates=False)
    with pytest.raises(UnsupportedCapabilityError, match="overlaps"):
        compile_program(arbitrary(sample_period_s=0.007), profile, config)
    result = compile_program(arbitrary(sample_period_s=0.005), capabilities, config)
    edge = result.sample_mapping(1)  # [7,9), fully inside first dwell.
    assert edge.wholly_settled_in_one_dwell
    profile = replace(capabilities, measurement_aperture_s=0.003, measurement_overhead_s=0)
    result = compile_program(arbitrary(sample_period_s=0.005), profile, config)
    assert result.sample_mapping(1).aperture_end_tick == 10
    assert result.sample_mapping(1).last_logical_point == 0
    with pytest.raises(ValidationError):
        result.sample_mapping(result.measurement.record_count)


def test_prbs_full_bits_repeats_mean_and_commanded_charge(capabilities, config):
    p = PRBSWaveform(
        source_function="current",
        voltage_limit_v=5,
        sample_period_s=0.01,
        low_level=-0.01,
        high_level=0.01,
        bit_period_s=0.01,
        order=3,
        seed=1,
        repeats=3,
    )
    result = compile_program(p, capabilities, config)
    assert tuple(s.level for s in result.source.steps) == (
        0.01,
        -0.01,
        -0.01,
        0.01,
        -0.01,
        0.01,
        0.01,
    )
    assert result.source.boundaries_ticks == (0, 10, 20, 30, 40, 50, 60, 70)
    assert result.budget.logical_points == 21
    assert result.achieved_duration_s == 0.21
    assert result.mean_level == pytest.approx(0.01 / 7)
    assert result.commanded_charge_c == pytest.approx(0.0003)


def test_cv_exact_vertices_joins_cycles_and_terminal_endpoint(capabilities, config):
    p = CyclicVoltammetry(
        start_voltage_v=0,
        first_vertex_v=0.2,
        second_vertex_v=-0.2,
        scan_rate_v_per_s=1,
        cycles=2,
        sample_period_s=0.1,
        current_limit_a=0.1,
    )
    result = compile_program(p, capabilities, config)
    expected = (0, 0.1, 0.2, 0.1, 0, -0.1, -0.2, -0.1, 0, 0.1, 0.2, 0.1, 0, -0.1, -0.2, -0.1, 0)
    assert tuple(s.level for s in result.source.steps) == pytest.approx(expected)
    assert result.achieved_scan_rates_v_per_s == (1, 1, 1)
    assert result.achieved_duration_s == 1.7 and result.budget.measurement_records == 17
    assert result.source.steps[-1].cycle_index == 1
    assert result.source.steps[-1].dwell_ticks == 100
    with pytest.raises(ValidationError, match="rate"):
        compile_program(replace(p, first_vertex_v=0.25), capabilities, config)


def test_cv_start_at_vertex_skips_zero_length_leg(capabilities, config):
    p = CyclicVoltammetry(
        start_voltage_v=0.2,
        first_vertex_v=0.2,
        second_vertex_v=-0.2,
        scan_rate_v_per_s=1,
        cycles=1,
        sample_period_s=0.1,
        current_limit_a=0.1,
    )
    result = compile_program(p, capabilities, config)
    assert len(result.source.steps) == 9 and result.achieved_scan_rates_v_per_s == (0, 1, 1)
    assert result.source.steps[0].level == result.source.steps[-1].level == 0.2


def test_cv_rejects_cadence_that_would_lose_terminal_endpoint(capabilities, config):
    p = CyclicVoltammetry(
        start_voltage_v=0,
        first_vertex_v=0.006,
        second_vertex_v=-0.006,
        scan_rate_v_per_s=1,
        cycles=1,
        sample_period_s=0.003,
        current_limit_a=0.1,
    )
    with pytest.raises(ValidationError, match="endpoint"):
        compile_program(p, capabilities, config)


def test_initial_settling_delay_enters_cutoff_bound(capabilities, config):
    p = GalvanostaticHold(current_a=0.01, duration_s=1, sample_period_s=0.01, voltage_limit_v=5)
    slow = replace(capabilities, source_settling_time_s=0.1)
    requirement = replace(config, timing=replace(config.timing, required_cutoff_latency_s=0.05))
    with pytest.raises(UnsupportedCapabilityError, match="cadence"):
        compile_program(p, slow, requirement)


def test_pulse_starts_at_pulse_then_baseline_with_weighted_charge(capabilities, config):
    p = CurrentPulseSequence(
        baseline_current_a=-0.01,
        pulse_current_a=0.05,
        pulse_width_s=0.01,
        period_s=0.04,
        count=3,
        sample_period_s=0.005,
        voltage_limit_v=5,
    )
    result = compile_program(p, capabilities, config)
    assert result.source.boundaries_ticks == (0, 10, 40)
    assert result.achieved_duration_s == 0.12
    assert result.mean_level == pytest.approx(0.005)
    assert result.commanded_charge_c == pytest.approx(0.0006)


def test_bipolar_current_pulse_has_zero_net_commanded_charge(capabilities, config):
    p = CurrentPulseSequence(
        baseline_current_a=-0.01,
        pulse_current_a=0.01,
        pulse_width_s=0.02,
        period_s=0.04,
        count=4,
        sample_period_s=0.005,
        voltage_limit_v=5,
    )
    result = compile_program(p, capabilities, config)
    assert result.source_function == "current"
    assert tuple(step.level for step in result.source.steps) == (0.01, -0.01)
    assert result.source.boundaries_ticks == (0, 20, 40)
    assert result.achieved_duration_s == 0.16
    assert result.mean_level == pytest.approx(0)
    assert result.commanded_charge_c == pytest.approx(0)


def test_voltage_pulse_uses_current_compliance_and_full_baseline_dwell(capabilities, config):
    p = VoltagePulseSequence(
        baseline_voltage_v=0.1,
        pulse_voltage_v=0.2,
        pulse_width_s=0.01,
        period_s=0.04,
        count=3,
        sample_period_s=0.005,
        current_limit_a=0.1,
    )
    result = compile_program(p, capabilities, config)
    assert result.source_function == "voltage"
    assert tuple(step.level for step in result.source.steps) == (0.2, 0.1)
    assert result.source.boundaries_ticks == (0, 10, 40)
    assert result.achieved_duration_s == 0.12
    assert result.mean_level == pytest.approx(0.125)
    assert result.commanded_charge_c is None
    assert result.effective_voltage_cutoffs_v is None
    current_only = replace(capabilities, sources=(capabilities.sources[0],))
    with pytest.raises(UnsupportedCapabilityError, match="voltage sourcing"):
        compile_program(p, current_only, config)


def test_holds_use_complete_apertures_and_safe_cutoff_envelope(capabilities, config):
    current = GalvanostaticHold(
        current_a=-0.02,
        duration_s=1,
        sample_period_s=0.1,
        voltage_limit_v=5,
        lower_cutoff_v=-4,
        upper_cutoff_v=4,
    )
    result = compile_program(current, capabilities, config)
    assert result.budget.measurement_records == 10
    assert result.effective_voltage_cutoffs_v == (-4, 4)
    assert result.commanded_charge_c == pytest.approx(-0.02)
    voltage = PotentiostaticHold(voltage=1, duration_s=1, sample_period_s=0.1, current_limit_a=0.1)
    assert compile_program(voltage, capabilities, config).commanded_charge_c is None
    wider = replace(
        capabilities,
        sources=(
            capabilities.sources[0],
            replace(capabilities.sources[1], level_min=-10, level_max=10),
        ),
    )
    with pytest.raises(ValidationError, match="cell"):
        compile_program(replace(voltage, voltage=6), wider, config)


def test_slow_measurements_cannot_inherit_fast_cutoff_claim(capabilities, config):
    with pytest.raises(UnsupportedCapabilityError, match="cadence"):
        compile_program(arbitrary(sample_period_s=1), capabilities, config)
    p = GalvanostaticHold(current_a=0.01, duration_s=2, sample_period_s=1, voltage_limit_v=5)
    independent = replace(capabilities, local_cutoffs_use_measurements=False)
    assert compile_program(p, independent, config).measurement.record_count == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"max_unique_points": 2},
        {"max_logical_points": 5},
        {"max_duration_s": 0.05},
        {"max_measurement_records": 5},
        {"max_buffer_records": 11},
        {"max_trigger_blocks": 17},
        {"max_upload_bytes": 10},
    ],
)
def test_each_resource_budget_rejects_before_any_runtime(capabilities, config, changes):
    with pytest.raises(ValidationError):
        compile_program(arbitrary(), replace(capabilities, **changes), config)


def test_expansion_budget_is_checked_before_prbs_generation(capabilities, config, monkeypatch):
    from ophyd_electrochemistry.keithley.k2460 import compiler

    def forbidden(*args, **kwargs):
        raise AssertionError("PRBS expanded before rejecting its budget")

    monkeypatch.setattr(compiler, "prbs_bits", forbidden)
    p = PRBSWaveform(
        source_function="current",
        voltage_limit_v=5,
        sample_period_s=0.01,
        low_level=0,
        high_level=0.01,
        bit_period_s=0.01,
        order=10,
        seed=1,
    )
    with pytest.raises(ValidationError, match="budget"):
        compile_program(p, replace(capabilities, max_unique_points=10), config)
    with pytest.raises(ValidationError, match="budget"):
        compile_program(arbitrary(repeats=10**100), capabilities, config)


def test_quantization_requires_point_and_accumulated_duration_tolerances(capabilities, config):
    p = arbitrary(point_period_s=0.0101, repeats=10)
    with pytest.raises(ValidationError, match="source period"):
        compile_program(p, capabilities, config)
    adjusted = replace(config, timing=replace(config.timing, source_period_tolerance_s=0.0002))
    with pytest.raises(ValidationError, match="Accumulated"):
        compile_program(p, capabilities, adjusted)
    adjusted = replace(adjusted, timing=replace(adjusted.timing, duration_tolerance_s=0.004))
    result = compile_program(p, capabilities, adjusted)
    assert result.requested_duration_s == 0.303 and result.achieved_duration_s == 0.3
    assert result.achieved_source_period_s == 0.01


@pytest.mark.parametrize(
    "changes",
    [
        {"supports_external_start": False},
        {"supports_local_cutoffs": False},
        {"supports_independent_measurement": False},
        {"abort_latency_s": 0.03},
        {"cutoff_latency_s": 0.3},
        {"sources": ()},
    ],
)
def test_undeclared_capabilities_are_rejected(capabilities, config, changes):
    with pytest.raises((ValidationError, UnsupportedCapabilityError)):
        compile_program(
            arbitrary(), replace(capabilities, **changes), config, StartMode.EXTERNAL_TRIGGER
        )


def test_external_abort_stays_gated_and_hashes_cover_configuration(capabilities, config):
    result = compile_program(arbitrary(), capabilities, config)
    repeated = compile_program(arbitrary(), capabilities, config)
    assert result == repeated
    changed = compile_program(
        arbitrary(), replace(capabilities, profile_id="another-profile"), config
    )
    assert result.request_sha256 == changed.request_sha256
    assert result.waveform_sha256 == changed.waveform_sha256
    assert result.program_sha256 != changed.program_sha256
    assert result.budget.upload_bytes == len(result.canonical_program_json.encode())
    with pytest.raises(UnsupportedCapabilityError, match="ABORT"):
        compile_program(
            arbitrary(),
            capabilities,
            replace(config, io=DigitalIOConfig(external_abort_enabled=True)),
        )


def test_cell_power_cutoffs_step_and_repeat_boundary_limits(capabilities, config):
    with pytest.raises(ValidationError, match="power"):
        compile_program(
            arbitrary(levels=(0.05,)),
            capabilities,
            replace(config, safety=replace(config.safety, power_abs_max_w=0.1)),
        )
    with pytest.raises(ValidationError, match="Cutoffs"):
        compile_program(arbitrary(lower_cutoff_v=-6), capabilities, config)
    limited = replace(
        capabilities,
        sources=(replace(capabilities.sources[0], max_step=0.06), capabilities.sources[1]),
    )
    compile_program(arbitrary(levels=(0, 0.05, 0.1), repeats=1), limited, config)
    with pytest.raises(ValidationError, match="boundary"):
        compile_program(arbitrary(levels=(0, 0.05, 0.1), repeats=2), limited, config)


def test_multisine_envelope_fidelity_statistics_and_provenance(capabilities, config):
    spec = MultisineSpec(
        source_function="current",
        voltage_limit_v=5,
        sample_period_s=0.02,
        bias=0.02,
        frequencies_hz=(1.0, 3.0),
        amplitudes=(0.01, 0.005),
        phases_rad=(0.2, 1.1),
        point_period_s=0.01,
        point_count=100,
    )
    waveform = generate_multisine(spec)
    result = compile_program(waveform, capabilities, config)
    assert result.achieved_tone_frequencies_hz == (1, 3)
    assert result.mean_level == pytest.approx(0.02)
    assert result.ac_rms_level == pytest.approx(math.sqrt((0.01**2 + 0.005**2) / 2))
    assert result.ac_crest_factor > 1
    with pytest.raises(ValidationError, match="reproduce"):
        compile_program(replace(waveform, levels=(0.02,) * 100), capabilities, config)
    with pytest.raises(UnsupportedCapabilityError, match="under-resolved"):
        compile_program(waveform, replace(capabilities, min_points_per_highest_tone=40), config)
    # Phases can hide the conservative peak at grid points; do not relax the envelope.
    envelope = generate_multisine(
        replace(spec, bias=0.19, amplitudes=(0.01, 0.01), phases_rad=(0, 0))
    )
    with pytest.raises(ValidationError, match="cell"):
        compile_program(envelope, capabilities, config)
