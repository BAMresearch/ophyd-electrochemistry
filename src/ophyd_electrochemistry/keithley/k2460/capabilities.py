"""Explicit planning profiles, never guessed instrument defaults or runtime approval."""

from dataclasses import dataclass
from typing import Literal

from ...exceptions import ValidationError
from ...validation import integer, numeric_fields, positive, text
from ...waveforms import SourceFunction


@dataclass(frozen=True, kw_only=True)
class SourceCapabilities:
    source_function: SourceFunction
    level_min: float
    level_max: float
    compliance_min: float
    compliance_max: float
    min_dwell_s: float
    max_step: float
    max_command_slope_per_s: float

    def __post_init__(self) -> None:
        if self.source_function not in ("current", "voltage"):
            raise ValidationError("Unsupported capability source function")
        numeric_fields(self, ("level_min", "level_max"))
        if self.level_min >= self.level_max:
            raise ValidationError("Capability level_min must be below level_max")
        numeric_fields(
            self,
            (
                "compliance_min",
                "compliance_max",
                "min_dwell_s",
                "max_step",
                "max_command_slope_per_s",
            ),
            positive_only=True,
        )
        if self.compliance_min > self.compliance_max:
            raise ValidationError("Invalid compliance capability interval")


@dataclass(frozen=True, kw_only=True)
class Keithley2460Capabilities:
    profile_id: str
    evidence_kind: Literal["simulation", "bench"]
    sources: tuple[SourceCapabilities, ...]
    timer_resolution_s: float
    source_action_time_s: float
    source_settling_time_s: float
    measurement_aperture_s: float
    measurement_overhead_s: float
    min_measurement_period_s: float
    max_duration_s: float
    max_unique_points: int
    max_logical_points: int
    max_measurement_records: int
    max_buffer_records: int
    source_records_per_point: int
    max_upload_bytes: int
    max_trigger_blocks: int
    fixed_trigger_blocks: int
    trigger_blocks_per_point: int
    max_tones: int
    min_points_per_highest_tone: int
    supported_off_modes: tuple[str, ...]
    supported_terminals: tuple[str, ...]
    supported_sense_modes: tuple[str, ...]
    supports_external_start: bool
    supports_external_abort: bool
    supports_local_cutoffs: bool
    local_cutoffs_use_measurements: bool
    supports_independent_measurement: bool
    aperture_may_cross_updates: bool
    abort_latency_s: float
    cutoff_latency_s: float

    def __post_init__(self) -> None:
        text(self.profile_id, "profile_id")
        if self.evidence_kind not in ("simulation", "bench"):
            raise ValidationError("evidence_kind must be simulation or bench")
        if (
            not isinstance(self.sources, tuple)
            or not self.sources
            or any(not isinstance(s, SourceCapabilities) for s in self.sources)
        ):
            raise ValidationError("sources must be a nonempty tuple of SourceCapabilities")
        if len({s.source_function for s in self.sources}) != len(self.sources):
            raise ValidationError("Duplicate source capability")
        numeric_fields(
            self,
            (
                "timer_resolution_s",
                "source_action_time_s",
                "measurement_aperture_s",
                "min_measurement_period_s",
                "max_duration_s",
                "abort_latency_s",
                "cutoff_latency_s",
            ),
            positive_only=True,
        )
        for name in ("source_settling_time_s", "measurement_overhead_s"):
            object.__setattr__(self, name, positive(getattr(self, name), name, zero=True))
        for name in (
            "max_unique_points",
            "max_logical_points",
            "max_measurement_records",
            "max_buffer_records",
            "max_upload_bytes",
            "max_trigger_blocks",
            "max_tones",
            "trigger_blocks_per_point",
        ):
            integer(getattr(self, name), name)
        integer(self.fixed_trigger_blocks, "fixed_trigger_blocks", minimum=0)
        integer(self.min_points_per_highest_tone, "min_points_per_highest_tone", minimum=3)
        if type(self.source_records_per_point) is not int or self.source_records_per_point not in (
            0,
            1,
        ):
            raise ValidationError("source_records_per_point must be 0 or 1")
        for name in ("supported_off_modes", "supported_terminals", "supported_sense_modes"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or not values or len(set(values)) != len(values):
                raise ValidationError(f"{name} requires a nonempty unique tuple")
            for value in values:
                text(value, name)
        for name in (
            "supports_external_start",
            "supports_external_abort",
            "supports_local_cutoffs",
            "local_cutoffs_use_measurements",
            "supports_independent_measurement",
            "aperture_may_cross_updates",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValidationError(f"{name} must be boolean")
