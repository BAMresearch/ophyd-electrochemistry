"""Versioned, checksummed archive for the narrow raw K2460 buffer proof."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ...acquisition import StartMode
from ...exceptions import RetainedDataError, ValidationError
from ...serialization import canonical_json, sha256_json
from ...validation import integer, text
from .runtime import (
    K2460_BUFFER_SCHEMA,
    RUNTIME_ABI,
    RUNTIME_BUILD,
    BufferedReading,
    CurrentHoldAcquisitionProof,
    RuntimeRecordChunk,
)

K2460_BUFFER_ARCHIVE_SCHEMA = "ophyd-electrochemistry/k2460-buffer-archive-v2"
_LEGACY_K2460_BUFFER_ARCHIVE_SCHEMA = "ophyd-electrochemistry/k2460-buffer-archive-v1"
K2460_BUFFER_TIMESTAMP_ORIGIN = "relative-to-first-buffer-reading"
_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
_SUPPORTED_RUNTIME_BUILDS = {
    "m4-finite-current-hold-v5",
    "m4-finite-current-hold-v6",
    "m4-finite-current-hold-v7",
    "m4-finite-current-hold-v8",
    "m4-finite-current-hold-v9",
    "m4-finite-current-hold-v10",
    "m4-finite-current-hold-v11",
    RUNTIME_BUILD,
}
_ARCHIVE_PAYLOAD_KEYS = {
    "schema",
    "acquisition_id",
    "recorded_at_utc",
    "instrument_identity",
    "runtime_abi",
    "runtime_build",
    "runtime_sha256",
    "runtime_script_name",
    "request",
    "capacity_records",
    "record_count",
    "records_sha256",
    "buffer_timestamp_origin",
    "records",
}


def _sha256(value: str, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValidationError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _records_sha256(records: tuple[BufferedReading, ...]) -> str:
    return sha256_json(canonical_json({"schema": K2460_BUFFER_SCHEMA, "records": records}))


def _validate_utc_timestamp(value: str) -> str:
    text(value, "recorded_at_utc")
    if not value.endswith("Z"):
        raise ValidationError("recorded_at_utc must use UTC Z notation")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ValidationError("recorded_at_utc must be an ISO-8601 timestamp") from exc
    if parsed.utcoffset() != timedelta(0):
        raise ValidationError("recorded_at_utc must be UTC")
    return value


@dataclass(frozen=True, kw_only=True)
class RuntimeBufferArchive:
    """Complete raw target proof with identity and transfer-integrity metadata."""

    acquisition_id: str
    recorded_at_utc: str
    instrument_identity: str
    runtime_build: str
    runtime_sha256: str
    runtime_script_name: str
    request: CurrentHoldAcquisitionProof
    capacity_records: int
    records: tuple[BufferedReading, ...]
    records_sha256: str
    runtime_abi: str = RUNTIME_ABI
    buffer_timestamp_origin: str = K2460_BUFFER_TIMESTAMP_ORIGIN
    schema: str = K2460_BUFFER_ARCHIVE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema not in (
            _LEGACY_K2460_BUFFER_ARCHIVE_SCHEMA,
            K2460_BUFFER_ARCHIVE_SCHEMA,
        ):
            raise ValidationError("Unsupported K2460 buffer archive schema")
        if self.runtime_abi != RUNTIME_ABI:
            raise ValidationError("K2460 buffer archive runtime ABI is incompatible")
        if self.runtime_build not in _SUPPORTED_RUNTIME_BUILDS:
            raise ValidationError("K2460 buffer archive runtime build is incompatible")
        if self.buffer_timestamp_origin != K2460_BUFFER_TIMESTAMP_ORIGIN:
            raise ValidationError("Unsupported K2460 buffer timestamp origin")
        text(self.acquisition_id, "acquisition_id")
        object.__setattr__(self, "recorded_at_utc", _validate_utc_timestamp(self.recorded_at_utc))
        for name in ("instrument_identity", "runtime_build", "runtime_script_name"):
            text(getattr(self, name), name)
        _sha256(self.runtime_sha256, "runtime_sha256")
        _sha256(self.records_sha256, "records_sha256")
        if self.runtime_script_name != f"oe_m4_{self.runtime_sha256[:24]}":
            raise ValidationError("Runtime script name does not match its SHA-256 identity")
        if not isinstance(self.request, CurrentHoldAcquisitionProof):
            raise ValidationError("request must be CurrentHoldAcquisitionProof")
        integer(self.capacity_records, "capacity_records")
        if not isinstance(self.records, tuple) or not all(
            isinstance(record, BufferedReading) for record in self.records
        ):
            raise ValidationError("records must be a tuple of BufferedReading values")
        if len(self.records) != self.request.measurement_count:
            raise RetainedDataError("Complete archive must contain every requested record")
        if len(self.records) > self.capacity_records:
            raise RetainedDataError("Archive record count exceeds instrument buffer capacity")
        for expected_index, record in enumerate(self.records):
            if record.sample_index != expected_index:
                raise RetainedDataError("Archive records must be contiguous from zero")
        if self.records_sha256 != _records_sha256(self.records):
            raise RetainedDataError("Archive record checksum does not match its records")


def assemble_runtime_buffer_archive(
    *,
    acquisition_id: str,
    recorded_at_utc: str,
    instrument_identity: str,
    runtime_build: str,
    runtime_sha256: str,
    runtime_script_name: str,
    request: CurrentHoldAcquisitionProof,
    capacity_records: int,
    chunks: tuple[RuntimeRecordChunk, ...],
) -> RuntimeBufferArchive:
    """Assemble a complete archive from contiguous, independently checked chunks."""

    if (
        not isinstance(chunks, tuple)
        or not chunks
        or not all(isinstance(chunk, RuntimeRecordChunk) for chunk in chunks)
    ):
        raise ValidationError("chunks must be a nonempty tuple of RuntimeRecordChunk values")
    total_records = chunks[0].total_records
    next_offset = 0
    records: list[BufferedReading] = []
    for chunk in chunks:
        if chunk.total_records != total_records or chunk.offset != next_offset:
            raise RetainedDataError("Archive chunks must cover one contiguous retained extent")
        records.extend(chunk.records)
        next_offset = chunk.next_offset
    if next_offset != total_records or not chunks[-1].final:
        raise RetainedDataError("Archive chunks do not contain the complete retained extent")
    retained = tuple(records)
    return RuntimeBufferArchive(
        acquisition_id=acquisition_id,
        recorded_at_utc=recorded_at_utc,
        instrument_identity=instrument_identity,
        runtime_build=runtime_build,
        runtime_sha256=runtime_sha256,
        runtime_script_name=runtime_script_name,
        request=request,
        capacity_records=capacity_records,
        records=retained,
        records_sha256=_records_sha256(retained),
    )


def _record_payload(record: BufferedReading) -> dict[str, int | float]:
    return {
        "sample_index": record.sample_index,
        "source_current_a": record.source_current_a,
        "measured_voltage_v": record.measured_voltage_v,
        "buffer_relative_time_s": record.buffer_relative_time_s,
        "source_status": record.source_status,
        "measurement_status": record.measurement_status,
    }


def _archive_payload(archive: RuntimeBufferArchive) -> dict[str, object]:
    request: dict[str, object] = {
        "current_a": archive.request.current_a,
        "source_range_a": archive.request.source_range_a,
        "voltage_limit_v": archive.request.voltage_limit_v,
        "measurement_count": archive.request.measurement_count,
    }
    if archive.schema == K2460_BUFFER_ARCHIVE_SCHEMA:
        request["start_mode"] = archive.request.start_mode.value
    return {
        "schema": archive.schema,
        "acquisition_id": archive.acquisition_id,
        "recorded_at_utc": archive.recorded_at_utc,
        "instrument_identity": archive.instrument_identity,
        "runtime_abi": archive.runtime_abi,
        "runtime_build": archive.runtime_build,
        "runtime_sha256": archive.runtime_sha256,
        "runtime_script_name": archive.runtime_script_name,
        "request": request,
        "capacity_records": archive.capacity_records,
        "record_count": len(archive.records),
        "records_sha256": archive.records_sha256,
        "buffer_timestamp_origin": archive.buffer_timestamp_origin,
        "records": [_record_payload(record) for record in archive.records],
    }


def runtime_buffer_archive_json(archive: RuntimeBufferArchive) -> str:
    """Return canonical JSON with a checksum over the complete plain-data payload."""

    if not isinstance(archive, RuntimeBufferArchive):
        raise ValidationError("archive must be RuntimeBufferArchive")
    payload = _archive_payload(archive)
    payload_json = canonical_json(payload)
    return canonical_json({"sha256": sha256_json(payload_json), "payload": payload})


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def runtime_buffer_archive_from_json(encoded: str) -> RuntimeBufferArchive:
    """Verify and decode an allowlisted raw K2460 archive envelope."""

    if not isinstance(encoded, str) or len(encoded.encode("utf-8")) > _MAX_ARCHIVE_BYTES:
        raise ValidationError("K2460 archive JSON must be a string <=256 MiB")
    try:
        envelope = json.loads(encoded, object_pairs_hook=_unique_object)
        if not isinstance(envelope, dict) or set(envelope) != {"sha256", "payload"}:
            raise ValidationError("Invalid K2460 archive envelope")
        payload = envelope["payload"]
        if not isinstance(payload, dict) or set(payload) != _ARCHIVE_PAYLOAD_KEYS:
            raise ValidationError("Invalid K2460 archive payload")
        if envelope["sha256"] != sha256_json(canonical_json(payload)):
            raise RetainedDataError("K2460 archive payload checksum does not match")
        request = payload["request"]
        records = payload["records"]
        request_keys = {
            "current_a",
            "source_range_a",
            "voltage_limit_v",
            "measurement_count",
        }
        if payload["schema"] == K2460_BUFFER_ARCHIVE_SCHEMA:
            request_keys.add("start_mode")
        elif payload["schema"] != _LEGACY_K2460_BUFFER_ARCHIVE_SCHEMA:
            raise ValidationError("Unsupported K2460 buffer archive schema")
        if not isinstance(request, dict) or set(request) != request_keys:
            raise ValidationError("Invalid K2460 archive request")
        decoded_request = dict(request)
        if payload["schema"] == K2460_BUFFER_ARCHIVE_SCHEMA:
            decoded_request["start_mode"] = StartMode(decoded_request["start_mode"])
        if not isinstance(records, list):
            raise ValidationError("Invalid K2460 archive records")
        decoded_record_list: list[BufferedReading] = []
        for record in records:
            if not isinstance(record, dict):
                raise ValidationError("Invalid K2460 archive record")
            decoded_record_list.append(BufferedReading(**record))
        decoded_records = tuple(decoded_record_list)
        if payload["record_count"] != len(decoded_records):
            raise RetainedDataError("K2460 archive record count does not match its payload")
        return RuntimeBufferArchive(
            schema=payload["schema"],
            acquisition_id=payload["acquisition_id"],
            recorded_at_utc=payload["recorded_at_utc"],
            instrument_identity=payload["instrument_identity"],
            runtime_abi=payload["runtime_abi"],
            runtime_build=payload["runtime_build"],
            runtime_sha256=payload["runtime_sha256"],
            runtime_script_name=payload["runtime_script_name"],
            request=CurrentHoldAcquisitionProof(**decoded_request),
            capacity_records=payload["capacity_records"],
            records=decoded_records,
            records_sha256=payload["records_sha256"],
            buffer_timestamp_origin=payload["buffer_timestamp_origin"],
        )
    except (TypeError, ValueError, KeyError, RecursionError) as exc:
        raise ValidationError("Invalid K2460 archive JSON") from exc


def write_runtime_buffer_archive(destination: str | Path, archive: RuntimeBufferArchive) -> str:
    """Create, but never overwrite, one verified archive and return its digest."""

    encoded = runtime_buffer_archive_json(archive)
    envelope = json.loads(encoded)
    path = Path(destination)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(encoded + "\n")
    return str(envelope["sha256"])
