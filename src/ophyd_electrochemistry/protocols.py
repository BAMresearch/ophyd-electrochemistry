"""Structurally validated immutable SI intent; compile to check physical feasibility."""

from dataclasses import dataclass
from typing import TypeAlias

from .exceptions import ValidationError
from .validation import cutoffs, integer, numeric_fields
from .waveforms import ArbitraryWaveform, PRBSWaveform


@dataclass(frozen=True, kw_only=True)
class PotentiostaticHold:
    voltage: float
    duration_s: float
    sample_period_s: float
    current_limit_a: float

    def __post_init__(self) -> None:
        numeric_fields(self, ("voltage",))
        numeric_fields(
            self, ("duration_s", "sample_period_s", "current_limit_a"), positive_only=True
        )


@dataclass(frozen=True, kw_only=True)
class GalvanostaticHold:
    current_a: float
    duration_s: float
    sample_period_s: float
    voltage_limit_v: float
    lower_cutoff_v: float | None = None
    upper_cutoff_v: float | None = None

    def __post_init__(self) -> None:
        numeric_fields(self, ("current_a",))
        numeric_fields(
            self, ("duration_s", "sample_period_s", "voltage_limit_v"), positive_only=True
        )
        cutoffs(self)


@dataclass(frozen=True, kw_only=True)
class CyclicVoltammetry:
    """A cycle follows start -> first vertex -> second vertex -> start."""

    start_voltage_v: float
    first_vertex_v: float
    second_vertex_v: float
    scan_rate_v_per_s: float
    cycles: int
    sample_period_s: float
    current_limit_a: float

    def __post_init__(self) -> None:
        numeric_fields(self, ("start_voltage_v", "first_vertex_v", "second_vertex_v"))
        numeric_fields(
            self, ("scan_rate_v_per_s", "sample_period_s", "current_limit_a"), positive_only=True
        )
        integer(self.cycles, "cycles")
        if self.first_vertex_v == self.second_vertex_v:
            raise ValidationError("CV vertices must differ")
        if (
            not min(self.first_vertex_v, self.second_vertex_v)
            <= self.start_voltage_v
            <= max(self.first_vertex_v, self.second_vertex_v)
        ):
            raise ValidationError("CV start must lie between its vertices")


@dataclass(frozen=True, kw_only=True)
class CurrentPulseSequence:
    baseline_current_a: float
    pulse_current_a: float
    pulse_width_s: float
    period_s: float
    count: int
    sample_period_s: float
    voltage_limit_v: float
    lower_cutoff_v: float | None = None
    upper_cutoff_v: float | None = None

    def __post_init__(self) -> None:
        numeric_fields(self, ("baseline_current_a", "pulse_current_a"))
        numeric_fields(
            self,
            ("pulse_width_s", "period_s", "sample_period_s", "voltage_limit_v"),
            positive_only=True,
        )
        integer(self.count, "count")
        if self.pulse_width_s >= self.period_s:
            raise ValidationError("pulse_width_s must be below period_s")
        cutoffs(self)


ElectrochemicalProgram: TypeAlias = (
    PotentiostaticHold
    | GalvanostaticHold
    | CyclicVoltammetry
    | CurrentPulseSequence
    | ArbitraryWaveform
    | PRBSWaveform
)
