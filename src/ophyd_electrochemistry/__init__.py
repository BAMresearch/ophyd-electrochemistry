"""Contract template; no operational instrument backend is implemented."""

from .acquisition import AcquisitionRequest, StartMode
from .protocols import (
    CurrentPulseSequence,
    CyclicVoltammetry,
    GalvanostaticHold,
    PotentiostaticHold,
)
from .state import DeviceState

__version__ = "0.0.0.dev0"
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
