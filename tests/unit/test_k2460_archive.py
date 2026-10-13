"""Raw K2460 proof archive integrity and no-overwrite tests."""

import json

import pytest

from ophyd_electrochemistry.acquisition import StartMode
from ophyd_electrochemistry.exceptions import RetainedDataError, ValidationError
from ophyd_electrochemistry.keithley.k2460 import (
    K2460_BUFFER_ARCHIVE_SCHEMA,
    K2460_BUFFER_SCHEMA,
    BufferedReading,
    CurrentHoldAcquisitionProof,
    K2460MeasurementProfile,
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


def test_archive_v3_preserves_profile_and_reads_legacy_v2_and_v1():
    external = archive(
        request=CurrentHoldAcquisitionProof(
            current_a=0.001,
            source_range_a=0.001,
            voltage_limit_v=0.2,
            measurement_count=3,
            start_mode=StartMode.EXTERNAL_TRIGGER,
            measurement_profile=K2460MeasurementProfile.NPLC_0_1,
        )
    )
    encoded = runtime_buffer_archive_json(external)
    encoded_request = json.loads(encoded)["payload"]["request"]
    assert encoded_request["start_mode"] == "external_trigger"
    assert encoded_request["measurement_profile"] == "k2460_0p2v_0p1nplc_v1"
    decoded_external = runtime_buffer_archive_from_json(encoded).request
    assert decoded_external.start_mode == StartMode.EXTERNAL_TRIGGER
    assert decoded_external.measurement_profile == K2460MeasurementProfile.NPLC_0_1

    legacy_v2 = json.loads(runtime_buffer_archive_json(archive()))
    legacy_v2["payload"]["schema"] = "ophyd-electrochemistry/k2460-buffer-archive-v2"
    del legacy_v2["payload"]["request"]["measurement_profile"]
    legacy_v2["sha256"] = sha256_json(canonical_json(legacy_v2["payload"]))
    decoded_v2 = runtime_buffer_archive_from_json(canonical_json(legacy_v2))
    assert decoded_v2.schema.endswith("v2")
    assert decoded_v2.request.start_mode == StartMode.IMMEDIATE
    assert decoded_v2.request.measurement_profile == K2460MeasurementProfile.NPLC_1

    legacy = json.loads(runtime_buffer_archive_json(archive()))
    legacy["payload"]["schema"] = "ophyd-electrochemistry/k2460-buffer-archive-v1"
    del legacy["payload"]["request"]["start_mode"]
    del legacy["payload"]["request"]["measurement_profile"]
    legacy["sha256"] = sha256_json(canonical_json(legacy["payload"]))
    decoded = runtime_buffer_archive_from_json(canonical_json(legacy))
    assert decoded.schema.endswith("v1")
    assert decoded.request.start_mode == StartMode.IMMEDIATE
    assert decoded.request.measurement_profile == K2460MeasurementProfile.NPLC_1


def test_archive_accepts_current_and_predecessor_runtime_builds():
    assert archive().runtime_build == "m4-finite-current-hold-v5"
    assert archive(runtime_build="m4-finite-current-hold-v6").runtime_build.endswith("v6")
    assert archive(runtime_build="m4-finite-current-hold-v7").runtime_build.endswith("v7")
    assert archive(runtime_build="m4-finite-current-hold-v8").runtime_build.endswith("v8")
    assert archive(runtime_build="m4-finite-current-hold-v9").runtime_build.endswith("v9")
    assert archive(runtime_build="m4-finite-current-hold-v10").runtime_build.endswith("v10")
    assert archive(runtime_build="m4-finite-current-hold-v11").runtime_build.endswith("v11")
    assert archive(runtime_build="m4-finite-current-hold-v12").runtime_build.endswith("v12")
    assert archive(runtime_build="m4-finite-current-hold-v13").runtime_build.endswith("v13")
    assert archive(runtime_build="m4-finite-current-hold-v14").runtime_build.endswith("v14")


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
