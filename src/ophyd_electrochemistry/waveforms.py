"""Pure finite waveform intent and generators; no instrument communication."""

import math
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Literal

from .exceptions import UnsupportedCapabilityError, ValidationError
from .validation import cutoffs, finite_sum, integer, number, positive

SourceFunction = Literal["current", "voltage"]
PRBS_GENERATOR = "fibonacci-xor-right-lsb-v1"
MULTISINE_GENERATOR = "coherent-multisine-v1"
# Reciprocal primitive polynomials from XAPP052, constant term included;
# feedback XORs these zero-based register bits, excluding the leading degree.
PRBS_TAPS = MappingProxyType(
    {
        2: (0, 1),
        3: (0, 1),
        4: (0, 1),
        5: (0, 2),
        6: (0, 1),
        7: (0, 1),
        8: (0, 2, 3, 4),
        9: (0, 4),
        10: (0, 3),
    }
)


@dataclass(frozen=True, kw_only=True)
class WaveformSettings:
    source_function: SourceFunction
    sample_period_s: float
    voltage_limit_v: float | None = None
    current_limit_a: float | None = None
    lower_cutoff_v: float | None = None
    upper_cutoff_v: float | None = None
    repeats: int = 1

    def __post_init__(self) -> None:
        if self.source_function not in ("current", "voltage"):
            raise ValidationError("source_function must be current or voltage")
        object.__setattr__(
            self, "sample_period_s", positive(self.sample_period_s, "sample_period_s")
        )
        integer(self.repeats, "repeats")
        required = "voltage_limit_v" if self.source_function == "current" else "current_limit_a"
        forbidden = "current_limit_a" if self.source_function == "current" else "voltage_limit_v"
        object.__setattr__(self, required, positive(getattr(self, required), required))
        if getattr(self, forbidden) is not None:
            raise ValidationError(
                f"{forbidden} is inapplicable for {self.source_function} sourcing"
            )
        cutoffs(self)
        if self.source_function == "voltage" and (
            self.lower_cutoff_v is not None or self.upper_cutoff_v is not None
        ):
            raise ValidationError(
                "Voltage cutoffs apply to current sourcing; voltage intent uses limits"
            )


@dataclass(frozen=True, kw_only=True)
class PRBSWaveform(WaveformSettings):
    low_level: float
    high_level: float
    bit_period_s: float
    order: int
    seed: int
    generator_id: str = PRBS_GENERATOR

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in ("low_level", "high_level"):
            object.__setattr__(self, name, number(getattr(self, name), name))
        if self.low_level >= self.high_level:
            raise ValidationError("PRBS low_level must be below high_level")
        object.__setattr__(self, "bit_period_s", positive(self.bit_period_s, "bit_period_s"))
        _prbs_parameters(self.order, self.seed, self.generator_id)


def _prbs_parameters(order: int, seed: int, generator_id: str) -> None:
    integer(order, "order")
    if generator_id != PRBS_GENERATOR or order not in PRBS_TAPS:
        raise UnsupportedCapabilityError("PRBS supports fibonacci-xor-right-lsb-v1 orders 2..10")
    integer(seed, "seed")
    if seed >= 1 << order:
        raise ValidationError("PRBS seed must fit the nonzero order-bit register")


def prbs_bits(
    order: int, seed: int, *, generator_id: str = PRBS_GENERATOR, max_points: int = 100_000
) -> tuple[int, ...]:
    """Output LSB before shifting right; XOR taps enter the most significant bit."""
    _prbs_parameters(order, seed, generator_id)
    integer(max_points, "max_points")
    length = (1 << order) - 1
    if length > max_points:
        raise ValidationError("PRBS expansion exceeds max_points")
    state = seed
    result = []
    for _ in range(length):
        result.append(state & 1)
        feedback = 0
        for tap in PRBS_TAPS[order]:
            feedback ^= (state >> tap) & 1
        state = (state >> 1) | (feedback << (order - 1))
    return tuple(result)


@dataclass(frozen=True, kw_only=True)
class MultisineSpec(WaveformSettings):
    bias: float
    frequencies_hz: tuple[float, ...]
    amplitudes: tuple[float, ...]
    phases_rad: tuple[float, ...]
    point_period_s: float
    point_count: int
    coherence_tolerance_bins: float = 1e-9
    generator_id: str = MULTISINE_GENERATOR

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.generator_id != MULTISINE_GENERATOR:
            raise UnsupportedCapabilityError("Unknown multisine generator")
        integer(self.point_count, "point_count", minimum=3)
        object.__setattr__(self, "bias", number(self.bias, "bias"))
        object.__setattr__(self, "point_period_s", positive(self.point_period_s, "point_period_s"))
        tolerance = positive(self.coherence_tolerance_bins, "coherence_tolerance_bins", zero=True)
        if tolerance > 1e-6:
            raise ValidationError(
                "coherence_tolerance_bins is numeric tolerance, not frequency snapping"
            )
        object.__setattr__(self, "coherence_tolerance_bins", tolerance)
        for name in ("frequencies_hz", "amplitudes", "phases_rad"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or not values:
                raise ValidationError(f"{name} must be a nonempty tuple")
            check = number if name == "phases_rad" else positive
            object.__setattr__(self, name, tuple(check(v, name) for v in values))
        if not len(self.frequencies_hz) == len(self.amplitudes) == len(self.phases_rad):
            raise ValidationError("Multisine tones/amplitudes/phases must have equal lengths")
        object.__setattr__(self, "phases_rad", tuple(p % math.tau for p in self.phases_rad))
        _ = self.tone_bins
        if not math.isfinite(abs(self.bias) + finite_sum(self.amplitudes, "amplitude sum")):
            raise ValidationError("Multisine combined envelope overflows")

    @property
    def tone_bins(self) -> tuple[int, ...]:
        period = number(
            float(self.point_count * Decimal(str(self.point_period_s))), "multisine period"
        )
        result = []
        for frequency in self.frequencies_hz:
            raw = frequency * period
            if not math.isfinite(raw) or not 0 < 2 * raw < self.point_count:
                raise ValidationError("Multisine excludes DC/Nyquist/out-of-band tones")
            index = round(raw)
            if index <= 0 or 2 * index >= self.point_count:
                raise ValidationError("Multisine tone must be a positive sub-Nyquist integer bin")
            if abs(raw - index) > self.coherence_tolerance_bins:
                raise ValidationError("Multisine frequency must be coherent with the finite period")
            result.append(index)
        if len(set(result)) != len(result):
            raise ValidationError("Duplicate multisine tone bins")
        return tuple(result)


@dataclass(frozen=True, kw_only=True)
class ArbitraryWaveform(WaveformSettings):
    levels: tuple[float, ...]
    point_period_s: float
    multisine_spec: MultisineSpec | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.levels, tuple) or not self.levels:
            raise ValidationError("levels must be a nonempty immutable tuple")
        object.__setattr__(self, "levels", tuple(number(v, "levels") for v in self.levels))
        object.__setattr__(self, "point_period_s", positive(self.point_period_s, "point_period_s"))
        if self.multisine_spec is not None:
            spec = self.multisine_spec
            if not isinstance(spec, MultisineSpec) or len(self.levels) != spec.point_count:
                raise ValidationError("Invalid retained multisine specification")
            for name in WaveformSettings.__dataclass_fields__:
                if getattr(self, name) != getattr(spec, name):
                    raise ValidationError(f"Retained multisine {name} differs from waveform")
            if self.point_period_s != spec.point_period_s:
                raise ValidationError("Retained multisine source period differs")


def multisine_levels(
    spec: MultisineSpec, *, max_points: int = 100_000, max_tones: int = 64
) -> tuple[float, ...]:
    integer(max_points, "max_points")
    integer(max_tones, "max_tones")
    if spec.point_count > max_points or len(spec.amplitudes) > max_tones:
        raise ValidationError("Multisine generation exceeds point/tone budget")
    # Evaluate the requested frequencies; bin matching never silently changes them.
    return tuple(
        number(
            spec.bias
            + math.fsum(
                amplitude * math.sin(math.tau * (frequency * (k * spec.point_period_s)) + phase)
                for frequency, amplitude, phase in zip(
                    spec.frequencies_hz, spec.amplitudes, spec.phases_rad, strict=True
                )
            ),
            "generated multisine level",
        )
        for k in range(spec.point_count)
    )


def generate_multisine(
    spec: MultisineSpec, *, max_points: int = 100_000, max_tones: int = 64
) -> ArbitraryWaveform:
    """Generate uniform finite levels and retain the complete intent, without clipping."""
    settings = {name: getattr(spec, name) for name in WaveformSettings.__dataclass_fields__}
    return ArbitraryWaveform(
        **settings,
        levels=multisine_levels(spec, max_points=max_points, max_tones=max_tones),
        point_period_s=spec.point_period_s,
        multisine_spec=spec,
    )
