"""Deterministic M2 simulation; no VISA, TSP interpreter or physical guarantees."""

from .runtime import (
    FakeCell,
    FaultInjection,
    SimulatedRecord,
    SimulatedRuntime,
    Snapshot,
    SourceTransition,
    TerminalOutcome,
)
from .transport import FakeTransport

__all__ = [
    "FakeCell",
    "FakeTransport",
    "FaultInjection",
    "SimulatedRecord",
    "SimulatedRuntime",
    "Snapshot",
    "SourceTransition",
    "TerminalOutcome",
]
