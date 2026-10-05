"""Explicit read-only commissioning diagnostic for a Keithley 2460."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from time import monotonic

from ... import __version__
from ...exceptions import ElectrochemistryError, TransportProtocolError
from .transport import (
    CommandLanguage,
    PyVisaKeithley2460Transport,
    ResourceManagerFactory,
    VisaTransportConfig,
)

_REVIEWED_COMMON_QUERIES = ("*IDN?", "*LANG?")
_SCPI_OUTPUT_QUERY = ":OUTPut:STATe?"
_TSP_OUTPUT_QUERY = "print(smu.source.output)"


@dataclass(frozen=True)
class CommissioningReport:
    """Raw identity evidence plus the host/backend configuration that produced it."""

    schema: str
    observed_at_utc: str
    elapsed_s: float
    resource_name: str
    backend_specification: str
    backend_description: str
    open_timeout_ms: int
    io_timeout_ms: int
    read_termination: str
    write_termination: str
    chunk_size_bytes: int
    max_response_bytes: int
    reviewed_queries: tuple[str, ...]
    identity_raw: str
    manufacturer: str
    model: str
    serial_number: str
    firmware_version: str
    command_language_raw: str
    command_language: CommandLanguage
    output_state_query: str
    output_state_raw: str
    output_enabled: bool
    python_version: str
    platform: str
    package_version: str
    pyvisa_version: str
    pyvisa_py_version: str
    mutating_commands_sent: bool
    output_state_checked: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def run_read_only_diagnostic(
    config: VisaTransportConfig,
    *,
    resource_manager_factory: ResourceManagerFactory | None = None,
) -> CommissioningReport:
    """Run only the two manual-reviewed common queries and close the session."""

    started = monotonic()
    transport = PyVisaKeithley2460Transport(
        config, resource_manager_factory=resource_manager_factory
    )
    try:
        transport.connect()
        identity = transport.identity
        command_language = transport.command_language
        backend_description = transport.backend_description
        output_state_query = _SCPI_OUTPUT_QUERY if command_language == "SCPI" else _TSP_OUTPUT_QUERY
        output_state_raw = transport.query(output_state_query)
        output_enabled = _parse_output_enabled(output_state_raw)
        return CommissioningReport(
            schema="k2460-read-only-diagnostic-v2",
            observed_at_utc=datetime.now(UTC).isoformat(),
            elapsed_s=monotonic() - started,
            resource_name=config.resource_name,
            backend_specification=config.backend,
            backend_description=backend_description,
            open_timeout_ms=config.open_timeout_ms,
            io_timeout_ms=config.io_timeout_ms,
            read_termination=config.read_termination,
            write_termination=config.write_termination,
            chunk_size_bytes=config.chunk_size_bytes,
            max_response_bytes=config.max_response_bytes,
            reviewed_queries=(*_REVIEWED_COMMON_QUERIES, output_state_query),
            identity_raw=identity.raw,
            manufacturer=identity.manufacturer,
            model=identity.model,
            serial_number=identity.serial_number,
            firmware_version=identity.firmware_version,
            command_language_raw=command_language,
            command_language=command_language,
            output_state_query=output_state_query,
            output_state_raw=output_state_raw,
            output_enabled=output_enabled,
            python_version=platform.python_version(),
            platform=platform.platform(),
            package_version=__version__,
            pyvisa_version=_distribution_version("pyvisa"),
            pyvisa_py_version=_distribution_version("pyvisa-py"),
            mutating_commands_sent=False,
            output_state_checked=True,
        )
    finally:
        transport.close()


def _distribution_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "not-installed"


def _parse_output_enabled(response: str) -> bool:
    if response in ("0", "OFF"):
        return False
    if response in ("1", "ON"):
        return True
    raise TransportProtocolError(f"Unsupported source-output state response {response!r}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read Keithley 2460 identity, command language, and source-output state; no reset, "
            "configuration, measurement, runtime upload, or output-control command is sent."
        )
    )
    parser.add_argument("--resource", required=True, help="Explicit PyVISA resource name")
    parser.add_argument("--backend", required=True, help="Explicit PyVISA backend, for example @py")
    parser.add_argument("--open-timeout-ms", type=int, default=3_000)
    parser.add_argument("--io-timeout-ms", type=int, default=3_000)
    parser.add_argument("--chunk-size-bytes", type=int, default=4_096)
    parser.add_argument("--max-response-bytes", type=int, default=65_536)
    parser.add_argument("--require-language", choices=("SCPI", "TSP"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = VisaTransportConfig(
            resource_name=args.resource,
            backend=args.backend,
            open_timeout_ms=args.open_timeout_ms,
            io_timeout_ms=args.io_timeout_ms,
            chunk_size_bytes=args.chunk_size_bytes,
            max_response_bytes=args.max_response_bytes,
            required_language=args.require_language,
        )
        report = run_read_only_diagnostic(config)
    except ElectrochemistryError as exc:
        print(f"diagnostic failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    if report.output_enabled:
        print("diagnostic unsafe: source output is ON", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the console entry point
    raise SystemExit(main())
