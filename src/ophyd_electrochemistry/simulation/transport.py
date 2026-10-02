"""Typed, transaction-locked fake host link; intentionally no raw TSP/string API."""

from ..acquisition import AcquisitionRequest
from ..exceptions import ElectrochemistryError, ShutdownUnconfirmed
from ..keithley.k2460.compiler import CompiledProgram
from ..state import DeviceState
from ..validation import integer
from .runtime import SimulatedRecord, SimulatedRuntime, Snapshot, TerminalOutcome


class FakeTransport:
    """Connection loss never stops the independent runtime or replays commands.

    Reconnect retains UNKNOWN host output until inspect() reconciles local state.
    Commands are synchronous typed transactions, not a PyVISA framing emulator.
    """

    def __init__(self, runtime: SimulatedRuntime):
        if not isinstance(runtime, SimulatedRuntime):
            raise TypeError("Expected SimulatedRuntime")
        self.runtime = runtime
        self.connected = False
        self.observed_state = DeviceState.ERROR
        self.observed_output: bool | None = None
        self._fail_next = False

    def connect(self) -> None:
        with self.runtime.lock:
            self.connected = True
            self.observed_output = None
            self.observed_state = DeviceState.ERROR

    def disconnect(self) -> None:
        with self.runtime.lock:
            self.connected = False
            self.observed_state = DeviceState.ERROR
            self.observed_output = None

    def fail_next_transaction(self) -> None:
        """Test hook: lose reply AFTER mutation; callers must inspect, never retry."""
        with self.runtime.lock:
            self._fail_next = True

    def _check(self) -> None:
        if not self.connected:
            raise ShutdownUnconfirmed("Fake transport disconnected; host output UNKNOWN")

    def _reply(self) -> None:
        if self._fail_next:
            self._fail_next = False
            self.disconnect()
            raise ShutdownUnconfirmed("Simulated lost reply; command execution is ambiguous")
        self.observed_state = self.runtime.state
        self.observed_output = self.runtime.output

    def inspect(self) -> tuple[DeviceState, bool | None]:
        with self.runtime.lock:
            self._check()
            self._reply()
            return self.observed_state, self.observed_output

    def prepare(self, request: AcquisitionRequest, *, acquisition_id: str) -> CompiledProgram:
        with self.runtime.lock:
            self._check()
            result = self.runtime.prepare(request, acquisition_id=acquisition_id)
            self._reply()
            return result

    def kickoff(self) -> None:
        with self.runtime.lock:
            self._check()
            self.runtime.kickoff()
            self._reply()

    def completion(self) -> TerminalOutcome | None:
        with self.runtime.lock:
            self._check()
            self._reply()
            return self.runtime.completion()

    def abort(self, *, reason: str = "programmatic") -> TerminalOutcome | None:
        with self.runtime.lock:
            self._check()
            try:
                result = self.runtime.abort(reason=reason)
            except ShutdownUnconfirmed:
                self.observed_state = DeviceState.ERROR
                self.observed_output = None
                raise
            self._reply()
            return result

    def recover(self) -> None:
        with self.runtime.lock:
            self._check()
            try:
                self.runtime.recover()
            except ShutdownUnconfirmed:
                self.observed_state = DeviceState.ERROR
                self.observed_output = None
                raise
            self._reply()

    def latch_snapshot(self) -> Snapshot:
        with self.runtime.lock:
            self._check()
            result = self.runtime.latch_snapshot()
            self._reply()
            return result

    def read_snapshot(self) -> Snapshot:
        with self.runtime.lock:
            self._check()
            result = self.runtime.read_snapshot()
            self._reply()
            return result

    def collect_chunk(self, *, max_records: int) -> tuple[SimulatedRecord, ...]:
        with self.runtime.lock:
            self._check()
            result = self.runtime.collect_chunk(max_records=max_records)
            self._reply()
            return result

    def export_retained_data(self, destination: str) -> str:
        with self.runtime.lock:
            self._check()
            result = self.runtime.export_retained_data(destination)
            self._reply()
            return result

    def discard_retained_data(self, *, acquisition_id: str, reason: str) -> None:
        with self.runtime.lock:
            self._check()
            self.runtime.discard_retained_data(acquisition_id=acquisition_id, reason=reason)
            self._reply()

    def close(self) -> None:
        with self.runtime.lock:
            self._check()
            if self.runtime.output is not False or self.runtime.state in (
                DeviceState.RUNNING,
                DeviceState.WAITING_START,
                DeviceState.PREPARED,
            ):
                raise ElectrochemistryError("Confirm terminal shutdown before closing")
            self.disconnect()

    def poll_until_terminal(self, *, max_ticks: int, poll_ticks: int) -> TerminalOutcome:
        """Bounded test convenience; advance the independent runtime's local time.

        This is not host-paced source timing: runtime processes every scheduled
        event independently, irrespective of the polling interval.
        """
        integer(max_ticks, "max_ticks", minimum=0)
        integer(poll_ticks, "poll_ticks")
        remaining = max_ticks
        while True:
            result = self.completion()
            if result is not None:
                return result
            if remaining == 0:
                raise TimeoutError("Simulator polling budget exhausted; runtime still active")
            delta = min(remaining, poll_ticks)
            self.runtime.advance_ticks(delta)
            remaining -= delta
