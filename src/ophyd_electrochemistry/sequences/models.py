"""Pure, immutable SI step sequences; validation is not hardware approval.

Positive current flows out of HI into the cell's positive terminal and raises
its voltage. Absolute safety limits must be enforced instrument-local as STOP
cutoffs by the eventual backend, independently of per-step cutoff actions.
"""

import math
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Any, TypeVar

from ..exceptions import ValidationError
from ..validation import integer, number, positive


class StepMode(StrEnum):
    CURRENT = "current"
    VOLTAGE = "voltage"
    REST = "rest"


class CutoffAction(StrEnum):
    NEXT = "next"
    STOP = "stop"


@dataclass(frozen=True, slots=True)
class Sampling:
    interval_s: float
    fast_interval_s: float | None = None
    fast_window_s: float = 0.0

    def __post_init__(self) -> None:
        positive(self.interval_s, "interval_s")
        positive(self.fast_window_s, "fast_window_s", zero=True)
        if self.fast_interval_s is None:
            if self.fast_window_s != 0:
                raise ValidationError("fast_window_s requires fast_interval_s")
        else:
            positive(self.fast_interval_s, "fast_interval_s")
            if self.fast_window_s == 0:
                raise ValidationError("fast_interval_s requires positive fast_window_s")
            if self.fast_interval_s >= self.interval_s:
                raise ValidationError("fast_interval_s must be below interval_s")


@dataclass(frozen=True, slots=True)
class Step:
    mode: StepMode
    level: float
    duration_s: float
    sampling: Sampling
    v_min: float | None = None
    v_max: float | None = None
    i_abs_min: float | None = None
    on_cutoff: CutoffAction = CutoffAction.NEXT
    label: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.mode, StepMode):
            raise ValidationError("mode must be a StepMode")
        number(self.level, "level")
        positive(self.duration_s, "duration_s")
        if not isinstance(self.sampling, Sampling):
            raise ValidationError("sampling must be Sampling")
        if not isinstance(self.on_cutoff, CutoffAction):
            raise ValidationError("on_cutoff must be a CutoffAction")
        if not isinstance(self.label, str):
            raise ValidationError("label must be a string")
        for name in ("v_min", "v_max"):
            if (value := getattr(self, name)) is not None:
                number(value, name)
        if self.i_abs_min is not None:
            positive(self.i_abs_min, "i_abs_min", zero=True)
        if self.mode == StepMode.REST and self.level != 0:
            raise ValidationError("level must be zero for REST")
        if self.mode == StepMode.VOLTAGE:
            if self.v_min is not None or self.v_max is not None:
                raise ValidationError("v_min/v_max are forbidden for VOLTAGE")
        elif self.i_abs_min is not None:
            raise ValidationError("i_abs_min is only allowed for VOLTAGE")


@dataclass(frozen=True, slots=True)
class RepeatBlock:
    first: int
    last: int
    count: int

    def __post_init__(self) -> None:
        integer(self.first, "first", minimum=0)
        integer(self.last, "last", minimum=0)
        integer(self.count, "count")
        if self.last < self.first:
            raise ValidationError("last must be >= first")


@dataclass(frozen=True, slots=True)
class Sequence:
    steps: tuple[Step, ...]
    repeat: RepeatBlock | None = None
    name: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.steps, tuple) or not 1 <= len(self.steps) <= 30:
            raise ValidationError("steps must be a tuple of 1..30 Steps")
        for index, step in enumerate(self.steps):
            if not isinstance(step, Step):
                raise ValidationError(f"step {index}: steps entry must be a Step")
        if self.repeat is not None and not isinstance(self.repeat, RepeatBlock):
            raise ValidationError("repeat must be RepeatBlock or None")
        if not isinstance(self.name, str):
            raise ValidationError("name must be a string")


@dataclass(frozen=True, slots=True)
class SafetyLimits:
    """Session bounds; the absolute voltage window always requires STOP cutoffs."""

    v_min_abs: float
    v_max_abs: float
    i_max_abs: float
    max_duration_s: float
    max_records: int
    min_interval_s: float = 0.005

    def __post_init__(self) -> None:
        number(self.v_min_abs, "v_min_abs")
        number(self.v_max_abs, "v_max_abs")
        if self.v_min_abs >= self.v_max_abs:
            raise ValidationError("v_min_abs must be below v_max_abs")
        positive(self.i_max_abs, "i_max_abs")
        positive(self.max_duration_s, "max_duration_s")
        integer(self.max_records, "max_records")
        positive(self.min_interval_s, "min_interval_s")


def _check_sequence(sequence: Sequence) -> None:
    if not isinstance(sequence, Sequence):
        raise ValidationError("sequence must be a Sequence")
    if sequence.repeat is not None:
        for field in ("first", "last"):
            index = getattr(sequence.repeat, field)
            if index >= len(sequence.steps):
                raise ValidationError(f"step {index}: repeat.{field} is outside steps")


def expanded(sequence: Sequence) -> list[tuple[int, int, Step]]:
    """Execution order, with one-based repeat indices (zero outside the block)."""
    _check_sequence(sequence)
    result: list[tuple[int, int, Step]] = []
    block = sequence.repeat
    for index, step in enumerate(sequence.steps):
        if block is None or index < block.first or index > block.last:
            result.append((index, 0, step))
        elif index == block.first:
            for repeat_index in range(1, block.count + 1):
                result.extend(
                    (i, repeat_index, sequence.steps[i]) for i in range(block.first, block.last + 1)
                )
    return result


_COUNT_TOLERANCE = 1e-9


def _phase(span_s: float, interval_s: float) -> tuple[int, float]:
    count = math.ceil(span_s / interval_s - _COUNT_TOLERANCE)
    return count, span_s / count


def sample_schedule(step: Step) -> tuple[tuple[int, float], ...]:
    """``(count, effective_interval_s)`` per sampling phase, fast phase first.

    The instrument times a step by its sample count, so each phase's interval is
    shortened to ``span / ceil(span / requested)``: the step lasts exactly
    ``duration_s`` and never samples slower than requested. Sample k of a phase
    is taken at the end of its k-th interval. A phase of zero span is omitted.
    """
    if not isinstance(step, Step):
        raise ValidationError("step must be a Step")
    sampling = step.sampling
    phases: list[tuple[int, float]] = []
    try:
        fast_span = 0.0
        if sampling.fast_interval_s is not None:
            fast_span = min(sampling.fast_window_s, step.duration_s)
            phases.append(_phase(fast_span, sampling.fast_interval_s))
        slow_span = step.duration_s - fast_span
        if slow_span > _COUNT_TOLERANCE * step.duration_s:
            phases.append(_phase(slow_span, sampling.interval_s))
    except OverflowError as exc:
        raise ValidationError("duration_s / sampling interval overflows record count") from exc
    return tuple(phases)


def step_records(step: Step) -> int:
    """Full-duration samples, an upper bound when a cutoff ends a step early."""
    return sum(count for count, _ in sample_schedule(step))


def _executions(sequence: Sequence, index: int) -> int:
    block = sequence.repeat
    return block.count if block is not None and block.first <= index <= block.last else 1


def nominal_duration_s(sequence: Sequence) -> float:
    """Full-duration sum over expanded steps, calculated without allocating them."""
    _check_sequence(sequence)
    try:
        return number(
            math.fsum(
                step.duration_s * _executions(sequence, index)
                for index, step in enumerate(sequence.steps)
            ),
            "nominal_duration_s",
        )
    except OverflowError as exc:
        raise ValidationError("nominal_duration_s overflows") from exc


def expected_records(sequence: Sequence) -> int:
    """Full-duration record upper bound, including every block execution."""
    _check_sequence(sequence)
    total = 0
    for index, step in enumerate(sequence.steps):
        try:
            total += step_records(step) * _executions(sequence, index)
        except ValidationError as exc:
            raise ValidationError(f"step {index}: {exc}") from exc
    return total


def validate(sequence: Sequence, limits: SafetyLimits) -> None:
    """Check a structurally valid sequence against configured session limits.

    This does not commission measurement timing or implement output protection.
    A backend must enforce the absolute voltage window and current compliance.
    """
    _check_sequence(sequence)
    if not isinstance(limits, SafetyLimits):
        raise ValidationError("limits must be SafetyLimits")
    for index, step in enumerate(sequence.steps):
        if step.mode == StepMode.CURRENT and abs(step.level) > limits.i_max_abs:
            raise ValidationError(f"step {index}: level exceeds i_max_abs")
        if step.mode == StepMode.VOLTAGE and not (
            limits.v_min_abs <= step.level <= limits.v_max_abs
        ):
            raise ValidationError(f"step {index}: level is outside absolute voltage window")
        for field in ("v_min", "v_max"):
            value = getattr(step, field)
            if value is not None and not limits.v_min_abs <= value <= limits.v_max_abs:
                raise ValidationError(f"step {index}: {field} is outside absolute voltage window")
        if step.v_min is not None and step.v_max is not None and step.v_min >= step.v_max:
            raise ValidationError(f"step {index}: v_min must be below v_max")
        for field in ("interval_s", "fast_interval_s"):
            value = getattr(step.sampling, field)
            if value is not None and value < limits.min_interval_s:
                raise ValidationError(f"step {index}: sampling.{field} is below min_interval_s")
        for _, effective_s in sample_schedule(step):
            if effective_s < limits.min_interval_s * (1 - _COUNT_TOLERANCE):
                raise ValidationError(
                    f"step {index}: effective interval {effective_s:.6g} s is below "
                    "min_interval_s; choose a duration that is a multiple of the interval"
                )
    if nominal_duration_s(sequence) > limits.max_duration_s:
        raise ValidationError("nominal_duration_s exceeds max_duration_s")
    if expected_records(sequence) > limits.max_records:
        raise ValidationError("expected_records exceeds max_records")


_Model = Sampling | Step | RepeatBlock | Sequence | SafetyLimits
_T = TypeVar("_T", Sampling, Step, RepeatBlock, Sequence, SafetyLimits)


def to_dict(obj: _Model) -> dict[str, Any]:
    """Encode a sequence model as plain JSON-safe fields, with string enums."""
    if not isinstance(obj, (Sampling, Step, RepeatBlock, Sequence, SafetyLimits)):
        raise ValidationError("to_dict requires a sequence model")

    def encode(value: Any) -> Any:
        if isinstance(value, StrEnum):
            return str(value)
        if isinstance(value, (Sampling, Step, RepeatBlock, Sequence, SafetyLimits)):
            return to_dict(value)
        if isinstance(value, tuple):
            return [encode(item) for item in value]
        return value

    return {field.name: encode(getattr(obj, field.name)) for field in fields(obj)}


def _body(d: object, cls: type[_T]) -> dict[str, Any]:
    if not isinstance(d, dict):
        raise ValidationError(f"{cls.__name__} must be a dictionary")
    if any(key not in {field.name for field in fields(cls)} for key in d):
        raise ValidationError(f"{cls.__name__} has unknown fields")
    return d.copy()


def _construct(cls: type[_T], body: dict[str, Any]) -> _T:
    try:
        return cls(**body)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{cls.__name__}: {exc}") from exc


def sequence_from_dict(d: dict[str, Any]) -> Sequence:
    """Decode metadata using the same type and value checks as construction."""
    body = _body(d, Sequence)
    steps = body.get("steps")
    if not isinstance(steps, list):
        raise ValidationError("Sequence.steps must be a list")
    decoded = []
    for index, entry in enumerate(steps):
        try:
            step = _body(entry, Step)
            step["sampling"] = _construct(Sampling, _body(step.get("sampling"), Sampling))
            mode = step.get("mode")
            if not isinstance(mode, str):
                raise ValidationError("mode must be a known string enum value")
            try:
                step["mode"] = StepMode(mode)
                step["on_cutoff"] = CutoffAction(step.get("on_cutoff", CutoffAction.NEXT))
            except (TypeError, ValueError) as exc:
                raise ValidationError("mode or on_cutoff is not a known enum value") from exc
            decoded.append(_construct(Step, step))
        except ValidationError as exc:
            raise ValidationError(f"step {index}: {exc}") from exc
    body["steps"] = tuple(decoded)
    if body.get("repeat") is not None:
        body["repeat"] = _construct(RepeatBlock, _body(body["repeat"], RepeatBlock))
    return _construct(Sequence, body)


def limits_from_dict(d: dict[str, Any]) -> SafetyLimits:
    """Decode configured safety limits without coercing numeric types."""
    return _construct(SafetyLimits, _body(d, SafetyLimits))
