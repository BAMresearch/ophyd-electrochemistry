"""Pure sequencer compilation and incremental replay (runtime ABI oe-k2460-seq-v1).

Timing values are commissioned profile inputs, not guarantees. Compilation never
communicates with an instrument; only the packaged runtime interprets its tables.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import IntEnum
from types import MappingProxyType
from typing import Any, Literal

import numpy as np
from numpy.typing import ArrayLike, NDArray

from ...exceptions import ValidationError
from ...sequences.backend import READOUT_FIELDS, SequenceEvent
from ...sequences.models import (
    CutoffAction,
    RepeatBlock,
    SafetyLimits,
    Sequence,
    StepMode,
    expected_records,
    sample_schedule,
    validate,
)
from ...validation import integer, positive

# Firmware 1.7.16a accepts trigger blocks 1..63 only (event 1130, 6 Nov 2026).
# The backend and the packaged runtime refuse larger programs whatever the
# profile; a larger profile is for offline compiler checks only.
FIRMWARE_MAX_BLOCKS = 63


@dataclass(frozen=True)
class SequencerProfile:
    ready_line: int = 1
    busy_line: int = 2
    marker_line: int = 4
    start_line: int = 6
    vlimit_margin_v: float = 0.1
    reading_s: Mapping[float, float] = field(
        default_factory=lambda: {
            1: 0.04084,
            0.5: 0.02083,
            0.2: 0.00884,
            0.1: 0.004827,
            0.05: 0.002825,
            0.01: 0.001238,
        }
    )
    block_overhead_s: float = 0.00014
    step_entry_s: float = 0.00132
    margin_s: float = 0.0003
    max_blocks: int = FIRMWARE_MAX_BLOCKS
    max_call_chars: int = 900
    voltage_ranges_v: tuple[float, ...] = (0.2, 2, 7, 10, 20, 100)
    current_ranges_a: tuple[float, ...] = (1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 0.1, 1, 4, 5, 7)

    def __post_init__(self) -> None:
        lines = (self.ready_line, self.busy_line, self.marker_line, self.start_line)
        for line in lines:
            integer(line, "digital line")
            if line > 6:
                raise ValidationError("digital lines must be in 1..6")
        if len(set(lines)) != 4:
            raise ValidationError("digital lines must differ")
        for name in ("vlimit_margin_v", "block_overhead_s", "step_entry_s", "margin_s"):
            positive(getattr(self, name), name, zero=True)
        integer(self.max_blocks, "max_blocks")
        integer(self.max_call_chars, "max_call_chars")
        if self.max_blocks > 255 or self.max_call_chars > 900:
            raise ValidationError("profile exceeds compiler block/call limits (255/900)")
        if not self.reading_s:
            raise ValidationError("reading_s must contain commissioned timings")
        for nplc, seconds in self.reading_s.items():
            positive(nplc, "nplc")
            positive(seconds, "reading_s")
            if not 0.01 <= nplc <= 10:
                raise ValidationError("nplc must be in [0.01, 10]")
        object.__setattr__(self, "reading_s", MappingProxyType(dict(self.reading_s)))
        for name, maximum in (("voltage_ranges_v", 100), ("current_ranges_a", 7)):
            ranges = tuple(getattr(self, name))
            if not ranges:
                raise ValidationError(f"{name} must not be empty")
            for value in ranges:
                positive(value, name)
                if value > maximum:
                    raise ValidationError(f"{name} exceeds runtime maximum {maximum}")
            object.__setattr__(self, name, tuple(sorted(set(ranges))))


class BlockKind(IntEnum):
    RECALL = 1
    OUTPUT = 2
    DIGIO = 3
    DELAY = 4
    MEASURE = 5
    LIMIT_OUTSIDE = 6
    LIMIT_INSIDE = 7
    COUNTER = 8
    RESET_COUNTER = 9
    WAIT_START = 10
    LOG = 11
    ALWAYS = 12


@dataclass(frozen=True)
class SourceEntry:
    mode: Literal["current", "voltage"]
    level: float
    range: float
    limit: float


@dataclass(frozen=True)
class MeasureEntry:
    function: Literal["voltage", "current"]
    range: float
    nplc: float


@dataclass(frozen=True)
class Block:
    kind: BlockKind
    args: tuple[float, ...]


@dataclass(frozen=True)
class PhasePlan:
    count: int
    interval_s: float
    nplc: float
    delay_s: float
    measure_entry: int
    measure_block: int


@dataclass(frozen=True)
class StepPlan:
    step_index: int
    mode: StepMode
    level: float
    source_entry: int
    phases: tuple[PhasePlan, ...]
    window: tuple[float, float] | None
    taper: float | None
    on_cutoff: CutoffAction
    abs_window_first: bool
    start_block: int


def _call(name: str, *values: float) -> str:
    args = ", ".join(str(v) if isinstance(v, int) else repr(float(v)) for v in values)
    return f"oe_seq_{name}({args})"


@dataclass(frozen=True)
class SequencerProgram:
    sequence: Sequence
    limits: SafetyLimits
    profile: SequencerProfile
    external_start: bool
    sources: tuple[SourceEntry, ...]
    measures: tuple[MeasureEntry, ...]
    blocks: tuple[Block, ...]
    steps: tuple[StepPlan, ...]
    repeat: RepeatBlock | None
    capacity: int

    def runtime_calls(self) -> list[str]:
        """Upload calls only; session configuration and arming belong to the backend."""
        calls = [
            _call("begin", len(self.sources), len(self.measures), len(self.blocks), self.capacity)
        ]
        calls.extend(
            _call("source", i, 1 if s.mode == "current" else 2, s.level, s.range, s.limit)
            for i, s in enumerate(self.sources, 1)
        )
        calls.extend(
            _call("measure", i, 1 if m.function == "voltage" else 2, m.range, m.nplc)
            for i, m in enumerate(self.measures, 1)
        )
        calls.extend(
            _call("block", i, int(b.kind), *b.args, *((0,) * (4 - len(b.args))))
            for i, b in enumerate(self.blocks, 1)
        )
        calls.append(_call("commit"))
        if any(len(call) >= self.profile.max_call_chars for call in calls):
            raise ValidationError("runtime call exceeds max_call_chars")
        return calls

    def settings(self) -> dict[str, str | int | float | bool]:
        """Flat equipment metadata, including every timing calibration used."""
        result: dict[str, str | int | float | bool] = {
            "sequencer_abi": "oe-k2460-seq-v1",
            "external_start": self.external_start,
            "capacity": self.capacity,
            "block_count": len(self.blocks),
            "source_count": len(self.sources),
            "measure_count": len(self.measures),
            "output_off_mode": "HIGH-Z",
            "autozero": False,
            "source_readback": True,
        }
        for name in (
            "ready_line",
            "busy_line",
            "marker_line",
            "start_line",
            "vlimit_margin_v",
            "block_overhead_s",
            "step_entry_s",
            "margin_s",
            "max_blocks",
            "max_call_chars",
        ):
            result[name] = getattr(self.profile, name)
        for name in ("v_min_abs", "v_max_abs", "i_max_abs"):
            result[name] = getattr(self.limits, name)
        # Bluesky metadata keys may not contain '.': 0.1 NPLC -> reading_s_nplc_0p1.
        for nplc, seconds in sorted(self.profile.reading_s.items()):
            result[f"reading_s_nplc_{nplc:g}".replace(".", "p")] = seconds
        for i, source in enumerate(self.sources, 1):
            for name in ("mode", "level", "range", "limit"):
                result[f"source_{i}_{name}"] = getattr(source, name)
        for i, measure in enumerate(self.measures, 1):
            for name in ("function", "range", "nplc"):
                result[f"measure_{i}_{name}"] = getattr(measure, name)
        return result


def _range(ranges: tuple[float, ...], level: float, name: str) -> float:
    for value in ranges:
        if value >= level:
            return value
    raise ValidationError(f"no {name} range large enough for {level:g}")


def _phase(count: int, interval: float, index: int, profile: SequencerProfile) -> PhasePlan:
    overhead = profile.block_overhead_s + profile.step_entry_s / count
    for nplc in sorted(profile.reading_s, reverse=True):
        delay = interval - profile.reading_s[nplc] - overhead
        if delay >= profile.margin_s:
            if delay > 3600:
                raise ValidationError(f"step {index}: delay exceeds runtime maximum 3600 s")
            return PhasePlan(count, interval, nplc, delay, 0, 0)
    shortest = min(profile.reading_s.values()) + overhead + profile.margin_s
    raise ValidationError(
        f"step {index}: interval {interval:g} s infeasible; shortest feasible interval "
        f"{shortest:g} s for {count} samples"
    )


def _source_mode(mode: StepMode) -> Literal["current", "voltage"]:
    return "voltage" if mode == StepMode.VOLTAGE else "current"


def _assert_reachable(blocks: list[Block]) -> None:
    pending, seen = [1], set()
    while pending:
        index = pending.pop()
        if index in seen:
            continue
        assert 1 <= index <= len(blocks), "branch target outside model"
        seen.add(index)
        block = blocks[index - 1]
        if block.kind == BlockKind.ALWAYS:
            pending.append(int(block.args[0]))
            continue
        if block.kind in (BlockKind.LIMIT_OUTSIDE, BlockKind.LIMIT_INSIDE):
            pending.append(int(block.args[2]))
        elif block.kind == BlockKind.COUNTER:
            pending.append(int(block.args[1]))
        if index < len(blocks):
            pending.append(index + 1)
    assert len(seen) == len(blocks), "unreachable trigger blocks"


_DEFAULT_PROFILE = SequencerProfile()


def compile_program(
    sequence: Sequence,
    limits: SafetyLimits,
    profile: SequencerProfile = _DEFAULT_PROFILE,
    *,
    external_start: bool = False,
) -> SequencerProgram:
    """Validate and lower a finite sequence to the packaged numeric runtime ABI.

    The runtime requires nonzero-width limit windows, including a positive CV
    taper, even though the general sequence models also accept a zero taper.
    """
    validate(sequence, limits)
    if not isinstance(profile, SequencerProfile) or not isinstance(external_start, bool):
        raise ValidationError("profile must be SequencerProfile and external_start must be bool")
    capacity = expected_records(sequence) + 16
    if capacity > 1_000_000:
        raise ValidationError("runtime buffer capacity exceeds 1000000; split the run")
    if limits.i_max_abs > 7:
        raise ValidationError("i_max_abs exceeds runtime maximum 7 A")
    vlimit = max(abs(limits.v_min_abs), abs(limits.v_max_abs)) + profile.vlimit_margin_v
    if vlimit > 100:
        raise ValidationError("voltage compliance exceeds runtime maximum 100 V")
    sources: list[SourceEntry] = []
    measures: list[MeasureEntry] = []
    plans: list[StepPlan] = []
    current_range = (
        _range(
            profile.current_ranges_a,
            max(1e-6, *(abs(s.level) for s in sequence.steps if s.mode != StepMode.VOLTAGE)),
            "current source",
        )
        if any(s.mode != StepMode.VOLTAGE for s in sequence.steps)
        else 0.0
    )
    voltage_range = (
        _range(
            profile.voltage_ranges_v,
            max(abs(s.level) for s in sequence.steps if s.mode == StepMode.VOLTAGE),
            "voltage source",
        )
        if any(s.mode == StepMode.VOLTAGE for s in sequence.steps)
        else 0.0
    )
    for index, step in enumerate(sequence.steps):
        mode = _source_mode(step.mode)
        is_current = mode == "current"
        source = SourceEntry(
            mode,
            step.level,
            current_range if is_current else voltage_range,
            vlimit if is_current else limits.i_max_abs,
        )
        if source not in sources:
            sources.append(source)
        phases = []
        for count, interval in sample_schedule(step):
            phase = _phase(count, interval, index, profile)
            measure = MeasureEntry(
                "voltage" if is_current else "current",
                _range(profile.voltage_ranges_v, vlimit, "voltage measure")
                if is_current
                else _range(profile.current_ranges_a, limits.i_max_abs, "current measure"),
                phase.nplc,
            )
            if measure not in measures:
                measures.append(measure)
            phases.append(replace(phase, measure_entry=measures.index(measure) + 1))
        if step.i_abs_min is not None and not 0 < step.i_abs_min <= limits.i_max_abs:
            raise ValidationError(f"step {index}: runtime taper must be positive and <= i_max_abs")
        window = (
            None
            if not is_current
            else (
                limits.v_min_abs if step.v_min is None else step.v_min,
                limits.v_max_abs if step.v_max is None else step.v_max,
            )
        )
        if window is not None and window[0] >= window[1]:
            raise ValidationError(f"step {index}: runtime cutoff window must have low < high")
        has_step_cutoff = step.v_min is not None or step.v_max is not None
        plans.append(
            StepPlan(
                index,
                step.mode,
                step.level,
                sources.index(source) + 1,
                tuple(phases),
                window,
                step.i_abs_min,
                step.on_cutoff,
                is_current and (step.on_cutoff == CutoffAction.NEXT or not has_step_cutoff),
                0,
            )
        )

    blocks: list[Block] = []
    # Branch fixups are host-only labels, never runtime text.
    fixups: list[tuple[int, int, str]] = []
    labels: dict[str, int] = {}

    def add(kind: BlockKind, *args: float) -> int:
        blocks.append(Block(kind, args))
        return len(blocks)

    def fix(block: int, argument: int, label: str) -> None:
        fixups.append((block, argument, label))

    ready, busy, marker = (
        1 << (line - 1) for line in (profile.ready_line, profile.busy_line, profile.marker_line)
    )
    if external_start:
        add(BlockKind.DIGIO, ready, ready)
        add(BlockKind.WAIT_START)
    add(BlockKind.LOG, 1)
    add(BlockKind.DIGIO, busy, busy | ready if external_start else busy)
    repeat = sequence.repeat
    for index, plan in enumerate(plans):
        if repeat is not None and index == repeat.first:
            labels["repeat_entry"] = len(blocks) + 1
            fix(add(BlockKind.RESET_COUNTER, 0), 0, "repeat_counter")
        labels[f"step_{index}"] = len(blocks) + 1
        predecessors = [] if index == 0 else [plans[index - 1].mode]
        if repeat is not None and index == repeat.first:
            predecessors.append(plans[repeat.last].mode)
        output_on = index == 0 or any(
            _source_mode(m) != _source_mode(plan.mode) for m in predecessors
        )
        phases = []
        for phase_index, phase in enumerate(plan.phases):
            counter_label = f"counter_{index}_{phase_index}"
            fix(add(BlockKind.RESET_COUNTER, 0), 0, counter_label)
            add(BlockKind.RECALL, plan.source_entry, phase.measure_entry)
            if phase_index == 0:
                if output_on:
                    add(BlockKind.OUTPUT, 1)
                add(BlockKind.DIGIO, marker, marker)
            delay_block = add(BlockKind.DELAY, phase.delay_s)
            measure_block = add(BlockKind.MEASURE, 1)
            if phase_index == 0:
                add(BlockKind.DIGIO, 0, marker)
            next_label = (
                "repeat_counter"
                if repeat is not None and index == repeat.last
                else (f"step_{index + 1}" if index + 1 < len(plans) else "end")
            )
            # Entry from outside the repeat must execute its reset, whereas the
            # repeat's own back edge must bypass that reset.
            if repeat is not None and index + 1 == repeat.first:
                next_label = "repeat_entry"
            target = "stop" if plan.on_cutoff == CutoffAction.STOP else next_label
            if plan.window is not None:
                if plan.abs_window_first:
                    b = add(
                        BlockKind.LIMIT_OUTSIDE,
                        limits.v_min_abs,
                        limits.v_max_abs,
                        0,
                        measure_block,
                    )
                    fix(b, 2, "stop")
                step = sequence.steps[index]
                if step.v_min is not None or step.v_max is not None:
                    b = add(BlockKind.LIMIT_OUTSIDE, *plan.window, 0, measure_block)
                    fix(b, 2, target)
            elif plan.taper is not None:
                b = add(BlockKind.LIMIT_INSIDE, -plan.taper, plan.taper, 0, measure_block)
                fix(b, 2, target)
            labels[counter_label] = add(BlockKind.COUNTER, phase.count, delay_block)
            phases.append(replace(phase, measure_block=measure_block))
        plans[index] = replace(plan, phases=tuple(phases), start_block=labels[f"step_{index}"])
        if repeat is not None and index == repeat.last:
            labels["repeat_counter"] = add(BlockKind.COUNTER, repeat.count, 0)
            fix(labels["repeat_counter"], 1, f"step_{repeat.first}")
    labels["end"] = add(BlockKind.OUTPUT, 0)
    mask = ready | busy | marker
    add(BlockKind.DIGIO, 0, mask)
    add(BlockKind.LOG, 2)
    if any(label == "stop" for _, _, label in fixups):
        fix(add(BlockKind.ALWAYS, 0), 0, "done")
        labels["stop"] = add(BlockKind.OUTPUT, 0)
        add(BlockKind.DIGIO, 0, mask)
        add(BlockKind.LOG, 3)
    labels["done"] = add(BlockKind.LOG, 4)
    for block, argument, label in fixups:
        args = list(blocks[block - 1].args)
        args[argument] = labels[label]
        blocks[block - 1] = replace(blocks[block - 1], args=tuple(args))
    if len(blocks) > profile.max_blocks:
        raise ValidationError(
            f"{len(blocks)} trigger blocks exceed {profile.max_blocks}; split the run"
        )
    _assert_reachable(blocks)
    program = SequencerProgram(
        sequence,
        limits,
        profile,
        external_start,
        tuple(sources),
        tuple(measures),
        tuple(blocks),
        tuple(plans),
        repeat,
        capacity,
    )
    program.runtime_calls()  # Reject unuploadable calls at compile time.
    return program


class Replayer:
    """Walk the finite sample plan without expanding repeats or buffering records.

    Readback errors are counted per step execution and emitted when that execution
    ends. Extra records remain in the returned arrays with unknown indices (-1)
    and voltage/current NaN, so inconsistent data is never silently discarded.
    """

    def __init__(self, program: SequencerProgram) -> None:
        self.program = program
        self._step = 0
        self._repeat = 1 if program.repeat is not None and program.repeat.first == 0 else 0
        self._phase = self._sample = 0
        self._started = False
        self._previous_t = 0.0
        self._predicted = "running"
        self._errors: list[str] = []
        self._mismatches = 0
        self._extra = False

    def _event(self, time: float, kind: str, detail: str) -> SequenceEvent:
        return time, self._step, self._repeat, kind, detail

    def _readback_error(self, events: list[SequenceEvent]) -> None:
        if self._mismatches:
            events.append(
                self._event(
                    self._previous_t, "error", f"readback mismatch, {self._mismatches} samples"
                )
            )
            self._mismatches = 0

    def _advance(self) -> None:
        repeat = self.program.repeat
        if repeat is not None and self._step == repeat.last and self._repeat < repeat.count:
            self._step = repeat.first
            self._repeat += 1
        else:
            self._step += 1
            self._repeat = (
                1
                if repeat is not None and self._step == repeat.first
                else self._repeat
                if repeat is not None and repeat.first <= self._step <= repeat.last
                else 0
            )
        self._phase = self._sample = 0
        self._started = False
        if self._step == len(self.program.steps):
            self._predicted = "completed"

    def _cutoff(self, reading: float, plan: StepPlan) -> tuple[str, bool]:
        limits = self.program.limits
        if plan.window is not None:
            if plan.abs_window_first and (reading < limits.v_min_abs or reading > limits.v_max_abs):
                return "abs_window", True
            lo, hi = plan.window
            if reading < lo or reading > hi:
                step = self.program.sequence.steps[self._step]
                detail = (
                    "v_min"
                    if reading < lo and step.v_min is not None
                    else ("v_max" if reading > hi and step.v_max is not None else "abs_window")
                )
                return detail, plan.on_cutoff == CutoffAction.STOP or detail == "abs_window"
        elif plan.taper is not None and -plan.taper <= reading <= plan.taper:
            return "i_abs_min", plan.on_cutoff == CutoffAction.STOP
        return "", False

    def feed(
        self,
        t_s: ArrayLike,
        reading: ArrayLike,
        source_value: ArrayLike,
        status: ArrayLike,
        source_status: ArrayLike,
    ) -> tuple[dict[str, NDArray[Any]], list[SequenceEvent]]:
        arrays = [np.asarray(a) for a in (t_s, reading, source_value, status, source_status)]
        if any(a.ndim != 1 for a in arrays) or len({len(a) for a in arrays}) != 1:
            raise ValidationError("replay inputs must be equal-length one-dimensional arrays")
        if any(not np.all(np.isfinite(a)) for a in arrays):
            raise ValidationError("replay inputs must be finite")
        for values in arrays[3:]:
            if (
                np.any(values < 0)
                or np.any(values > np.iinfo(np.int32).max)
                or np.any(values != np.floor(values))
            ):
                raise ValidationError("replay statuses must be nonnegative int32 values")
        times, readings, sources, statuses, source_statuses = arrays
        if len(times) and (times[0] < self._previous_t or np.any(np.diff(times) < 0)):
            raise ValidationError("replay timestamps must be ordered")
        size = len(times)
        result: dict[str, NDArray[Any]] = {
            name: np.full(size, np.nan if i < 3 else -1, dtype="f8" if i < 3 else "i4")
            for i, name in enumerate(READOUT_FIELDS)
        }
        result["t_s"] = times.astype("f8", copy=True)
        result["measure_status"] = statuses.astype("i4", copy=True)
        result["source_status"] = source_statuses.astype("i4", copy=True)
        events: list[SequenceEvent] = []
        for i in range(size):
            time, value, source = float(times[i]), float(readings[i]), float(sources[i])
            if self._predicted != "running":
                if not self._extra:
                    detail = f"records beyond plan (predicted {self._predicted})"
                    events.append(self._event(time, "error", detail))
                    self._errors.append(detail)
                    self._extra = True
                self._previous_t = time
                continue
            plan = self.program.steps[self._step]
            if not self._started:
                events.append(
                    self._event(
                        self._previous_t,
                        "step_start",
                        self.program.sequence.steps[self._step].label,
                    )
                )
                self._started = True
            result["step_index"][i], result["repeat_index"][i] = self._step, self._repeat
            voltage, current = (source, value) if plan.mode == StepMode.VOLTAGE else (value, source)
            result["voltage_v"][i], result["current_a"][i] = voltage, current
            entry = self.program.sources[plan.source_entry - 1]
            # A step's first readback can still be settling (B1, 5 Nov: -71 uA on
            # a 0 A rest right after output ON, -0.06 uA from the second sample).
            settled = self._phase > 0 or self._sample > 0
            if (
                settled
                and not int(source_statuses[i]) & 32
                and abs(source - plan.level) > (0.002 * entry.range + 0.002 * abs(plan.level))
            ):
                self._mismatches += 1
                if "readback mismatch" not in self._errors:
                    self._errors.append("readback mismatch")
            self._previous_t = time
            detail, stop = self._cutoff(value, plan)
            if detail:
                events.append(self._event(time, "cutoff", detail))
                if stop:
                    events.append(self._event(time, "stop", detail))
                    self._predicted = "stop"
            self._sample += 1
            if detail or self._sample == plan.phases[self._phase].count:
                if detail or self._phase + 1 == len(plan.phases):
                    self._readback_error(events)
                    if not stop:
                        self._advance()
                else:
                    self._phase += 1
                    self._sample = 0
        return result, events

    def finish(self, logged: Literal["end", "stop"] | None) -> tuple[str, str]:
        if logged is None:
            return "aborted", "no terminal log (aborted elsewhere)"
        if not self._errors and (self._predicted, logged) == ("completed", "end"):
            return "completed", ""
        if not self._errors and (self._predicted, logged) == ("stop", "stop"):
            return "stopped_cutoff", ""
        detail = f"predicted {self._predicted}, logged {logged}"
        if self._errors:
            detail += "; " + "; ".join(self._errors)
        return "error", detail
