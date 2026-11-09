"""Virtual sequence timing, cell dynamics, cutoff priority and readout contracts."""

from dataclasses import FrozenInstanceError, replace
from time import perf_counter

import numpy as np
import pytest

from ophyd_electrochemistry.exceptions import ValidationError
from ophyd_electrochemistry.sequences import (
    CutoffAction,
    RepeatBlock,
    SafetyLimits,
    Sampling,
    Sequence,
    Step,
    StepMode,
    bipolar_pulse_sequence,
    cc_step,
    cv_step,
    cycling_sequence,
    expanded,
    expected_records,
    nominal_duration_s,
    pulse_train_sequence,
    rest_step,
    sample_schedule,
)
from ophyd_electrochemistry.sequences.run_file import READOUT_FIELDS, RunFile, read_run_file
from ophyd_electrochemistry.sequences.simulator import (
    CellModel,
    iter_simulated_chunks,
    simulate_sequence,
)


@pytest.fixture
def limits():
    return SafetyLimits(-5, 5, 0.02, 1e6, 1_000_000)


@pytest.fixture
def cell():
    # Flat OCV isolates the analytic RC response and avoids accidental cutoffs.
    return CellModel(noise_v=0, ocv_soc=(0.0, 1.0), ocv_v=(1.0, 1.0))


def test_full_bipolar_record_count_repeat_order_and_zero_net_charge(limits, cell):
    sequence = bipolar_pulse_sequence(0.001, 0.1, 100)
    result = simulate_sequence(sequence, limits, cell)
    assert result.outcome == "completed"
    assert tuple(result.readout) == READOUT_FIELDS
    assert all(len(values) == expected_records(sequence) for values in result.readout.values())
    assert result.readout["t_s"][-1] == pytest.approx(nominal_duration_s(sequence))
    assert np.all(np.diff(result.readout["t_s"]) > 0)
    assert result.final_soc == pytest.approx(cell.soc0, abs=1e-9)
    starts = [event for event in result.events if event[3] == "step_start"]
    assert [(event[1], event[2]) for event in starts] == [
        (index, repeat) for index, repeat, _ in expanded(sequence)
    ]
    for name in READOUT_FIELDS[3:]:
        assert result.readout[name].dtype == np.dtype("i4")
    assert np.all(result.readout["source_status"] == 0)
    assert np.all(result.readout["measure_status"] == 0)


@pytest.mark.parametrize(
    "duration,sampling",
    [
        (0.31, Sampling(0.1)),
        (0.93, Sampling(0.2, 0.03, 0.2)),
        (0.1, Sampling(0.1, 0.007, 0.5)),
    ],
)
def test_sample_schedule_is_the_only_clock(duration, sampling, limits, cell):
    step = Step(StepMode.CURRENT, 0.001, duration, sampling, label="pulse")
    sequence = Sequence((rest_step(0.23, Sampling(0.1)), step), RepeatBlock(1, 1, 2))
    result = simulate_sequence(sequence, limits, cell)
    expected_times = []
    starts = []
    elapsed = 0.0
    for index, repeat, item in expanded(sequence):
        starts.append((elapsed, index, repeat, "step_start", item.label))
        phase_start = elapsed
        for count, interval in sample_schedule(item):
            expected_times.extend(phase_start + k * interval for k in range(1, count + 1))
            phase_start += count * interval
        elapsed += item.duration_s
    np.testing.assert_allclose(result.readout["t_s"], expected_times, rtol=0, atol=1e-15)
    assert result.events == starts
    assert result.readout["t_s"][-1] == pytest.approx(elapsed)
    # Charge integration must use the effective intervals too.
    assert result.final_soc == pytest.approx(cell.soc0 + 2 * 0.001 * duration / 1.8)


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("action", list(CutoffAction))
def test_cc_cutoff_ends_step_at_sample_and_applies_action(direction, action, limits, cell):
    step = cc_step(direction * 0.001, 1 + direction * 0.04, 30, Sampling(0.1), action)
    result = simulate_sequence(Sequence((step, rest_step(1, Sampling(0.1)))), limits, cell)
    cutoff = next(event for event in result.events if event[3] == "cutoff")
    assert 0 < cutoff[0] < step.duration_s
    assert cutoff[4] == ("v_max" if direction > 0 else "v_min")
    before = result.readout["voltage_v"][result.readout["step_index"] == 0]
    assert np.all(direction * (before[:-1] - 1) < 0.04)
    assert direction * (before[-1] - 1) >= 0.04
    if action == CutoffAction.NEXT:
        assert result.outcome == "completed"
        assert result.events[2] == (cutoff[0], 1, 0, "step_start", "")
        assert result.readout["t_s"][len(before)] == pytest.approx(cutoff[0] + 0.1)
    else:
        assert result.outcome == "stopped_cutoff"
        assert result.events[-1][3] == "stop"
        assert result.readout["t_s"][-1] == cutoff[0]
        assert np.all(result.readout["step_index"] == 0)


@pytest.mark.parametrize("direction", [-1, 1])
def test_absolute_window_takes_priority_over_step_next(direction, limits, cell):
    limits = replace(limits, v_min_abs=0.99, v_max_abs=1.01)
    step = cc_step(direction * 0.001, 1 + direction * 0.005, 10, Sampling(0.1))
    result = simulate_sequence(Sequence((step, rest_step(1, Sampling(0.1)))), limits, cell)
    assert result.outcome == "stopped_cutoff"
    assert len(result.readout["t_s"]) == 1
    assert result.events == [
        (0, 0, 0, "step_start", ""),
        (0.1, 0, 0, "stop", "absolute voltage window"),
    ]


def test_absolute_window_is_inclusive_but_step_cutoffs_include_equality(limits, cell):
    rest = rest_step(1, Sampling(0.1))
    limits = replace(limits, v_min_abs=1, v_max_abs=2)
    assert simulate_sequence(Sequence((rest,)), limits, cell).outcome == "completed"
    limits = replace(limits, v_min_abs=0, v_max_abs=1)
    assert simulate_sequence(Sequence((rest,)), limits, cell).outcome == "completed"
    for field in ("v_min", "v_max"):
        result = simulate_sequence(Sequence((replace(rest, **{field: 1}),)), limits, cell)
        assert result.outcome == "completed"
        assert result.events[-1] == (0.1, 0, 0, "cutoff", field)
        assert len(result.readout["t_s"]) == 1


@pytest.mark.parametrize("direction", [-1, 1])
def test_cv_compliance_then_taper_cutoff(direction, limits, cell):
    limits = replace(limits, i_max_abs=0.001)
    step = cv_step(1 + direction * 0.06, 0.0009, 100, Sampling(0.1))
    result = simulate_sequence(Sequence((step, rest_step(1, Sampling(0.1)))), limits, cell)
    current = result.readout["current_a"][result.readout["step_index"] == 0]
    assert current[0] == direction * limits.i_max_abs
    assert np.all(np.abs(current) <= limits.i_max_abs)
    assert np.all(np.abs(current[:-1]) > step.i_abs_min)
    assert abs(current[-1]) <= step.i_abs_min
    assert result.events[1][3:] == ("cutoff", "i_abs_min")
    assert result.events[2][:4] == (result.events[1][0], 1, 0, "step_start")
    assert result.outcome == "completed"


@pytest.mark.parametrize("interval_s", [0.1, 1.0, 5.0, 10.0])
def test_cv_hold_is_stable_for_coarse_sampling(interval_s, limits):
    # A held-current shortcut oscillates once interval > tau*2*R0/(R0+R1) (~5.7 s).
    # Starts in compliance (needs ~45 mA, limit 20 mA). OCV is frozen within an
    # interval, so the capacity keeps the SOC change per interval realistic.
    cell = CellModel(noise_v=0, capacity_ah=50e-3)
    step = cv_step(1.0, 1e-6, 100, Sampling(interval_s))
    result = simulate_sequence(Sequence((step,)), limits, cell)
    current, voltage = result.readout["current_a"], result.readout["voltage_v"]
    assert np.all(np.diff(current) <= 0)  # Monotonic decay toward OCV equilibrium.
    assert np.all((current > 0) & (current <= limits.i_max_abs))
    assert np.all(voltage <= 1.0 + 1e-12)  # No overshoot.
    held = current < limits.i_max_abs
    assert held[-1]
    np.testing.assert_allclose(voltage[held], 1.0, atol=1e-12)


def test_cv_cutoff_can_stop_and_zero_current_counts_as_cutoff(limits, cell):
    step = replace(cv_step(1, 0, 2, Sampling(0.1)), on_cutoff=CutoffAction.STOP)
    result = simulate_sequence(Sequence((step, rest_step(1, Sampling(0.1)))), limits, cell)
    assert result.outcome == "stopped_cutoff"
    np.testing.assert_array_equal(result.readout["current_a"], [0])
    assert [event[3] for event in result.events] == ["step_start", "cutoff", "stop"]


def test_exact_rc_response_and_relaxation(limits, cell):
    pulse = Step(StepMode.CURRENT, 0.001, 5, Sampling(0.1))
    result = simulate_sequence(Sequence((pulse, rest_step(30, Sampling(0.1)))), limits, cell)
    time = result.readout["t_s"]
    voltage = result.readout["voltage_v"]
    pulse_mask = result.readout["step_index"] == 0
    tau = cell.r1_ohm * cell.c1_f
    expected_rc = 0.001 * cell.r1_ohm * (1 - np.exp(-time[pulse_mask] / tau))
    np.testing.assert_allclose(voltage[pulse_mask], 1 + 0.001 * cell.r0_ohm + expected_rc)
    rest_times = time[~pulse_mask] - pulse.duration_s
    rest_rc = voltage[~pulse_mask] - 1
    np.testing.assert_allclose(rest_rc, expected_rc[-1] * np.exp(-rest_times / tau))
    slope, _ = np.polyfit(rest_times, np.log(rest_rc), 1)
    assert -1 / slope == pytest.approx(tau, rel=0.02)


def test_soc_sign_interpolation_and_charge_conservation_outside_grid(limits):
    cell = CellModel(
        capacity_ah=1 / 3600, soc0=0.5, noise_v=0, ocv_soc=(0.0, 0.5, 1.0), ocv_v=(0.0, 0.5, 2.0)
    )
    pulse = Step(StepMode.CURRENT, 0.01, 100, Sampling(1))
    sequence = Sequence((pulse, replace(pulse, level=-0.01)))
    result = simulate_sequence(sequence, limits, cell)
    assert result.readout["voltage_v"][0] > 0.5 + 0.01 * cell.r0_ohm
    assert result.final_soc == pytest.approx(0.5, abs=1e-9)


def test_equal_seeds_are_identical_and_noise_does_not_change_state(limits):
    sequence = bipolar_pulse_sequence(0.001, 0.1, 2)
    a = simulate_sequence(sequence, limits, seed=42)
    b = simulate_sequence(sequence, limits, seed=42)
    c = simulate_sequence(sequence, limits, seed=43)
    for name in READOUT_FIELDS:
        np.testing.assert_array_equal(a.readout[name], b.readout[name])
        if name != "voltage_v":
            np.testing.assert_array_equal(a.readout[name], c.readout[name])
    assert not np.array_equal(a.readout["voltage_v"], c.readout["voltage_v"])
    assert a.events == b.events == c.events
    assert a.final_soc == b.final_soc == c.final_soc


def test_validation_precedes_rng_creation_and_execution(limits):
    sequence = Sequence((Step(StepMode.CURRENT, 1, 1, Sampling(0.1)),))
    # Invalid seed would fail if RNG construction happened before validation.
    with pytest.raises(ValidationError, match="i_max_abs"):
        simulate_sequence(sequence, limits, seed=-1)


@pytest.mark.parametrize(
    "changes",
    [
        {"capacity_ah": 0},
        {"capacity_ah": True},
        {"soc0": -0.1},
        {"soc0": 1.1},
        {"r0_ohm": 0},
        {"r1_ohm": -1},
        {"c1_f": float("nan")},
        {"noise_v": -1},
        {"ocv_soc": (0.0,)},
        {"ocv_soc": (0.0, 0.0, 1.0)},
        {"ocv_soc": (0.1, 1.0), "ocv_v": (0.1, 1.0)},
        {"ocv_soc": (0.0, 1.0), "ocv_v": (0.1, float("inf"))},
        {"ocv_v": [0.1, 1.0]},
    ],
)
def test_cell_parameters_are_validated(changes):
    with pytest.raises(ValidationError):
        CellModel(**changes)


def test_cell_and_result_are_frozen(limits):
    cell = CellModel()
    with pytest.raises(FrozenInstanceError):
        cell.soc0 = 0.1
    result = simulate_sequence(Sequence((rest_step(1, Sampling(0.1)),)), limits)
    with pytest.raises(FrozenInstanceError):
        result.outcome = "error"


@pytest.mark.parametrize("chunk_records", [1, 7, 500, 10_000])
def test_chunks_preserve_schema_order_and_data(chunk_records, limits, cell):
    result = simulate_sequence(Sequence((rest_step(2.3, Sampling(0.1)),)), limits, cell)
    chunks = list(iter_simulated_chunks(result, chunk_records))
    assert all(0 < len(chunk["t_s"]) <= chunk_records for chunk in chunks)
    assert all(tuple(chunk) == READOUT_FIELDS for chunk in chunks)
    for name in READOUT_FIELDS:
        np.testing.assert_array_equal(
            np.concatenate([c[name] for c in chunks]), result.readout[name]
        )


@pytest.mark.parametrize("size", [0, -1, True, 1.5])
def test_chunk_size_must_be_positive_integer(size, limits):
    result = simulate_sequence(Sequence((rest_step(1, Sampling(0.1)),)), limits)
    with pytest.raises(ValidationError, match="chunk_records"):
        list(iter_simulated_chunks(result, size))


def test_readout_and_events_round_trip_through_run_file(tmp_path, limits, cell):
    sequence = Sequence((cc_step(0.001, 1.04, 30, Sampling(0.1), CutoffAction.STOP),))
    result = simulate_sequence(sequence, limits, cell)
    path = tmp_path / "simulation.h5"
    with RunFile.create(
        path,
        measurement_id="sim",
        equipment={"simulated": True},
        sequence=sequence,
        limits=limits,
        start_mode="immediate",
    ) as writer:
        for chunk in iter_simulated_chunks(result, 7):
            writer.append_readout(chunk)
        for event in result.events:
            writer.append_event(*event)
        writer.finalize(result.outcome)
    stored = read_run_file(path)
    assert stored["run"]["outcome"] == result.outcome
    for name in READOUT_FIELDS:
        np.testing.assert_array_equal(stored["readout"][name], result.readout[name])
    assert list(stored["events"]["kind"]) == [event[3] for event in result.events]


@pytest.mark.parametrize(
    "sequence",
    [
        bipolar_pulse_sequence(0.001, 0.1, 100),
        cycling_sequence(
            0.001,
            0.05,
            0.2,
            3,
            cv_i_abs_min=0.0001,
            max_step_s=600,
            cv_max_s=60,
            rest_s=10,
            interval_s=0.1,
        ),
    ],
)
def test_representative_protocol_runs_in_under_two_seconds(sequence, limits):
    start = perf_counter()
    result = simulate_sequence(sequence, limits)
    elapsed = perf_counter() - start
    assert elapsed < 2
    assert result.outcome == "completed"
    assert {event[1] for event in result.events if event[3] == "step_start"} == set(
        range(len(sequence.steps))
    )


def test_relaxed_end_ignores_the_voltage_under_load(limits):
    # 2.8 % SOC per 1 mA, 1 s pulse; the OCV falls 0.5 mV per % SOC. Every pulse
    # dips below the end voltage under load, but only the relaxed voltage stops the run.
    cell = CellModel(capacity_ah=1e-5, noise_v=0)
    sequence = pulse_train_sequence(-1e-3, 1, 20, rest_s=60, pre_rest_s=2, rest_end_v=0.095)
    result = simulate_sequence(sequence, limits, cell)
    r = result.readout
    assert result.outcome == "stopped_cutoff"
    assert result.events[-1][1:] == (3, result.events[-1][2], "stop", "v_min")
    stopped_in = result.events[-1][2]
    assert 0 < stopped_in < 19
    pulses = r["step_index"] == 1
    assert (r["voltage_v"][pulses] < 0.095).any()
    checks = r["step_index"] == 3
    earlier = checks & (r["repeat_index"] < stopped_in)
    assert (r["voltage_v"][earlier] > 0.095).all()
    assert r["voltage_v"][-1] <= 0.095
