"""Golden trigger tables and independent simulator/replay contract checks."""

import re
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import numpy as np
import pytest

from ophyd_electrochemistry.exceptions import ValidationError
from ophyd_electrochemistry.keithley.k2460.sequencer import (
    BlockKind as K,
)
from ophyd_electrochemistry.keithley.k2460.sequencer import (
    Replayer,
    SequencerProfile,
    compile_program,
)
from ophyd_electrochemistry.sequences.backend import READOUT_FIELDS
from ophyd_electrochemistry.sequences.builders import (
    bipolar_pulse_sequence,
    cc_step,
    cv_step,
    cycling_sequence,
    pulse_train_sequence,
    rest_step,
)
from ophyd_electrochemistry.sequences.models import (
    CutoffAction as A,
)
from ophyd_electrochemistry.sequences.models import (
    RepeatBlock,
    SafetyLimits,
    Sampling,
    Sequence,
    Step,
    expanded,
    expected_records,
)
from ophyd_electrochemistry.sequences.models import (
    StepMode as M,
)
from ophyd_electrochemistry.sequences.simulator import CellModel, simulate_sequence


@pytest.fixture
def limits():
    return SafetyLimits(-2, 2.5, 0.005, 1e6, 999984, 0.001)


def branches(program):
    return {
        i: tuple(b.args)
        for i, b in enumerate(program.blocks, 1)
        if b.kind in (K.COUNTER, K.RESET_COUNTER, K.LIMIT_OUTSIDE, K.LIMIT_INSIDE, K.ALWAYS)
    }


def assert_reachable(program):
    todo, seen = [1], set()
    while todo:
        i = todo.pop()
        if i in seen:
            continue
        assert 1 <= i <= len(program.blocks)
        seen.add(i)
        block = program.blocks[i - 1]
        if block.kind == K.ALWAYS:
            todo.append(int(block.args[0]))
        else:
            if i < len(program.blocks):
                todo.append(i + 1)
            if block.kind in (K.LIMIT_INSIDE, K.LIMIT_OUTSIDE):
                todo.append(int(block.args[2]))
                assert program.blocks[int(block.args[3]) - 1].kind == K.MEASURE
            if block.kind == K.COUNTER:
                todo.append(int(block.args[1]))
            if block.kind == K.RESET_COUNTER:
                assert program.blocks[int(block.args[0]) - 1].kind == K.COUNTER
    assert seen == set(range(1, len(program.blocks) + 1))


@pytest.mark.parametrize("external", [False, True])
def test_s1_golden_layout(limits, external):
    sequence = bipolar_pulse_sequence(
        0.0049,
        0.1,
        3,
        rest_s=0.1,
        pre_rest_s=0.1,
        fast_interval_s=0.00625,
        v_min=-0.8,
        v_max=0.8,
    )
    limits = replace(limits, v_min_abs=-0.8, v_max_abs=0.8)
    program = compile_program(sequence, limits, external_start=external)
    # Hand-checked S1: 2 prologue, 9 first step, repeat reset, 4*8
    # repeated steps, repeat counter, 8 ending blocks. READY+WAIT add 2.
    phase = [
        K.RESET_COUNTER,
        K.RECALL,
        K.DIGIO,
        K.DELAY,
        K.MEASURE,
        K.DIGIO,
        K.LIMIT_OUTSIDE,
        K.COUNTER,
    ]
    assert [b.kind for b in program.blocks] == (
        ([K.DIGIO, K.WAIT_START] if external else [])
        + [K.LOG, K.DIGIO]
        + phase[:2]
        + [K.OUTPUT]
        + phase[2:]
        + [K.RESET_COUNTER]
        + phase * 4
        + [K.COUNTER, K.OUTPUT, K.DIGIO, K.LOG, K.ALWAYS, K.OUTPUT, K.DIGIO, K.LOG, K.LOG]
    )
    assert len(program.blocks) == (55 if external else 53)
    offset = 2 if external else 0
    expected = {
        3: (11,),
        10: (-0.8, 0.8, 50, 8),
        11: (16, 7),
        12: (45,),
        13: (20,),
        19: (-0.8, 0.8, 50, 17),
        20: (16, 16),
        21: (28,),
        27: (-0.8, 0.8, 50, 25),
        28: (16, 24),
        29: (36,),
        35: (-0.8, 0.8, 50, 33),
        36: (16, 32),
        37: (44,),
        43: (-0.8, 0.8, 50, 41),
        44: (16, 40),
        45: (3, 13),
        49: (53,),
    }
    adjusted = {}
    for block, args in expected.items():
        kind = program.blocks[block + offset - 1].kind
        positions = {
            K.RESET_COUNTER: (0,),
            K.COUNTER: (1,),
            K.LIMIT_OUTSIDE: (2, 3),
            K.ALWAYS: (0,),
        }[kind]
        adjusted[block + offset] = tuple(
            v + offset if j in positions else v for j, v in enumerate(args)
        )
    assert branches(program) == adjusted
    assert [
        i for i, b in enumerate(program.blocks, 1) if b.kind == K.OUTPUT and b.args == (1,)
    ] == [5 + offset]
    assert [s.start_block for s in program.steps] == [i + offset for i in (3, 13, 21, 29, 37)]
    assert program.capacity == 208 + 16
    if external:
        assert program.blocks[0].args == (1, 1)
        assert program.blocks[3].args == (2, 3)
    assert_reachable(program)


def test_two_phase_golden_layout(limits):
    sequence = bipolar_pulse_sequence(
        0.004,
        0.2,
        2,
        rest_s=0.2,
        pre_rest_s=0.2,
        fast_interval_s=0.006,
        fast_window_s=0.06,
        v_min=-1,
        v_max=2,
    )
    # 83 blocks: firmware 1.7.16a refuses this (event 1130, max 63), so the
    # layout is checked under the packaged runtime's own 255-block ceiling.
    with pytest.raises(ValidationError, match="83 trigger blocks exceed 63"):
        compile_program(sequence, limits)
    p = compile_program(sequence, limits, SequencerProfile(max_blocks=255))
    first = [
        K.RESET_COUNTER,
        K.RECALL,
        K.DIGIO,
        K.DELAY,
        K.MEASURE,
        K.DIGIO,
        K.LIMIT_OUTSIDE,
        K.COUNTER,
    ]
    second = [K.RESET_COUNTER, K.RECALL, K.DELAY, K.MEASURE, K.LIMIT_OUTSIDE, K.COUNTER]
    assert [b.kind for b in p.blocks] == (
        [K.LOG, K.DIGIO]
        + first[:2]
        + [K.OUTPUT]
        + first[2:]
        + second
        + [K.RESET_COUNTER]
        + (first + second) * 4
        + [K.COUNTER, K.OUTPUT, K.DIGIO, K.LOG, K.ALWAYS, K.OUTPUT, K.DIGIO, K.LOG, K.LOG]
    )
    assert [s.start_block for s in p.steps] == [3, 19, 33, 47, 61]
    assert len(p.blocks) == 83
    expected = {18: (75,), 75: (2, 19), 79: (83,)}
    for start, first_step, window in [
        (3, True, (-2, 2.5)),
        (19, False, (-1, 2)),
        (33, False, (-2, 2.5)),
        (47, False, (-1, 2)),
        (61, False, (-2, 2.5)),
    ]:
        shift = int(first_step)
        expected[start] = (start + 7 + shift,)
        expected[start + 6 + shift] = (*window, 80, start + 4 + shift)
        expected[start + 7 + shift] = (10, start + 3 + shift)
        slow = start + 8 + shift
        expected[slow] = (slow + 5,)
        expected[slow + 4] = (*window, 80, slow + 3)
        expected[slow + 5] = (2, slow + 2)
    assert branches(p) == expected
    assert [(x.nplc, x.count) for x in p.steps[1].phases] == [(0.1, 10), (1, 2)]
    assert_reachable(p)


def test_cc_cv_golden_layout(limits):
    sequence = cycling_sequence(
        0.004,
        0.2,
        2,
        3,
        rest_s=0.2,
        cv_i_abs_min=0.0001,
        max_step_s=1,
        cv_max_s=1,
        interval_s=0.1,
    )
    p = compile_program(sequence, limits)
    # CC,next needs absolute STOP before local NEXT; CV switches on,
    # following rest switches on; all other current/rest transitions stay on.
    assert [s.start_block for s in p.steps] == [4, 14, 23, 32, 41]
    assert [i for i, b in enumerate(p.blocks, 1) if b.kind == K.OUTPUT and b.args == (1,)] == [
        6,
        16,
        25,
    ]
    assert len(p.blocks) == 57
    assert branches(p) == {
        3: (49,),
        4: (13,),
        11: (-2, 2.5, 54, 9),
        12: (-2, 2, 14, 9),
        13: (10, 8),
        14: (22,),
        21: (-0.0001, 0.0001, 23, 19),
        22: (10, 18),
        23: (31,),
        30: (-2, 2.5, 54, 28),
        31: (2, 27),
        32: (40,),
        38: (-2, 2.5, 54, 36),
        39: (0.2, 2.5, 41, 36),
        40: (10, 35),
        41: (48,),
        47: (-2, 2.5, 54, 45),
        48: (2, 44),
        49: (3, 4),
        53: (57,),
    }
    assert_reachable(p)


def test_repeat_predecessor_mode_and_cutoff_entry(limits):
    sequence = Sequence(
        (
            cc_step(0.001, 1, 0.2, Sampling(0.1)),
            rest_step(0.2, Sampling(0.1)),
            cv_step(1, 0.001, 0.2, Sampling(0.1)),
            rest_step(0.2, Sampling(0.1)),
        ),
        RepeatBlock(1, 2, 3),
    )
    p = compile_program(sequence, limits)
    # The repeated first rest can follow either current or voltage: ON needed.
    for step in p.steps:
        assert p.blocks[step.start_block + 1].kind == K.OUTPUT
    first = p.steps[1].start_block
    next_branch = [b for b in p.blocks[: first - 1] if b.kind == K.LIMIT_OUTSIDE][-1]
    assert next_branch.args[2] == first - 1  # reset outer counter on first entry
    outer = next(b for b in p.blocks if b.kind == K.COUNTER and b.args[0] == 3)
    assert outer.args[1] == first
    assert_reachable(p)


def test_voltage_only_has_no_unreachable_stop_path(limits):
    p = compile_program(Sequence((Step(M.VOLTAGE, 1, 1, Sampling(0.1)),)), limits)
    assert [b.kind for b in p.blocks] == [
        K.LOG,
        K.DIGIO,
        K.RESET_COUNTER,
        K.RECALL,
        K.OUTPUT,
        K.DIGIO,
        K.DELAY,
        K.MEASURE,
        K.DIGIO,
        K.COUNTER,
        K.OUTPUT,
        K.DIGIO,
        K.LOG,
        K.LOG,
    ]
    assert branches(p) == {3: (10,), 10: (10, 7)}
    assert_reachable(p)


@pytest.mark.parametrize("interval,nplc", [(0.006, 0.1), (0.002, 0.01)])
def test_delays(limits, interval, nplc):
    p = compile_program(Sequence((rest_step(interval * 100, Sampling(interval)),)), limits)
    phase = p.steps[0].phases[0]
    assert phase.nplc == nplc
    assert phase.delay_s == pytest.approx(
        interval - p.profile.reading_s[nplc] - 0.00014 - 0.00132 / 100
    )


def test_short_pulse_and_rest_choose_different_nplc(limits):
    p = compile_program(pulse_train_sequence(0.001, 0.1, 2, fast_interval_s=0.006), limits)
    assert p.steps[1].phases[0].nplc == 0.1
    assert [ph.nplc for ph in p.steps[2].phases] == [0.1, 1]


def test_infeasible_delay_names_step_and_shortest(limits):
    with pytest.raises(ValidationError, match=r"step 0:.*shortest feasible interval"):
        compile_program(Sequence((rest_step(1, Sampling(0.001)),)), limits)


def test_ranges_dedup_and_compliance(limits):
    limits = replace(limits, v_min_abs=0)
    p = compile_program(
        Sequence(
            (
                rest_step(1, Sampling(0.1)),
                cc_step(0.004, 2, 1, Sampling(0.1)),
                rest_step(1, Sampling(0.1)),
                cv_step(2, 0.001, 1, Sampling(0.1)),
            )
        ),
        limits,
        SequencerProfile(start_on_lowest_range=False),  # dedup of the two rests
    )
    assert len(p.sources) == 3
    assert len(p.measures) == 2
    assert [s.source_entry for s in p.steps] == [1, 2, 1, 3]
    assert [(s.range, s.limit) for s in p.sources] == [(0.01, 2.6), (0.01, 2.6), (2, 0.005)]
    assert [(m.range, m.function) for m in p.measures] == [(7, "voltage"), (0.01, "current")]
    rest = compile_program(Sequence((rest_step(1, Sampling(0.1)),)), limits)
    assert rest.sources[0].range == 1e-6


@pytest.mark.parametrize(
    "profile",
    [
        SequencerProfile(voltage_ranges_v=(0.2, 2)),
        SequencerProfile(current_ranges_a=(1e-6,)),
    ],
)
def test_no_range(limits, profile):
    with pytest.raises(ValidationError, match="no .* range"):
        compile_program(Sequence((cc_step(0.004, 2, 1, Sampling(0.1)),)), limits, profile)


def test_budgets_and_runtime_invalid_inputs(limits):
    with pytest.raises(ValidationError, match="split the run"):
        compile_program(Sequence((rest_step(1, Sampling(0.1, 0.006, 0.06)),) * 30), limits)
    with pytest.raises(ValidationError, match="max_call_chars"):
        compile_program(
            Sequence((rest_step(1, Sampling(0.1)),)), limits, SequencerProfile(max_call_chars=20)
        )
    with pytest.raises(ValidationError, match="capacity"):
        compile_program(
            Sequence((rest_step(99999, Sampling(0.1)),)), replace(limits, max_records=1_000_000)
        )
    for taper in (0, 0.01):
        with pytest.raises(ValidationError, match="runtime taper"):
            compile_program(Sequence((cv_step(1, taper, 1, Sampling(0.1)),)), limits)
    with pytest.raises(ValidationError, match="low < high"):
        compile_program(Sequence((cc_step(0.001, limits.v_min_abs, 1, Sampling(0.1)),)), limits)
    with pytest.raises(ValidationError, match="i_max_abs"):
        compile_program(Sequence((rest_step(1, Sampling(0.1)),)), replace(limits, i_max_abs=8))
    with pytest.raises(ValidationError, match="compliance"):
        compile_program(Sequence((rest_step(1, Sampling(0.1)),)), replace(limits, v_max_abs=100))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"ready_line": 0},
        {"start_line": 7},
        {"busy_line": 1},
        {"marker_line": True},
        {"reading_s": {}},
        {"reading_s": {0.001: 0.01}},
        {"reading_s": {1: float("nan")}},
        {"margin_s": -1},
        {"max_blocks": 256},
        {"max_call_chars": 901},
        {"current_ranges_a": ()},
        {"voltage_ranges_v": (float("inf"),)},
    ],
)
def test_profile_validation(kwargs):
    with pytest.raises(ValidationError):
        SequencerProfile(**kwargs)


def test_profile_copies_calibration_and_is_frozen():
    table = {0.1: 0.005}
    profile = SequencerProfile(reading_s=table)
    table[0.1] = 1
    assert profile.reading_s[0.1] == 0.005
    with pytest.raises(TypeError):
        profile.reading_s[0.1] = 3
    with pytest.raises(FrozenInstanceError):
        profile.margin_s = 0


def test_runtime_calls_exact_abi_and_flat_settings(limits):
    p = compile_program(Sequence((rest_step(1, Sampling(0.1)),)), limits)
    calls = p.runtime_calls()
    assert calls[:3] == [
        "oe_seq_begin(1, 1, 19, 26)",
        "oe_seq_source(1, 1, 0.0, 1e-06, 2.6)",
        "oe_seq_measure(1, 1, 7, 1)",
    ]
    assert calls[3] == "oe_seq_block(1, 11, 1, 0, 0, 0)"
    assert calls[-1] == "oe_seq_commit()"
    runtime = Path("src/ophyd_electrochemistry/keithley/k2460/tsp/sequencer.tsp").read_text()
    signatures = {
        name: len(args.split(",")) if args else 0
        for name, args in re.findall(r"function (oe_seq_\w+)\(([^)\n]*)\)", runtime)
    }
    for call in calls:
        assert len(call) < 900
        assert re.fullmatch(r"oe_seq_[a-z]+\([-0-9.e, ]*\)", call)
        name, args = call[:-1].split("(")
        assert (len(args.split(",")) if args else 0) == signatures[name]
    assert all(isinstance(v, str | int | float | bool) for v in p.settings().values())
    assert p.settings()["output_off_mode"] == "HIGH-Z"
    assert p.settings()["marker_line"] == 4


def replay_simulation(sequence, limits, cell, seed):
    p = compile_program(sequence, limits)
    sim = simulate_sequence(sequence, limits, cell, seed=seed)
    r = sim.readout
    modes = np.array([s.mode == M.VOLTAGE for s in sequence.steps])[r["step_index"]]
    reading = np.where(modes, r["current_a"], r["voltage_v"])
    source = np.where(modes, r["voltage_v"], r["current_a"])
    replay = Replayer(p)
    results, events = [], []
    rng = np.random.default_rng(seed)
    start = 0
    while start < len(reading):
        end = start + int(rng.integers(1, 27))
        chunk, new_events = replay.feed(
            *(
                a[start:end]
                for a in (r["t_s"], reading, source, r["measure_status"], r["source_status"])
            )
        )
        results.append(chunk)
        events.extend(new_events)
        start = end
    for field in READOUT_FIELDS:
        np.testing.assert_array_equal(np.concatenate([c[field] for c in results]), r[field])
    assert [e[1:] for e in events] == [e[1:] for e in sim.events]
    np.testing.assert_allclose([e[0] for e in events], [e[0] for e in sim.events], atol=1e-12)
    assert replay.finish("end" if sim.outcome == "completed" else "stop") == (sim.outcome, "")
    return sim


@pytest.mark.parametrize("seed", [0, 3, 17])
@pytest.mark.parametrize("case", ["pulse", "bipolar", "relaxed", "cc_next", "cc_stop", "cc_cv"])
def test_replay_equals_independent_noisy_simulator(limits, case, seed):
    cell = CellModel(ocv_soc=(0.0, 1.0), ocv_v=(1.0, 1.0), noise_v=1e-6)
    if case == "pulse":
        sequence = pulse_train_sequence(
            0.001,
            0.1,
            4,
            rest_s=0.6,
            pre_rest_s=0.6,
            fast_interval_s=0.006,
        )
    elif case == "bipolar":
        sequence = bipolar_pulse_sequence(  # one sampling rate: 53 blocks
            0.001, 0.1, 4, rest_s=0.6, pre_rest_s=0.6, fast_interval_s=0.006, fast_window_s=0.6
        )
    elif case == "relaxed":
        cell = CellModel(capacity_ah=1e-5, noise_v=1e-6)
        sequence = pulse_train_sequence(-1e-3, 1, 20, rest_s=60, pre_rest_s=2, rest_end_v=0.095)
    elif case == "cc_cv":
        sequence = cycling_sequence(
            0.001,
            0.96,
            1.04,
            2,
            rest_s=1,
            cv_i_abs_min=0.0007,
            cv_max_s=30,
            max_step_s=30,
            interval_s=0.1,
        )
    else:
        action = A.NEXT if case == "cc_next" else A.STOP
        sequence = Sequence(
            (
                cc_step(0.001, 1.04, 30, Sampling(0.1), action),
                rest_step(1, Sampling(0.1)),
                cc_step(-0.001, 0.96, 30, Sampling(0.1), action),
            ),
            RepeatBlock(0, 2, 3),
        )
    sim = replay_simulation(sequence, limits, cell, seed)
    if case == "relaxed":
        assert sim.events[-1][1] == 3 and sim.outcome == "stopped_cutoff"
    if case not in ("pulse", "bipolar"):
        assert any(e[3] == "cutoff" for e in sim.events)
    if case == "cc_cv":
        assert any(e[4] == "i_abs_min" for e in sim.events)


def feed(replay, readings, sources=None, times=None, status=None, source_status=None):
    n = len(readings)
    return replay.feed(
        np.arange(1, n + 1) * 0.1 if times is None else times,
        readings,
        np.zeros(n) if sources is None else sources,
        np.zeros(n, dtype=int) if status is None else status,
        np.zeros(n, dtype=int) if source_status is None else source_status,
    )


@pytest.mark.parametrize("value,detail", [(-0.1, "v_min"), (1.1, "v_max")])
def test_strict_voltage_boundaries_and_absolute_priority(limits, value, detail):
    sequence = Sequence(
        (Step(M.REST, 0, 0.3, Sampling(0.1), v_min=0, v_max=1), rest_step(0.1, Sampling(0.1)))
    )
    p = compile_program(sequence, limits)
    replay = Replayer(p)
    r, events = feed(replay, [0, 1, value, 0.5])
    assert list(r["step_index"]) == [0, 0, 0, 1]
    assert events[1][3:] == ("cutoff", detail)
    assert events[2][:4] == (pytest.approx(0.3), 1, 0, "step_start")
    assert replay.finish("end")[0] == "completed"
    replay = Replayer(p)
    _, events = feed(replay, [3])
    assert [e[3:] for e in events] == [
        ("step_start", ""),
        ("cutoff", "abs_window"),
        ("stop", "abs_window"),
    ]
    assert replay.finish("stop")[0] == "stopped_cutoff"


@pytest.mark.parametrize("value", [-0.001, 0, 0.001])
def test_inclusive_taper_stop(limits, value):
    step = replace(cv_step(1, 0.001, 1, Sampling(0.1)), on_cutoff=A.STOP)
    replay = Replayer(compile_program(Sequence((step,)), limits))
    _, events = feed(replay, [value], [1])
    assert [e[3:] for e in events] == [
        ("step_start", ""),
        ("cutoff", "i_abs_min"),
        ("stop", "i_abs_min"),
    ]
    assert replay.finish("stop") == ("stopped_cutoff", "")


def test_next_at_last_repeat_step_and_after_block(limits):
    sequence = Sequence(
        (
            rest_step(0.1, Sampling(0.1)),
            Step(M.REST, 0, 1, Sampling(0.1), v_max=1),
            rest_step(0.1, Sampling(0.1)),
        ),
        RepeatBlock(1, 1, 3),
    )
    p = compile_program(sequence, limits)
    replay = Replayer(p)
    r, _ = feed(replay, [0.5, 1.1, 1.1, 1.1, 0.5])
    assert list(zip(r["step_index"], r["repeat_index"], strict=True)) == [
        (0, 0),
        (1, 1),
        (1, 2),
        (1, 3),
        (2, 0),
    ]
    assert replay.finish("end") == ("completed", "")
    assert_reachable(p)


def test_readback_errors_are_aggregated_per_execution_across_chunks(limits):
    p = compile_program(Sequence((rest_step(0.3, Sampling(0.1)),), RepeatBlock(0, 0, 2)), limits)
    replay = Replayer(p)
    _, events = feed(replay, [1], [0.1])
    assert [e[3] for e in events] == ["step_start"]
    _, events = feed(
        replay,
        [1, 1, 1, 1, 1],
        [0.1] * 5,
        times=[0.2, 0.3, 0.4, 0.5, 0.6],
        source_status=[0, 32, 32, 0, 0],
    )
    errors = [e for e in events if e[3] == "error"]
    # Each execution's first sample is not checked (settling).
    assert [(e[2], e[4]) for e in errors] == [
        (1, "readback mismatch, 1 samples"),
        (2, "readback mismatch, 2 samples"),
    ]
    assert replay.finish("end")[0] == "error"
    assert "readback mismatch" in replay.finish("end")[1]


def test_first_readback_of_a_step_may_settle(limits):
    """B1, 5 Nov: the first 0 A readback after output ON was -71 uA."""
    p = compile_program(Sequence((rest_step(0.3, Sampling(0.1)),)), limits)
    replay = Replayer(p)
    _, events = feed(replay, [1, 1, 1], [-7.1e-5, 0, 0], times=[0.1, 0.2, 0.3])
    assert [e[3] for e in events] == ["step_start"]
    assert replay.finish("end") == ("completed", "")


def test_status_copy_and_in_limit_readback(limits):
    replay = Replayer(compile_program(Sequence((rest_step(0.1, Sampling(0.1)),)), limits))
    r, events = feed(replay, [1], [0.1], status=[12], source_status=[32])
    assert list(r["measure_status"]) == [12]
    assert list(r["source_status"]) == [32]
    assert [e[3] for e in events] == ["step_start"]
    assert replay.finish("end") == ("completed", "")


@pytest.mark.parametrize("stop", [False, True])
def test_extra_records_and_terminal_mismatch(limits, stop):
    p = compile_program(Sequence((rest_step(0.1, Sampling(0.1)),)), limits)
    replay = Replayer(p)
    feed(replay, [3 if stop else 1])
    correct, wrong = ("stop", "end") if stop else ("end", "stop")
    assert replay.finish(correct)[0] == ("stopped_cutoff" if stop else "completed")
    outcome, detail = replay.finish(wrong)
    assert outcome == "error" and f"logged {wrong}" in detail and "predicted" in detail
    r, events = feed(replay, [1], times=[0.2])
    assert r["step_index"][0] == -1
    assert np.isnan(r["voltage_v"][0])
    assert events[0][3] == "error"
    assert replay.finish(correct)[0] == "error"
    assert replay.finish(None)[0] == "aborted"


def test_empty_chunks_and_incomplete_run(limits):
    replay = Replayer(compile_program(Sequence((rest_step(1, Sampling(0.1)),)), limits))
    r, events = feed(replay, [])
    assert tuple(r) == READOUT_FIELDS
    assert all(a.size == 0 for a in r.values()) and not events
    assert replay.finish("end") == ("error", "predicted running, logged end")
    assert replay.finish(None)[0] == "aborted"


@pytest.mark.parametrize(
    "inputs",
    [
        ([0.1], [1, 2], [0], [0], [0]),
        ([[0.1]], [1], [0], [0], [0]),
        ([0.1], [float("nan")], [0], [0], [0]),
        ([0.1], [1], [0], [0.5], [0]),
        ([0.1], [1], [0], [0], [-1]),
        ([0.2, 0.1], [1, 1], [0, 0], [0, 0], [0, 0]),
    ],
)
def test_bad_replay_input_does_not_consume_plan(limits, inputs):
    replay = Replayer(compile_program(Sequence((rest_step(0.1, Sampling(0.1)),)), limits))
    with pytest.raises(ValidationError):
        replay.feed(*inputs)
    assert replay.finish("end")[1] == "predicted running, logged end"
    assert feed(replay, [1])[0]["step_index"][0] == 0


def test_replay_chunk_independence_with_cutoffs_and_errors(limits):
    sequence = Sequence((Step(M.REST, 0, 1, Sampling(0.1), v_max=1),), RepeatBlock(0, 0, 3))
    program = compile_program(sequence, limits)
    records = (
        [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        [0.5, 1.1, 0.5, 1.1, 0.5, 1.1],
        [0.1, 0, 0.1, 0.1, 0, 0],
        [0] * 6,
        [0] * 6,
    )
    reference = Replayer(program)
    full, expected_events = reference.feed(*records)
    chunked = Replayer(program)
    chunks, events = [], []
    for i in range(6):
        chunk, ev = chunked.feed(*(v[i : i + 1] for v in records))
        chunks.append(chunk)
        events.extend(ev)
    assert events == expected_events
    for key in READOUT_FIELDS:
        np.testing.assert_array_equal(np.concatenate([c[key] for c in chunks]), full[key])
    assert chunked.finish("end") == reference.finish("end")
    assert expected_records(sequence) == 30
    assert len(expanded(sequence)) == 3


def test_hard_carbon_reference_cycle_compiles():
    # docs/beamtime-concept.md P4: CC sodiation to 5 mV, CV hold there, rest, CC to 0.2 V, rest.
    sequence = cycling_sequence(
        2e-5,
        0.005,
        0.2,
        1,
        start="discharge",
        cv_after="discharge",
        cv_i_abs_min=4e-6,
        rest_s=60,
        max_step_s=3600,
        cv_max_s=600,
    )
    program = compile_program(sequence, SafetyLimits(0.0, 0.3, 1e-3, 1e5, 10**6))
    cv = program.sources[program.steps[1].source_entry - 1]
    assert (cv.mode, cv.level, cv.range, cv.limit) == ("voltage", 0.005, 0.2, 1e-3)
    assert program.sources[0].range == 1e-4  # 20 uA on the 100 uA range
    assert program.steps[1].taper == 4e-6
    # Output ON at the start and after each source-function switch (CV, then the rest).
    on = [b for b in program.blocks if b.kind == K.OUTPUT and b.args == (1,)]
    assert len(on) == 3
    assert_reachable(program)


def test_start_on_lowest_range_gives_the_leading_rest_its_own_entry(limits):
    sequence = pulse_train_sequence(0.004, 0.1, 3, rest_s=0.5, pre_rest_s=1)
    plain = compile_program(sequence, limits, SequencerProfile(start_on_lowest_range=False))
    settled = compile_program(sequence, limits)  # the default since 10 Nov
    assert SequencerProfile().start_on_lowest_range is True
    assert [s.range for s in plain.sources] == [0.01, 0.01]
    assert [(s.level, s.range) for s in settled.sources] == [(0, 1e-6), (0.004, 0.01), (0, 0.01)]
    assert [p.source_entry for p in settled.steps] == [1, 2, 3]
    assert len(settled.blocks) == len(plain.blocks)
    assert settled.settings()["start_on_lowest_range"] is True
    # Not when the run starts inside the repeat block or with a current step.
    for other in (
        Sequence(sequence.steps, RepeatBlock(0, 2, 3)),
        Sequence((sequence.steps[1], sequence.steps[2])),
    ):
        program = compile_program(other, limits, SequencerProfile(start_on_lowest_range=True))
        assert {s.range for s in program.sources} == {0.01}
    with pytest.raises(ValidationError, match="start_on_lowest_range must be bool"):
        SequencerProfile(start_on_lowest_range=1)
