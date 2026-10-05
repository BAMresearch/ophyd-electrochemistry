"""Validated models/generators and transport; no operational sourcing device yet."""

from .acquisition import AcquisitionRequest, StartMode
from .protocols import (
    CurrentPulseSequence,
    CyclicVoltammetry,
    GalvanostaticHold,
    PotentiostaticHold,
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
    "MultisineSpec",
    "PRBSWaveform",
    "PotentiostaticHold",
    "StartMode",
    "canonical_request_json",
    "generate_multisine",
    "prbs_bits",
    "request_from_json",
    "request_sha256",
]
