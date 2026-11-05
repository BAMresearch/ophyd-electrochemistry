"""Classic ophyd Flyer for finite sequences with incremental collection."""

# mypy: disable-error-code="attr-defined,no-untyped-call,misc"

import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from event_model.documents import DataKey, PartialEvent
from ophyd.device import Device
from ophyd.status import Status

from ..exceptions import (
    AcquisitionAborted,
    ElectrochemistryError,
    ShutdownUnconfirmed,
    UnsupportedCapabilityError,
)
from ..state import DeviceState
from ..validation import integer, positive
from .backend import READOUT_FIELDS, SequenceBackend, SequencePoll, SequenceRequest
from .models import nominal_duration_s, validate

EVENT_FIELDS = (
    "event_t_s",
    "event_step_index",
    "event_repeat_index",
    "event_kind",
    "event_detail",
)
_ACTIVE = {
    DeviceState.RUNNING,
    DeviceState.WAITING_START,
    DeviceState.ARMING,
    DeviceState.PREPARING,
    DeviceState.ABORTING,
}


def archive_retained(backend: SequenceBackend, directory: Path) -> Path:
    """Save the unread records of an earlier run to ``directory/retained-<UTC>.npz``.

    For a run whose host died: the instrument kept its records, and the next
    prepare refuses until they are saved. Connects if needed, closes after.
    """
    archive = getattr(backend, "archive_retained", None)
    if archive is None:
        raise UnsupportedCapabilityError(f"{type(backend).__name__} keeps no records to archive")
    directory.mkdir(parents=True, exist_ok=True)
    backend.connect()
    try:
        path: Path = archive(directory / f"retained-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.npz")
    finally:
        backend.close()
    return path


class SequenceFlyer(Device):
    """One monitor drains bounded backend polls; collect only consumes pending data."""

    def __init__(
        self,
        backend: SequenceBackend,
        *,
        name: str,
        poll_period_s: float = 1.0,
        chunk_records: int = 2000,
        start_timeout_s: float | None = None,
        completion_margin_s: float = 60.0,
    ) -> None:
        super().__init__("", name=name)
        self._backend = backend
        self._poll_period = positive(poll_period_s, "poll_period_s")
        self._chunk_records = integer(chunk_records, "chunk_records")
        self._start_timeout = (
            None if start_timeout_s is None else positive(start_timeout_s, "start_timeout_s")
        )
        self._margin = positive(completion_margin_s, "completion_margin_s", zero=True)
        self._operation = threading.RLock()
        self._pending_lock = threading.Lock()
        self._pending: list[PartialEvent] = []
        self._is_staged = False
        self._kicked = False
        self._request: SequenceRequest | None = None
        self._equipment: dict[str, str | int | float | bool] = {}
        self._completion: Status | None = None
        self._monitor: threading.Thread | None = None
        self._halt = threading.Event()
        self._kickoff_time = self._kickoff_epoch = 0.0
        self._running_time: float | None = None
        self._outcome: str | None = None
        self._outcome_detail = ""

    @property
    def equipment(self) -> dict[str, str | int | float | bool]:
        return dict(self._equipment)

    @property
    def outcome(self) -> str | None:
        return self._outcome

    @property
    def outcome_detail(self) -> str:
        return self._outcome_detail

    @property
    def state(self) -> DeviceState:
        with self._operation:
            return self._backend.inspect()[0]

    @property
    def output_enabled(self) -> bool | None:
        with self._operation:
            return self._backend.inspect()[1]

    def _confirmed(self, state: DeviceState | None = None) -> DeviceState:
        actual, output = self._backend.inspect()
        if output is not False:
            raise ShutdownUnconfirmed(f"Output OFF unconfirmed: {actual}, {output}")
        if state is not None and actual != state:
            raise ElectrochemistryError(f"Expected {state}, got {actual}")
        return actual

    def stage(self) -> list[Device]:
        with self._operation:
            if self._is_staged:
                raise ElectrochemistryError("Flyer is already staged")
            self._backend.connect()
            # Keep the connection available for inspection if OFF is uncertain.
            if self._confirmed() in _ACTIVE:
                raise ElectrochemistryError("Cannot stage an active backend")
            self._is_staged = True
        return [self]

    def unstage(self) -> list[Device]:
        self.stop()
        if self._monitor is not None:
            self._monitor.join(timeout=5)
            if self._monitor.is_alive():
                raise ShutdownUnconfirmed("Completion monitor did not stop")
        with self._operation:
            self._confirmed()
            self._backend.close()
            self._is_staged = False
        return [self]

    def _status(self, operation: Callable[[], None]) -> Status:
        status = Status(obj=self)
        try:
            with self._operation:
                operation()
        except Exception as exc:
            status.set_exception(exc)
        else:
            status.set_finished()
        return status

    def prepare(self, request: SequenceRequest) -> Status:
        def operation() -> None:
            # PREPARED is allowed: a run that failed to open (e.g. its file could
            # not be created) leaves a loaded, never-started sequence behind.
            if not self._is_staged or self._backend.inspect()[0] not in (
                DeviceState.IDLE,
                DeviceState.PREPARED,
                DeviceState.COMPLETE,
            ):
                raise ElectrochemistryError("Prepare requires staged IDLE, PREPARED or COMPLETE")
            if self._monitor is not None and self._monitor.is_alive():
                raise ElectrochemistryError("Previous completion monitor is still running")
            validate(request.sequence, request.limits)
            with self._pending_lock:
                if self._pending:
                    raise ElectrochemistryError("Collect pending data before preparing another run")
            self._backend.prepare(request)
            self._confirmed(DeviceState.PREPARED)
            self._equipment = self._backend.equipment()
            self._request = request
            self._kicked = False
            self._completion = None
            self._outcome, self._outcome_detail = None, ""
            self._running_time = None
            self._halt.clear()

        return self._status(operation)

    def kickoff(self) -> Status:
        def operation() -> None:
            if not self._is_staged or self._request is None or self._kicked:
                raise ElectrochemistryError("Kickoff requires a prepared staged Flyer")
            self._kickoff_time, self._kickoff_epoch = time.monotonic(), time.time()
            self._backend.kickoff()
            self._kicked = True
            state, output = self._backend.inspect()
            if state == DeviceState.RUNNING:
                self._running_time = time.monotonic()
            elif state == DeviceState.WAITING_START:
                if output is not False:
                    raise ShutdownUnconfirmed("Waiting for START with output OFF unconfirmed")
            elif state != DeviceState.COMPLETE or output is not False:
                raise ElectrochemistryError(f"Unexpected kickoff state: {state}, {output}")

        return self._status(operation)

    def _append(self, poll: SequencePoll) -> None:
        origin = self._backend.time_origin_epoch_s()
        # A pre-START abort has no physical time origin; use the host kickoff envelope.
        origin = self._kickoff_epoch if origin is None else origin
        events: list[PartialEvent] = []
        for i, t_s in enumerate(poll.readout["t_s"]):
            data = {key: poll.readout[key][i].item() for key in READOUT_FIELDS}
            timestamp = origin + float(t_s)
            events.append(
                {"time": timestamp, "data": data, "timestamps": dict.fromkeys(data, timestamp)}
            )
        for event in poll.events:
            data = dict(zip(EVENT_FIELDS, event, strict=True))
            timestamp = origin + event[0]
            events.append(
                {"time": timestamp, "data": data, "timestamps": dict.fromkeys(data, timestamp)}
            )
        with self._pending_lock:
            self._pending.extend(events)

    def _poll_once(self) -> SequencePoll:
        # Lock only one bounded poll at a time so abort can interleave a long drain.
        with self._operation:
            poll = self._backend.poll(self._chunk_records)
            self._append(poll)
            return poll

    def _finish(self, poll: SequencePoll) -> None:
        assert self._completion is not None
        if self._completion.done:
            return
        if poll.outcome in ("completed", "stopped_cutoff"):
            self._confirmed(DeviceState.COMPLETE)
            self._outcome, self._outcome_detail = poll.outcome, poll.detail
            self._completion.set_finished()
        else:
            self._confirmed()
            self._outcome, self._outcome_detail = poll.outcome, poll.detail
            self._completion.set_exception(AcquisitionAborted(poll.detail or str(poll.outcome)))
        self._halt.set()

    def _watch(self) -> None:
        try:
            while not self._halt.is_set():
                while not self._halt.is_set():
                    poll = self._poll_once()
                    with self._operation:
                        if self._halt.is_set():
                            return
                        if (
                            poll.outcome is not None
                            and len(poll.readout["t_s"]) < self._chunk_records
                        ):
                            self._finish(poll)
                            return
                    if len(poll.readout["t_s"]) < self._chunk_records:
                        break
                with self._operation:
                    if self._halt.is_set():
                        return
                    state, _ = self._backend.inspect()
                    now = time.monotonic()
                    if state == DeviceState.WAITING_START:
                        expired = (
                            self._start_timeout is not None
                            and now - self._kickoff_time >= self._start_timeout
                        )
                    else:
                        if self._running_time is None:
                            self._running_time = now
                        assert self._request is not None
                        bound = nominal_duration_s(self._request.sequence) + self._margin
                        expired = now - self._running_time >= bound
                if expired:
                    self.abort("host_completion_timeout").wait(timeout=5)
                    return
                self._halt.wait(self._poll_period)
        except Exception as exc:
            # A read/inspection failure must still attempt shutdown and retain what is readable.
            try:
                self.abort(f"monitor_error: {exc}").wait(timeout=5)
            except Exception:
                pass
            with self._operation:
                if self._completion is not None and not self._completion.done:
                    self._outcome, self._outcome_detail = "error", str(exc)
                    self._completion.set_exception(exc)
                self._halt.set()

    def complete(self) -> Status:
        with self._operation:
            if self._completion is not None:
                return self._completion
            self._completion = Status(obj=self)
            if not self._kicked:
                self._completion.set_exception(ElectrochemistryError("Complete requires kickoff"))
                return self._completion
            self._monitor = threading.Thread(
                target=self._watch, daemon=True, name=f"{self.name}-poll"
            )
            self._monitor.start()
            return self._completion

    def abort(self, reason: str = "programmatic") -> Status:
        def operation() -> None:
            state, _ = self._backend.inspect()
            if state not in _ACTIVE:
                return
            self._backend.abort(reason)
            self._confirmed(DeviceState.ABORTED)
            self._halt.set()
            self._outcome, self._outcome_detail = "aborted", reason
            try:
                while True:
                    poll = self._backend.poll(self._chunk_records)
                    self._append(poll)
                    if len(poll.readout["t_s"]) < self._chunk_records:
                        break
            finally:
                if self._completion is None:
                    self._completion = Status(obj=self)
                if not self._completion.done:
                    self._completion.set_exception(AcquisitionAborted(reason))

        return self._status(operation)

    def stop(self, *, success: bool = False) -> None:
        self.abort("bluesky_stop").wait(timeout=5)

    def pause(self) -> None:
        self.stop()

    def resume(self) -> None:
        raise UnsupportedCapabilityError("Prepare a new sequence after interruption")

    def archive_retained(self, directory: Path) -> Path:
        """Save the instrument's unread records of an earlier run (between runs only)."""
        with self._operation:
            if self._is_staged:
                raise ElectrochemistryError("Archive between runs, not during one")
            return archive_retained(self._backend, directory)

    def recover(self) -> Status:
        def operation() -> None:
            self._backend.recover()
            self._confirmed(DeviceState.IDLE)

        return self._status(operation)

    def collect(self) -> Iterator[PartialEvent]:
        if not self._kicked:
            raise ElectrochemistryError("Collect requires kickoff")
        with self._pending_lock:
            events, self._pending = self._pending, []
        yield from events

    def describe_collect(self) -> dict[str, dict[str, DataKey]]:
        def key(field: str, dtype: str, units: str = "") -> DataKey:
            return {
                "source": f"sequence:{field}",
                "dtype": dtype,  # type: ignore[typeddict-item]
                "shape": [],
                "units": units,
            }

        readout = {
            field: key(
                field,
                "number" if index < 3 else "integer",
                ("s", "V", "A")[index] if index < 3 else "",
            )
            for index, field in enumerate(READOUT_FIELDS)
        }
        events = {
            field: key(
                field,
                "number" if index == 0 else "integer" if index < 3 else "string",
                "s" if index == 0 else "",
            )
            for index, field in enumerate(EVENT_FIELDS)
        }
        return {"readout": readout, "sequence_events": events}

    def read_configuration(self) -> dict[str, Any]:
        return {
            f"{self.name}_{key}": {"value": value, "timestamp": time.time()}
            for key, value in {
                "backend": type(self._backend).__name__,
                "poll_period_s": self._poll_period,
                "chunk_records": self._chunk_records,
            }.items()
        }

    def describe_configuration(self) -> dict[str, DataKey]:
        return {
            key: {
                "source": f"sequence:{key}",
                "dtype": "string"
                if isinstance(value["value"], str)
                else "integer"
                if isinstance(value["value"], int)
                else "number",
                "shape": [],
            }
            for key, value in self.read_configuration().items()
        }

    def trigger(self) -> Status:
        return self._status(lambda: None)

    def read(self) -> dict[str, Any]:
        return {}

    def describe(self) -> dict[str, DataKey]:
        return {}
