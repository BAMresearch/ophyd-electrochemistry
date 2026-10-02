"""Structural SI validation; physical feasibility is checked by the compiler."""

import math
from collections.abc import Iterable
from typing import Protocol

from .exceptions import ValidationError


def number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a finite real number, not {value!r}")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValidationError(f"{name} is too large") from exc
    if not math.isfinite(result):
        raise ValidationError(f"{name} must be finite")
    return 0.0 if result == 0 else result


def positive(value: object, name: str, *, zero: bool = False) -> float:
    result = number(value, name)
    if result < 0 or (not zero and result == 0):
        raise ValidationError(f"{name} must be {'nonnegative' if zero else 'positive'}")
    return result


def integer(value: object, name: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValidationError(f"{name} must be an integer >= {minimum}")
    return value


def text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} must be a nonempty string")
    return value


def finite_sum(values: Iterable[float], name: str) -> float:
    try:
        return number(math.fsum(values), name)
    except OverflowError as exc:
        raise ValidationError(f"{name} overflows") from exc


def numeric_fields(instance: object, names: Iterable[str], *, positive_only: bool = False) -> None:
    for name in names:
        check = positive if positive_only else number
        object.__setattr__(instance, name, check(getattr(instance, name), name))


class _CutoffHolder(Protocol):
    @property
    def lower_cutoff_v(self) -> float | None: ...

    @property
    def upper_cutoff_v(self) -> float | None: ...


def cutoffs(instance: _CutoffHolder) -> None:
    for name in ("lower_cutoff_v", "upper_cutoff_v"):
        value = getattr(instance, name)
        if value is not None:
            object.__setattr__(instance, name, number(value, name))
    lower = instance.lower_cutoff_v
    upper = instance.upper_cutoff_v
    if lower is not None and upper is not None and lower >= upper:
        raise ValidationError("lower_cutoff_v must be below upper_cutoff_v")
