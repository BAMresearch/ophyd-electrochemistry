"""Contract template; no operational instrument backend is implemented."""

from .acquisition import AcquisitionRequest, StartMode
from .protocols import (
    CurrentPulseSequence,
    CyclicVoltammetry,
    GalvanostaticHold,
    PotentiostaticHold,
)
from .state import DeviceState

# SemVer bootstrap sentinel; not a release. PSR prepares the first 0.1.0 PR.
__version__ = "0.0.0"
CONTRACT_REVISION = "0.1"

__all__ = [
    "AcquisitionRequest",
    "CurrentPulseSequence",
    "CyclicVoltammetry",
    "DeviceState",
    "GalvanostaticHold",
    "PotentiostaticHold",
    "StartMode",
]
