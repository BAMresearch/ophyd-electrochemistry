"""Mocked M3 framing, serialization, identity, and failure tests."""

from __future__ import annotations

import threading
import time
from collections import deque

import pytest

from ophyd_electrochemistry.exceptions import (
    AmbiguousTransportError,
    IncompatibleInstrumentError,
    ShutdownUnconfirmed,
    TransportConnectionError,
    TransportProtocolError,
    TransportResponseTooLarge,
    TransportTimeoutError,
    UnsupportedCapabilityError,
    ValidationError,
)
from ophyd_electrochemistry.keithley.k2460.commissioning import run_read_only_diagnostic
from ophyd_electrochemistry.keithley.k2460.runtime import packaged_runtime
from ophyd_electrochemistry.keithley.k2460.transport import (
    PyVisaKeithley2460Transport,
    VisaTransportConfig,
    parse_command_language,
    parse_identity,
)

IDENTITY = b"KEITHLEY INSTRUMENTS,MODEL 2460,04686198,1.7.16a\n"


class FakeVisaError(Exception):
    def __init__(self, message: str, error_code: int) -> None:
        super().__init__(message)
        self.error_code = error_code


class FakeResource:
    def __init__(self, *responses: bytes | Exception, read_delay_s: float = 0.0) -> None:
        self.responses = deque(responses or (IDENTITY, b"SCPI\n"))
        self.read_delay_s = read_delay_s
        self.writes: list[str] = []
        self.read_calls: list[tuple[int, int | None, bool]] = []
        self.closed = False
        self.write_error: Exception | None = None
        self.short_write = False
        self.timeout = 0
        self.read_termination: str | None = None
        self.write_termination: str | None = None
        self.chunk_size = 0
        self._activity_lock = threading.Lock()
        self._active_transactions = 0
        self.max_active_transactions = 0

    def write(self, message: str) -> int:
        with self._activity_lock:
            self._active_transactions += 1
            self.max_active_transactions = max(
                self.max_active_transactions, self._active_transactions
            )
        self.writes.append(message)
        if self.write_error is not None:
            error = self.write_error
            self.write_error = None
            with self._activity_lock:
                self._active_transactions -= 1
            raise error
        count = len(message.encode("ascii")) + len((self.write_termination or "").encode("ascii"))
        return count - 1 if self.short_write else count

    def read_bytes(
        self,
        count: int,
        chunk_size: int | None = None,
        break_on_termchar: bool = False,
    ) -> bytes:
        self.read_calls.append((count, chunk_size, break_on_termchar))
        if self.read_delay_s:
            time.sleep(self.read_delay_s)
        response = self.responses.popleft()
        with self._activity_lock:
            self._active_transactions -= 1
        if isinstance(response, Exception):
            raise response
        return response[:count]

    def close(self) -> None:
        self.closed = True


class FakeManager:
    def __init__(self, resource: FakeResource, *, open_error: Exception | None = None) -> None:
        self.resource = resource
        self.open_error = open_error
        self.open_calls: list[tuple[str, dict[str, object]]] = []
        self.closed = False
        self.visalib = "fake-visa-library"

    def open_resource(self, resource_name: str, **kwargs: object) -> FakeResource:
        self.open_calls.append((resource_name, kwargs))
        if self.open_error is not None:
            raise self.open_error
        for name in ("timeout", "read_termination", "write_termination", "chunk_size"):
            setattr(self.resource, name, kwargs[name])
        return self.resource

    def close(self) -> None:
        self.closed = True


class FakeFactory:
    def __init__(self, manager: FakeManager) -> None:
        self.manager = manager
        self.backends: list[str] = []

    def __call__(self, backend: str) -> FakeManager:
        self.backends.append(backend)
        return self.manager


def config(**changes: object) -> VisaTransportConfig:
    values: dict[str, object] = {
        "resource_name": "TCPIP0::192.0.2.1::5025::SOCKET",
        "backend": "@py",
        "script_line_delay_s": 0,
    }
    values.update(changes)
    return VisaTransportConfig(**values)  # type: ignore[arg-type]


def transport_with(
    resource: FakeResource, **config_changes: object
) -> tuple[PyVisaKeithley2460Transport, FakeManager, FakeFactory]:
    manager = FakeManager(resource)
    factory = FakeFactory(manager)
    transport = PyVisaKeithley2460Transport(
        config(**config_changes), resource_manager_factory=factory
    )
    return transport, manager, factory


def test_connect_uses_explicit_backend_limits_framing_and_validates_identity():
    resource = FakeResource(IDENTITY, b"SCPI\n")
    transport, manager, factory = transport_with(resource)

    transport.connect()

    assert factory.backends == ["@py"]
    assert manager.open_calls == [
        (
            "TCPIP0::192.0.2.1::5025::SOCKET",
            {
                "open_timeout": 3_000,
                "timeout": 3_000,
                "read_termination": "\n",
                "write_termination": "\n",
                "chunk_size": 4_096,
            },
        )
    ]
    assert resource.writes == ["*IDN?", "*LANG?"]
    assert resource.read_calls == [(65_537, 4_096, True), (65_537, 4_096, True)]
    assert transport.identity.serial_number == "04686198"
    assert transport.identity.firmware_version == "1.7.16a"
    assert transport.command_language == "SCPI"
    assert transport.backend_description == "fake-visa-library"
    transport.connect()
    assert len(manager.open_calls) == 1


@pytest.mark.parametrize(
    "response,match",
    [
        ("OTHER,MODEL 2460,1234,1.0", "manufacturer"),
        ("KEITHLEY INSTRUMENTS,MODEL 2450,1234,1.0", "Expected"),
        ("KEITHLEY INSTRUMENTS,2460,1234,1.0", "MODEL"),
        ("KEITHLEY INSTRUMENTS,MODEL 2460,1234", "four fields"),
        ("KEITHLEY INSTRUMENTS,MODEL 2460,bad serial,1.0", "serial"),
        (" KEITHLEY INSTRUMENTS,MODEL 2460,1234,1.0", "whitespace"),
    ],
)
def test_identity_parser_rejects_incompatible_or_ambiguous_responses(response, match):
    with pytest.raises(IncompatibleInstrumentError, match=match):
        parse_identity(response)


def test_command_language_is_exact_and_optional_required_mode_is_enforced():
    assert parse_command_language("SCPI") == "SCPI"
    assert parse_command_language("TSP") == "TSP"
    with pytest.raises(IncompatibleInstrumentError):
        parse_command_language("SCPI ")

    transport, _, _ = transport_with(FakeResource(IDENTITY, b"SCPI\n"), required_language="TSP")
    with pytest.raises(IncompatibleInstrumentError, match="Required command language TSP"):
        transport.connect()
    assert not transport.connected


@pytest.mark.parametrize(
    "changes",
    [
        {"backend": ""},
        {"open_timeout_ms": 0},
        {"read_termination": ""},
        {"write_termination": "\0"},
        {"max_response_bytes": 16 * 1024 * 1024 + 1},
        {"max_command_bytes": 1024 * 1024 + 1},
        {"max_response_bytes": 10, "chunk_size_bytes": 12},
        {"required_language": "legacy"},
    ],
)
def test_transport_config_rejects_unbounded_or_ambiguous_values(changes):
    with pytest.raises(ValidationError):
        config(**changes)


def test_timeout_invalidates_session_and_is_not_retried():
    timeout = FakeVisaError("timeout", -1073807339)
    resource = FakeResource(IDENTITY, b"SCPI\n", timeout)
    transport, manager, _ = transport_with(resource)
    transport.connect()

    with pytest.raises(TransportTimeoutError, match="session invalidated"):
        transport.query("*OPC?")

    assert resource.writes.count("*OPC?") == 1
    assert resource.closed and manager.closed and not transport.connected
    with pytest.raises(TransportConnectionError):
        transport.query("*OPC?")


@pytest.mark.parametrize(
    "response,error",
    [
        (b"unterminated", TransportProtocolError),
        (b"\xff\n", TransportProtocolError),
        (b"\n", TransportProtocolError),
        (b"x" * 65, TransportResponseTooLarge),
    ],
)
def test_malformed_partial_or_overlong_reply_invalidates_session(response, error):
    resource = FakeResource(IDENTITY, b"SCPI\n", response)
    transport, manager, _ = transport_with(resource, max_response_bytes=64, chunk_size_bytes=16)
    transport.connect()

    with pytest.raises(error):
        transport.query("*OPC?")

    assert resource.closed and manager.closed and not transport.connected


def test_mutating_write_failure_is_ambiguous_and_never_retried():
    resource = FakeResource(IDENTITY, b"SCPI\n")
    transport, manager, _ = transport_with(resource)
    transport.connect()
    resource.write_error = OSError("connection dropped after send")

    with pytest.raises(AmbiguousTransportError, match="inspect instrument state"):
        transport.write("source.output = source.ON")

    assert resource.writes.count("source.output = source.ON") == 1
    assert resource.closed and manager.closed and not transport.connected


def test_partial_mutating_write_is_ambiguous_and_never_retried():
    resource = FakeResource(IDENTITY, b"SCPI\n")
    transport, _, _ = transport_with(resource)
    transport.connect()
    resource.short_write = True

    with pytest.raises(AmbiguousTransportError, match="partial write"):
        transport.write("OUTPut ON")

    assert resource.writes.count("OUTPut ON") == 1


@pytest.mark.parametrize("command", ["", "*IDN?\n*LANG?", "snowman-☃"])
def test_invalid_command_is_rejected_before_any_write(command):
    resource = FakeResource(IDENTITY, b"SCPI\n")
    transport, _, _ = transport_with(resource)
    transport.connect()
    writes_before = tuple(resource.writes)

    with pytest.raises(TransportProtocolError):
        transport.query(command)

    assert tuple(resource.writes) == writes_before
    assert transport.connected


def test_whole_query_transaction_is_serialized_across_threads():
    resource = FakeResource(
        IDENTITY,
        b"SCPI\n",
        b"1\n",
        b"2\n",
        read_delay_s=0.02,
    )
    transport, _, _ = transport_with(resource)
    transport.connect()
    barrier = threading.Barrier(3)
    replies: list[str] = []

    def worker(command: str) -> None:
        barrier.wait()
        replies.append(transport.query(command))

    threads = [
        threading.Thread(target=worker, args=("FIRST?",)),
        threading.Thread(target=worker, args=("SECOND?",)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=1)

    assert all(not thread.is_alive() for thread in threads)
    assert sorted(replies) == ["1", "2"]
    assert resource.max_active_transactions == 1


def test_read_only_diagnostic_records_raw_evidence_and_closes():
    resource = FakeResource(IDENTITY, b"SCPI\n", b"0\n")
    manager = FakeManager(resource)
    report = run_read_only_diagnostic(config(), resource_manager_factory=FakeFactory(manager))

    assert report.reviewed_queries == ("*IDN?", "*LANG?", ":OUTPut:STATe?")
    assert report.identity_raw == IDENTITY.decode("ascii").removesuffix("\n")
    assert report.serial_number == "04686198"
    assert report.command_language_raw == report.command_language == "SCPI"
    assert report.output_state_raw == "0"
    assert report.output_enabled is False
    assert report.mutating_commands_sent is False
    assert report.output_state_checked is True
    assert resource.writes == ["*IDN?", "*LANG?", ":OUTPut:STATe?"]
    assert resource.closed and manager.closed


@pytest.mark.parametrize(("raw", "enabled"), [(b"smu.OFF\n", False), (b"smu.ON\n", True)])
def test_tsp_diagnostic_reads_output_attribute_without_setting_it(raw, enabled):
    resource = FakeResource(IDENTITY, b"TSP\n", raw)
    manager = FakeManager(resource)
    report = run_read_only_diagnostic(config(), resource_manager_factory=FakeFactory(manager))

    assert report.command_language == "TSP"
    assert report.output_state_query == "print(smu.source.output)"
    assert report.output_state_raw == raw.decode("ascii").removesuffix("\n")
    assert report.output_enabled is enabled
    assert resource.writes == ["*IDN?", "*LANG?", "print(smu.source.output)"]


def test_diagnostic_rejects_ambiguous_output_state():
    resource = FakeResource(IDENTITY, b"SCPI\n", b"UNKNOWN\n")
    manager = FakeManager(resource)

    with pytest.raises(TransportProtocolError, match="source-output"):
        run_read_only_diagnostic(config(), resource_manager_factory=FakeFactory(manager))

    assert resource.closed and manager.closed


def test_connect_open_failure_is_typed_and_closes_manager():
    resource = FakeResource()
    manager = FakeManager(resource, open_error=OSError("no route"))
    transport = PyVisaKeithley2460Transport(config(), resource_manager_factory=FakeFactory(manager))
    with pytest.raises(TransportConnectionError):
        transport.connect()
    assert manager.closed and not transport.connected


def test_runtime_upload_rejects_every_source_except_packaged_artifact():
    transport, _, _ = transport_with(FakeResource())
    with pytest.raises(UnsupportedCapabilityError, match="exact packaged"):
        transport.load_runtime("print('not sent')", abi="x", sha256="0" * 64)


def test_runtime_upload_is_serialized_verified_and_leaves_output_off():
    artifact = packaged_runtime()
    resource = FakeResource(
        IDENTITY,
        b"TSP\n",
        b"smu.OFF\n",
        b"true\n",
        b"true\n",
        b"false\n",
        f"{artifact.abi}\n".encode(),
        f"{artifact.build}\n".encode(),
        b"function\n",
        b"smu.OFF\n",
    )
    transport, _, _ = transport_with(resource, required_language="TSP")
    transport.connect()

    transport.load_runtime(artifact.source, abi=artifact.abi, sha256=artifact.sha256)

    assert f"loadscript {artifact.script_name}" in resource.writes
    assert "endscript" in resource.writes
    assert f"{artifact.script_name}.run()" in resource.writes
    load_index = resource.writes.index(f"loadscript {artifact.script_name}")
    end_index = resource.writes.index("endscript")
    assert "\n".join(resource.writes[load_index + 1 : end_index]) + "\n" == artifact.source
    assert resource.writes[-5:] == [
        "print(oe_m4_runtime_abi)",
        "print(oe_m4_runtime_build)",
        "print(type(oe_m4_initialize))",
        "oe_m4_initialize()",
        "print(smu.source.output)",
    ]
    assert resource.writes[end_index + 1] == f"print({artifact.script_name} == nil)"


def test_runtime_upload_reuses_digest_named_runtime_without_replacing_it():
    artifact = packaged_runtime()
    resource = FakeResource(
        IDENTITY,
        b"TSP\n",
        b"smu.OFF\n",
        b"false\n",
        f"{artifact.abi}\n".encode(),
        f"{artifact.build}\n".encode(),
        b"function\n",
        b"0\n",
    )
    transport, _, _ = transport_with(resource, required_language="TSP")
    transport.connect()

    transport.load_runtime(artifact.source, abi=artifact.abi, sha256=artifact.sha256)

    assert not any(command.startswith("loadscript ") for command in resource.writes)
    assert f"{artifact.script_name}.run()" not in resource.writes


def test_runtime_upload_rejects_stale_globals_before_loading_new_digest():
    artifact = packaged_runtime()
    resource = FakeResource(
        IDENTITY,
        b"TSP\n",
        b"smu.OFF\n",
        b"true\n",
        b"false\n",
    )
    transport, _, _ = transport_with(resource, required_language="TSP")
    transport.connect()

    with pytest.raises(IncompatibleInstrumentError, match="globals already exist"):
        transport.load_runtime(artifact.source, abi=artifact.abi, sha256=artifact.sha256)

    assert not any(command.startswith("loadscript ") for command in resource.writes)


def test_runtime_upload_rejects_scpi_and_output_on_before_any_script_write():
    artifact = packaged_runtime()
    scpi, _, _ = transport_with(FakeResource(IDENTITY, b"SCPI\n"))
    scpi.connect()
    with pytest.raises(IncompatibleInstrumentError, match="TSP"):
        scpi.load_runtime(artifact.source, abi=artifact.abi, sha256=artifact.sha256)

    output_on_resource = FakeResource(IDENTITY, b"TSP\n", b"1\n")
    output_on, _, _ = transport_with(output_on_resource, required_language="TSP")
    output_on.connect()
    with pytest.raises(ShutdownUnconfirmed, match="output OFF"):
        output_on.load_runtime(artifact.source, abi=artifact.abi, sha256=artifact.sha256)
    assert not any(command.startswith("loadscript ") for command in output_on_resource.writes)
