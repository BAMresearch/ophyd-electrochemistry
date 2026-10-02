"""Typed public interface contract; this Protocol is not an operational Device."""

from collections.abc import Iterator, Mapping
from typing import Any, Protocol

from bluesky.protocols import Reading, Status
from event_model.documents import DataKey, PartialEvent

from .acquisition import AcquisitionRequest
from .state import DeviceState


class ElectrochemistryDevice(Protocol):
    name: str
    parent: Any

    @property
    def state(self) -> DeviceState: ...

    def stage(self) -> list[Any]: ...
    def unstage(self) -> list[Any]: ...

    def prepare(self, value: AcquisitionRequest) -> Status:
        """C02: validate/configure; output OFF, PREPARED, neither flag asserted."""
        ...

    def kickoff(self) -> Status:
        """C03: immediate execution, or external armed wait before sourcing."""
        ...

    def complete(self) -> Status:
        """C03: status for finite acquisition outcome; never an early stop."""
        ...

    def abort(self, *, reason: str = "programmatic") -> Status:
        """C04: succeeds on confirmed shutdown; acquisition completion fails."""
        ...

    def recover(self) -> Status:
        """C04: confirmed safe IDLE, no sourcing/re-arm/data destruction."""
        ...

    def stop(self, *, success: bool = False) -> None:
        """C05: synchronous bounded Stoppable cleanup; raise if unconfirmed."""
        ...

    def pause(self) -> None:
        """Initial policy: abort and reject automatic replay/resume."""
        ...

    def resume(self) -> None:
        """Reject replay of an interrupted acquisition; require a new request."""
        ...

    def trigger(self) -> Status:
        """C06: latch latest valid buffered sample, never issue competing measure."""
        ...

    def read(self) -> dict[str, Reading[Any]]: ...
    def describe(self) -> dict[str, DataKey]: ...
    def describe_collect(self) -> dict[str, dict[str, DataKey]]: ...
    def collect(self) -> Iterator[PartialEvent]: ...
    def read_configuration(self) -> dict[str, Reading[Any]]: ...
    def describe_configuration(self) -> dict[str, DataKey]: ...

    def configure(
        self, values: Mapping[str, Any]
    ) -> tuple[dict[str, Reading[Any]], dict[str, Reading[Any]]]:
        """C07: ophyd configuration mapping; programs use prepare(request)."""
        ...

    def export_retained_data(self, destination: str) -> None:
        """Diagnostic export; must preserve raw data and record integrity metadata."""
        ...

    def discard_retained_data(self, *, acquisition_id: str, reason: str) -> None:
        """Explicit operator action after archive/review; no implicit data clearing."""
        ...
