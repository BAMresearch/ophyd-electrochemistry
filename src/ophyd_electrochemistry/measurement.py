"""Typed M5 electrical records and deterministic frozen-buffer readback."""

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from .exceptions import RetainedDataError, ValidationError
from .serialization import canonical_json, sha256_json
from .validation import integer, number, positive, text
from .waveforms import SourceFunction

MEASUREMENT_SCHEMA = "ophyd-electrochemistry/measurement-v1"
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


class TimestampReference(Enum):
    """Physical point represented by a sample timestamp."""

    APERTURE_START = "aperture_start"
    APERTURE_MIDPOINT = "aperture_midpoint"
    APERTURE_END = "aperture_end"


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
    """Run-constant meaning of every :class:`MeasurementRecord` field."""

    source_function: SourceFunction
    voltage_origin: FieldOrigin
    current_origin: FieldOrigin
    timing_origin: TimingOrigin
    timestamp_reference: TimestampReference
    instrument_timestamp_origin: str
    instrument_timestamp_resolution_s: float
    instrument_start_timestamp_s: float | None
    status_bits_definition: str
    synthetic: bool
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
        ):
            if not isinstance(getattr(self, name), enum_type):
                raise ValidationError(f"{name} must be {enum_type.__name__}")
        if type(self.synthetic) is not bool:
            raise ValidationError("synthetic must be boolean")
        electrical_origins = (self.voltage_origin, self.current_origin)
        if self.synthetic != all(origin == FieldOrigin.SYNTHETIC for origin in electrical_origins):
            raise ValidationError(
                "synthetic records require two synthetic electrical origins, and vice versa"
            )
        if self.synthetic != (self.timing_origin == TimingOrigin.SYNTHETIC):
            raise ValidationError("synthetic records require synthetic timing, and vice versa")
        text(self.instrument_timestamp_origin, "instrument_timestamp_origin")
        object.__setattr__(
            self,
            "instrument_timestamp_resolution_s",
            positive(self.instrument_timestamp_resolution_s, "instrument timestamp resolution"),
        )
        if self.instrument_start_timestamp_s is not None:
            object.__setattr__(
                self,
                "instrument_start_timestamp_s",
                number(self.instrument_start_timestamp_s, "instrument start timestamp"),
            )
        text(self.status_bits_definition, "status_bits_definition")
        if self.clock_mapping is not None and not isinstance(self.clock_mapping, ClockMapping):
            raise ValidationError("clock_mapping must be ClockMapping or None")


@dataclass(frozen=True, kw_only=True)
class MeasurementRecord:
    """One complete paired electrical record with explicit aperture mapping."""

    sample_index: int
    instrument_timestamp_s: float
    time_relative_s: float
    aperture_start_relative_s: float
    aperture_end_relative_s: float
    available_relative_s: float
    voltage_v: float
    current_a: float
    source_function: SourceFunction
    source_setpoint: float
    status_bits: int
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
            "aperture_start_relative_s",
            "aperture_end_relative_s",
            "available_relative_s",
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
        integer(self.status_bits, "status_bits", minimum=0)
        if not isinstance(self.mapping_quality, MappingQuality):
            raise ValidationError("mapping_quality must be MappingQuality")
        if not (
            self.aperture_start_relative_s
            < self.aperture_end_relative_s
            <= self.available_relative_s
        ):
            raise ValidationError("Aperture must have positive width and be available afterward")

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
        if self.schema.instrument_start_timestamp_s is None:
            raise RetainedDataError("Records require an actual START timestamp")
        raw_relative = record.instrument_timestamp_s - self.schema.instrument_start_timestamp_s
        if abs(raw_relative - record.time_relative_s) > resolution:
            raise RetainedDataError("Instrument and relative timestamps exceed one resolution")
        expected_time = {
            TimestampReference.APERTURE_START: record.aperture_start_relative_s,
            TimestampReference.APERTURE_MIDPOINT: (
                record.aperture_start_relative_s + record.aperture_end_relative_s
            )
            / 2,
            TimestampReference.APERTURE_END: record.aperture_end_relative_s,
        }[self.schema.timestamp_reference]
        if abs(record.time_relative_s - expected_time) > resolution:
            raise RetainedDataError("Relative timestamp disagrees with its declared reference")

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
