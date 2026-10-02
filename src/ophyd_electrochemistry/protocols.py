"""Unvalidated immutable SI-valued protocol templates, with no hardware commands.

Construction does not establish safety or timing feasibility. M1 must implement
pure validation; the eventual backend must also enforce C02 at prepare time.
"""

from dataclasses import dataclass
from typing import TypeAlias


@dataclass(frozen=True, kw_only=True)
class PotentiostaticHold:
    voltage: float
    duration_s: float
    sample_period_s: float
    current_limit_a: float


@dataclass(frozen=True, kw_only=True)
class GalvanostaticHold:
    current_a: float
    duration_s: float
    sample_period_s: float
    voltage_limit_v: float
    lower_cutoff_v: float | None = None
    upper_cutoff_v: float | None = None


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


@dataclass(frozen=True, kw_only=True)
class CurrentPulseSequence:
    baseline_current_a: float
    pulse_current_a: float
    pulse_width_s: float
    period_s: float
    count: int
    sample_period_s: float
    voltage_limit_v: float


ElectrochemicalProgram: TypeAlias = (
    PotentiostaticHold | GalvanostaticHold | CyclicVoltammetry | CurrentPulseSequence
)
