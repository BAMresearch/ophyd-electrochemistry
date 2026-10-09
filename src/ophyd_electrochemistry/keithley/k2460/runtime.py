"""Narrow M4 hold and M5 buffered-measurement proof runtime adapter.

This module does not provide an operational acquisition runtime or an ophyd
device. It accepts only the packaged, reviewed TSP resource and keeps live work
limited to finite current holds and explicitly bounded readings on a resistive load.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from importlib.resources import files
from typing import Protocol

from ...acquisition import StartMode
from ...exceptions import IncompatibleInstrumentError, TransportProtocolError, ValidationError
from ...serialization import canonical_json, sha256_json
from ...validation import integer, number, positive, text
from .config import Keithley2460Config
from .transport import CommandLanguage, parse_source_output_enabled

RUNTIME_ABI = "oe-k2460-m4-hold-v1"
RUNTIME_BUILD = "m4-finite-current-hold-v12"
RUNTIME_RESOURCE = "tsp/runtime.tsp"
K2460_BUFFER_SCHEMA = "ophyd-electrochemistry/k2460-buffer-proof-v1"
_MAX_CURRENT_A = 0.01
_MAX_VOLTAGE_LIMIT_V = 2.0
_MAX_DURATION_S = 60.0
_MIN_DURATION_S = 0.001
_MIN_EVENT_POLL_S = 0.001
_MAX_EVENT_POLL_S = 0.1
_SCRIPT_DIGEST_PREFIX_LENGTH = 24
_MAX_MEASUREMENT_COUNT = 5_000_000
_MAX_TRANSFER_CHUNK_RECORDS = 4_096
_RUNTIME_GLOBALS_V4 = (
    "oe_m4_runtime_abi",
    "oe_m4_runtime_build",
    "oe_m4_state",
    "oe_m4_mode",
    "oe_m4_ready_line",
    "oe_m4_busy_line",
    "oe_m4_start_line",
    "oe_m4_ready_asserted",
    "oe_m4_busy_asserted",
    "oe_m4_ready_level",
    "oe_m4_busy_level",
    "oe_m4_start_level",
    "oe_m4_complete_block",
    "oe_m4_timeout_block",
    "oe_m4_initialize",
    "oe_m4_prepare_current_hold",
    "oe_m4_arm",
    "oe_m4_refresh_state",
    "oe_m4_status",
    "oe_m4_abort",
    "oe_m4_force_safe",
    "oe_m4_recover",
)
_RUNTIME_GLOBALS_V5 = _RUNTIME_GLOBALS_V4 + (
    "oe_m5_buffer",
    "oe_m5_buffer_capacity",
    "oe_m5_measurement_count",
    "oe_m5_prepare_current_hold_acquisition",
    "oe_m5_buffer_status",
    "oe_m5_print_buffer",
    "oe_m5_discard_buffer",
)
_REPLACEABLE_RUNTIMES = {
    "m4-finite-current-hold-v4": (
        "oe_m4_ce741e17ffcdd7f55d489c7b",
        _RUNTIME_GLOBALS_V4,
        False,
    ),
    "m4-finite-current-hold-v5": (
        "oe_m4_083f3d3a4749ad9b8cb7a771",
        _RUNTIME_GLOBALS_V5,
        True,
    ),
    "m4-finite-current-hold-v6": (
        "oe_m4_e6f0b55d4b2e240dd65e9d3f",
        _RUNTIME_GLOBALS_V5,
        True,
    ),
    "m4-finite-current-hold-v7": (
        "oe_m4_7aa380ecef2189eee1bf3798",
        _RUNTIME_GLOBALS_V5,
        True,
    ),
    "m4-finite-current-hold-v8": (
        "oe_m4_9b1f6b90992b46c362073347",
        _RUNTIME_GLOBALS_V5,
        True,
    ),
    "m4-finite-current-hold-v9": (
        "oe_m4_6df8a9ae8a86703bedbd0bf2",
        _RUNTIME_GLOBALS_V5,
        True,
    ),
    "m4-finite-current-hold-v10": (
        "oe_m4_d2255a33f73e7d593a78ee80",
        _RUNTIME_GLOBALS_V5,
        True,
    ),
    "m4-finite-current-hold-v11": (
        "oe_m4_f85daa49ffefcd782bb1f89b",
        _RUNTIME_GLOBALS_V5,
        True,
    ),
}


def _tsp_start_edge(edge: str) -> str:
    """Encode the validated START edge for the narrow TSP runtime."""

    return {"falling": "0", "rising": "1", "either": "2"}[edge]


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
class CurrentHoldAcquisitionProof:
    """Finite 1 NPLC resistor acquisition for narrow M5 commissioning."""

    current_a: float
    source_range_a: float
    voltage_limit_v: float
    measurement_count: int
    start_mode: StartMode = StartMode.IMMEDIATE

    def __post_init__(self) -> None:
        for name in ("current_a", "source_range_a", "voltage_limit_v"):
            object.__setattr__(self, name, number(getattr(self, name), name))
        if abs(self.current_a) > _MAX_CURRENT_A:
            raise ValidationError("M5 proof current is limited to 10 mA")
        if not abs(self.current_a) <= self.source_range_a <= _MAX_CURRENT_A:
            raise ValidationError("M5 proof source range must cover the level and not exceed 10 mA")
        if not 0.01 <= self.voltage_limit_v <= _MAX_VOLTAGE_LIMIT_V:
            raise ValidationError("M5 proof voltage limit must be in 0.01..2 V")
        integer(self.measurement_count, "measurement_count")
        if self.measurement_count > _MAX_MEASUREMENT_COUNT:
            raise ValidationError(
                f"M5 measurement_count must not exceed {_MAX_MEASUREMENT_COUNT:,}"
            )
        if not isinstance(self.start_mode, StartMode):
            raise ValidationError("start_mode must be a StartMode")


@dataclass(frozen=True, kw_only=True)
class BufferedReading:
    """Raw target buffer fields; timestamp remains relative to the first reading."""

    sample_index: int
    source_current_a: float
    measured_voltage_v: float
    buffer_relative_time_s: float
    source_status: int
    measurement_status: int

    def __post_init__(self) -> None:
        integer(self.sample_index, "sample_index", minimum=0)
        for name in ("source_current_a", "measured_voltage_v", "buffer_relative_time_s"):
            value = number(getattr(self, name), name)
            if name == "buffer_relative_time_s" and value < 0:
                raise ValidationError("buffer_relative_time_s must be nonnegative")
            object.__setattr__(self, name, value)
        integer(self.source_status, "source_status", minimum=0)
        integer(self.measurement_status, "measurement_status", minimum=0)


@dataclass(frozen=True, kw_only=True)
class RuntimeBufferInfo:
    record_count: int
    start_index: int | None
    end_index: int | None
    capacity_records: int

    def __post_init__(self) -> None:
        integer(self.record_count, "record_count", minimum=0)
        integer(self.capacity_records, "capacity_records", minimum=0)
        if self.record_count == 0:
            if self.start_index is not None or self.end_index is not None:
                raise ValidationError("Empty runtime buffer cannot have an index extent")
        else:
            if self.start_index is None or self.end_index is None:
                raise ValidationError("Nonempty runtime buffer requires an index extent")
            integer(self.start_index, "start_index")
            integer(self.end_index, "end_index")
            if self.end_index - self.start_index + 1 != self.record_count:
                raise ValidationError("Runtime buffer extent does not match its record count")
        if self.record_count > self.capacity_records:
            raise ValidationError("Runtime buffer record count exceeds capacity")


@dataclass(frozen=True, kw_only=True)
class RuntimeRecordChunk:
    offset: int
    total_records: int
    records: tuple[BufferedReading, ...]
    records_sha256: str
    schema: str = K2460_BUFFER_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != K2460_BUFFER_SCHEMA:
            raise ValidationError("Unsupported K2460 proof-buffer schema")
        integer(self.offset, "offset", minimum=0)
        integer(self.total_records, "total_records", minimum=0)
        if not isinstance(self.records, tuple) or not all(
            isinstance(record, BufferedReading) for record in self.records
        ):
            raise ValidationError("records must be a tuple of BufferedReading values")
        if self.offset + len(self.records) > self.total_records:
            raise ValidationError("Runtime record chunk exceeds its declared extent")
        for record_offset, record in enumerate(self.records):
            if record.sample_index != self.offset + record_offset:
                raise ValidationError("Runtime record chunk indices do not match its offset")
        if self.records_sha256 != _buffered_records_sha256(self.records):
            raise ValidationError("Runtime record chunk checksum does not match its records")

    @property
    def next_offset(self) -> int:
        return self.offset + len(self.records)

    @property
    def final(self) -> bool:
        return self.next_offset == self.total_records


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


def _parse_nonnegative_integer(value: str, label: str) -> int:
    parsed = number(float(value), label)
    if parsed < 0 or parsed != int(parsed):
        raise TransportProtocolError(f"M5 {label} must be a nonnegative integer")
    return int(parsed)


def parse_runtime_buffer_info(response: str) -> RuntimeBufferInfo:
    fields = response.split("\t")
    if len(fields) != 4:
        raise TransportProtocolError(
            f"M5 buffer status must contain 4 tab-separated fields, received {len(fields)}"
        )
    try:
        count, start, end, capacity = (
            _parse_nonnegative_integer(value, label)
            for value, label in zip(
                fields,
                ("record count", "start index", "end index", "capacity"),
                strict=True,
            )
        )
        return RuntimeBufferInfo(
            record_count=count,
            start_index=None if count == 0 else start,
            end_index=None if count == 0 else end,
            capacity_records=capacity,
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise TransportProtocolError("M5 buffer status contains invalid fields") from exc


def parse_buffered_readings(
    response: str, *, offset: int, expected_count: int
) -> tuple[BufferedReading, ...]:
    integer(offset, "offset", minimum=0)
    integer(expected_count, "expected_count", minimum=0)
    fields = tuple(field.strip() for field in response.split(","))
    if len(fields) != expected_count * 5 or any(not field for field in fields):
        raise TransportProtocolError(
            f"M5 buffer reply must contain {expected_count * 5} nonempty comma-separated fields"
        )
    result = []
    try:
        for record_offset in range(expected_count):
            base = record_offset * 5
            result.append(
                BufferedReading(
                    sample_index=offset + record_offset,
                    source_current_a=number(float(fields[base]), "source current"),
                    measured_voltage_v=number(float(fields[base + 1]), "measured voltage"),
                    buffer_relative_time_s=number(float(fields[base + 2]), "buffer relative time"),
                    source_status=_parse_nonnegative_integer(fields[base + 3], "source status"),
                    measurement_status=_parse_nonnegative_integer(
                        fields[base + 4], "measurement status"
                    ),
                )
            )
    except (TypeError, ValueError, ValidationError) as exc:
        raise TransportProtocolError("M5 buffer reply contains invalid record fields") from exc
    return tuple(result)


def _buffered_records_sha256(records: tuple[BufferedReading, ...]) -> str:
    return sha256_json(canonical_json({"schema": K2460_BUFFER_SCHEMA, "records": records}))


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

    def replace_known_v4_and_install(self) -> RuntimeArtifact:
        """Compatibility entry point restricted to the exact commissioned v4 artifact."""

        return self._replace_known_runtime_and_install(
            {"m4-finite-current-hold-v4": _REPLACEABLE_RUNTIMES["m4-finite-current-hold-v4"]}
        )

    def replace_known_runtime_and_install(self) -> RuntimeArtifact:
        """Replace one exact allowlisted prior artifact while safely idle."""

        return self._replace_known_runtime_and_install(_REPLACEABLE_RUNTIMES)

    def _replace_known_runtime_and_install(
        self, allowed: dict[str, tuple[str, tuple[str, ...], bool]]
    ) -> RuntimeArtifact:
        """Delete only a recognized digest/build and its known owned globals.

        This is deliberately not a general script-deletion API. If any identity,
        state, output, level, or script-name check differs, no deletion occurs.
        """

        if self.transport.command_language != "TSP":
            raise IncompatibleInstrumentError("Runtime replacement requires TSP mode")
        status = self.status()
        if status.state != RuntimeState.IDLE or status.output_enabled:
            raise IncompatibleInstrumentError(
                "Known runtime replacement requires output-OFF IDLE runtime state"
            )
        source_level = self._query_source_level()
        if source_level != 0:
            raise IncompatibleInstrumentError(
                "Known runtime replacement requires a zero programmed source level"
            )
        loaded_abi = self.transport.query("print(oe_m4_runtime_abi)")
        loaded_build = self.transport.query("print(oe_m4_runtime_build)")
        if loaded_abi != RUNTIME_ABI or loaded_build not in allowed:
            raise IncompatibleInstrumentError(
                "Loaded runtime is not an exact allowlisted ABI/build"
            )
        script_name, owned_globals, has_m5_buffer = allowed[loaded_build]
        script_absent = self.transport.query(f"print({script_name} == nil)")
        if script_absent != "false":
            raise IncompatibleInstrumentError(
                "Exact allowlisted digest-named script is not present"
            )

        if has_m5_buffer:
            buffer_absent = self.transport.query("print(oe_m5_buffer == nil)")
            if buffer_absent not in ("true", "false"):
                raise TransportProtocolError("Could not determine prior runtime buffer presence")
            if buffer_absent == "false":
                info = self.buffer_info()
                if info.record_count != 0:
                    raise IncompatibleInstrumentError(
                        "Prior runtime buffer contains retained records; archive/discard first"
                    )
                self.transport.write("buffer.delete(oe_m5_buffer)")
                self.transport.write("oe_m5_buffer = nil")
                self.transport.write("collectgarbage()")

        self.transport.write(f'script.delete("{script_name}")')
        if self.transport.query(f"print({script_name} == nil)") != "true":
            raise IncompatibleInstrumentError("Known runtime script deletion was not confirmed")
        for name in owned_globals:
            self.transport.write(f"{name} = nil")
        globals_absent = self.transport.query(
            "print(oe_m4_runtime_abi == nil and oe_m4_initialize == nil)"
        )
        if globals_absent != "true":
            raise IncompatibleInstrumentError("Known runtime globals were not cleared")
        if parse_source_output_enabled(self.transport.query("print(smu.source.output)")):
            raise IncompatibleInstrumentError("Output changed during known runtime replacement")
        if self._query_source_level() != 0:
            raise IncompatibleInstrumentError(
                "Source level changed during known runtime replacement"
            )
        return self.install()

    def prepare_current_hold(self, hold: CurrentHoldProof) -> RuntimeStatus:
        if not isinstance(hold, CurrentHoldProof):
            raise ValidationError("hold must be CurrentHoldProof")
        self._validate_against_config(hold)
        self._validate_runtime_config()
        io = self.config.io
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
                    _tsp_start_edge(io.start_edge),
                )
            )
            + ")"
        )
        self.transport.write(command)
        status = self.status()
        if status.state != RuntimeState.PREPARED or status.output_enabled:
            raise TransportProtocolError("M4 runtime did not reach output-OFF PREPARED")
        return status

    def prepare_current_hold_acquisition(
        self, acquisition: CurrentHoldAcquisitionProof
    ) -> RuntimeStatus:
        if not isinstance(acquisition, CurrentHoldAcquisitionProof):
            raise ValidationError("acquisition must be CurrentHoldAcquisitionProof")
        self._validate_against_config(acquisition)
        self._validate_runtime_config()
        if acquisition.measurement_count > self.config.buffer.capacity_ceiling_records:
            raise ValidationError(
                "Measurement count exceeds the configured buffer capacity ceiling"
            )
        io = self.config.io
        command = (
            "oe_m5_prepare_current_hold_acquisition("
            + ",".join(
                (
                    _tsp_number(acquisition.current_a),
                    _tsp_number(acquisition.source_range_a),
                    _tsp_number(acquisition.voltage_limit_v),
                    str(acquisition.measurement_count),
                    "1" if acquisition.start_mode == StartMode.EXTERNAL_TRIGGER else "0",
                    _tsp_number(self.config.timing.external_start_timeout_s),
                    _tsp_number(self.config.timing.poll_period_s),
                    "1" if self.config.source_terminal == "front" else "0",
                    "1" if self.config.sense == "remote" else "0",
                    str(io.ready),
                    str(io.busy),
                    str(io.start),
                    str(io.ready_asserted_level),
                    str(io.busy_asserted_level),
                    _tsp_start_edge(io.start_edge),
                )
            )
            + ")"
        )
        self.transport.write(command)
        status = self.status()
        if status.state != RuntimeState.PREPARED or status.output_enabled:
            raise TransportProtocolError("M5 runtime did not reach output-OFF PREPARED")
        info = self.buffer_info()
        if info.record_count != 0 or info.capacity_records < acquisition.measurement_count:
            raise TransportProtocolError(
                "M5 runtime buffer is not empty or is smaller than the requested count"
            )
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

    def buffer_info(self) -> RuntimeBufferInfo:
        return parse_runtime_buffer_info(self.transport.query("print(oe_m5_buffer_status())"))

    def read_buffer_chunk(self, *, offset: int, max_records: int) -> RuntimeRecordChunk:
        integer(offset, "offset", minimum=0)
        integer(max_records, "max_records")
        if max_records > self.config.buffer.transfer_chunk_records:
            raise ValidationError("max_records exceeds the configured transfer chunk ceiling")
        if max_records > _MAX_TRANSFER_CHUNK_RECORDS:
            raise ValidationError(f"M5 max_records must not exceed {_MAX_TRANSFER_CHUNK_RECORDS:,}")
        status = self.status()
        if status.state not in (RuntimeState.COMPLETE, RuntimeState.ABORTED, RuntimeState.ERROR):
            raise TransportProtocolError("M5 buffer retrieval requires a terminal runtime state")
        if status.output_enabled:
            raise TransportProtocolError("M5 buffer retrieval requires confirmed output OFF")
        info = self.buffer_info()
        if offset > info.record_count:
            raise ValidationError("M5 chunk offset exceeds retained record count")
        count = min(max_records, info.record_count - offset)
        if count:
            assert info.start_index is not None
            first = info.start_index + offset
            response = self.transport.query(f"oe_m5_print_buffer({first},{first + count - 1})")
            records = parse_buffered_readings(response, offset=offset, expected_count=count)
        else:
            records = ()
        return RuntimeRecordChunk(
            offset=offset,
            total_records=info.record_count,
            records=records,
            records_sha256=_buffered_records_sha256(records),
        )

    def discard_buffer(self, *, reason: str) -> RuntimeBufferInfo:
        text(reason, "discard reason")
        status = self.status()
        if status.output_enabled or status.state in (
            RuntimeState.PREPARED,
            RuntimeState.WAITING_START,
            RuntimeState.RUNNING,
        ):
            raise TransportProtocolError("M5 discard requires a stopped runtime with output OFF")
        self.transport.write("oe_m5_discard_buffer()")
        info = self.buffer_info()
        if info.record_count != 0:
            raise TransportProtocolError("M5 discard did not empty the runtime buffer")
        return info

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

    def _validate_runtime_config(self) -> None:
        if self.config.io.external_abort_enabled:
            raise ValidationError("External ABORT remains disabled for the proof runtime")
        if self.config.safety.source_off_mode.lower() != "normal":
            raise ValidationError("Proof runtime currently supports only NORMAL output-off mode")
        if not _MIN_EVENT_POLL_S <= self.config.timing.poll_period_s <= _MAX_EVENT_POLL_S:
            raise ValidationError("Event poll period must be in 0.001..0.1 s")

    def _query_source_level(self) -> float:
        response = self.transport.query("print(smu.source.level)")
        try:
            return number(float(response), "source level")
        except (TypeError, ValueError, ValidationError) as exc:
            raise TransportProtocolError("Source level reply is not a finite number") from exc

    def _validate_against_config(
        self, hold: CurrentHoldProof | CurrentHoldAcquisitionProof
    ) -> None:
        safety = self.config.safety
        allowed_current = (
            safety.charge_current_max_a if hold.current_a >= 0 else safety.discharge_current_max_a
        )
        if abs(hold.current_a) > allowed_current:
            raise ValidationError("Proof current exceeds configured directional safety limit")
        voltage_envelope = max(abs(safety.voltage_min_v), abs(safety.voltage_max_v))
        if hold.voltage_limit_v > voltage_envelope:
            raise ValidationError("Proof voltage limit exceeds configured voltage envelope")
        if abs(hold.current_a) * hold.voltage_limit_v > safety.power_abs_max_w:
            raise ValidationError("Proof worst-case power exceeds configured safety limit")
        positive(self.config.timing.external_start_timeout_s, "external_start_timeout_s")
