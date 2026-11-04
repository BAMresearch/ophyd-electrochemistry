"""Finite battery protocols and preferred-number helpers; no instrument I/O."""

import math

from ..exceptions import ValidationError
from ..validation import integer, number, positive
from .models import CutoffAction, RepeatBlock, Sampling, Sequence, Step, StepMode

# Decided 3 Nov 2026: 6 ms at 0.1 NPLC on the 2460 (5 ms needs 0.05 NPLC).
DEFAULT_FAST_INTERVAL_S = 0.006


def renard_series(t_min: float, t_max: float, series: int = 5) -> list[float]:
    """R5 or R10 preferred times in the closed interval [t_min, t_max]."""
    positive(t_min, "t_min")
    positive(t_max, "t_max")
    if t_min > t_max:
        raise ValidationError("t_min must be <= t_max")
    integer(series, "series")
    if series not in (5, 10):
        raise ValidationError("series must be 5 or 10")
    preferred = (
        (1.0, 1.6, 2.5, 4.0, 6.3)
        if series == 5
        else (1.0, 1.25, 1.6, 2.0, 2.5, 3.15, 4.0, 5.0, 6.3, 8.0)
    )
    # Decimal construction avoids 6.3 * 0.1 becoming 0.6300000000000001,
    # which would incorrectly exclude a requested endpoint of 0.63.
    return [
        value
        for exponent in range(math.floor(math.log10(t_min)), math.floor(math.log10(t_max)) + 1)
        for base in preferred
        if t_min <= (value := float(f"{base}e{exponent}")) <= t_max
    ]


def log_repeats(
    t: float, t_lo: float = 0.1, n_lo: int = 100, t_hi: float = 100, n_hi: int = 6
) -> int:
    """Rounded log-log interpolation, clamped to the endpoint repeat counts."""
    positive(t, "t")
    positive(t_lo, "t_lo")
    positive(t_hi, "t_hi")
    integer(n_lo, "n_lo")
    integer(n_hi, "n_hi")
    if t_lo >= t_hi:
        raise ValidationError("t_lo must be below t_hi")
    if n_hi > n_lo:
        raise ValidationError("n_hi must be <= n_lo")
    if t <= t_lo:
        return n_lo
    if t >= t_hi:
        return n_hi
    if n_lo == n_hi:
        return n_lo

    def log_span(upper: float, lower: float) -> float:
        relative = (upper - lower) / lower
        # log1p preserves distinct nearby endpoints. Log differences avoid
        # overflow when the endpoints span more than the float ratio range.
        return (
            math.log1p(relative) if math.isfinite(relative) else math.log(upper) - math.log(lower)
        )

    fraction = log_span(t, t_lo) / log_span(t_hi, t_lo)
    try:
        interpolated = math.exp(math.log(n_lo) * (1 - fraction) + math.log(n_hi) * fraction)
    except OverflowError as exc:
        raise ValidationError("n_lo/n_hi overflow log_repeats interpolation") from exc
    return min(n_lo, max(n_hi, round(interpolated)))


def two_phase_sampling(
    duration_s: float,
    fast_interval_s: float = DEFAULT_FAST_INTERVAL_S,
    fast_window_s: float = 0.5,
    interval_s: float = 0.1,
) -> Sampling:
    """Use only the fast interval when the entire step fits in the fast window."""
    positive(duration_s, "duration_s")
    sampling = Sampling(interval_s, fast_interval_s, fast_window_s)
    if duration_s <= fast_window_s:
        return Sampling(fast_interval_s)
    return sampling


def cc_step(
    current_a: float,
    target_v: float,
    max_duration_s: float,
    sampling: Sampling,
    on_cutoff: CutoffAction = CutoffAction.NEXT,
) -> Step:
    """CC toward a voltage target; the caller supplies the current direction."""
    # Validate before using the sign to select the voltage cutoff.
    number(current_a, "current_a")
    number(target_v, "target_v")
    if current_a == 0:
        raise ValidationError("current_a must be nonzero; use rest_step for 0 A")
    return Step(
        StepMode.CURRENT,
        current_a,
        max_duration_s,
        sampling,
        v_max=target_v if current_a > 0 else None,
        v_min=target_v if current_a <= 0 else None,
        on_cutoff=on_cutoff,
    )


def rest_step(duration_s: float, sampling: Sampling) -> Step:
    """Zero-current rest with output on, measuring open-circuit voltage."""
    return Step(StepMode.REST, 0.0, duration_s, sampling)


def cv_step(voltage_v: float, i_abs_min: float, max_duration_s: float, sampling: Sampling) -> Step:
    number(voltage_v, "voltage_v")
    positive(i_abs_min, "i_abs_min", zero=True)
    return Step(StepMode.VOLTAGE, voltage_v, max_duration_s, sampling, i_abs_min=i_abs_min)


def bipolar_pulse_sequence(
    current_a: float,
    pulse_s: float,
    repeats: int,
    *,
    rest_s: float | None = None,
    pre_rest_s: float = 10.0,
    v_min: float | None = None,
    v_max: float | None = None,
    fast_interval_s: float = DEFAULT_FAST_INTERVAL_S,
    fast_window_s: float = 0.5,
    interval_s: float = 0.1,
    start: str = "charge",
    name: str = "",
) -> Sequence:
    """Rest, then {pulse, rest, opposite pulse, rest} x repeats.

    ``start="charge"`` pulses +I first; ``"discharge"`` pulses -I first (e.g.
    sodiation first for hard carbon against Na).
    """
    positive(current_a, "current_a")
    positive(pulse_s, "pulse_s")
    if start not in ("charge", "discharge"):
        raise ValidationError("start must be charge or discharge")
    first = current_a if start == "charge" else -current_a
    if rest_s is None:
        rest_s = max(pulse_s, 1.0)

    def sampling(duration: float) -> Sampling:
        return two_phase_sampling(duration, fast_interval_s, fast_window_s, interval_s)

    def pulse(level: float) -> Step:
        return Step(
            StepMode.CURRENT,
            level,
            pulse_s,
            sampling(pulse_s),
            v_min=v_min,
            v_max=v_max,
            on_cutoff=CutoffAction.STOP,
        )

    rest = rest_step(rest_s, sampling(rest_s))
    return Sequence(
        (
            rest_step(pre_rest_s, sampling(pre_rest_s)),
            pulse(first),
            rest,
            pulse(-first),
            rest,
        ),
        RepeatBlock(1, 4, repeats),
        name,
    )


def pulse_train_sequence(
    current_a: float,
    pulse_s: float,
    repeats: int,
    *,
    rest_s: float | None = None,
    pre_rest_s: float = 10.0,
    v_min: float | None = None,
    v_max: float | None = None,
    fast_interval_s: float = DEFAULT_FAST_INTERVAL_S,
    fast_window_s: float = 0.5,
    interval_s: float = 0.1,
    name: str = "",
) -> Sequence:
    """Rest, then {pulse, rest} x repeats, all pulses in one direction.

    The sign of ``current_a`` sets the direction (negative: sodiation for hard
    carbon against Na). A pulse reaching ``v_min`` or ``v_max`` stops the run.
    """
    number(current_a, "current_a")
    if current_a == 0:
        raise ValidationError("current_a must be nonzero")
    positive(pulse_s, "pulse_s")
    if rest_s is None:
        rest_s = max(pulse_s, 1.0)

    def sampling(duration: float) -> Sampling:
        return two_phase_sampling(duration, fast_interval_s, fast_window_s, interval_s)

    pulse = Step(
        StepMode.CURRENT,
        current_a,
        pulse_s,
        sampling(pulse_s),
        v_min=v_min,
        v_max=v_max,
        on_cutoff=CutoffAction.STOP,
    )
    return Sequence(
        (rest_step(pre_rest_s, sampling(pre_rest_s)), pulse, rest_step(rest_s, sampling(rest_s))),
        RepeatBlock(1, 2, repeats),
        name,
    )


def cycling_sequence(
    current_a: float,
    v_low: float,
    v_high: float,
    cycles: int,
    *,
    rest_s: float = 600.0,
    cv_i_abs_min: float | None = None,
    cv_max_s: float = 3600.0,
    max_step_s: float = 86400.0,
    start: str = "charge",
    cv_after: str = "charge",
    interval_s: float = 1.0,
    name: str = "",
) -> Sequence:
    """Repeat charge/rest and discharge/rest halves, ``start`` first.

    With ``cv_i_abs_min`` a CV hold follows the CC step of the ``cv_after`` half:
    at v_high after the charge, or at v_low after the discharge.
    """
    positive(current_a, "current_a")
    number(v_low, "v_low")
    number(v_high, "v_high")
    if v_low >= v_high:
        raise ValidationError("v_low must be below v_high")
    if start not in ("charge", "discharge"):
        raise ValidationError("start must be charge or discharge")
    if cv_after not in ("charge", "discharge"):
        raise ValidationError("cv_after must be charge or discharge")
    positive(cv_max_s, "cv_max_s")
    sampling = Sampling(interval_s)
    rest = rest_step(rest_s, sampling)
    charge = [cc_step(current_a, v_high, max_step_s, sampling)]
    discharge = [cc_step(-current_a, v_low, max_step_s, sampling)]
    if cv_i_abs_min is not None:
        if cv_after == "charge":
            charge.append(cv_step(v_high, cv_i_abs_min, cv_max_s, sampling))
        else:
            discharge.append(cv_step(v_low, cv_i_abs_min, cv_max_s, sampling))
    charge.append(rest)
    discharge.append(rest)
    steps = tuple(charge + discharge if start == "charge" else discharge + charge)
    return Sequence(steps, RepeatBlock(0, len(steps) - 1, cycles), name)


def soc_position_sequence(
    current_a: float,
    target_v: float,
    *,
    max_s: float,
    rest_s: float,
    interval_s: float = 1.0,
) -> Sequence:
    sampling = Sampling(interval_s)
    return Sequence((cc_step(current_a, target_v, max_s, sampling), rest_step(rest_s, sampling)))
