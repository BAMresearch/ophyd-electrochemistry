"""Validated acquisition models, simulation, Ophyd and guarded target candidates."""

from .acquisition import AcquisitionRequest, PreparedAcquisition, StartMode
from .measurement import (
    MEASUREMENT_SCHEMA,
    ClockMapping,
    ElectricalField,
    FieldOrigin,
    MappingQuality,
    MeasurementAperture,
    MeasurementRecord,
    MeasurementSchema,
    RecordChunk,
    RelativeTimeOrigin,
    RetainedBuffer,
    RetainedBufferMetadata,
    TimestampReference,
    TimingOrigin,
    mapping_index,
    source_setpoint_unit,
)
from .protocols import (
    CurrentPulseSequence,
    CyclicVoltammetry,
    GalvanostaticHold,
    PotentiostaticHold,
    VoltagePulseSequence,
)
from .serialization import canonical_request_json, request_from_json, request_sha256
from .state import DeviceState
from .waveforms import ArbitraryWaveform, MultisineSpec, PRBSWaveform, generate_multisine, prbs_bits

# SemVer bootstrap sentinel; not a release. PSR prepares the first 0.1.0 PR.
__version__ = "0.0.0"
CONTRACT_REVISION = "0.2"

__all__ = [
    "AcquisitionRequest",
    "ArbitraryWaveform",
    "CONTRACT_REVISION",
    "CurrentPulseSequence",
    "CyclicVoltammetry",
    "DeviceState",
    "GalvanostaticHold",
    "MEASUREMENT_SCHEMA",
    "ClockMapping",
    "ElectricalField",
    "FieldOrigin",
    "MappingQuality",
    "MeasurementAperture",
    "MeasurementRecord",
    "MeasurementSchema",
    "RelativeTimeOrigin",
    "MultisineSpec",
    "PRBSWaveform",
    "PotentiostaticHold",
    "PreparedAcquisition",
    "RecordChunk",
    "RetainedBuffer",
    "RetainedBufferMetadata",
    "StartMode",
    "TimestampReference",
    "TimingOrigin",
    "VoltagePulseSequence",
    "canonical_request_json",
    "generate_multisine",
    "mapping_index",
    "prbs_bits",
    "request_from_json",
    "request_sha256",
    "source_setpoint_unit",
]
