"""Pure projection of frozen 2460 hold records without inferred electrical timing."""

from dataclasses import replace

from ...exceptions import RetainedDataError, ValidationError
from ...measurement import (
    FieldOrigin,
    MappingQuality,
    MeasurementRecord,
    MeasurementSchema,
    RelativeTimeOrigin,
    RetainedBuffer,
    TimestampReference,
    TimingOrigin,
)
from ...validation import number
from .runtime import BufferedReading, RuntimeRecordChunk


def project_current_hold_buffer(
    *,
    chunks: tuple[RuntimeRecordChunk, ...],
    acquisition_id: str,
    request_sha256: str,
    program_sha256: str,
    capacity_records: int,
    expected_records: int,
    terminal_cause: str,
    waveform_complete: bool,
    source_setpoint_a: float,
) -> RetainedBuffer:
    """Preserve a complete retained extent, including an interrupted hold's prefix.

    The input must contain every retained record, not necessarily every requested
    record. An empty retained buffer is represented by one empty runtime chunk.
    Buffer-relative timestamps are copied unchanged into both timestamp columns;
    zero is the first stored reading, with no actual-START or epoch anchor.
    Neither timestamp resolution nor either field's aperture is established by
    the buffer transfer's numeric formatting.
    """
    if (
        not isinstance(chunks, tuple)
        or not chunks
        or not all(isinstance(chunk, RuntimeRecordChunk) for chunk in chunks)
    ):
        raise ValidationError("chunks must be a nonempty tuple of RuntimeRecordChunk values")
    setpoint = number(source_setpoint_a, "source_setpoint_a")
    total_records = chunks[0].total_records
    next_offset = 0
    raw_records: list[BufferedReading] = []
    for chunk in chunks:
        # Recheck payload checksums at the projection boundary, including callers
        # restoring independently stored chunk objects.
        replace(chunk)
        if chunk.total_records != total_records or chunk.offset != next_offset:
            raise RetainedDataError("Projection chunks must cover one contiguous retained extent")
        if not chunk.records and total_records:
            raise RetainedDataError("Nonempty retained extent cannot contain an empty chunk")
        raw_records.extend(chunk.records)
        next_offset = chunk.next_offset
    if next_offset != total_records or not chunks[-1].final:
        raise RetainedDataError("Projection chunks do not contain the complete retained extent")
    if raw_records and raw_records[0].buffer_relative_time_s != 0:
        raise RetainedDataError("First stored reading must have buffer-relative timestamp zero")
    if any(
        after.buffer_relative_time_s < before.buffer_relative_time_s
        for before, after in zip(raw_records[:-1], raw_records[1:], strict=True)
    ):
        raise RetainedDataError("Buffer-relative timestamps must be nondecreasing")

    schema = MeasurementSchema(
        source_function="current",
        voltage_origin=FieldOrigin.MEASURED,
        current_origin=FieldOrigin.SOURCE_READBACK,
        timing_origin=TimingOrigin.INSTRUMENT_REPORTED,
        timestamp_reference=TimestampReference.UNRESOLVED,
        timestamp_field=None,
        instrument_timestamp_origin="Keithley 2460 buffer.relativetimestamps; first stored reading",
        instrument_timestamp_resolution_s=None,
        instrument_start_timestamp_s=None,
        relative_time_origin=RelativeTimeOrigin.FIRST_STORED_READING,
        source_status_definition=(
            "Keithley Model 2460 Reference Manual Rev. C: bufferVar.sourcestatuses; "
            "unmodified raw source-status bitmask"
        ),
        measurement_status_definition=(
            "Keithley Model 2460 Reference Manual Rev. C: bufferVar.statuses; "
            "unmodified raw measurement-status bitmask"
        ),
        synthetic=False,
        clock_mapping=None,
    )
    records = tuple(
        MeasurementRecord(
            sample_index=raw.sample_index,
            instrument_timestamp_s=raw.buffer_relative_time_s,
            time_relative_s=raw.buffer_relative_time_s,
            voltage_aperture=None,
            current_aperture=None,
            available_relative_s=None,
            voltage_v=raw.measured_voltage_v,
            current_a=raw.source_current_a,
            source_function="current",
            source_setpoint=setpoint,
            source_status=raw.source_status,
            measurement_status=raw.measurement_status,
            mapping_quality=MappingQuality.UNKNOWN,
            first_logical_point=None,
            last_logical_point=None,
            repeat_index=None,
            point_index=None,
            cycle_index=None,
            segment_index=None,
        )
        for raw in raw_records
    )
    return RetainedBuffer(
        schema=schema,
        acquisition_id=acquisition_id,
        request_sha256=request_sha256,
        program_sha256=program_sha256,
        capacity_records=capacity_records,
        expected_records=expected_records,
        waveform_complete=waveform_complete,
        terminal_cause=terminal_cause,
        records=records,
    )
