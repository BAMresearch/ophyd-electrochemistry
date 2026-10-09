"""Raw K2460 proof archive integrity and no-overwrite tests."""

import json

import pytest

from ophyd_electrochemistry.exceptions import RetainedDataError, ValidationError
from ophyd_electrochemistry.keithley.k2460 import (
    K2460_BUFFER_ARCHIVE_SCHEMA,
    K2460_BUFFER_SCHEMA,
    BufferedReading,
    CurrentHoldAcquisitionProof,
    RuntimeRecordChunk,
    assemble_runtime_buffer_archive,
    runtime_buffer_archive_from_json,
    runtime_buffer_archive_json,
    write_runtime_buffer_archive,
)
from ophyd_electrochemistry.serialization import canonical_json, sha256_json


def records() -> tuple[BufferedReading, ...]:
    return tuple(
        BufferedReading(
            sample_index=index,
            source_current_a=0.001,
            measured_voltage_v=0.1 + index * 1e-6,
            buffer_relative_time_s=index * 0.04,
            source_status=200,
            measurement_status=264 if index == 0 else 8,
        )
        for index in range(3)
    )


def record_digest(values: tuple[BufferedReading, ...]) -> str:
    return sha256_json(canonical_json({"schema": K2460_BUFFER_SCHEMA, "records": values}))


def chunks() -> tuple[RuntimeRecordChunk, ...]:
    values = records()
    return (
        RuntimeRecordChunk(
            offset=0,
            total_records=3,
            records=values[:2],
            records_sha256=record_digest(values[:2]),
        ),
        RuntimeRecordChunk(
            offset=2,
            total_records=3,
            records=values[2:],
            records_sha256=record_digest(values[2:]),
        ),
    )


def archive(**changes):
    values = dict(
        acquisition_id="target-proof-1",
        recorded_at_utc="2026-10-09T10:00:00Z",
        instrument_identity="KEITHLEY INSTRUMENTS,MODEL 2460,04686198,1.7.16a",
        runtime_build="m4-finite-current-hold-v5",
        runtime_sha256="a" * 64,
        runtime_script_name="oe_m4_aaaaaaaaaaaaaaaaaaaaaaaa",
        request=CurrentHoldAcquisitionProof(
            current_a=0.001,
            source_range_a=0.001,
            voltage_limit_v=0.2,
            measurement_count=3,
        ),
        capacity_records=16,
        chunks=chunks(),
    )
    return assemble_runtime_buffer_archive(**(values | changes))


def test_archive_is_canonical_checksummed_and_round_trips():
    value = archive()
    encoded = runtime_buffer_archive_json(value)
    envelope = json.loads(encoded)

    assert value.schema == K2460_BUFFER_ARCHIVE_SCHEMA
    assert len(value.records) == 3
    assert envelope["sha256"] == sha256_json(canonical_json(envelope["payload"]))
    assert runtime_buffer_archive_from_json(encoded) == value
    assert runtime_buffer_archive_json(runtime_buffer_archive_from_json(encoded)) == encoded


def test_archive_writer_exclusively_creates_and_never_overwrites(tmp_path):
    value = archive()
    destination = tmp_path / "proof.json"
    digest = write_runtime_buffer_archive(destination, value)

    assert digest == json.loads(destination.read_text())["sha256"]
    assert runtime_buffer_archive_from_json(destination.read_text()) == value
    with pytest.raises(FileExistsError):
        write_runtime_buffer_archive(destination, value)


def test_archive_rejects_outer_and_inner_checksum_tampering():
    envelope = json.loads(runtime_buffer_archive_json(archive()))
    envelope["payload"]["records"][0]["measured_voltage_v"] = 999
    with pytest.raises(RetainedDataError, match="payload checksum"):
        runtime_buffer_archive_from_json(canonical_json(envelope))

    envelope["sha256"] = sha256_json(canonical_json(envelope["payload"]))
    with pytest.raises(RetainedDataError, match="record checksum"):
        runtime_buffer_archive_from_json(canonical_json(envelope))


def test_archive_rejects_chunk_gaps_incomplete_data_and_ambiguous_time():
    first, final = chunks()
    bad_final = RuntimeRecordChunk(
        offset=1,
        total_records=3,
        records=(records()[1], records()[2]),
        records_sha256=record_digest((records()[1], records()[2])),
    )
    with pytest.raises(RetainedDataError, match="contiguous"):
        archive(chunks=(first, bad_final))
    with pytest.raises(RetainedDataError, match="complete"):
        archive(chunks=(first,))
    with pytest.raises(ValidationError, match="UTC Z"):
        archive(recorded_at_utc="2026-10-09T12:00:00+02:00")
    with pytest.raises(ValidationError, match="script name"):
        archive(runtime_script_name="oe_m4_wrong")
    with pytest.raises(ValidationError, match="build"):
        archive(runtime_build="unknown")
