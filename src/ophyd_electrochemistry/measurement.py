"""Typed M5 electrical records and deterministic frozen-buffer readback."""

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from .exceptions import RetainedDataError, ValidationError
from .serialization import canonical_json, sha256_json
from .validation import integer, number, positive, text
from .waveforms import SourceFunction

MEASUREMENT_SCHEMA = "ophyd-electrochemistry/measurement-v4"
_SHA256_LENGTH = 64


class FieldOrigin(Enum):
    """Physical origin of an electrical value."""

    MEASURED = "measured"
    SOURCE_READBACK = "source_readback"
    SYNTHETIC = "synthetic"


class TimingOrigin(Enum):
    """How aperture and availability times were obtained."""

    INSTRUMENT_REPORTED = "instrument_reported"
    DERIVED_FROM_CONFIGURATION = "derived_from_configuration"
    SYNTHETIC = "synthetic"


class ElectricalField(Enum):
    """Electrical field whose aperture a timestamp references."""

    VOLTAGE = "voltage"
    CURRENT = "current"


class TimestampReference(Enum):
    """Physical point represented by a sample timestamp."""

    APERTURE_START = "aperture_start"
    APERTURE_MIDPOINT = "aperture_midpoint"
    APERTURE_END = "aperture_end"
    BUFFER_STORED = "buffer_stored"
    UNRESOLVED = "unresolved"


class RelativeTimeOrigin(Enum):
    """Zero point of a record's relative timestamp, distinct from its reference."""

    ACTUAL_START = "actual_start"
    FIRST_STORED_READING = "first_stored_reading"


class MappingQuality(Enum):
    """Relationship between one integration aperture and source dwells."""

    SETTLED_SINGLE_DWELL = "settled_single_dwell"
    UNSETTLED_SINGLE_DWELL = "unsettled_single_dwell"
    CROSSES_TRANSITION = "crosses_transition"
    UNKNOWN = "unknown"


@dataclass(frozen=True, kw_only=True)
class ClockMapping:
    """Affine mapping from an instrument clock to epoch seconds.

    ``epoch = epoch_anchor_s + (instrument - instrument_anchor_s) * scale``.
    The uncertainty is a conservative bound supplied by the calibration method,
    not a claim that host and instrument clocks are synchronized.
    """

    instrument_anchor_s: float
    epoch_anchor_s: float
    scale: float
    uncertainty_s: float
    method: str

    def __post_init__(self) -> None:
        for name in ("instrument_anchor_s", "epoch_anchor_s"):
            object.__setattr__(self, name, number(getattr(self, name), name))
        object.__setattr__(self, "scale", positive(self.scale, "scale"))
        object.__setattr__(
            self, "uncertainty_s", positive(self.uncertainty_s, "uncertainty_s", zero=True)
        )
        text(self.method, "clock mapping method")

    def to_epoch(self, instrument_timestamp_s: float) -> float:
        timestamp = number(instrument_timestamp_s, "instrument_timestamp_s")
        return number(
            self.epoch_anchor_s + (timestamp - self.instrument_anchor_s) * self.scale,
            "mapped epoch timestamp",
        )


@dataclass(frozen=True, kw_only=True)
class MeasurementSchema:
    """Run-constant meaning of every :class:`MeasurementRecord` field.

    Timestamp resolution may be unknown for first-stored-reading relative
    records. Formatting precision alone does not establish clock resolution.
    """

    source_function: SourceFunction
    voltage_origin: FieldOrigin
    current_origin: FieldOrigin
    timing_origin: TimingOrigin
    timestamp_reference: TimestampReference
    timestamp_field: ElectricalField | None
    instrument_timestamp_origin: str
    instrument_timestamp_resolution_s: float | None
    instrument_start_timestamp_s: float | None
    source_status_definition: str
    measurement_status_definition: str
    synthetic: bool
    relative_time_origin: RelativeTimeOrigin = RelativeTimeOrigin.ACTUAL_START
    clock_mapping: ClockMapping | None = None
    schema: str = MEASUREMENT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != MEASUREMENT_SCHEMA:
            raise ValidationError("Unsupported measurement schema")
        if self.source_function not in ("current", "voltage"):
            raise ValidationError("source_function must be current or voltage")
        for name, enum_type in (
            ("voltage_origin", FieldOrigin),
            ("current_origin", FieldOrigin),
            ("timing_origin", TimingOrigin),
            ("timestamp_reference", TimestampReference),
            ("relative_time_origin", RelativeTimeOrigin),
        ):
            if not isinstance(getattr(self, name), enum_type):
                raise ValidationError(f"{name} must be {enum_type.__name__}")
        aperture_reference = self.timestamp_reference in (
            TimestampReference.APERTURE_START,
            TimestampReference.APERTURE_MIDPOINT,
            TimestampReference.APERTURE_END,
        )
        if aperture_reference:
            if not isinstance(self.timestamp_field, ElectricalField):
                raise ValidationError("Aperture timestamp reference requires an electrical field")
        elif self.timestamp_field is not None:
            raise ValidationError("Non-aperture timestamp reference must not select a field")
        if type(self.synthetic) is not bool:
            raise ValidationError("synthetic must be boolean")
        electrical_origins = (self.voltage_origin, self.current_origin)
        if any(
            (origin == FieldOrigin.SYNTHETIC) != self.synthetic for origin in electrical_origins
        ):
            raise ValidationError(
                "synthetic records require two synthetic electrical origins, and vice versa"
            )
        if self.synthetic != (self.timing_origin == TimingOrigin.SYNTHETIC):
            raise ValidationError("synthetic records require synthetic timing, and vice versa")
        text(self.instrument_timestamp_origin, "instrument_timestamp_origin")
        if self.instrument_timestamp_resolution_s is not None:
            object.__setattr__(
                self,
                "instrument_timestamp_resolution_s",
                positive(self.instrument_timestamp_resolution_s, "instrument timestamp resolution"),
            )
        elif self.relative_time_origin == RelativeTimeOrigin.ACTUAL_START:
            raise ValidationError(
                "Actual-START origin requires known instrument timestamp resolution"
            )
        if self.instrument_start_timestamp_s is not None:
            object.__setattr__(
                self,
                "instrument_start_timestamp_s",
                number(self.instrument_start_timestamp_s, "instrument start timestamp"),
            )
        if self.relative_time_origin == RelativeTimeOrigin.FIRST_STORED_READING:
            if self.instrument_start_timestamp_s is not None:
                raise ValidationError("First-stored-reading origin requires unknown actual START")
            if aperture_reference:
                raise ValidationError("Unknown actual START cannot declare an aperture reference")
        text(self.source_status_definition, "source_status_definition")
        text(self.measurement_status_definition, "measurement_status_definition")
        if self.clock_mapping is not None and not isinstance(self.clock_mapping, ClockMapping):
            raise ValidationError("clock_mapping must be ClockMapping or None")


@dataclass(frozen=True, kw_only=True)
class MeasurementAperture:
    """One field's half-open integration window relative to actual START.

    ``uncertainty_s`` bounds each boundary when established; ``None`` makes no
    quantitative uncertainty claim. Instrument clock resolution is separate.
    """

    start_relative_s: float
    end_relative_s: float
    origin: TimingOrigin
    uncertainty_s: float | None = None

    def __post_init__(self) -> None:
        for name in ("start_relative_s", "end_relative_s"):
            object.__setattr__(self, name, positive(getattr(self, name), name, zero=True))
        if self.start_relative_s >= self.end_relative_s:
            raise ValidationError("Aperture must have positive width")
        if not isinstance(self.origin, TimingOrigin):
            raise ValidationError("Aperture origin must be TimingOrigin")
        if self.uncertainty_s is not None:
            object.__setattr__(
                self,
                "uncertainty_s",
                positive(self.uncertainty_s, "aperture uncertainty", zero=True),
            )


@dataclass(frozen=True, kw_only=True)
class MeasurementRecord:
    """One paired electrical record with independent, possibly unknown windows.

    ``time_relative_s`` uses the schema's explicit relative-time origin. Field
    apertures and availability always use actual START and must remain unknown
    when the record is relative to the first stored reading instead.
    """

    sample_index: int
    instrument_timestamp_s: float
    time_relative_s: float
    voltage_aperture: MeasurementAperture | None
    current_aperture: MeasurementAperture | None
    available_relative_s: float | None
    voltage_v: float
    current_a: float
    source_function: SourceFunction
    source_setpoint: float
    source_status: int
    measurement_status: int
    mapping_quality: MappingQuality
    first_logical_point: int | None
    last_logical_point: int | None
    repeat_index: int | None
    point_index: int | None
    cycle_index: int | None
    segment_index: int | None

    def __post_init__(self) -> None:
        integer(self.sample_index, "sample_index", minimum=0)
        for name in (
            "instrument_timestamp_s",
            "time_relative_s",
            "voltage_v",
            "current_a",
            "source_setpoint",
        ):
            value = number(getattr(self, name), name)
            if name not in ("voltage_v", "current_a", "source_setpoint") and value < 0:
                raise ValidationError(f"{name} must be nonnegative")
            object.__setattr__(self, name, value)
        if self.source_function not in ("current", "voltage"):
            raise ValidationError("source_function must be current or voltage")
        integer(self.source_status, "source_status", minimum=0)
        integer(self.measurement_status, "measurement_status", minimum=0)
        if not isinstance(self.mapping_quality, MappingQuality):
            raise ValidationError("mapping_quality must be MappingQuality")
        if self.available_relative_s is not None:
            object.__setattr__(
                self,
                "available_relative_s",
                positive(self.available_relative_s, "available_relative_s", zero=True),
            )
        for aperture in (self.voltage_aperture, self.current_aperture):
            if aperture is not None and not isinstance(aperture, MeasurementAperture):
                raise ValidationError("Field aperture must be MeasurementAperture or None")
            if (
                aperture is not None
                and self.available_relative_s is not None
                and aperture.end_relative_s > self.available_relative_s
            ):
                raise ValidationError(
                    "Complete record cannot be available before a field aperture ends"
                )
        if (
            self.voltage_aperture is None or self.current_aperture is None
        ) and self.mapping_quality != MappingQuality.UNKNOWN:
            raise ValidationError("Unknown field aperture requires unknown waveform mapping")

        mapping = (
            self.first_logical_point,
            self.last_logical_point,
            self.repeat_index,
            self.point_index,
            self.cycle_index,
            self.segment_index,
        )
        if self.mapping_quality == MappingQuality.UNKNOWN:
            if any(value is not None for value in mapping):
                raise ValidationError("Unknown mapping cannot carry valid program indices")
            return
        if any(value is None for value in mapping):
            raise ValidationError("Known mapping requires every program index")
        for name, mapping_value in zip(
            (
                "first_logical_point",
                "last_logical_point",
                "repeat_index",
                "point_index",
                "cycle_index",
                "segment_index",
            ),
            mapping,
            strict=True,
        ):
            assert mapping_value is not None
            integer(mapping_value, name, minimum=0)
        assert self.first_logical_point is not None and self.last_logical_point is not None
        if self.first_logical_point > self.last_logical_point:
            raise ValidationError("first_logical_point must not exceed last_logical_point")
        crossing = self.first_logical_point != self.last_logical_point
        if crossing != (self.mapping_quality == MappingQuality.CROSSES_TRANSITION):
            raise ValidationError("Mapping quality must agree with the logical-point span")


def _sha256(value: str, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValidationError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _records_sha256(records: tuple[MeasurementRecord, ...]) -> str:
    return sha256_json(canonical_json({"schema": MEASUREMENT_SCHEMA, "records": records}))


@dataclass(frozen=True, kw_only=True)
class RetainedBufferMetadata:
    """Integrity and extent metadata for one terminal, immutable acquisition."""

    acquisition_id: str
    request_sha256: str
    program_sha256: str
    capacity_records: int
    expected_records: int
    retained_records: int
    first_sample_index: int | None
    last_sample_index: int | None
    waveform_complete: bool
    terminal_cause: str
    records_sha256: str
    schema: str = MEASUREMENT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != MEASUREMENT_SCHEMA:
            raise ValidationError("Unsupported measurement schema")
        text(self.acquisition_id, "acquisition_id")
        _sha256(self.request_sha256, "request_sha256")
        _sha256(self.program_sha256, "program_sha256")
        integer(self.capacity_records, "capacity_records")
        integer(self.expected_records, "expected_records", minimum=0)
        integer(self.retained_records, "retained_records", minimum=0)
        if self.expected_records > self.capacity_records:
            raise RetainedDataError("Expected record count exceeds retained-buffer capacity")
        if self.retained_records > min(self.expected_records, self.capacity_records):
            raise RetainedDataError("Retained record extent exceeds its declared bounds")
        if self.retained_records == 0:
            if self.first_sample_index is not None or self.last_sample_index is not None:
                raise RetainedDataError("An empty buffer cannot declare a sample extent")
        else:
            assert self.first_sample_index is not None and self.last_sample_index is not None
            integer(self.first_sample_index, "first_sample_index", minimum=0)
            integer(self.last_sample_index, "last_sample_index", minimum=0)
            if self.first_sample_index != 0 or self.last_sample_index != self.retained_records - 1:
                raise RetainedDataError("Retained sample extent must be contiguous from zero")
        if type(self.waveform_complete) is not bool:
            raise ValidationError("waveform_complete must be boolean")
        if self.waveform_complete and self.retained_records != self.expected_records:
            raise RetainedDataError("A complete waveform must retain every expected record")
        text(self.terminal_cause, "terminal_cause")
        _sha256(self.records_sha256, "records_sha256")


@dataclass(frozen=True, kw_only=True)
class RecordChunk:
    """An offset-addressed slice of a frozen acquisition."""

    acquisition_id: str
    offset: int
    total_records: int
    records: tuple[MeasurementRecord, ...]
    records_sha256: str
    schema: str = MEASUREMENT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != MEASUREMENT_SCHEMA:
            raise ValidationError("Unsupported measurement schema")
        text(self.acquisition_id, "acquisition_id")
        integer(self.offset, "offset", minimum=0)
        integer(self.total_records, "total_records", minimum=0)
        if not isinstance(self.records, tuple) or not all(
            isinstance(record, MeasurementRecord) for record in self.records
        ):
            raise ValidationError("records must be a tuple of MeasurementRecord values")
        if self.offset + len(self.records) > self.total_records:
            raise RetainedDataError("Chunk extends beyond the retained record count")
        for local_index, record in enumerate(self.records):
            if record.sample_index != self.offset + local_index:
                raise RetainedDataError("Chunk sample indices must match its offset")
        if self.records_sha256 != _records_sha256(self.records):
            raise RetainedDataError("Chunk record checksum does not match its payload")

    @property
    def next_offset(self) -> int:
        return self.offset + len(self.records)

    @property
    def final(self) -> bool:
        return self.next_offset == self.total_records


@dataclass(frozen=True, kw_only=True)
class RetainedBuffer:
    """Frozen terminal records with deterministic, retryable chunk boundaries."""

    schema: MeasurementSchema
    acquisition_id: str
    request_sha256: str
    program_sha256: str
    capacity_records: int
    expected_records: int
    waveform_complete: bool
    terminal_cause: str
    records: tuple[MeasurementRecord, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.schema, MeasurementSchema):
            raise ValidationError("schema must be MeasurementSchema")
        text(self.acquisition_id, "acquisition_id")
        _sha256(self.request_sha256, "request_sha256")
        _sha256(self.program_sha256, "program_sha256")
        integer(self.capacity_records, "capacity_records")
        integer(self.expected_records, "expected_records", minimum=0)
        if self.expected_records > self.capacity_records:
            raise RetainedDataError("Expected record count exceeds retained-buffer capacity")
        if type(self.waveform_complete) is not bool:
            raise ValidationError("waveform_complete must be boolean")
        text(self.terminal_cause, "terminal_cause")
        if not isinstance(self.records, tuple) or not all(
            isinstance(record, MeasurementRecord) for record in self.records
        ):
            raise ValidationError("records must be a tuple of MeasurementRecord values")
        if len(self.records) > self.expected_records or len(self.records) > self.capacity_records:
            raise RetainedDataError("Retained record extent exceeds its declared bounds")
        if self.waveform_complete and len(self.records) != self.expected_records:
            raise RetainedDataError("A complete waveform must retain every expected record")
        for expected_index, record in enumerate(self.records):
            if record.sample_index != expected_index:
                raise RetainedDataError("Retained sample indices must be contiguous from zero")
            self._validate_record(record)

    def _validate_record(self, record: MeasurementRecord) -> None:
        if record.source_function != self.schema.source_function:
            raise RetainedDataError("Record source function differs from the fixed schema")
        resolution = self.schema.instrument_timestamp_resolution_s
        if self.schema.relative_time_origin == RelativeTimeOrigin.ACTUAL_START:
            anchor = self.schema.instrument_start_timestamp_s
            if anchor is None:
                raise RetainedDataError("Actual-START relative records require a START timestamp")
        else:
            anchor = self.records[0].instrument_timestamp_s
            if self.records[0].time_relative_s != 0:
                raise RetainedDataError("First-stored-reading relative time must start at zero")
            if (
                record.voltage_aperture is not None
                or record.current_aperture is not None
                or record.available_relative_s is not None
                or record.mapping_quality != MappingQuality.UNKNOWN
            ):
                raise RetainedDataError(
                    "Unknown actual START requires unknown field windows, availability and mapping"
                )
        raw_relative = record.instrument_timestamp_s - anchor
        if resolution is None and raw_relative != record.time_relative_s:
            raise RetainedDataError(
                "Instrument and relative timestamps must match exactly when resolution is unknown"
            )
        if resolution is not None and abs(raw_relative - record.time_relative_s) > resolution:
            raise RetainedDataError("Instrument and relative timestamps exceed one resolution")
        # Unknown resolution permits exact identity only, without an inferred
        # physical tolerance. Actual-START windows always have known resolution.
        tolerance = 0.0 if resolution is None else resolution
        for aperture in (record.voltage_aperture, record.current_aperture):
            if self.schema.synthetic and aperture is None:
                raise RetainedDataError("Synthetic records require both field apertures")
            if aperture is not None and (
                (aperture.origin == TimingOrigin.SYNTHETIC) != self.schema.synthetic
            ):
                raise RetainedDataError(
                    "Field aperture origin disagrees with synthetic declaration"
                )
        reference = self.schema.timestamp_reference
        if reference in (TimestampReference.UNRESOLVED, TimestampReference.BUFFER_STORED):
            if reference == TimestampReference.BUFFER_STORED:
                for aperture in (record.voltage_aperture, record.current_aperture):
                    if (
                        aperture is not None
                        and record.time_relative_s + tolerance < aperture.end_relative_s
                    ):
                        raise RetainedDataError("Stored timestamp precedes a field aperture end")
            if (
                reference == TimestampReference.BUFFER_STORED
                and record.available_relative_s is not None
                and record.time_relative_s > record.available_relative_s + tolerance
            ):
                raise RetainedDataError("Record availability precedes its stored timestamp")
            return
        aperture = (
            record.voltage_aperture
            if self.schema.timestamp_field == ElectricalField.VOLTAGE
            else record.current_aperture
        )
        if aperture is None:
            raise RetainedDataError("Aperture timestamp reference requires a known field aperture")
        expected_time = {
            TimestampReference.APERTURE_START: aperture.start_relative_s,
            TimestampReference.APERTURE_MIDPOINT: (
                aperture.start_relative_s + aperture.end_relative_s
            )
            / 2,
            TimestampReference.APERTURE_END: aperture.end_relative_s,
        }[reference]
        if abs(record.time_relative_s - expected_time) > tolerance:
            raise RetainedDataError(
                "Relative timestamp disagrees with its declared field reference"
            )

    @property
    def metadata(self) -> RetainedBufferMetadata:
        retained = len(self.records)
        return RetainedBufferMetadata(
            acquisition_id=self.acquisition_id,
            request_sha256=self.request_sha256,
            program_sha256=self.program_sha256,
            capacity_records=self.capacity_records,
            expected_records=self.expected_records,
            retained_records=retained,
            first_sample_index=0 if retained else None,
            last_sample_index=retained - 1 if retained else None,
            waveform_complete=self.waveform_complete,
            terminal_cause=self.terminal_cause,
            records_sha256=_records_sha256(self.records),
        )

    def chunk(self, *, offset: int, max_records: int) -> RecordChunk:
        """Return a deterministic slice; safe callers may retry the same offset."""
        integer(offset, "offset", minimum=0)
        integer(max_records, "max_records")
        if offset > len(self.records):
            raise RetainedDataError("Chunk offset exceeds the retained record count")
        records = self.records[offset : offset + max_records]
        return RecordChunk(
            acquisition_id=self.acquisition_id,
            offset=offset,
            total_records=len(self.records),
            records=records,
            records_sha256=_records_sha256(records),
        )


def mapping_index(value: int | None) -> int:
    """Fixed event-schema encoding: ``-1`` means no valid program association."""
    return -1 if value is None else value


SourceSetpointUnit = Literal["A", "V"]


def source_setpoint_unit(source_function: SourceFunction) -> SourceSetpointUnit:
    if source_function == "current":
        return "A"
    if source_function == "voltage":
        return "V"
    raise ValidationError("source_function must be current or voltage")
