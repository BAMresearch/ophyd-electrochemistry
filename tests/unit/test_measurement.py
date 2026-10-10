"""M5 record meaning, validation, integrity and chunk-boundary tests."""

from dataclasses import FrozenInstanceError, replace

import pytest

from ophyd_electrochemistry import (
    MEASUREMENT_SCHEMA,
    ClockMapping,
    FieldOrigin,
    MappingQuality,
    MeasurementRecord,
    MeasurementSchema,
    RetainedBuffer,
    TimestampReference,
    TimingOrigin,
    mapping_index,
    source_setpoint_unit,
)
from ophyd_electrochemistry.exceptions import RetainedDataError, ValidationError


def schema(**changes):
    values = dict(
        source_function="current",
        voltage_origin=FieldOrigin.SYNTHETIC,
        current_origin=FieldOrigin.SYNTHETIC,
        timing_origin=TimingOrigin.SYNTHETIC,
        timestamp_reference=TimestampReference.APERTURE_START,
        instrument_timestamp_origin="test virtual clock",
        instrument_timestamp_resolution_s=0.001,
        instrument_start_timestamp_s=10.0,
        source_status_definition="test source bit 0: synthetic compliance",
        measurement_status_definition="test measurement bits: none",
        synthetic=True,
    )
    return MeasurementSchema(**(values | changes))


def record(index=0, **changes):
    start = 0.002 + index * 0.01
    values = dict(
        sample_index=index,
        instrument_timestamp_s=10.0 + start,
        time_relative_s=start,
        aperture_start_relative_s=start,
        aperture_end_relative_s=start + 0.002,
        available_relative_s=start + 0.003,
        voltage_v=0.1 + index * 0.001,
        current_a=0.01,
        source_function="current",
        source_setpoint=0.01,
        source_status=0,
        measurement_status=0,
        mapping_quality=MappingQuality.SETTLED_SINGLE_DWELL,
        first_logical_point=index,
        last_logical_point=index,
        repeat_index=0,
        point_index=index,
        cycle_index=0,
        segment_index=0,
    )
    return MeasurementRecord(**(values | changes))


def buffer(records=None, **changes):
    retained = tuple(record(index) for index in range(3)) if records is None else records
    values = dict(
        schema=schema(),
        acquisition_id="acquisition-1",
        request_sha256="0" * 64,
        program_sha256="1" * 64,
        capacity_records=10,
        expected_records=3,
        waveform_complete=True,
        terminal_cause="normal",
        records=retained,
    )
    return RetainedBuffer(**(values | changes))


def test_schema_keeps_origin_clock_and_source_units_explicit():
    mapping = ClockMapping(
        instrument_anchor_s=12.0,
        epoch_anchor_s=1_800_000_000.0,
        scale=1.000001,
        uncertainty_s=0.002,
        method="bracketed host round trip",
    )
    value = schema(clock_mapping=mapping)
    assert value.schema == MEASUREMENT_SCHEMA == "ophyd-electrochemistry/measurement-v2"
    assert value.voltage_origin == value.current_origin == FieldOrigin.SYNTHETIC
    assert mapping.to_epoch(14.0) == pytest.approx(1_800_000_002.000002)
    assert source_setpoint_unit("current") == "A"
    assert source_setpoint_unit("voltage") == "V"
    with pytest.raises(FrozenInstanceError):
        value.synthetic = False


@pytest.mark.parametrize(
    "changes",
    [
        {"source_function": "power"},
        {"voltage_origin": "synthetic"},
        {"instrument_timestamp_resolution_s": 0},
        {"instrument_start_timestamp_s": float("nan")},
        {"synthetic": False},
        {"timing_origin": TimingOrigin.DERIVED_FROM_CONFIGURATION},
        {"schema": "unknown"},
    ],
)
def test_schema_rejects_ambiguous_or_invalid_meaning(changes):
    with pytest.raises(ValidationError):
        schema(**changes)


def test_measurement_v1_is_not_silently_accepted_as_v2():
    with pytest.raises(ValidationError, match="Unsupported measurement schema"):
        schema(schema="ophyd-electrochemistry/measurement-v1")


def test_mapping_quality_requires_consistent_indices():
    assert mapping_index(None) == -1 and mapping_index(3) == 3
    unknown = record(
        mapping_quality=MappingQuality.UNKNOWN,
        first_logical_point=None,
        last_logical_point=None,
        repeat_index=None,
        point_index=None,
        cycle_index=None,
        segment_index=None,
    )
    assert unknown.mapping_quality == MappingQuality.UNKNOWN
    crossing = record(
        mapping_quality=MappingQuality.CROSSES_TRANSITION,
        first_logical_point=2,
        last_logical_point=3,
    )
    assert crossing.first_logical_point == 2 and crossing.last_logical_point == 3
    with pytest.raises(ValidationError, match="quality"):
        replace(crossing, mapping_quality=MappingQuality.SETTLED_SINGLE_DWELL)
    with pytest.raises(ValidationError, match="Unknown"):
        replace(record(), mapping_quality=MappingQuality.UNKNOWN)


@pytest.mark.parametrize(
    "changes",
    [
        {"sample_index": -1},
        {"voltage_v": float("inf")},
        {"source_status": -1},
        {"measurement_status": -1},
        {"aperture_end_relative_s": 0.002},
        {"available_relative_s": 0.003},
        {"first_logical_point": 2, "last_logical_point": 1},
    ],
)
def test_record_rejects_nonfinite_invalid_or_incomplete_values(changes):
    with pytest.raises(ValidationError):
        record(**changes)


def test_frozen_buffer_has_stable_full_and_offset_chunk_checksums():
    retained = buffer()
    metadata = retained.metadata
    assert metadata.retained_records == 3
    assert metadata.first_sample_index == 0 and metadata.last_sample_index == 2
    first = retained.chunk(offset=0, max_records=2)
    retry = retained.chunk(offset=0, max_records=2)
    final = retained.chunk(offset=2, max_records=2)
    empty = retained.chunk(offset=3, max_records=2)
    assert first == retry and first.records_sha256 == retry.records_sha256
    assert [value.sample_index for value in first.records] == [0, 1]
    assert first.next_offset == 2 and not first.final
    assert [value.sample_index for value in final.records] == [2] and final.final
    assert empty.records == () and empty.final
    assert len(metadata.records_sha256) == 64
    assert metadata.records_sha256 != first.records_sha256
    with pytest.raises(RetainedDataError, match="offset"):
        retained.chunk(offset=4, max_records=1)


def test_buffer_rejects_wrap_gaps_capacity_and_timestamp_mismatch():
    with pytest.raises(RetainedDataError, match="contiguous"):
        buffer(records=(record(0), record(2)), expected_records=2, waveform_complete=True)
    with pytest.raises(RetainedDataError, match="capacity"):
        buffer(capacity_records=2)
    with pytest.raises(RetainedDataError, match="Instrument"):
        buffer(records=(record(0, instrument_timestamp_s=11),), expected_records=1)
    with pytest.raises(RetainedDataError, match="reference"):
        buffer(
            records=(record(0, time_relative_s=0.01, instrument_timestamp_s=10.01),),
            expected_records=1,
        )


def test_partial_and_empty_prestart_terminal_buffers_are_explicit():
    partial = buffer(records=(record(0),), waveform_complete=False)
    assert partial.metadata.retained_records == 1
    empty = buffer(
        schema=schema(instrument_start_timestamp_s=None),
        records=(),
        waveform_complete=False,
    )
    assert empty.metadata.first_sample_index is None
    assert empty.chunk(offset=0, max_records=5).final
    with pytest.raises(RetainedDataError, match="START"):
        buffer(
            schema=schema(instrument_start_timestamp_s=None),
            records=(record(0),),
            expected_records=1,
        )
