"""Offline provenance and integrity checks for raw 2460 record projection."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ophyd_electrochemistry.exceptions import RetainedDataError, ValidationError
from ophyd_electrochemistry.keithley.k2460.projection import project_current_hold_buffer
from ophyd_electrochemistry.keithley.k2460.runtime import (
    BufferedReading,
    RuntimeRecordChunk,
    _buffered_records_sha256,
)
from ophyd_electrochemistry.measurement import (
    FieldOrigin,
    MappingQuality,
    RelativeTimeOrigin,
    RetainedBuffer,
    TimestampReference,
    TimingOrigin,
)


def reading(index: int, timestamp: float | None = None) -> BufferedReading:
    return BufferedReading(
        sample_index=index,
        source_current_a=0.00100001 + index * 1e-9,
        measured_voltage_v=0.1024 + index * 1e-5,
        buffer_relative_time_s=index * 0.01012345678 if timestamp is None else timestamp,
        source_status=200 + index,
        measurement_status=264 if index == 0 else 8,
    )


def chunk(
    records: tuple[BufferedReading, ...], *, total: int, offset: int = 0
) -> RuntimeRecordChunk:
    return RuntimeRecordChunk(
        offset=offset,
        total_records=total,
        records=records,
        records_sha256=_buffered_records_sha256(records),
    )


def project(chunks: tuple[RuntimeRecordChunk, ...], **changes: object) -> RetainedBuffer:
    values = {
        "chunks": chunks,
        "acquisition_id": "target-hold-1",
        "request_sha256": "a" * 64,
        "program_sha256": "b" * 64,
        "capacity_records": 16,
        "expected_records": 3,
        "terminal_cause": "normal_completion",
        "waveform_complete": True,
        "source_setpoint_a": 0.001,
    }
    return project_current_hold_buffer(**(values | changes))  # type: ignore[arg-type]


def test_projection_preserves_raw_values_statuses_and_unknown_timing() -> None:
    raw = tuple(reading(i) for i in range(3))
    result = project((chunk(raw[:2], total=3), chunk(raw[2:], total=3, offset=2)))
    schema = result.schema
    assert schema.voltage_origin == FieldOrigin.MEASURED
    assert schema.current_origin == FieldOrigin.SOURCE_READBACK
    assert schema.timing_origin == TimingOrigin.INSTRUMENT_REPORTED
    assert schema.timestamp_reference == TimestampReference.UNRESOLVED
    assert schema.relative_time_origin == RelativeTimeOrigin.FIRST_STORED_READING
    assert schema.instrument_timestamp_resolution_s is None
    assert schema.instrument_start_timestamp_s is None
    assert schema.clock_mapping is None
    assert schema.timestamp_field is None
    assert not schema.synthetic
    assert "sourcestatuses" in schema.source_status_definition
    assert "bufferVar.statuses" in schema.measurement_status_definition
    for projected, source in zip(result.records, raw, strict=True):
        assert projected.instrument_timestamp_s == source.buffer_relative_time_s
        assert projected.time_relative_s == source.buffer_relative_time_s
        assert projected.current_a == source.source_current_a
        assert projected.voltage_v == source.measured_voltage_v
        assert projected.source_status == source.source_status
        assert projected.measurement_status == source.measurement_status
        assert projected.source_setpoint == 0.001
        assert projected.current_a != projected.source_setpoint
        assert projected.voltage_aperture is None
        assert projected.current_aperture is None
        assert projected.available_relative_s is None
        assert projected.mapping_quality == MappingQuality.UNKNOWN
        assert all(
            getattr(projected, name) is None
            for name in (
                "first_logical_point",
                "last_logical_point",
                "point_index",
                "repeat_index",
                "cycle_index",
                "segment_index",
            )
        )
    assert result.records[0].time_relative_s == 0
    assert result.metadata.retained_records == 3
    assert result.metadata.request_sha256 == "a" * 64
    assert result.metadata.program_sha256 == "b" * 64
    assert result.metadata.acquisition_id == "target-hold-1"
    assert result.chunk(offset=1, max_records=2) == result.chunk(offset=1, max_records=2)


@pytest.mark.parametrize("retained", [0, 1, 2, 3])
def test_interrupted_hold_keeps_complete_retained_prefix(retained: int) -> None:
    result = project(
        (chunk(tuple(reading(i) for i in range(retained)), total=retained),),
        waveform_complete=False,
        terminal_cause="operator_abort",
    )
    assert result.metadata.retained_records == retained
    assert result.metadata.expected_records == 3
    assert not result.metadata.waveform_complete
    assert result.metadata.terminal_cause == "operator_abort"
    assert result.metadata.first_sample_index == (0 if retained else None)
    assert result.metadata.last_sample_index == (retained - 1 if retained else None)


@pytest.mark.parametrize(
    "chunks",
    [
        (),
        ("not a chunk",),
    ],
)
def test_missing_or_wrong_chunk_objects_are_rejected(chunks: object) -> None:
    with pytest.raises(ValidationError, match="nonempty tuple"):
        project(chunks)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "chunks",
    [
        (chunk((reading(1),), total=3, offset=1),),
        (chunk((reading(0),), total=3), chunk((reading(2),), total=3, offset=2)),
        (chunk((reading(0), reading(1)), total=3), chunk((reading(1),), total=3, offset=1)),
        (chunk((reading(0),), total=3), chunk((reading(1),), total=2, offset=1)),
        (chunk((reading(0),), total=3),),
        (chunk((), total=3), chunk(tuple(reading(i) for i in range(3)), total=3)),
    ],
)
def test_chunks_must_cover_one_complete_retained_extent(
    chunks: tuple[RuntimeRecordChunk, ...],
) -> None:
    with pytest.raises(RetainedDataError):
        project(chunks)


def test_projection_rechecks_chunk_payload_checksum() -> None:
    damaged = chunk(tuple(reading(i) for i in range(3)), total=3)
    object.__setattr__(damaged, "records_sha256", "0" * 64)
    with pytest.raises(ValidationError, match="checksum"):
        project((damaged,))


def test_missing_requested_records_cannot_be_reported_complete() -> None:
    with pytest.raises(RetainedDataError, match="every expected record"):
        project((chunk((reading(0),), total=1),))


@pytest.mark.parametrize("changes", [{"expected_records": 2}, {"capacity_records": 2}])
def test_projection_rejects_records_outside_prepared_bounds(changes: dict[str, int]) -> None:
    with pytest.raises(RetainedDataError):
        project((chunk(tuple(reading(i) for i in range(3)), total=3),), **changes)


def test_nonzero_first_timestamp_is_not_silently_reanchored() -> None:
    with pytest.raises(RetainedDataError, match="timestamp zero"):
        project((chunk((reading(0, 0.1),), total=1),), expected_records=1)


def test_backward_timestamps_are_rejected_without_inventing_a_correction() -> None:
    raw = (reading(0), reading(1, 0.02), reading(2, 0.01))
    with pytest.raises(RetainedDataError, match="nondecreasing"):
        project((chunk(raw, total=3),))


def test_repeated_reported_timestamp_is_preserved() -> None:
    raw = (reading(0), reading(1, 0), reading(2, 0.01))
    assert project((chunk(raw, total=3),)).records[1].instrument_timestamp_s == 0


def test_projection_does_not_mutate_input_or_depend_on_chunk_boundaries() -> None:
    raw = tuple(reading(i) for i in range(3))
    combined = chunk(raw, total=3)
    original = replace(combined)
    one = project((combined,))
    split = project((chunk(raw[:1], total=3), chunk(raw[1:], total=3, offset=1)))
    assert one == split
    assert one.metadata.records_sha256 == split.metadata.records_sha256
    assert combined == original


def test_recorded_run08_projects_without_promoting_timing_evidence() -> None:
    """Translate existing bench evidence locally; do not operate an instrument."""
    evidence_path = (
        Path(__file__).resolve().parents[2]
        / "docs/evidence/k2460-2026-11-02-timestamp-attempt-08.json"
    )
    evidence = json.loads(evidence_path.read_text())
    raw_chunk = evidence["raw_chunk"]
    assert raw_chunk == evidence["fresh_session_raw_chunk"]
    assert raw_chunk == evidence["retained_idle_raw_chunk"]
    restored = RuntimeRecordChunk(
        offset=raw_chunk["offset"],
        total_records=raw_chunk["total_records"],
        records=tuple(BufferedReading(**record) for record in raw_chunk["records"]),
        records_sha256=raw_chunk["records_sha256"],
        schema=raw_chunk["schema"],
    )
    result = project(
        (restored,),
        acquisition_id=f"recorded-proof-{evidence['proof_run_id']}",
        expected_records=evidence["request"]["measurement_count"],
        source_setpoint_a=evidence["request"]["current_a"],
    )
    record = result.records[0]
    assert record.current_a == 0.0010000127368
    assert record.voltage_v == 0.10248923302
    assert record.source_status == 200
    assert record.measurement_status == 8
    assert record.time_relative_s == record.instrument_timestamp_s == 0
    assert record.voltage_aperture is None
    assert record.current_aperture is None
    assert record.available_relative_s is None
    assert record.mapping_quality == MappingQuality.UNKNOWN
    assert result.schema.timestamp_reference == TimestampReference.UNRESOLVED
    assert result.schema.relative_time_origin == RelativeTimeOrigin.FIRST_STORED_READING
    assert result.schema.instrument_start_timestamp_s is None
    assert result.schema.instrument_timestamp_resolution_s is None
    assert result.schema.clock_mapping is None
