"""M5 record meaning, validation, integrity and chunk-boundary tests."""

from dataclasses import FrozenInstanceError, replace

import pytest

from ophyd_electrochemistry import (
    MEASUREMENT_SCHEMA,
    ClockMapping,
    ElectricalField,
    FieldOrigin,
    MappingQuality,
    MeasurementAperture,
    MeasurementRecord,
    MeasurementSchema,
    RelativeTimeOrigin,
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
        timestamp_field=ElectricalField.VOLTAGE,
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
        voltage_aperture=MeasurementAperture(
            start_relative_s=start,
            end_relative_s=start + 0.002,
            origin=TimingOrigin.SYNTHETIC,
            uncertainty_s=0,
        ),
        current_aperture=MeasurementAperture(
            start_relative_s=start,
            end_relative_s=start + 0.002,
            origin=TimingOrigin.SYNTHETIC,
            uncertainty_s=0,
        ),
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
    assert value.schema == MEASUREMENT_SCHEMA == "ophyd-electrochemistry/measurement-v4"
    assert value.relative_time_origin == RelativeTimeOrigin.ACTUAL_START
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
        {"instrument_timestamp_resolution_s": None},
        {"instrument_start_timestamp_s": float("nan")},
        {"synthetic": False},
        {"timing_origin": TimingOrigin.DERIVED_FROM_CONFIGURATION},
        {"relative_time_origin": "actual_start"},
        {"schema": "unknown"},
    ],
)
def test_schema_rejects_ambiguous_or_invalid_meaning(changes):
    with pytest.raises(ValidationError):
        schema(**changes)


@pytest.mark.parametrize("version", ["v1", "v2", "v3"])
def test_older_measurement_schema_is_not_silently_reinterpreted(version):
    with pytest.raises(ValidationError, match="Unsupported measurement schema"):
        schema(schema=f"ophyd-electrochemistry/measurement-{version}")


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
        {"voltage_aperture": "unknown"},
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


@pytest.mark.parametrize(
    "changes",
    [
        {"start_relative_s": -0.001},
        {"end_relative_s": 0.002},
        {"end_relative_s": float("inf")},
        {"origin": "synthetic"},
        {"uncertainty_s": -0.001},
        {"uncertainty_s": float("nan")},
    ],
)
def test_aperture_rejects_invalid_boundaries_origins_and_uncertainties(changes):
    with pytest.raises(ValidationError):
        MeasurementAperture(
            **(
                dict(
                    start_relative_s=0.002,
                    end_relative_s=0.004,
                    origin=TimingOrigin.INSTRUMENT_REPORTED,
                )
                | changes
            )
        )


def physical_schema(**changes):
    return schema(
        **(
            dict(
                voltage_origin=FieldOrigin.MEASURED,
                current_origin=FieldOrigin.SOURCE_READBACK,
                timing_origin=TimingOrigin.INSTRUMENT_REPORTED,
                synthetic=False,
                instrument_timestamp_resolution_s=1e-6,
            )
            | changes
        )
    )


def unknown_mapping():
    return dict(
        mapping_quality=MappingQuality.UNKNOWN,
        first_logical_point=None,
        last_logical_point=None,
        repeat_index=None,
        point_index=None,
        cycle_index=None,
        segment_index=None,
    )


def test_sequential_field_apertures_and_selected_timestamp_reference():
    current = MeasurementAperture(
        start_relative_s=0.002,
        end_relative_s=0.022,
        origin=TimingOrigin.DERIVED_FROM_CONFIGURATION,
        uncertainty_s=1e-6,
    )
    voltage = MeasurementAperture(
        start_relative_s=0.023, end_relative_s=0.043, origin=TimingOrigin.INSTRUMENT_REPORTED
    )
    value = record(
        current_aperture=current,
        voltage_aperture=voltage,
        time_relative_s=0.043,
        instrument_timestamp_s=10.043,
        available_relative_s=0.044,
        **unknown_mapping(),
    )
    retained = buffer(
        records=(value,),
        expected_records=1,
        schema=physical_schema(timestamp_reference=TimestampReference.APERTURE_END),
    )
    assert retained.records[0].current_aperture.end_relative_s < voltage.start_relative_s
    assert voltage.uncertainty_s is None  # resolution is not an uncertainty claim
    with pytest.raises(RetainedDataError, match="reference"):
        replace(retained, schema=replace(retained.schema, timestamp_field=ElectricalField.CURRENT))
    changed = replace(value, current_aperture=replace(current, uncertainty_s=2e-6))
    assert (
        replace(retained, records=(changed,)).metadata.records_sha256
        != retained.metadata.records_sha256
    )
    with pytest.raises(ValidationError, match="available"):
        replace(value, available_relative_s=0.042)


@pytest.mark.parametrize(
    "reference", [TimestampReference.BUFFER_STORED, TimestampReference.UNRESOLVED]
)
def test_unknown_windows_keep_raw_timestamps_without_fabricated_apertures(reference):
    value = record(
        voltage_aperture=None,
        current_aperture=None,
        available_relative_s=None,
        instrument_timestamp_s=10.05,
        time_relative_s=0.05,
        **unknown_mapping(),
    )
    retained = buffer(
        records=(value,),
        expected_records=1,
        schema=physical_schema(timestamp_reference=reference, timestamp_field=None),
    )
    assert retained.records[0].voltage_v == 0.1
    assert retained.records[0].current_aperture is None
    assert retained.records[0].available_relative_s is None
    assert retained.chunk(offset=0, max_records=1) == retained.chunk(offset=0, max_records=1)
    with pytest.raises(RetainedDataError, match="known field aperture"):
        replace(retained, schema=physical_schema())
    with pytest.raises(ValidationError, match="Unknown field aperture"):
        record(voltage_aperture=None)


def test_unproved_timestamp_reference_cannot_masquerade_as_field_reference():
    with pytest.raises(ValidationError, match="electrical field"):
        physical_schema(timestamp_field=None)
    with pytest.raises(ValidationError, match="must not select"):
        physical_schema(timestamp_reference=TimestampReference.UNRESOLVED)
    with pytest.raises(ValidationError, match="must not select"):
        physical_schema(timestamp_reference=TimestampReference.BUFFER_STORED)


def test_field_timing_origin_cannot_convert_synthetic_evidence_to_hardware():
    synthetic = record()
    with pytest.raises(RetainedDataError, match="origin"):
        buffer(records=(synthetic,), expected_records=1, schema=physical_schema())
    unknown = record(voltage_aperture=None, **unknown_mapping())
    with pytest.raises(RetainedDataError, match="both field apertures"):
        buffer(records=(unknown,), expected_records=1)


def test_stored_timestamp_does_not_prove_record_available_earlier():
    value = record(
        voltage_aperture=None,
        current_aperture=None,
        instrument_timestamp_s=10.05,
        time_relative_s=0.05,
        available_relative_s=0.04,
        **unknown_mapping(),
    )
    with pytest.raises(RetainedDataError, match="availability"):
        buffer(
            records=(value,),
            expected_records=1,
            schema=physical_schema(
                timestamp_reference=TimestampReference.BUFFER_STORED, timestamp_field=None
            ),
        )


@pytest.mark.parametrize("field", [ElectricalField.VOLTAGE, ElectricalField.CURRENT])
def test_stored_timestamp_cannot_precede_either_known_field_window(field):
    aperture = MeasurementAperture(
        start_relative_s=0.002, end_relative_s=0.022, origin=TimingOrigin.INSTRUMENT_REPORTED
    )
    value = record(
        voltage_aperture=aperture if field == ElectricalField.VOLTAGE else None,
        current_aperture=aperture if field == ElectricalField.CURRENT else None,
        time_relative_s=0.003,
        instrument_timestamp_s=10.003,
        available_relative_s=0.024,
        **unknown_mapping(),
    )
    with pytest.raises(RetainedDataError, match="Stored timestamp precedes"):
        buffer(
            records=(value,),
            expected_records=1,
            schema=physical_schema(
                timestamp_reference=TimestampReference.BUFFER_STORED, timestamp_field=None
            ),
        )


def test_mixed_synthetic_and_physical_electrical_origins_are_rejected():
    with pytest.raises(ValidationError, match="electrical origins"):
        physical_schema(current_origin=FieldOrigin.SYNTHETIC)


def unanchored_schema(**changes):
    return physical_schema(
        **(
            dict(
                instrument_start_timestamp_s=None,
                instrument_timestamp_resolution_s=None,
                relative_time_origin=RelativeTimeOrigin.FIRST_STORED_READING,
                timestamp_reference=TimestampReference.UNRESOLVED,
                timestamp_field=None,
                instrument_timestamp_origin="raw buffer-relative clock; first stored reading zero",
            )
            | changes
        )
    )


def unanchored_record(index=0, **changes):
    return record(
        index,
        **(
            dict(
                instrument_timestamp_s=index * 0.041,
                time_relative_s=index * 0.041,
                voltage_aperture=None,
                current_aperture=None,
                available_relative_s=None,
            )
            | unknown_mapping()
            | changes
        ),
    )


@pytest.mark.parametrize(
    "reference", [TimestampReference.UNRESOLVED, TimestampReference.BUFFER_STORED]
)
@pytest.mark.parametrize("raw_anchor", [0.0, 42.0])
def test_unknown_start_preserves_raw_clock_and_explicit_first_reading_origin(reference, raw_anchor):
    records = tuple(
        unanchored_record(
            index,
            instrument_timestamp_s=raw_anchor + index * 0.041,
            time_relative_s=(raw_anchor + index * 0.041) - raw_anchor,
        )
        for index in range(3)
    )
    retained = buffer(records=records, schema=unanchored_schema(timestamp_reference=reference))
    assert retained.schema.instrument_start_timestamp_s is None
    assert retained.schema.relative_time_origin == RelativeTimeOrigin.FIRST_STORED_READING
    assert retained.schema.clock_mapping is None
    assert retained.schema.instrument_timestamp_resolution_s is None
    assert retained.records[0].instrument_timestamp_s == raw_anchor
    assert retained.records[0].time_relative_s == 0
    assert retained.records[-1].time_relative_s == pytest.approx(0.082)
    assert retained.records[-1].current_aperture is None
    assert retained.records[-1].available_relative_s is None
    assert retained.records[-1].mapping_quality == MappingQuality.UNKNOWN
    chunk = retained.chunk(offset=1, max_records=2)
    assert chunk == retained.chunk(offset=1, max_records=2)
    assert chunk.records[0].time_relative_s == records[1].time_relative_s
    assert chunk.records[0].instrument_timestamp_s == raw_anchor + 0.041


def test_unknown_start_empty_terminal_buffer_does_not_invent_a_first_reading():
    retained = buffer(schema=unanchored_schema(), records=(), waveform_complete=False)
    assert retained.schema.instrument_start_timestamp_s is None
    assert retained.metadata.first_sample_index is None
    assert retained.chunk(offset=0, max_records=1).records == ()


@pytest.mark.parametrize(
    "changes",
    [
        {"instrument_start_timestamp_s": 0},
        {
            "timestamp_reference": TimestampReference.APERTURE_END,
            "timestamp_field": ElectricalField.CURRENT,
        },
    ],
)
def test_first_reading_origin_rejects_invented_start_or_aperture_reference(changes):
    with pytest.raises(ValidationError, match="actual START"):
        unanchored_schema(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"time_relative_s": 1e-9},
        {"available_relative_s": 0.1},
        {
            "voltage_aperture": MeasurementAperture(
                start_relative_s=0,
                end_relative_s=0.02,
                origin=TimingOrigin.INSTRUMENT_REPORTED,
            )
        },
    ],
)
def test_unknown_start_rejects_shifted_zero_or_fabricated_actual_start_timing(changes):
    with pytest.raises(RetainedDataError):
        buffer(
            records=(unanchored_record(**changes),), expected_records=1, schema=unanchored_schema()
        )


def test_first_reading_relative_time_must_match_raw_clock_for_every_record():
    with pytest.raises(RetainedDataError, match="Instrument and relative"):
        buffer(
            records=(unanchored_record(), unanchored_record(1, time_relative_s=0.05)),
            expected_records=2,
            schema=unanchored_schema(),
        )
    with pytest.raises(RetainedDataError, match="unknown field windows"):
        buffer(records=(record(time_relative_s=0),), expected_records=1, schema=unanchored_schema())


def test_unknown_resolution_allows_no_invented_timestamp_tolerance():
    shifted = unanchored_record(1, time_relative_s=0.041 + 1e-15)
    with pytest.raises(RetainedDataError, match="exactly when resolution is unknown"):
        buffer(
            records=(unanchored_record(), shifted),
            expected_records=2,
            schema=unanchored_schema(),
        )
    retained = buffer(
        records=(unanchored_record(), shifted),
        expected_records=2,
        schema=unanchored_schema(instrument_timestamp_resolution_s=1e-6),
    )
    assert retained.schema.instrument_timestamp_resolution_s == 1e-6


@pytest.mark.parametrize("resolution", [0, -1e-6, float("nan"), float("inf")])
def test_first_reading_origin_rejects_invalid_known_resolution(resolution):
    with pytest.raises(ValidationError, match="resolution"):
        unanchored_schema(instrument_timestamp_resolution_s=resolution)


def test_independent_clock_mapping_does_not_claim_actual_start():
    mapping = ClockMapping(
        instrument_anchor_s=0,
        epoch_anchor_s=1_800_000_000,
        scale=1,
        uncertainty_s=0.1,
        method="independent clock calibration; physical START unobserved",
    )
    retained = buffer(
        records=(unanchored_record(),),
        expected_records=1,
        schema=unanchored_schema(clock_mapping=mapping),
    )
    assert retained.schema.clock_mapping.to_epoch(0) == 1_800_000_000
    assert retained.schema.instrument_start_timestamp_s is None
