"""Narrow M4 finite-hold proof runtime and host-side command adapter.

This module does not provide the operational M5 acquisition runtime or an ophyd
device. It accepts only the packaged, reviewed TSP resource and keeps the live
proof surface deliberately limited to finite current holds on a resistive load.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from importlib.resources import files
from typing import Protocol

from ...acquisition import StartMode
from ...exceptions import IncompatibleInstrumentError, TransportProtocolError, ValidationError
from ...validation import number, positive
from .config import Keithley2460Config
from .transport import CommandLanguage, parse_source_output_enabled

RUNTIME_ABI = "oe-k2460-m4-hold-v1"
RUNTIME_BUILD = "m4-finite-current-hold-v2"
RUNTIME_RESOURCE = "tsp/runtime.tsp"
_MAX_CURRENT_A = 0.01
_MAX_VOLTAGE_LIMIT_V = 2.0
_MAX_DURATION_S = 60.0
_MIN_DURATION_S = 0.001
_MIN_EVENT_POLL_S = 0.001
_MAX_EVENT_POLL_S = 0.1
_SCRIPT_DIGEST_PREFIX_LENGTH = 24


class RuntimeState(StrEnum):
    IDLE = "idle"
    PREPARED = "prepared"
    WAITING_START = "waiting_start"
    RUNNING = "running"
    COMPLETE = "complete"
    START_TIMEOUT = "start_timeout"
    ABORTED = "aborted"
    ERROR = "error"


@dataclass(frozen=True, kw_only=True)
class RuntimeArtifact:
    abi: str
    build: str
    source: str
    sha256: str
    script_name: str


@dataclass(frozen=True, kw_only=True)
class CurrentHoldProof:
    """One bounded commissioning hold; not a general electrochemistry request."""

    current_a: float
    source_range_a: float
    voltage_limit_v: float
    duration_s: float
    start_mode: StartMode = StartMode.IMMEDIATE

    def __post_init__(self) -> None:
        for name in ("current_a", "source_range_a", "voltage_limit_v", "duration_s"):
            object.__setattr__(self, name, number(getattr(self, name), name))
        if not isinstance(self.start_mode, StartMode):
            raise ValidationError("start_mode must be a StartMode")
        if abs(self.current_a) > _MAX_CURRENT_A:
            raise ValidationError("M4 proof current is limited to 10 mA")
        if not abs(self.current_a) <= self.source_range_a <= _MAX_CURRENT_A:
            raise ValidationError("M4 proof source range must cover the level and not exceed 10 mA")
        if not 0.01 <= self.voltage_limit_v <= _MAX_VOLTAGE_LIMIT_V:
            raise ValidationError("M4 proof voltage limit must be in 0.01..2 V")
        if not _MIN_DURATION_S <= self.duration_s <= _MAX_DURATION_S:
            raise ValidationError("M4 proof duration must be in 0.001..60 s")


@dataclass(frozen=True, kw_only=True)
class RuntimeStatus:
    abi: str
    state: RuntimeState
    model_state: str
    model_substate: str
    last_block: int
    output_enabled: bool
    ready_level: int
    busy_level: int
    start_level: int
    start_overrun: bool
    timer_overrun: bool


class RuntimeTransport(Protocol):
    @property
    def command_language(self) -> CommandLanguage: ...

    def write(self, command: str) -> None: ...

    def query(self, command: str) -> str: ...

    def load_runtime(self, source: str, *, abi: str, sha256: str) -> None: ...


def packaged_runtime() -> RuntimeArtifact:
    readable_source = files(__package__).joinpath(RUNTIME_RESOURCE).read_text(encoding="ascii")
    if not readable_source.endswith("\n") or "\r" in readable_source:
        raise RuntimeError("Packaged TSP runtime must use LF framing and end with LF")
    # Firmware 1.7.16a accepts empty messages while collecting a script but the
    # resulting script does not return when run. Keep the resource readable and
    # make the digest cover the exact nonempty wire representation.
    source = "\n".join(line for line in readable_source.splitlines() if line) + "\n"
    digest = hashlib.sha256(source.encode("ascii")).hexdigest()
    if f'oe_m4_runtime_abi = "{RUNTIME_ABI}"' not in source:
        raise RuntimeError("Packaged TSP runtime ABI does not match the host adapter")
    if f'oe_m4_runtime_build = "{RUNTIME_BUILD}"' not in source:
        raise RuntimeError("Packaged TSP runtime build does not match the host adapter")
    return RuntimeArtifact(
        abi=RUNTIME_ABI,
        build=RUNTIME_BUILD,
        source=source,
        sha256=digest,
        script_name=f"oe_m4_{digest[:_SCRIPT_DIGEST_PREFIX_LENGTH]}",
    )


def parse_runtime_status(response: str) -> RuntimeStatus:
    fields = response.split("\t")
    if len(fields) != 11:
        raise TransportProtocolError(
            f"M4 status must contain 11 tab-separated fields, received {len(fields)}"
        )
    abi, state_raw, model_state, model_substate = fields[:4]
    if abi != RUNTIME_ABI:
        raise IncompatibleInstrumentError(f"Expected runtime ABI {RUNTIME_ABI!r}, got {abi!r}")
    try:
        state = RuntimeState(state_raw)
        last_block = int(fields[4])
        output_enabled = parse_source_output_enabled(fields[5])
        ready = _parse_digital_level(fields[6], "READY", allow_unknown=True)
        busy = _parse_digital_level(fields[7], "BUSY", allow_unknown=True)
        start = _parse_digital_level(fields[8], "START", allow_unknown=True)
        start_overrun = _parse_boolean(fields[9], "START overrun")
        timer_overrun = _parse_boolean(fields[10], "timer overrun")
    except ValueError as exc:
        raise TransportProtocolError("M4 status contains an invalid numeric/state field") from exc
    if last_block < 0 or start not in (-1, 0, 1):
        raise TransportProtocolError("M4 status block or START level is out of range")
    return RuntimeStatus(
        abi=abi,
        state=state,
        model_state=model_state,
        model_substate=model_substate,
        last_block=last_block,
        output_enabled=output_enabled,
        ready_level=ready,
        busy_level=busy,
        start_level=start,
        start_overrun=start_overrun,
        timer_overrun=timer_overrun,
    )


def _parse_binary(value: str, label: str) -> int:
    return _parse_digital_level(value, label, allow_unknown=False)


def _parse_digital_level(value: str, label: str, *, allow_unknown: bool) -> int:
    if allow_unknown and value == "-1":
        return -1
    if value in ("0", "digio.STATE_LOW"):
        return 0
    if value in ("1", "digio.STATE_HIGH"):
        return 1
    suffix = " or -1" if allow_unknown else ""
    raise TransportProtocolError(f"M4 {label} field must be a digital LOW/HIGH state{suffix}")


def _parse_boolean(value: str, label: str) -> bool:
    if value not in ("true", "false"):
        raise TransportProtocolError(f"M4 {label} field must be true or false")
    return value == "true"


def _tsp_number(value: float) -> str:
    return format(value, ".17g")


class M4RuntimeController:
    """Small commissioning adapter; M6 will own the operational device lifecycle."""

    def __init__(self, transport: RuntimeTransport, config: Keithley2460Config) -> None:
        if not isinstance(config, Keithley2460Config):
            raise ValidationError("config must be Keithley2460Config")
        self.transport = transport
        self.config = config

    def install(self) -> RuntimeArtifact:
        if self.transport.command_language != "TSP":
            raise IncompatibleInstrumentError("M4 runtime installation requires TSP mode")
        artifact = packaged_runtime()
        self.transport.load_runtime(
            artifact.source,
            abi=artifact.abi,
            sha256=artifact.sha256,
        )
        return artifact

    def prepare_current_hold(self, hold: CurrentHoldProof) -> RuntimeStatus:
        if not isinstance(hold, CurrentHoldProof):
            raise ValidationError("hold must be CurrentHoldProof")
        self._validate_against_config(hold)
        io = self.config.io
        if io.external_abort_enabled:
            raise ValidationError("External ABORT remains disabled for the M4 proof runtime")
        if self.config.safety.source_off_mode.lower() != "normal":
            raise ValidationError("M4 proof runtime currently supports only NORMAL output-off mode")
        if not _MIN_EVENT_POLL_S <= self.config.timing.poll_period_s <= _MAX_EVENT_POLL_S:
            raise ValidationError("M4 event poll period must be in 0.001..0.1 s")
        command = (
            "oe_m4_prepare_current_hold("
            + ",".join(
                (
                    _tsp_number(hold.current_a),
                    _tsp_number(hold.source_range_a),
                    _tsp_number(hold.voltage_limit_v),
                    _tsp_number(hold.duration_s),
                    "1" if hold.start_mode == StartMode.EXTERNAL_TRIGGER else "0",
                    _tsp_number(self.config.timing.external_start_timeout_s),
                    _tsp_number(self.config.timing.poll_period_s),
                    "1" if self.config.source_terminal == "front" else "0",
                    "1" if self.config.sense == "remote" else "0",
                    str(io.ready),
                    str(io.busy),
                    str(io.start),
                    str(io.ready_asserted_level),
                    str(io.busy_asserted_level),
                    "1" if io.start_edge == "rising" else "0",
                )
            )
            + ")"
        )
        self.transport.write(command)
        status = self.status()
        if status.state != RuntimeState.PREPARED or status.output_enabled:
            raise TransportProtocolError("M4 runtime did not reach output-OFF PREPARED")
        return status

    def arm(self) -> RuntimeStatus:
        self.transport.write("oe_m4_arm()")
        status = self.status()
        if status.state not in (
            RuntimeState.WAITING_START,
            RuntimeState.RUNNING,
            RuntimeState.COMPLETE,
            RuntimeState.START_TIMEOUT,
        ):
            raise TransportProtocolError(f"M4 runtime entered unexpected state {status.state}")
        return status

    def status(self) -> RuntimeStatus:
        return parse_runtime_status(self.transport.query("print(oe_m4_status())"))

    def abort(self) -> RuntimeStatus:
        self.transport.write("oe_m4_abort()")
        status = self.status()
        if status.state != RuntimeState.ABORTED or status.output_enabled:
            raise TransportProtocolError("M4 abort did not confirm ABORTED with output OFF")
        return status

    def force_safe(self) -> RuntimeStatus:
        self.transport.write("oe_m4_force_safe()")
        status = self.status()
        if status.output_enabled:
            raise TransportProtocolError("M4 force-safe did not confirm output OFF")
        return status

    def recover(self) -> RuntimeStatus:
        self.transport.write("oe_m4_recover()")
        status = self.status()
        if status.state != RuntimeState.IDLE or status.output_enabled:
            raise TransportProtocolError("M4 recovery did not confirm output-OFF IDLE")
        return status

    def _validate_against_config(self, hold: CurrentHoldProof) -> None:
        safety = self.config.safety
        allowed_current = (
            safety.charge_current_max_a if hold.current_a >= 0 else safety.discharge_current_max_a
        )
        if abs(hold.current_a) > allowed_current:
            raise ValidationError("M4 proof current exceeds configured directional safety limit")
        voltage_envelope = max(abs(safety.voltage_min_v), abs(safety.voltage_max_v))
        if hold.voltage_limit_v > voltage_envelope:
            raise ValidationError("M4 voltage limit exceeds configured voltage envelope")
        if abs(hold.current_a) * hold.voltage_limit_v > safety.power_abs_max_w:
            raise ValidationError("M4 worst-case hold power exceeds configured safety limit")
        positive(self.config.timing.external_start_timeout_s, "external_start_timeout_s")
