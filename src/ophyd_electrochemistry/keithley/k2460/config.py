"""Validated configuration intent; commissioning and compiler checks remain mandatory."""

from dataclasses import dataclass, field
from typing import Literal

from ...exceptions import ValidationError
from ...validation import integer, numeric_fields, positive, text


@dataclass(frozen=True, kw_only=True)
class DigitalIOConfig:
    ready: int = 1
    busy: int = 2
    start: int = 3
    abort: int | None = 4
    ready_asserted_level: Literal[0, 1] = 1
    busy_asserted_level: Literal[0, 1] = 1
    start_edge: Literal["rising", "falling", "either"] = "rising"
    abort_edge: Literal["rising", "falling"] = "rising"
    external_abort_enabled: bool = False

    def __post_init__(self) -> None:
        lines = (self.ready, self.busy, self.start) + (() if self.abort is None else (self.abort,))
        for line in lines:
            integer(line, "digital line")
            if line > 6:
                raise ValidationError("Digital lines must be in 1..6")
        if len(set(lines)) != len(lines):
            raise ValidationError("Digital lines must be unique")
        for value in (self.ready_asserted_level, self.busy_asserted_level):
            if type(value) is not int or value not in (0, 1):
                raise ValidationError("Output assertion levels must be integer 0 or 1")
        if self.start_edge not in ("rising", "falling", "either") or self.abort_edge not in (
            "rising",
            "falling",
        ):
            raise ValidationError("Unsupported digital edge")
        if type(self.external_abort_enabled) is not bool:
            raise ValidationError("external_abort_enabled must be boolean")
        if self.external_abort_enabled and self.abort is None:
            raise ValidationError("External abort requires an allocated line")


@dataclass(frozen=True, kw_only=True)
class SafetyConfig:
    voltage_min_v: float
    voltage_max_v: float
    charge_current_max_a: float
    discharge_current_max_a: float
    power_abs_max_w: float
    source_off_mode: str

    def __post_init__(self) -> None:
        numeric_fields(self, ("voltage_min_v", "voltage_max_v"))
        if self.voltage_min_v >= self.voltage_max_v:
            raise ValidationError("Safety voltage_min_v must be below voltage_max_v")
        numeric_fields(
            self,
            ("charge_current_max_a", "discharge_current_max_a", "power_abs_max_w"),
            positive_only=True,
        )
        text(self.source_off_mode, "source_off_mode")


@dataclass(frozen=True, kw_only=True)
class TimingPolicy:
    command_timeout_s: float
    external_start_timeout_s: float
    shutdown_timeout_s: float
    poll_period_s: float
    required_abort_latency_s: float
    required_cutoff_latency_s: float
    source_period_tolerance_s: float = 0.0
    measurement_period_tolerance_s: float = 0.0
    duration_tolerance_s: float = 0.0
    frequency_tolerance_hz: float = 0.0
    scan_rate_tolerance_v_per_s: float = 0.0

    def __post_init__(self) -> None:
        numeric_fields(
            self,
            (
                "command_timeout_s",
                "external_start_timeout_s",
                "shutdown_timeout_s",
                "poll_period_s",
                "required_abort_latency_s",
                "required_cutoff_latency_s",
            ),
            positive_only=True,
        )
        for name in (
            "source_period_tolerance_s",
            "measurement_period_tolerance_s",
            "duration_tolerance_s",
            "frequency_tolerance_hz",
            "scan_rate_tolerance_v_per_s",
        ):
            object.__setattr__(self, name, positive(getattr(self, name), name, zero=True))


@dataclass(frozen=True, kw_only=True)
class BufferPolicy:
    """Host-side allocation and transfer ceilings below runtime hard backstops."""

    capacity_ceiling_records: int = 250_000
    transfer_chunk_records: int = 128

    def __post_init__(self) -> None:
        integer(self.capacity_ceiling_records, "capacity_ceiling_records")
        integer(self.transfer_chunk_records, "transfer_chunk_records")
        if self.capacity_ceiling_records > 5_000_000:
            raise ValidationError("capacity_ceiling_records must not exceed 5,000,000")
        if self.transfer_chunk_records > 4_096:
            raise ValidationError("transfer_chunk_records must not exceed 4,096")
        if self.transfer_chunk_records > self.capacity_ceiling_records:
            raise ValidationError("transfer chunk cannot exceed the buffer capacity ceiling")


@dataclass(frozen=True, kw_only=True)
class Keithley2460Config:
    visa_resource: str
    safety: SafetyConfig
    timing: TimingPolicy
    source_terminal: Literal["front", "rear"]
    sense: Literal["local", "remote"]
    io: DigitalIOConfig = field(default_factory=DigitalIOConfig)
    buffer: BufferPolicy = field(default_factory=BufferPolicy)

    def __post_init__(self) -> None:
        text(self.visa_resource, "visa_resource")
        if not isinstance(self.safety, SafetyConfig) or not isinstance(self.timing, TimingPolicy):
            raise ValidationError("safety/timing require validated configuration objects")
        if not isinstance(self.io, DigitalIOConfig):
            raise ValidationError("io requires DigitalIOConfig")
        if not isinstance(self.buffer, BufferPolicy):
            raise ValidationError("buffer requires BufferPolicy")
        if self.source_terminal not in ("front", "rear") or self.sense not in ("local", "remote"):
            raise ValidationError("Unsupported terminals or sense mode")
