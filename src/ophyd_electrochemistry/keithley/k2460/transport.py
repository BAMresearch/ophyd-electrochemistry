"""Serialized, bounded PyVISA transport for a Keithley 2460.

Importing this module does not import PyVISA. The optional dependency is loaded
only when the default resource-manager factory is used, which keeps the pure
model/simulator installation usable without the ``visa`` extra.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from importlib import import_module
from threading import RLock
from time import sleep
from typing import Literal, Protocol, cast

from ...exceptions import (
    AmbiguousTransportError,
    IncompatibleInstrumentError,
    ShutdownUnconfirmed,
    TransportConnectionError,
    TransportError,
    TransportProtocolError,
    TransportResponseTooLarge,
    TransportTimeoutError,
    UnsupportedCapabilityError,
    ValidationError,
)
from ...validation import integer, number, text

CommandLanguage = Literal["SCPI", "TSP"]

_VISA_TIMEOUT_ERROR = -1073807339
_MAX_CONFIGURED_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_CONFIGURED_COMMAND_BYTES = 1024 * 1024
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")


class _MessageResource(Protocol):
    timeout: int
    read_termination: str | None
    write_termination: str | None
    chunk_size: int

    def write(self, message: str) -> int: ...

    def read_bytes(
        self,
        count: int,
        chunk_size: int | None = None,
        break_on_termchar: bool = False,
    ) -> bytes: ...

    def close(self) -> None: ...


class _ResourceManager(Protocol):
    visalib: object

    def open_resource(self, resource_name: str, **kwargs: object) -> _MessageResource: ...

    def close(self) -> None: ...


ResourceManagerFactory = Callable[[str], _ResourceManager]
Sleeper = Callable[[float], None]


@dataclass(frozen=True, kw_only=True)
class VisaTransportConfig:
    """Explicit finite limits and framing for one VISA session."""

    resource_name: str
    backend: str
    open_timeout_ms: int = 3_000
    io_timeout_ms: int = 3_000
    read_termination: str = "\n"
    write_termination: str = "\n"
    chunk_size_bytes: int = 4_096
    max_response_bytes: int = 65_536
    max_command_bytes: int = 16_384
    script_line_delay_s: float = 0.01
    expected_model: str = "2460"
    required_language: CommandLanguage | None = None

    def __post_init__(self) -> None:
        for name in ("resource_name", "backend", "expected_model"):
            text(getattr(self, name), name)
        for name in (
            "open_timeout_ms",
            "io_timeout_ms",
            "chunk_size_bytes",
            "max_response_bytes",
            "max_command_bytes",
        ):
            integer(getattr(self, name), name)
        if self.max_response_bytes > _MAX_CONFIGURED_RESPONSE_BYTES:
            raise ValidationError(
                f"max_response_bytes must not exceed {_MAX_CONFIGURED_RESPONSE_BYTES}"
            )
        if self.max_command_bytes > _MAX_CONFIGURED_COMMAND_BYTES:
            raise ValidationError(
                f"max_command_bytes must not exceed {_MAX_CONFIGURED_COMMAND_BYTES}"
            )
        if self.chunk_size_bytes > self.max_response_bytes + 1:
            raise ValidationError("chunk_size_bytes must not exceed max_response_bytes + 1")
        object.__setattr__(
            self,
            "script_line_delay_s",
            number(self.script_line_delay_s, "script_line_delay_s"),
        )
        if not 0 <= self.script_line_delay_s <= 0.1:
            raise ValidationError("script_line_delay_s must be in 0..0.1 s")
        for name in ("read_termination", "write_termination"):
            value = getattr(self, name)
            if value not in ("\n", "\r\n"):
                raise ValidationError(f"{name} must be LF or CRLF")
        if self.required_language not in (None, "SCPI", "TSP"):
            raise ValidationError("required_language must be SCPI, TSP, or None")


@dataclass(frozen=True)
class InstrumentIdentity:
    """Strictly parsed ``*IDN?`` response from the commissioned unit."""

    manufacturer: str
    model: str
    serial_number: str
    firmware_version: str
    raw: str


class Keithley2460Transport(Protocol):
    def connect(self) -> None:
        """Inspect identity/mode; never reset or energize implicitly."""
        ...

    def write(self, command: str) -> None: ...

    def query(self, command: str) -> str:
        """Hold the lock across write and the complete bounded response."""
        ...

    def load_runtime(self, source: str, *, abi: str, sha256: str) -> None:
        """Load a reviewed packaged runtime with output OFF; verify ABI/digest."""
        ...

    def close(self) -> None:
        """Release the session after the operational device confirms shutdown."""
        ...


def _default_resource_manager_factory(backend: str) -> _ResourceManager:
    try:
        pyvisa = import_module("pyvisa")
    except ModuleNotFoundError as exc:
        raise TransportConnectionError(
            "PyVISA is not installed; sync the project with the 'visa' extra"
        ) from exc
    constructor = pyvisa.ResourceManager
    return cast(_ResourceManager, constructor(backend))


def parse_identity(response: str, *, expected_model: str = "2460") -> InstrumentIdentity:
    """Parse and validate the documented four-field ``*IDN?`` reply."""

    if response != response.strip() or any(ord(character) < 32 for character in response):
        raise IncompatibleInstrumentError("*IDN? response contains unexpected whitespace/control")
    fields = response.split(",")
    if len(fields) != 4:
        raise IncompatibleInstrumentError("*IDN? response must contain exactly four fields")
    manufacturer, model_field, serial_number, firmware_version = fields
    if manufacturer != "KEITHLEY INSTRUMENTS":
        raise IncompatibleInstrumentError(f"Unsupported manufacturer {manufacturer!r}")
    model_prefix = "MODEL "
    if not model_field.startswith(model_prefix):
        raise IncompatibleInstrumentError("*IDN? model field does not start with 'MODEL '")
    model = model_field.removeprefix(model_prefix)
    if model != expected_model:
        raise IncompatibleInstrumentError(
            f"Expected Keithley model {expected_model!r}, received {model!r}"
        )
    for name, value in (("serial number", serial_number), ("firmware version", firmware_version)):
        if _TOKEN_PATTERN.fullmatch(value) is None:
            raise IncompatibleInstrumentError(f"Invalid {name} in *IDN? response")
    return InstrumentIdentity(
        manufacturer=manufacturer,
        model=model,
        serial_number=serial_number,
        firmware_version=firmware_version,
        raw=response,
    )


def parse_command_language(response: str) -> CommandLanguage:
    """Reject unknown or ambiguously framed ``*LANG?`` replies."""

    if response not in ("SCPI", "TSP"):
        raise IncompatibleInstrumentError(f"Unsupported command language response {response!r}")
    return cast(CommandLanguage, response)


def parse_source_output_enabled(response: str) -> bool:
    """Parse the documented numeric or TSP-enum source-output reply."""

    if response in ("0", "OFF", "smu.OFF"):
        return False
    if response in ("1", "ON", "smu.ON"):
        return True
    raise TransportProtocolError(f"Unsupported source-output state response {response!r}")


class PyVisaKeithley2460Transport:
    """One-owner VISA session with whole-transaction locking and hard bounds.

    No operation is retried. Any I/O failure invalidates and closes the session
    so a partial or stale reply cannot be consumed by a later transaction.
    """

    def __init__(
        self,
        config: VisaTransportConfig,
        *,
        resource_manager_factory: ResourceManagerFactory | None = None,
        sleeper: Sleeper = sleep,
    ) -> None:
        if not isinstance(config, VisaTransportConfig):
            raise ValidationError("config must be VisaTransportConfig")
        self.config = config
        self._factory = resource_manager_factory or _default_resource_manager_factory
        self._sleeper = sleeper
        self._lock = RLock()
        self._manager: _ResourceManager | None = None
        self._resource: _MessageResource | None = None
        self._identity: InstrumentIdentity | None = None
        self._command_language: CommandLanguage | None = None
        self._backend_description: str | None = None

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._resource is not None

    @property
    def identity(self) -> InstrumentIdentity:
        with self._lock:
            if self._identity is None or self._resource is None:
                raise TransportConnectionError(
                    "Instrument identity is unavailable while disconnected"
                )
            return self._identity

    @property
    def command_language(self) -> CommandLanguage:
        with self._lock:
            if self._command_language is None or self._resource is None:
                raise TransportConnectionError("Command language is unavailable while disconnected")
            return self._command_language

    @property
    def backend_description(self) -> str:
        with self._lock:
            if self._backend_description is None or self._resource is None:
                raise TransportConnectionError("VISA backend is unavailable while disconnected")
            return self._backend_description

    def connect(self) -> None:
        with self._lock:
            if self._resource is not None:
                return
            self._identity = None
            self._command_language = None
            self._backend_description = None
            manager: _ResourceManager | None = None
            try:
                manager = self._factory(self.config.backend)
                resource = manager.open_resource(
                    self.config.resource_name,
                    open_timeout=self.config.open_timeout_ms,
                    timeout=self.config.io_timeout_ms,
                    read_termination=self.config.read_termination,
                    write_termination=self.config.write_termination,
                    chunk_size=self.config.chunk_size_bytes,
                )
            except Exception as exc:
                if manager is not None:
                    with suppress(Exception):
                        manager.close()
                if isinstance(exc, TransportError):
                    raise
                if _is_timeout(exc):
                    raise TransportTimeoutError("Timed out opening VISA resource") from exc
                raise TransportConnectionError(
                    f"Could not open VISA resource {self.config.resource_name!r}"
                ) from exc

            self._manager = manager
            self._resource = resource
            self._backend_description = str(manager.visalib)
            try:
                identity_response = self._query_locked("*IDN?")
                language_response = self._query_locked("*LANG?")
                identity = parse_identity(
                    identity_response, expected_model=self.config.expected_model
                )
                language = parse_command_language(language_response)
                if (
                    self.config.required_language is not None
                    and language != self.config.required_language
                ):
                    raise IncompatibleInstrumentError(
                        "Required command language "
                        f"{self.config.required_language}, received {language}"
                    )
            except Exception:
                self._invalidate_locked()
                raise
            self._identity = identity
            self._command_language = language

    def write(self, command: str) -> None:
        """Send one mutation once; an uncertain delivery is never replayed."""

        with self._lock:
            self._write_locked(command)

    def _write_locked(self, command: str, *, allow_empty: bool = False) -> None:
        resource = self._require_resource_locked()
        message_bytes = b"" if allow_empty and command == "" else self._validate_command(command)
        expected_bytes = len(message_bytes) + len(self.config.write_termination.encode("ascii"))
        try:
            written = resource.write(command)
        except Exception as exc:
            self._invalidate_locked()
            raise AmbiguousTransportError(
                "Mutating command delivery is uncertain; inspect instrument state before recovery"
            ) from exc
        if written < expected_bytes:
            self._invalidate_locked()
            raise AmbiguousTransportError(
                "Mutating command reported a partial write; do not retry automatically"
            )

    def query(self, command: str) -> str:
        with self._lock:
            return self._query_locked(command)

    def _query_locked(self, command: str) -> str:
        resource = self._require_resource_locked()
        message_bytes = self._validate_command(command)
        expected_bytes = len(message_bytes) + len(self.config.write_termination.encode("ascii"))
        try:
            written = resource.write(command)
            if written < expected_bytes:
                raise TransportProtocolError("Query command reported a partial write")
            response = resource.read_bytes(
                self.config.max_response_bytes + 1,
                chunk_size=self.config.chunk_size_bytes,
                break_on_termchar=True,
            )
        except Exception as exc:
            self._invalidate_locked()
            if isinstance(exc, TransportError):
                raise
            if _is_timeout(exc):
                raise TransportTimeoutError(
                    f"Timed out during query {command!r}; session invalidated"
                ) from exc
            raise TransportError(f"VISA query {command!r} failed; session invalidated") from exc

        if len(response) > self.config.max_response_bytes:
            self._invalidate_locked()
            raise TransportResponseTooLarge(
                f"Reply exceeded {self.config.max_response_bytes} bytes; session invalidated"
            )
        termination = self.config.read_termination.encode("ascii")
        if not response.endswith(termination):
            self._invalidate_locked()
            raise TransportProtocolError("Reply did not end with the configured termination")
        payload = response[: -len(termination)]
        try:
            decoded = payload.decode("ascii")
        except UnicodeDecodeError as exc:
            self._invalidate_locked()
            raise TransportProtocolError("Reply is not strict ASCII") from exc
        if not decoded:
            self._invalidate_locked()
            raise TransportProtocolError("Instrument returned an empty reply")
        return decoded

    def load_runtime(self, source: str, *, abi: str, sha256: str) -> None:
        """Load only the exact packaged M4 runtime while output is confirmed OFF.

        The script name contains a source-digest prefix. A failed or partial
        upload invalidates the session and is never retried automatically.
        """

        from .runtime import packaged_runtime

        artifact = packaged_runtime()
        if (source, abi, sha256) != (artifact.source, artifact.abi, artifact.sha256):
            raise UnsupportedCapabilityError(
                "Only the exact packaged, reviewed M4 runtime may be loaded"
            )
        with self._lock:
            self._require_resource_locked()
            if self._command_language != "TSP":
                raise IncompatibleInstrumentError("Runtime upload requires TSP command language")
            output = self._query_locked("print(smu.source.output)")
            if parse_source_output_enabled(output):
                raise ShutdownUnconfirmed(
                    f"Runtime upload requires confirmed output OFF; received {output!r}"
                )
            absent = self._query_locked(f"print({artifact.script_name} == nil)")
            if absent not in ("true", "false"):
                raise TransportProtocolError(
                    f"Could not determine whether runtime exists: {absent!r}"
                )
            if absent == "true":
                globals_absent = self._query_locked(
                    "print(oe_m4_runtime_abi == nil and oe_m4_initialize == nil)"
                )
                if globals_absent != "true":
                    raise IncompatibleInstrumentError(
                        "M4 runtime globals already exist without the expected digest-named "
                        "script; reboot before installation"
                    )
                self._write_locked(f"loadscript {artifact.script_name}")
                for line in source.splitlines():
                    self._write_locked(line, allow_empty=True)
                    self._sleeper(self.config.script_line_delay_s)
                self._write_locked("endscript")
                compiled_absent = self._query_locked(f"print({artifact.script_name} == nil)")
                if compiled_absent != "false":
                    raise IncompatibleInstrumentError(
                        "Runtime did not compile into the expected digest-named script"
                    )
                self._write_locked(f"{artifact.script_name}.run()")
            loaded_abi = self._query_locked("print(oe_m4_runtime_abi)")
            loaded_build = self._query_locked("print(oe_m4_runtime_build)")
            initializer_type = self._query_locked("print(type(oe_m4_initialize))")
            if loaded_abi != artifact.abi or loaded_build != artifact.build:
                raise IncompatibleInstrumentError(
                    "Loaded runtime ABI/build does not match the packaged artifact"
                )
            if initializer_type != "function":
                raise IncompatibleInstrumentError("Loaded runtime initializer is unavailable")
            self._write_locked("oe_m4_initialize()")
            final_output = self._query_locked("print(smu.source.output)")
            if parse_source_output_enabled(final_output):
                raise ShutdownUnconfirmed("Runtime initialization did not leave output OFF")

    def close(self) -> None:
        with self._lock:
            resource, manager = self._resource, self._manager
            self._resource = None
            self._manager = None
            self._identity = None
            self._command_language = None
            self._backend_description = None
            error: Exception | None = None
            if resource is not None:
                try:
                    resource.close()
                except Exception as exc:
                    error = exc
            if manager is not None:
                try:
                    manager.close()
                except Exception as exc:
                    error = error or exc
            if error is not None:
                raise TransportError("Failed to close VISA session cleanly") from error

    def __enter__(self) -> PyVisaKeithley2460Transport:
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _require_resource_locked(self) -> _MessageResource:
        if self._resource is None:
            raise TransportConnectionError("VISA transport is not connected")
        return self._resource

    def _validate_command(self, command: str) -> bytes:
        if not isinstance(command, str) or not command:
            raise TransportProtocolError("Instrument command must be a nonempty string")
        if "\r" in command or "\n" in command:
            raise TransportProtocolError("Instrument command must contain exactly one framed line")
        try:
            encoded = command.encode("ascii")
        except UnicodeEncodeError as exc:
            raise TransportProtocolError("Instrument command must be strict ASCII") from exc
        if len(encoded) > self.config.max_command_bytes:
            raise TransportProtocolError(
                f"Instrument command exceeds {self.config.max_command_bytes} bytes"
            )
        return encoded

    def _invalidate_locked(self) -> None:
        resource, manager = self._resource, self._manager
        self._resource = None
        self._manager = None
        self._identity = None
        self._command_language = None
        self._backend_description = None
        if resource is not None:
            with suppress(Exception):
                resource.close()
        if manager is not None:
            with suppress(Exception):
                manager.close()


def _is_timeout(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    error_code = getattr(exc, "error_code", None)
    return bool(error_code == _VISA_TIMEOUT_ERROR)
