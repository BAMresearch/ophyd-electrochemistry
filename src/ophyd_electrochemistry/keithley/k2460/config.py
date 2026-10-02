"""Inert physical configuration templates; all fields require backend validation."""

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, kw_only=True)
class DigitalIOConfig:
    ready: int = 1
    busy: int = 2
    start: int = 3
    abort: int | None = 4
    ready_asserted_level: Literal[0, 1] = 1
    busy_asserted_level: Literal[0, 1] = 1
    start_edge: Literal["rising", "falling"] = "rising"
    abort_edge: Literal["rising", "falling"] = "rising"
    external_abort_enabled: bool = False


@dataclass(frozen=True, kw_only=True)
class SafetyConfig:
    voltage_min_v: float
    voltage_max_v: float
    charge_current_max_a: float
    discharge_current_max_a: float
    power_abs_max_w: float
    source_off_mode: str


@dataclass(frozen=True, kw_only=True)
class TimingPolicy:
    command_timeout_s: float
    external_start_timeout_s: float
    shutdown_timeout_s: float
    poll_period_s: float
    required_abort_latency_s: float


@dataclass(frozen=True, kw_only=True)
class Keithley2460Config:
    visa_resource: str
    safety: SafetyConfig
    timing: TimingPolicy
    source_terminal: Literal["front", "rear"]
    sense: Literal["local", "remote"]
    io: DigitalIOConfig = field(default_factory=DigitalIOConfig)
