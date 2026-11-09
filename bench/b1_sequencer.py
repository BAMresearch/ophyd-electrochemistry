"""B1: the sequencer on the 2460 through the production backend (tier 1, passive load).

Every run goes through ``Keithley2460SequenceBackend`` (and, for the long run,
``SequenceFlyer`` under a RunEngine with the HDF5 writer), so B1 tests the code
the beamtime uses. Plan and wiring: ``bench/plan-b1.md``. Results go to one
evidence file, written after every test. The matrix stops at the first
unexpected error after confirming output OFF; a failed check is a finding and
the matrix continues.

    uv run --locked --extra visa --extra experiment python bench/b1_sequencer.py \\
        --load resistor --i-confirm-resistor
    uv run --locked --extra visa --extra experiment python bench/b1_sequencer.py \\
        --load rc --i-confirm-rc --rc-current 0.002 --rc-v-low 0.2 --rc-v-high 1.0
    uv run --locked python bench/b1_sequencer.py --dry-run   # offline: compile everything
"""

import argparse
import datetime
import json
import os
import statistics
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from ophyd_electrochemistry.exceptions import RetainedDataError
from ophyd_electrochemistry.keithley.k2460.sequencer import compile_program
from ophyd_electrochemistry.sequences.backend import READOUT_FIELDS, SequenceRequest
from ophyd_electrochemistry.sequences.builders import (
    bipolar_pulse_sequence,
    cc_step,
    cv_step,
    pulse_train_sequence,
    rest_step,
)
from ophyd_electrochemistry.sequences.models import (
    CutoffAction,
    RepeatBlock,
    SafetyLimits,
    Sampling,
    Sequence,
    Step,
    StepMode,
    expected_records,
    nominal_duration_s,
    sample_schedule,
)
from ophyd_electrochemistry.state import DeviceState

RESOURCE = "TCPIP0::169.254.113.151::5025::SOCKET"
EXPECTED_SERIAL = "04686198"
EVIDENCE = Path("docs/evidence")
R_OHM = 102.5  # nominal 100 ohm; measured since B0
I_PULSE = 0.0049  # ±0.49 V on 100 ohm, as S1
FAST = 0.006  # decided fast interval (6 ms at 0.1 NPLC)
RESISTOR = SafetyLimits(-0.8, 0.8, 0.005, 3600, 1_000_000)
RESISTOR_7V = SafetyLimits(-2.5, 2.5, 0.005, 3600, 1_000_000)

Result = dict[str, Any]


# Requests


def s1_pulse(start_mode: str = "immediate") -> SequenceRequest:
    """The S1 scope layout: rest + (+I, 0, -I, 0) x 3 at 6.25 ms (13 step starts)."""
    sequence = bipolar_pulse_sequence(
        I_PULSE, 0.1, 3, rest_s=0.1, pre_rest_s=0.1, fast_interval_s=0.00625, v_min=-0.7, v_max=0.7
    )
    return SequenceRequest(f"b1-s1-{start_mode}", sequence, RESISTOR, start_mode=start_mode)  # type: ignore[arg-type]


def fast_rest(limits: SafetyLimits, name: str) -> SequenceRequest:
    return SequenceRequest(name, Sequence((rest_step(3.0, Sampling(FAST)),)), limits)


def cutoff_next_in_repeat() -> SequenceRequest:
    """+I on 100 ohm reads 0.49 V > v_max 0.3: the cutoff fires on the first
    sample of every repetition and leaves that step's counter part-way."""
    steps = (
        rest_step(0.2, Sampling(0.01)),
        cc_step(I_PULSE, 0.3, 0.5, Sampling(0.01), CutoffAction.NEXT),
        rest_step(0.2, Sampling(0.01)),
        rest_step(0.2, Sampling(0.01)),
    )
    return SequenceRequest("b1-next-repeat", Sequence(steps, RepeatBlock(1, 2, 3)), RESISTOR)


def cutoff_stop() -> SequenceRequest:
    steps = (
        rest_step(0.2, Sampling(0.01)),
        cc_step(I_PULSE, 0.3, 0.5, Sampling(0.01), CutoffAction.STOP),
        rest_step(0.2, Sampling(0.01)),
    )
    return SequenceRequest("b1-stop", Sequence(steps), RESISTOR)


def absolute_window() -> SequenceRequest:
    """No step cutoff, NEXT: 0.49 V outside the absolute window 0.4 V stops the run."""
    steps = (
        rest_step(0.2, Sampling(0.01)),
        Step(StepMode.CURRENT, I_PULSE, 0.5, Sampling(0.01)),
        rest_step(0.2, Sampling(0.01)),
    )
    limits = replace(RESISTOR, v_max_abs=0.4)
    return SequenceRequest("b1-abs-window", Sequence(steps), limits)


def function_switch() -> SequenceRequest:
    """Current -> voltage -> voltage -> current: in-model function switches with
    OUTPUT ON; CV at 0.3 V draws 3 mA, so taper 1 mA never fires, 4 mA fires at once."""
    steps = (
        rest_step(0.2, Sampling(0.01)),
        cv_step(0.3, 0.001, 1.0, Sampling(0.01)),
        cv_step(0.2, 0.004, 1.0, Sampling(0.01)),
        cc_step(-I_PULSE, -0.7, 0.5, Sampling(0.01)),
        rest_step(0.2, Sampling(0.01)),
    )
    return SequenceRequest("b1-cv-switch", Sequence(steps), RESISTOR)


def relaxed_end(rest_end_v: float, name: str, pulse_s: float = 0.5) -> SequenceRequest:
    """-1 mA pulses: about -0.10 V under load, 0 V at rest; the last 1 s of each
    3 s rest stops the run below rest_end_v (strict)."""
    sequence = pulse_train_sequence(
        -0.001,
        pulse_s,
        3,
        rest_s=3.0,
        pre_rest_s=2.0,
        fast_interval_s=FAST,
        fast_window_s=1.0,
        interval_s=0.1,
        rest_end_v=rest_end_v,
    )
    return SequenceRequest(name, sequence, RESISTOR)


# 9 Nov: the source sinks ~1.5 % of its range for ~30 ms at output ON. The D
# tests compare the present start (a) with a 1 uA-range start (b).
STAT_OUTPUT = 128
DIP_RANGES = {"1mA": 0.0005, "10mA": I_PULSE, "100mA": 0.011}
RESISTOR_100MA = SafetyLimits(-1.5, 1.5, 0.015, 3600, 1_000_000)


def output_on_dip(range_name: str) -> SequenceRequest:
    """2 s rest, 2 s rest, 50 ms pulse, 2 s rest; each step 6 ms for 1 s, then 50 ms.

    The pulse level sets the shared current range. With start_on_lowest_range
    the first rest is on 1 uA and the range changes, output ON, at step 1.
    """
    level = DIP_RANGES[range_name]
    fast = Sampling(0.05, FAST, 1.0)
    steps = (
        rest_step(2.0, fast),
        rest_step(2.0, fast),
        Step(StepMode.CURRENT, level, 0.05, Sampling(FAST)),
        rest_step(2.0, fast),
    )
    limits = RESISTOR_100MA if range_name == "100mA" else RESISTOR
    return SequenceRequest(f"b1-dip-{range_name}", Sequence(steps), limits)


def long_pulse(seconds: float = 300.0) -> SequenceRequest:
    """Bipolar 2 s pulses sampled at 6 ms throughout (~167 records/s)."""
    repeats = max(1, round((seconds - 4) / 8))
    sequence = bipolar_pulse_sequence(
        I_PULSE,
        2.0,
        repeats,
        rest_s=2.0,
        pre_rest_s=4.0,
        fast_interval_s=FAST,
        fast_window_s=2.0,
        interval_s=0.1,
        v_min=-0.7,
        v_max=0.7,
    )
    return SequenceRequest(f"b1-long-{int(seconds)}s", sequence, RESISTOR)


def rc_cycle(current: float, v_low: float, v_high: float) -> SequenceRequest:
    # Single-phase sampling: with a fast window on the three source steps the
    # program needs 81 trigger blocks; the 2460 takes 63 (event 1130).
    steps = (
        rest_step(1.0, Sampling(0.01)),
        cc_step(current, v_high, 120, Sampling(0.05), CutoffAction.NEXT),
        cv_step(v_high, current / 5, 60, Sampling(0.05)),
        rest_step(5.0, Sampling(0.05)),
        cc_step(-current, v_low, 120, Sampling(0.05), CutoffAction.STOP),
        rest_step(1.0, Sampling(0.05)),  # not reached: the run stops at v_low
    )
    limits = SafetyLimits(min(0.0, v_low - 0.3), v_high + 0.3, max(0.005, 2 * current), 3600, 10**6)
    return SequenceRequest("b1-rc-cycle", Sequence(steps), limits)


def rc_hold(v_low: float, v_high: float, current: float) -> SequenceRequest:
    limits = SafetyLimits(min(0.0, v_low - 0.3), v_high + 0.3, max(0.005, 2 * current), 3600, 10**6)
    return SequenceRequest("b1-rc-hold", Sequence((rest_step(2.0, Sampling(0.01)),)), limits)


# Analysis


def step_timing(sequence: Sequence, readout: dict[str, np.ndarray], events: list) -> Result:
    """Actual vs nominal duration of every step execution not ended by a cutoff."""
    starts = [(e[0], e[1], e[2]) for e in events if e[3] == "step_start"]
    cut = {(e[1], e[2]) for e in events if e[3] == "cutoff"}
    end = float(readout["t_s"][-1]) if len(readout["t_s"]) else 0.0
    deviations = []
    for k, (t, step, repeat) in enumerate(starts):
        if (step, repeat) in cut:
            continue
        stop = starts[k + 1][0] if k + 1 < len(starts) else end
        nominal = sum(c * i for c, i in sample_schedule(sequence.steps[step]))
        deviations.append(stop - t - nominal)
    if not deviations:
        return {}
    total = end - (starts[0][0] if starts else 0.0)
    return {
        "executions": len(deviations),
        "deviation_ms_median": 1e3 * statistics.median(deviations),
        "deviation_ms_min": 1e3 * min(deviations),
        "deviation_ms_max": 1e3 * max(deviations),
        "run_s_actual": total,
        "run_s_nominal": nominal_duration_s(sequence),
    }


def spacing(readout: dict[str, np.ndarray]) -> Result:
    t, step = readout["t_s"], readout["step_index"]
    same = np.diff(step) == 0
    gaps = np.diff(t)[same]
    if not len(gaps):
        return {}
    return {
        "spacing_ms_median": 1e3 * float(np.median(gaps)),
        "spacing_ms_p1": 1e3 * float(np.percentile(gaps, 1)),
        "spacing_ms_p99": 1e3 * float(np.percentile(gaps, 99)),
    }


def resistance(readout: dict[str, np.ndarray]) -> Result:
    i, v = readout["current_a"], readout["voltage_v"]
    loaded = np.abs(i) > 1e-3
    if not loaded.any():
        return {}
    r = v[loaded] / i[loaded]
    return {"r_ohm_median": float(np.median(r)), "r_ohm_spread": float(np.ptp(r))}


# Harness


@dataclass
class Context:
    backend: Any
    args: argparse.Namespace
    results: dict[str, Result]
    path: Path

    def save(self) -> None:
        self.path.write_text(json.dumps(self.results, indent=2, default=str) + "\n")


def say(text: str) -> None:
    print(text, flush=True)


def check(result: Result, name: str, ok: bool, detail: object = "") -> None:
    result.setdefault("checks", {})[name] = "ok" if ok else f"FAIL {detail}"
    say(f"  {'ok  ' if ok else 'FAIL'} {name} {'' if ok else detail}")


def run_backend(
    ctx: Context,
    request: SequenceRequest,
    *,
    poll_s: float = 0.2,
    max_records: int = 2000,
    on_kickoff: Callable[[], None] | None = None,
    abort_after_s: float | None = None,
    timeout_s: float | None = None,
) -> tuple[Result, dict[str, np.ndarray], list]:
    backend = ctx.backend
    program = compile_program(
        request.sequence, request.limits, external_start=request.start_mode == "external"
    )
    result: Result = {
        "measurement_id": request.measurement_id,
        "blocks": len(program.blocks),
        "expected_records": expected_records(request.sequence),
        "nominal_s": nominal_duration_s(request.sequence),
    }
    backend.prepare(request)
    result["clock_offset_s"] = backend.equipment().get("clock_offset_s")
    result["clock_uncertainty_s"] = backend.equipment().get("clock_uncertainty_s")
    backend.kickoff()
    started = time.monotonic()
    cpu = time.process_time()
    if on_kickoff is not None:
        on_kickoff()
    readouts, events, lags, sizes = [], [], [], []
    limit = (nominal_duration_s(request.sequence) if timeout_s is None else timeout_s) + 120
    while True:
        poll = backend.poll(max_records)
        readouts.append(poll.readout)
        events.extend(poll.events)
        n = len(poll.readout["t_s"])
        sizes.append(n)
        origin = backend.time_origin_epoch_s()
        if n and origin is not None:
            lags.append(time.time() - (origin + float(poll.readout["t_s"][-1])))
        if poll.outcome is not None and n < max_records:
            result["outcome"], result["detail"] = poll.outcome, poll.detail
            break
        elapsed = time.monotonic() - started
        if abort_after_s is not None and elapsed >= abort_after_s and "aborted_at_s" not in result:
            result["aborted_at_s"] = elapsed
            backend.abort("B1 abort")
            result["state_after_abort"] = list(backend.inspect())
            continue
        if elapsed > limit:
            raise TimeoutError(f"{request.measurement_id}: no outcome after {elapsed:.0f} s")
        if n < max_records:
            time.sleep(poll_s)
    result["wall_s"] = time.monotonic() - started
    result["cpu_s"] = time.process_time() - cpu
    readout = {k: np.concatenate([r[k] for r in readouts]) for k in READOUT_FIELDS}
    tag = f"-{ctx.args.tag}" if ctx.args.tag else ""
    ctx.args.data_dir.mkdir(parents=True, exist_ok=True)
    np.savez(ctx.args.data_dir / f"{request.measurement_id}{tag}.npz", **readout)
    result["records"] = len(readout["t_s"])
    result["polls"] = len(sizes)
    if lags:
        result["lag_s_median"] = statistics.median(lags)
        result["lag_s_max"] = max(lags)
    result["state_final"] = [str(x) for x in backend.inspect()]
    result["events"] = [list(e) for e in events if e[3] != "step_start"][:50]
    result["step_starts"] = sum(e[3] == "step_start" for e in events)
    result["instrument_log"] = list(backend.instrument_log)
    return result, readout, events


def confirm_off(ctx: Context, result: Result) -> None:
    state, output = ctx.backend.inspect()
    check(result, "output OFF after run", output is False, f"{state}, {output}")


# Tests (resistor)


def r1_upload(ctx: Context) -> Result:
    """Upload only; prepare fails on any error or warning event during the upload."""
    result: Result = {}
    backend = ctx.backend
    for request in (s1_pulse(), long_pulse(), function_switch()):
        backend.prepare(request)
        result[request.measurement_id] = {
            k: v for k, v in backend.equipment().items() if k.startswith(("clock", "block"))
        }
        check(
            result,
            f"{request.measurement_id} PREPARED, OFF",
            backend.inspect() == (DeviceState.PREPARED, False),
        )
    result["status_line"] = backend._run("oe_seq_status()")
    return result


def r2_pulse(ctx: Context) -> Result:
    """S1 layout through the backend. Scope: markers line 4, BUSY line 2."""
    request = s1_pulse()
    result, readout, events = run_backend(ctx, request)
    check(result, "completed", result["outcome"] == "completed", result.get("detail"))
    check(result, "all records", result["records"] == result["expected_records"], result["records"])
    check(result, "13 step starts", result["step_starts"] == 13, result["step_starts"])
    result["timing"] = step_timing(request.sequence, readout, events)
    result["resistance"] = resistance(readout)
    r = result["resistance"].get("r_ohm_median", 0)
    check(result, "R within 1 %", abs(r - R_OHM) < 1, r)
    confirm_off(ctx, result)
    return result


def r3_range(ctx: Context) -> Result:
    """Reading spacing at 6 ms on the 2 V (window ±0.8) and 7 V (window ±2.5) ranges."""
    result: Result = {}
    for limits, name in ((RESISTOR, "range_2v"), (RESISTOR_7V, "range_7v")):
        run, readout, _ = run_backend(ctx, fast_rest(limits, f"b1-{name}"))
        run["spacing"] = spacing(readout)
        result[name] = run
        check(result, f"{name} completed", run["outcome"] == "completed", run.get("detail"))
    two = result["range_2v"]["spacing"]["spacing_ms_median"]
    seven = result["range_7v"]["spacing"]["spacing_ms_median"]
    result["7v_minus_2v_ms"] = seven - two
    check(result, "7 V within 0.05 ms of 2 V", abs(seven - two) < 0.05, f"{seven - two:.3f} ms")
    confirm_off(ctx, result)
    return result


def r4_next_in_repeat(ctx: Context) -> Result:
    """Item 1: cutoff NEXT inside the repeat block leaves a counter part-way."""
    request = cutoff_next_in_repeat()
    result, readout, events = run_backend(ctx, request)
    cutoffs = [e for e in events if e[3] == "cutoff"]
    check(
        result,
        "completed (replay = logged END)",
        result["outcome"] == "completed",
        result.get("detail"),
    )
    check(result, "3 v_max cutoffs", [e[4] for e in cutoffs] == ["v_max"] * 3, cutoffs)
    per_cc = [
        int(np.sum((readout["step_index"] == 1) & (readout["repeat_index"] == k)))
        for k in (1, 2, 3)
    ]
    result["cc_samples_per_repeat"] = per_cc
    check(result, "one cc sample per repeat", per_cc == [1, 1, 1], per_cc)
    confirm_off(ctx, result)
    return result


def r5_stop(ctx: Context) -> Result:
    result, readout, events = run_backend(ctx, cutoff_stop())
    check(result, "stopped_cutoff", result["outcome"] == "stopped_cutoff", result.get("detail"))
    check(
        result,
        "no records after the stop",
        int(readout["step_index"][-1]) == 1,
        readout["step_index"][-1],
    )
    confirm_off(ctx, result)
    return result


def r6_absolute(ctx: Context) -> Result:
    result, _, events = run_backend(ctx, absolute_window())
    check(result, "stopped_cutoff", result["outcome"] == "stopped_cutoff", result.get("detail"))
    details = [e[4] for e in events if e[3] == "cutoff"]
    check(result, "abs_window", details == ["abs_window"], details)
    confirm_off(ctx, result)
    return result


def r7_function_switch(ctx: Context) -> Result:
    request = function_switch()
    result, readout, events = run_backend(ctx, request)
    check(result, "completed", result["outcome"] == "completed", result.get("detail"))
    tapers = [(e[1], e[4]) for e in events if e[3] == "cutoff"]
    check(result, "taper fires on step 2 only", tapers == [(2, "i_abs_min")], tapers)
    cv = readout["step_index"] == 1
    result["cv_v_median"] = float(np.median(readout["voltage_v"][cv]))
    result["cv_i_median"] = float(np.median(readout["current_a"][cv]))
    check(
        result, "CV 0.3 V readback", abs(result["cv_v_median"] - 0.3) < 0.002, result["cv_v_median"]
    )
    check(result, "CV 3 mA", abs(result["cv_i_median"] - 0.003) < 0.0001, result["cv_i_median"])
    result["resistance"] = resistance(readout)
    result["timing"] = step_timing(request.sequence, readout, events)
    confirm_off(ctx, result)
    return result


def r8_external(ctx: Context) -> Result:
    """READY on line 1 while waiting; START from the generator on line 6."""
    seen: list[str] = []

    def waiting() -> None:
        say("  armed: READY (line 1) should be high; waiting for a START edge on line 6 (<=110 s)")
        for _ in range(5):
            seen.append(ctx.backend._run("oe_seq_status()"))
            seen.append(str(ctx.backend.inspect()[0]))
            time.sleep(0.1)

    result, readout, events = run_backend(
        ctx, s1_pulse("external"), on_kickoff=waiting, timeout_s=120
    )
    result["status_while_waiting"] = seen
    check(result, "WAITING_START observed", str(DeviceState.WAITING_START) in seen, seen[:2])
    check(result, "completed", result["outcome"] == "completed", result.get("detail"))
    check(result, "all records", result["records"] == result["expected_records"], result["records"])
    confirm_off(ctx, result)
    return result


def r9_long_flyer(ctx: Context) -> Result:
    """Items 2-3: 5 minutes at 6 ms through the Flyer, RunEngine and HDF5 writer."""
    from bluesky import RunEngine

    from ophyd_electrochemistry.sequences import SequenceFlyer, run_sequence
    from ophyd_electrochemistry.sequences.callbacks import RunFileWriter
    from ophyd_electrochemistry.sequences.run_file import read_run_file

    request = long_pulse(ctx.args.long_s)
    backend = ctx.backend
    lags: list[float] = []
    poll = backend.poll

    def timed_poll(max_records: int):  # type: ignore[no-untyped-def]
        result = poll(max_records)
        origin = backend.time_origin_epoch_s()
        if len(result.readout["t_s"]) and origin is not None:
            lags.append(time.time() - (origin + float(result.readout["t_s"][-1])))
        return result

    backend.poll = timed_poll
    try:
        flyer = SequenceFlyer(backend, name="k2460", poll_period_s=0.5)
        engine = RunEngine({}, call_returns_result=True)
        writer = RunFileWriter(ctx.args.data_dir)
        engine.subscribe(writer)
        cpu, started = time.process_time(), time.monotonic()
        outcome = engine(run_sequence(flyer, request)).plan_result
        wall = time.monotonic() - started
    finally:
        del backend.poll
    saved = read_run_file(writer.path)
    readout = saved["readout"]
    events = [tuple(row) for row in zip(*saved["events"].values(), strict=True)]
    result: Result = {
        "outcome": outcome,
        "file": str(writer.path),
        "records": len(readout["t_s"]),
        "expected_records": expected_records(request.sequence),
        "wall_s": wall,
        "cpu_s": time.process_time() - cpu,
        "lag_s_median": statistics.median(lags) if lags else None,
        "lag_s_max": max(lags) if lags else None,
        "timing": step_timing(request.sequence, readout, events),
        "spacing": spacing(readout),
    }
    check(result, "completed", outcome == "completed", outcome)
    check(
        result,
        "all records in the file",
        result["records"] == result["expected_records"],
        result["records"],
    )
    timing = result["timing"]
    drift = timing["run_s_actual"] - timing["run_s_nominal"]
    result["drift_s"] = drift
    check(
        result,
        "run length within 0.1 %",
        abs(drift) < 1e-3 * timing["run_s_nominal"],
        f"{drift:.3f} s",
    )
    check(result, "live lag < 2 s", (result["lag_s_max"] or 0) < 2, result["lag_s_max"])
    ctx.backend.connect()  # the Flyer closed the session when it unstaged
    confirm_off(ctx, result)
    return result


def r10_abort(ctx: Context) -> Result:
    result, readout, events = run_backend(ctx, long_pulse(60), abort_after_s=5)
    check(result, "aborted", result["outcome"] == "aborted", result.get("outcome"))
    check(
        result,
        "ABORTED, OFF",
        result["state_after_abort"] == [DeviceState.ABORTED, False],
        result["state_after_abort"],
    )
    check(result, "abort event last", bool(events) and events[-1][3] == "abort", events[-1:])
    ctx.backend.recover()
    check(
        result,
        "recovered IDLE, OFF",
        ctx.backend.inspect() == (DeviceState.IDLE, False),
        ctx.backend.inspect(),
    )
    return result


def child_host_kill(args: argparse.Namespace) -> None:
    """Prepare and start a 20 s run, then exit without cleanup (as a crashed host)."""
    backend = make_backend(args)
    backend.connect()
    backend.prepare(long_pulse(20))
    backend.kickoff()
    print("child: started, exiting without cleanup", flush=True)
    os._exit(0)


def r11_host_kill(ctx: Context) -> Result:
    """Item 4: a dead host leaves a finite run that ends OFF; its records must be archived."""
    result: Result = {}
    ctx.backend.close()
    child = subprocess.run(
        [sys.executable, __file__, "--child-host-kill", "--resource", ctx.args.resource],
        capture_output=True,
        text=True,
        timeout=120,
    )
    result["child"] = (child.returncode, child.stdout[-500:], child.stderr[-500:])
    backend = ctx.backend
    backend.connect()  # a running model is observed, not reinstalled under
    states = [str(backend.inspect()[0])]
    deadline = time.monotonic() + 60
    while backend.inspect()[0] == DeviceState.RUNNING and time.monotonic() < deadline:
        time.sleep(1)
    states.append(str(backend.inspect()))
    result["states"] = states
    check(result, "running after the host died", states[0] == str(DeviceState.RUNNING), states)
    check(result, "ended OFF by itself", backend.inspect()[1] is False, states[-1])
    try:
        backend.prepare(s1_pulse())
        check(result, "prepare refuses unread records", False, "prepared")
    except RetainedDataError as exc:
        check(result, "prepare refuses unread records", True)
        result["refusal"] = str(exc)
    path = backend.archive_retained(ctx.args.data_dir / f"retained-b1-{int(time.time())}.npz")
    with np.load(path) as saved:
        result["archived_records"] = len(saved["reading"])
    result["archive"] = str(path)
    backend.prepare(s1_pulse())
    check(
        result,
        "prepare after archive",
        backend.inspect() == (DeviceState.PREPARED, False),
        backend.inspect(),
    )
    return result


def r12_relaxed_continue(ctx: Context) -> Result:
    """rest_end_v -0.05 V: pulses read about -0.10 V under load, rests 0 V; must complete."""
    result, readout, events = run_backend(ctx, relaxed_end(-0.05, "b1-relaxed-continue"))
    check(result, "completed", result["outcome"] == "completed", result.get("detail"))
    pulses = readout["voltage_v"][readout["step_index"] == 1]
    result["pulse_min_v"] = float(pulses.min())
    check(result, "pulses below rest_end_v under load", pulses.min() < -0.05, pulses.min())
    checks = readout["step_index"] == 3
    result["check_records_per_repeat"] = [
        int(np.sum(checks & (readout["repeat_index"] == k))) for k in (1, 2, 3)
    ]
    check(result, "no cutoff", not [e for e in events if e[3] == "cutoff"])
    confirm_off(ctx, result)
    return result


def r13_relaxed_stop(ctx: Context) -> Result:
    """rest_end_v +0.005 V: the first relaxed check (0 V) stops the run."""
    result, readout, events = run_backend(ctx, relaxed_end(0.005, "b1-relaxed-stop"))
    check(result, "stopped_cutoff", result["outcome"] == "stopped_cutoff", result.get("detail"))
    cutoffs = [(e[1], e[2], e[4]) for e in events if e[3] == "cutoff"]
    check(result, "v_min cutoff in the first check", cutoffs == [(3, 1, "v_min")], cutoffs)
    last = (int(readout["step_index"][-1]), int(readout["repeat_index"][-1]))
    check(result, "no records after the stop", last == (3, 1), last)
    confirm_off(ctx, result)
    return result


def r14_63_blocks(ctx: Context) -> Result:
    """A program of exactly 63 trigger blocks uploads and runs (64 fails, event 1130)."""
    request = relaxed_end(-0.05, "b1-63-blocks", pulse_s=2.0)
    blocks = len(compile_program(request.sequence, request.limits).blocks)
    result, _, _ = run_backend(ctx, request)
    result["blocks"] = blocks
    check(result, "63 blocks", blocks == 63, blocks)
    check(result, "completed", result["outcome"] == "completed", result.get("detail"))
    confirm_off(ctx, result)
    return result


def dip_test(ctx: Context, range_name: str) -> Result:
    """Present start (a) and 1 uA-range start (b), alternating a, b, a, b."""
    result: Result = {}
    backend = ctx.backend
    default = backend.profile
    for k, variant in enumerate("abab", 1):
        request = output_on_dip(range_name)
        request = replace(request, measurement_id=f"{request.measurement_id}-{variant}{k}")
        # Bench only: swap the compile profile of the one connected backend.
        backend.profile = replace(default, start_on_lowest_range=variant == "b")
        try:
            run, readout, _ = run_backend(ctx, request)
        finally:
            backend.profile = default
        t, v, s = readout["t_s"], readout["voltage_v"], readout["step_index"]
        status = readout["source_status"].astype(int)
        summary: Result = {"outcome": run["outcome"], "detail": run.get("detail")}
        for step in (0, 1):
            m = s == step
            settled = float(np.median(v[m & (t >= t[m][-1] - 0.5)]))
            summary[f"step{step}_first_t_s"] = float(t[m][0])
            summary[f"step{step}_first_dv_mv"] = (float(v[m][0]) - settled) * 1e3
            summary[f"step{step}_first5_ua"] = [float(x) / R_OHM * 1e6 for x in v[m][:5]]
        early = s <= 1
        summary["output_on_steps_0_1"] = bool(np.all(status[early] & STAT_OUTPUT))
        summary["source_status_values"] = sorted({int(x) for x in status[early]})
        summary["instrument_log"] = run["instrument_log"]
        result[f"{variant}{k}"] = summary
        say(
            f"    {variant}{k}: {run['outcome']}, step 0 first "
            f"{summary['step0_first_dv_mv']:+.2f} mV at {summary['step0_first_t_s'] * 1e3:.0f} ms,"
            f" step 1 first {summary['step1_first_dv_mv']:+.2f} mV,"
            f" output ON {summary['output_on_steps_0_1']}"
        )
        check(result, f"{variant}{k} completed", run["outcome"] == "completed", run.get("detail"))
        check(
            result,
            f"{variant}{k} output ON through steps 0-1",
            summary["output_on_steps_0_1"],
            summary["source_status_values"],
        )
    confirm_off(ctx, result)
    return result


def d1_dip_1ma(ctx: Context) -> Result:
    return dip_test(ctx, "1mA")


def d2_dip_10ma(ctx: Context) -> Result:
    return dip_test(ctx, "10mA")


def d3_dip_100ma(ctx: Context) -> Result:
    """Needs limits beyond the resistor matrix: +-1.5 V, 15 mA (11 mA, 1.13 V, 50 ms)."""
    return dip_test(ctx, "100mA")


# Tests (RC dummy cell)


def c1_rc_cycle(ctx: Context) -> Result:
    """CC to v_high (NEXT), CV taper (NEXT), rest, CC to v_low (STOP)."""
    a = ctx.args
    request = rc_cycle(a.rc_current, a.rc_v_low, a.rc_v_high)
    result, readout, events = run_backend(ctx, request)
    cutoffs = [(e[1], e[4]) for e in events if e[3] == "cutoff"]
    check(result, "stopped_cutoff", result["outcome"] == "stopped_cutoff", result.get("detail"))
    check(
        result,
        "cutoffs v_max, i_abs_min, v_min",
        cutoffs == [(1, "v_max"), (2, "i_abs_min"), (4, "v_min")],
        cutoffs,
    )
    result["last_v"] = float(readout["voltage_v"][-1])
    result["stopped_at"] = time.time()
    result["timing"] = step_timing(request.sequence, readout, events)
    confirm_off(ctx, result)
    return result


def c2_rc_hold(ctx: Context) -> Result:
    """HIGH-Z between runs: the next run's first reading still shows v_low."""
    a = ctx.args
    previous = ctx.results.get("C1", {})
    result, readout, _ = run_backend(ctx, rc_hold(a.rc_v_low, a.rc_v_high, a.rc_current))
    first = float(readout["voltage_v"][0])
    result["first_v"] = first
    result["previous_last_v"] = previous.get("last_v")
    if "stopped_at" in previous:
        result["seconds_since_stop"] = time.time() - previous["stopped_at"]
    if previous.get("last_v") is not None:
        check(
            result,
            "voltage held within 50 mV",
            abs(first - previous["last_v"]) < 0.05,
            f"{first} V",
        )
    confirm_off(ctx, result)
    return result


TESTS: dict[str, tuple[str, Callable[[Context], Result]]] = {
    "R1": ("resistor", r1_upload),
    "R2": ("resistor", r2_pulse),
    "R3": ("resistor", r3_range),
    "R4": ("resistor", r4_next_in_repeat),
    "R5": ("resistor", r5_stop),
    "R6": ("resistor", r6_absolute),
    "R7": ("resistor", r7_function_switch),
    "R8": ("resistor", r8_external),
    "R9": ("resistor", r9_long_flyer),
    "R10": ("resistor", r10_abort),
    "R11": ("resistor", r11_host_kill),
    "R12": ("resistor", r12_relaxed_continue),
    "R13": ("resistor", r13_relaxed_stop),
    "R14": ("resistor", r14_63_blocks),
    "D1": ("resistor", d1_dip_1ma),
    "D2": ("resistor", d2_dip_10ma),
    "D3": ("resistor", d3_dip_100ma),
    "C1": ("rc", c1_rc_cycle),
    "C2": ("rc", c2_rc_hold),
}


def all_requests(args: argparse.Namespace) -> list[SequenceRequest]:
    return [
        s1_pulse(),
        s1_pulse("external"),
        fast_rest(RESISTOR, "b1-range_2v"),
        fast_rest(RESISTOR_7V, "b1-range_7v"),
        cutoff_next_in_repeat(),
        cutoff_stop(),
        absolute_window(),
        function_switch(),
        long_pulse(args.long_s),
        long_pulse(60),
        long_pulse(20),
        relaxed_end(-0.05, "b1-relaxed-continue"),
        relaxed_end(0.005, "b1-relaxed-stop"),
        relaxed_end(-0.05, "b1-63-blocks", pulse_s=2.0),
        output_on_dip("1mA"),
        output_on_dip("10mA"),
        output_on_dip("100mA"),
        rc_cycle(args.rc_current, args.rc_v_low, args.rc_v_high),
        rc_hold(args.rc_v_low, args.rc_v_high, args.rc_current),
    ]


def dry_run(args: argparse.Namespace) -> int:
    for request in all_requests(args):
        program = compile_program(
            request.sequence, request.limits, external_start=request.start_mode == "external"
        )
        calls = program.runtime_calls()
        say(
            f"{request.measurement_id:18s} {len(program.blocks):4d} blocks "
            f"{expected_records(request.sequence):7d} records "
            f"{nominal_duration_s(request.sequence):8.2f} s  "
            f"NPLC {sorted({m.nplc for m in program.measures})}  "
            f"longest call {max(map(len, calls))}"
        )
    return 0


def make_backend(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    from ophyd_electrochemistry.keithley.k2460.sequence_backend import k2460_sequence_backend

    return k2460_sequence_backend(
        args.resource, terminals="front", expected_serial_number=EXPECTED_SERIAL
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--resource", default=RESOURCE)
    parser.add_argument("--load", choices=("resistor", "rc"))
    parser.add_argument("--only", help="comma-separated test names, e.g. R2,R4")
    parser.add_argument("--i-confirm-resistor", action="store_true")
    parser.add_argument("--i-confirm-rc", action="store_true")
    parser.add_argument("--rc-current", type=float, default=0.002)
    parser.add_argument("--rc-v-low", type=float, default=0.2)
    parser.add_argument("--rc-v-high", type=float, default=1.0)
    parser.add_argument("--long-s", type=float, default=300.0)
    parser.add_argument("--data-dir", type=Path, default=Path.home() / "k2460-data" / "b1")
    parser.add_argument("--tag", default="")
    parser.add_argument(
        "--archive-retained",
        action="store_true",
        help="first save unread records of an earlier session to --data-dir",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--child-host-kill", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.dry_run:
        return dry_run(args)
    if args.child_host_kill:
        child_host_kill(args)
        return 1
    if args.load is None:
        parser.error("--load is required")
    confirmed = args.i_confirm_resistor if args.load == "resistor" else args.i_confirm_rc
    if not confirmed:
        parser.error(f"confirm the {args.load} load, wiring and limits (bench/plan-b1.md)")
    names = [n for n, (load, _) in TESTS.items() if load == args.load]
    if args.only:
        names = [n for n in args.only.split(",") if n in names]
    args.data_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.date.today().isoformat()
    path = EVIDENCE / f"k2460-{day}-b1-{args.load}{'-' + args.tag if args.tag else ''}.json"
    if path.exists():
        parser.error(f"{path} exists; pass --tag")
    backend = make_backend(args)
    ctx = Context(backend, args, {"tests": names, "resource": args.resource}, path)
    backend.connect()
    if args.archive_retained:
        archive = backend.archive_retained(args.data_dir / f"retained-{int(time.time())}.npz")
        ctx.results["archived_before_start"] = str(archive)
        say(f"Archived earlier unread records to {archive}")
    ctx.results["equipment"] = backend.equipment()
    ctx.results["status_at_start"] = backend.inspect()
    if backend.inspect() != (DeviceState.IDLE, False):
        say(f"STOP: expected IDLE with output OFF, got {backend.inspect()}")
        return 2
    ctx.save()
    for name in names:
        _, test = TESTS[name]
        say(f"{name}: {test.__doc__.splitlines()[0] if test.__doc__ else test.__name__}")
        started = time.monotonic()
        try:
            ctx.results[name] = test(ctx)
        except (Exception, KeyboardInterrupt) as exc:
            ctx.results[name] = {"exception": repr(exc), "traceback": traceback.format_exc()}
            say(f"STOP: {name} raised {exc!r}")
            try:
                ctx.results[name]["off_after_exception"] = backend._run("oe_seq_off()")
            except Exception as off_exc:
                ctx.results[name]["off_after_exception"] = f"UNCONFIRMED: {off_exc!r}"
            say(f"Output OFF after the exception: {ctx.results[name]['off_after_exception']}")
            ctx.save()
            return 1
        ctx.results[name]["seconds"] = time.monotonic() - started
        ctx.save()
    backend.close()
    failed = {
        n: r["checks"]
        for n, r in ctx.results.items()
        if isinstance(r, dict) and "checks" in r and any(v != "ok" for v in r["checks"].values())
    }
    say(f"Done. Evidence: {path}. Tests with failed checks: {sorted(failed) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
