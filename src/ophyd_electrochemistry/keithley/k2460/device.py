"""Classic-ophyd Flyer for a finite, backend-owned electrochemistry acquisition.

The first operational slice deliberately consumes the hardware-neutral M5
``RetainedBuffer`` contract. The independent simulator satisfies this backend
today. A target adapter must not be added until the 2460 buffer timestamp can be
mapped to actual START and measurement apertures without inventing precision.
"""

# mypy: disable-error-code="attr-defined,no-untyped-call,misc"

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from typing import Any, Protocol

from event_model.documents import DataKey, PartialEvent
from ophyd.device import Device
from ophyd.status import Status

from ...acquisition import AcquisitionRequest, StartMode
from ...exceptions import (
    AcquisitionAborted,
    ElectrochemistryError,
    RetainedDataError,
    ShutdownUnconfirmed,
    UnsupportedCapabilityError,
    ValidationError,
)
from ...measurement import MeasurementRecord, RetainedBuffer, mapping_index, source_setpoint_unit
from ...protocols import GalvanostaticHold
from ...state import DeviceState
from ...validation import positive, text


class AcquisitionBackend(Protocol):
    """Typed operations required by the Flyer; no raw command/string escape hatch."""

    def connect(self) -> None: ...

    def close(self) -> None: ...

    def inspect(self) -> tuple[DeviceState, bool | None]: ...

    def prepare(self, request: AcquisitionRequest, *, acquisition_id: str) -> object: ...

    def kickoff(self) -> None: ...

    def completion(self) -> object | None: ...

    def abort(self, *, reason: str = "programmatic") -> object | None: ...

    def recover(self) -> None: ...

    def retained_buffer(self) -> RetainedBuffer: ...

    def export_retained_data(self, destination: str) -> str: ...

    def discard_retained_data(self, *, acquisition_id: str, reason: str) -> None: ...


_ACTIVE_STATES = {
    DeviceState.PREPARING,
    DeviceState.PREPARED,
    DeviceState.ARMING,
    DeviceState.WAITING_START,
    DeviceState.RUNNING,
    DeviceState.ABORTING,
}
_TERMINAL_STATES = {DeviceState.COMPLETE, DeviceState.ABORTED, DeviceState.ERROR}


def _finished_status(owner: object) -> Status:
    status = Status(obj=owner)
    status.set_finished()
    return status


def _failed_status(owner: object, exc: Exception) -> Status:
    status = Status(obj=owner)
    status.set_exception(exc)
    return status


class Keithley2460Device(Device):
    """Finite Flyer lifecycle over a typed acquisition backend.

    This initial M6 slice supports :class:`GalvanostaticHold` by default. It is
    operational with the independent simulator backend, but is intentionally not
    wired to ``M4RuntimeController`` while the target timestamp/aperture mapping
    remains unresolved.
    """

    _stream_name = "electrochemistry"

    def __init__(
        self,
        backend: AcquisitionBackend,
        *,
        name: str,
        poll_period_s: float = 0.01,
        completion_timeout_s: float = 120.0,
        shutdown_timeout_s: float = 5.0,
        acquisition_id_factory: Callable[[], str] | None = None,
        supported_program_types: tuple[type[Any], ...] = (GalvanostaticHold,),
        parent: Device | None = None,
    ) -> None:
        super().__init__("", name=name, parent=parent)
        if not supported_program_types or not all(
            isinstance(program_type, type) for program_type in supported_program_types
        ):
            raise ValidationError("supported_program_types must be a nonempty tuple of types")
        self._backend = backend
        self._poll_period_s = positive(poll_period_s, "poll_period_s")
        self._completion_timeout_s = positive(completion_timeout_s, "completion_timeout_s")
        self._shutdown_timeout_s = positive(shutdown_timeout_s, "shutdown_timeout_s")
        self._acquisition_id_factory = acquisition_id_factory or (lambda: str(uuid.uuid4()))
        self._supported_program_types = supported_program_types
        self._lock = threading.RLock()
        self._operation_lock = threading.Lock()
        self._state = DeviceState.IDLE
        self._output: bool | None = None
        self._is_staged = False
        self._request: AcquisitionRequest | None = None
        self._acquisition_id: str | None = None
        self._completion_status: Status | None = None
        self._completion_monitor_started = False
        self._retained: RetainedBuffer | None = None
        self._emitted = 0
        self._snapshot: MeasurementRecord | None = None
        self._snapshot_timestamp: float | None = None

    @property
    def state(self) -> DeviceState:
        with self._lock:
            return self._state

    @property
    def output_enabled(self) -> bool | None:
        with self._lock:
            return self._output

    @property
    def acquisition_id(self) -> str | None:
        with self._lock:
            return self._acquisition_id

    def _set_observation(self, state: DeviceState, output: bool | None) -> None:
        if not isinstance(state, DeviceState) or output not in (True, False, None):
            raise ValidationError("Backend returned an invalid state/output observation")
        with self._lock:
            self._state = state
            self._output = output

    def _inspect(self) -> tuple[DeviceState, bool | None]:
        state, output = self._backend.inspect()
        self._set_observation(state, output)
        return state, output

    def _inspect_after_error(self) -> None:
        try:
            self._inspect()
        except Exception:
            self._set_observation(DeviceState.ERROR, None)

    @staticmethod
    def _finish(status: Status, exc: Exception | None = None) -> None:
        if status.done:
            return
        if exc is None:
            status.set_finished()
        else:
            status.set_exception(exc)

    def _start_worker(self, status: Status, action: Callable[[], None]) -> None:
        def run() -> None:
            try:
                with self._operation_lock:
                    action()
            except Exception as exc:
                self._inspect_after_error()
                self._finish(status, exc)
            else:
                self._finish(status)

        threading.Thread(target=run, name=f"{self.name}-ophyd-action", daemon=True).start()

    def stage(self) -> list[object]:
        with self._operation_lock:
            with self._lock:
                if self._is_staged:
                    return [self]
            self._backend.connect()
            state, output = self._inspect()
            if output is not False:
                raise ShutdownUnconfirmed("Stage requires confirmed output OFF")
            if state in _ACTIVE_STATES:
                raise ElectrochemistryError(f"Cannot stage an active backend in {state.value}")
            with self._lock:
                self._is_staged = True
        return [self]

    def unstage(self) -> list[object]:
        with self._lock:
            if not self._is_staged:
                return [self]
            active = self._state in _ACTIVE_STATES
        if active:
            self.stop(success=False)
        with self._operation_lock:
            _, output = self._inspect()
            if output is not False:
                raise ShutdownUnconfirmed("Unstage requires confirmed output OFF")
            self._backend.close()
            with self._lock:
                self._is_staged = False
        return [self]

    def prepare(self, value: AcquisitionRequest) -> Status:
        if not isinstance(value, AcquisitionRequest):
            return _failed_status(self, ValidationError("prepare requires AcquisitionRequest"))
        if not isinstance(value.program, self._supported_program_types):
            names = ", ".join(kind.__name__ for kind in self._supported_program_types)
            return _failed_status(
                self,
                UnsupportedCapabilityError(f"This backend slice supports only: {names}"),
            )
        with self._lock:
            if not self._is_staged:
                return _failed_status(self, ElectrochemistryError("Device must be staged"))
            if self._state not in (DeviceState.IDLE, DeviceState.COMPLETE):
                return _failed_status(
                    self, ElectrochemistryError("Prepare requires IDLE or disposed COMPLETE")
                )
            acquisition_id = text(self._acquisition_id_factory(), "acquisition_id")
            status = Status(obj=self)
            self._state = DeviceState.PREPARING
            self._request = value
            self._acquisition_id = acquisition_id
            self._completion_status = Status(obj=self)
            self._completion_monitor_started = False
            self._retained = None
            self._emitted = 0
            self._snapshot = None
            self._snapshot_timestamp = None

        def action() -> None:
            self._backend.prepare(value, acquisition_id=acquisition_id)
            state, output = self._inspect()
            if state != DeviceState.PREPARED or output is not False:
                raise ShutdownUnconfirmed("Prepare did not confirm PREPARED with output OFF")

        self._start_worker(status, action)
        return status

    def kickoff(self) -> Status:
        with self._lock:
            if self._state != DeviceState.PREPARED or self._request is None:
                return _failed_status(self, ElectrochemistryError("Kickoff requires PREPARED"))
            request = self._request
            status = Status(obj=self)
            self._state = DeviceState.ARMING

        def action() -> None:
            self._backend.kickoff()
            state, output = self._inspect()
            allowed = (
                {DeviceState.WAITING_START}
                if request.start_mode == StartMode.EXTERNAL_TRIGGER
                else {DeviceState.RUNNING, DeviceState.COMPLETE}
            )
            if state not in allowed:
                raise ElectrochemistryError(f"Unexpected state after kickoff: {state.value}")
            if state == DeviceState.WAITING_START and output is not False:
                raise ShutdownUnconfirmed("External START wait did not keep output OFF")
            if state == DeviceState.COMPLETE and output is not False:
                raise ShutdownUnconfirmed("Completed acquisition did not confirm output OFF")

        self._start_worker(status, action)
        return status

    def complete(self) -> Status:
        with self._lock:
            if self._completion_status is None or self._state not in (
                DeviceState.WAITING_START,
                DeviceState.RUNNING,
                DeviceState.COMPLETE,
                DeviceState.ABORTED,
                DeviceState.ERROR,
            ):
                return _failed_status(
                    self, ElectrochemistryError("Complete requires a kicked-off acquisition")
                )
            status = self._completion_status
            if self._completion_monitor_started or status.done:
                return status
            self._completion_monitor_started = True

        def monitor() -> None:
            deadline = time.monotonic() + self._completion_timeout_s
            try:
                while True:
                    outcome = self._backend.completion()
                    state, output = self._inspect()
                    if outcome is not None:
                        if state != DeviceState.COMPLETE or output is not False:
                            raise ShutdownUnconfirmed(
                                "Normal completion did not confirm COMPLETE with output OFF"
                            )
                        self._finish(status)
                        return
                    if time.monotonic() >= deadline:
                        try:
                            self._backend.abort(reason="host_completion_timeout")
                            self._inspect()
                        except Exception as exc:
                            raise ShutdownUnconfirmed(
                                "Completion timed out and shutdown was not confirmed"
                            ) from exc
                        raise AcquisitionAborted("host_completion_timeout")
                    time.sleep(self._poll_period_s)
            except Exception as exc:
                self._inspect_after_error()
                self._finish(status, exc)

        threading.Thread(
            target=monitor, name=f"{self.name}-completion-monitor", daemon=True
        ).start()
        return status

    def abort(self, *, reason: str = "programmatic") -> Status:
        text(reason, "abort reason")
        with self._lock:
            if self._state in (DeviceState.IDLE, DeviceState.COMPLETE, DeviceState.ABORTED):
                if self._state == DeviceState.ABORTED and self._completion_status is not None:
                    self._finish(self._completion_status, AcquisitionAborted(reason))
                return _finished_status(self)
            status = Status(obj=self)
            self._state = DeviceState.ABORTING

        def action() -> None:
            self._backend.abort(reason=reason)
            state, output = self._inspect()
            if state != DeviceState.ABORTED or output is not False:
                raise ShutdownUnconfirmed("Abort did not confirm ABORTED with output OFF")
            with self._lock:
                completion = self._completion_status
            if completion is not None:
                self._finish(completion, AcquisitionAborted(reason))

        self._start_worker(status, action)
        return status

    def recover(self) -> Status:
        with self._lock:
            if self._state == DeviceState.IDLE:
                return _finished_status(self)
            if self._state not in (DeviceState.ABORTED, DeviceState.ERROR):
                return _failed_status(
                    self, ElectrochemistryError("Recovery requires ABORTED or ERROR")
                )
            status = Status(obj=self)
            self._state = DeviceState.RECOVERING

        def action() -> None:
            self._backend.recover()
            state, output = self._inspect()
            if state != DeviceState.IDLE or output is not False:
                raise ShutdownUnconfirmed("Recovery did not confirm IDLE with output OFF")

        self._start_worker(status, action)
        return status

    def stop(self, *, success: bool = False) -> None:
        del success  # An interrupted acquisition is never relabelled successful.
        with self._lock:
            state = self._state
        if state in (DeviceState.IDLE, DeviceState.COMPLETE, DeviceState.ABORTED):
            return
        status = self.abort(reason="bluesky_stop")
        status.wait(timeout=self._shutdown_timeout_s)

    def pause(self) -> None:
        self.stop(success=False)

    def resume(self) -> None:
        raise UnsupportedCapabilityError(
            "Interrupted acquisitions are not replayed; prepare a new request"
        )

    def _ensure_retained(self) -> RetainedBuffer:
        with self._lock:
            if self._retained is not None:
                return self._retained
            if self._state not in _TERMINAL_STATES:
                raise RetainedDataError("Collection requires a terminal acquisition")
        retained = self._backend.retained_buffer()
        with self._lock:
            if retained.acquisition_id != self._acquisition_id:
                raise RetainedDataError("Backend returned the wrong retained acquisition")
            self._retained = retained
            return retained

    def trigger(self) -> Status:
        try:
            retained = self._ensure_retained()
            if not retained.records:
                raise RetainedDataError("No complete buffered sample exists")
            with self._lock:
                self._snapshot = retained.records[-1]
                self._snapshot_timestamp = self._event_time(retained, self._snapshot)
            return _finished_status(self)
        except Exception as exc:
            return _failed_status(self, exc)

    def _field_name(self, suffix: str) -> str:
        return f"{self.name}_{suffix}"

    def _source(self, suffix: str) -> str:
        backend = type(self._backend)
        return f"ophyd-electrochemistry://{backend.__module__}.{backend.__qualname__}/{suffix}"

    def _descriptor(self, retained: RetainedBuffer) -> dict[str, DataKey]:
        unit = source_setpoint_unit(retained.schema.source_function)
        fields: tuple[tuple[str, str, str | None], ...] = (
            ("sample_index", "integer", None),
            ("time_relative", "number", "s"),
            ("instrument_timestamp", "number", "s"),
            ("aperture_start", "number", "s"),
            ("aperture_end", "number", "s"),
            ("available", "number", "s"),
            ("voltage", "number", "V"),
            ("current", "number", "A"),
            ("source_function", "string", None),
            ("source_setpoint", "number", unit),
            ("status_bits", "integer", None),
            ("mapping_quality", "string", None),
            ("first_logical_point", "integer", None),
            ("last_logical_point", "integer", None),
            ("repeat_index", "integer", None),
            ("point_index", "integer", None),
            ("cycle_index", "integer", None),
            ("segment_index", "integer", None),
        )
        result: dict[str, DataKey] = {}
        for suffix, dtype, units in fields:
            key: DataKey = {
                "source": self._source(suffix),
                "dtype": dtype,  # type: ignore[typeddict-item]
                "shape": [],
            }
            if units is not None:
                key["units"] = units
            result[self._field_name(suffix)] = key
        return result

    def _record_data(self, record: MeasurementRecord) -> dict[str, int | float | str]:
        return {
            self._field_name("sample_index"): record.sample_index,
            self._field_name("time_relative"): record.time_relative_s,
            self._field_name("instrument_timestamp"): record.instrument_timestamp_s,
            self._field_name("aperture_start"): record.aperture_start_relative_s,
            self._field_name("aperture_end"): record.aperture_end_relative_s,
            self._field_name("available"): record.available_relative_s,
            self._field_name("voltage"): record.voltage_v,
            self._field_name("current"): record.current_a,
            self._field_name("source_function"): record.source_function,
            self._field_name("source_setpoint"): record.source_setpoint,
            self._field_name("status_bits"): record.status_bits,
            self._field_name("mapping_quality"): record.mapping_quality.value,
            self._field_name("first_logical_point"): mapping_index(record.first_logical_point),
            self._field_name("last_logical_point"): mapping_index(record.last_logical_point),
            self._field_name("repeat_index"): mapping_index(record.repeat_index),
            self._field_name("point_index"): mapping_index(record.point_index),
            self._field_name("cycle_index"): mapping_index(record.cycle_index),
            self._field_name("segment_index"): mapping_index(record.segment_index),
        }

    @staticmethod
    def _event_time(retained: RetainedBuffer, record: MeasurementRecord) -> float:
        mapping = retained.schema.clock_mapping
        if mapping is not None:
            return mapping.to_epoch(record.instrument_timestamp_s)
        # Physical time remains in explicit data fields. Host emission time is
        # used only for the required Bluesky event envelope.
        return time.time()

    def describe_collect(self) -> dict[str, dict[str, DataKey]]:
        retained = self._ensure_retained()
        return {self._stream_name: self._descriptor(retained)}

    def collect(self) -> Iterator[PartialEvent]:
        retained = self._ensure_retained()
        while True:
            with self._lock:
                if self._emitted >= len(retained.records):
                    return
                record = retained.records[self._emitted]
                self._emitted += 1
            timestamp = self._event_time(retained, record)
            data = self._record_data(record)
            yield {
                "time": timestamp,
                "data": data,
                "timestamps": dict.fromkeys(data, timestamp),
            }

    def describe(self) -> dict[str, DataKey]:
        return self._descriptor(self._ensure_retained())

    def read(self) -> dict[str, dict[str, int | float | str]]:
        with self._lock:
            record = self._snapshot
            timestamp = self._snapshot_timestamp
        if record is None or timestamp is None:
            raise ElectrochemistryError("trigger() must latch a buffered sample before read()")
        return {
            key: {"value": value, "timestamp": timestamp}
            for key, value in self._record_data(record).items()
        }

    def _configuration_values(self) -> dict[str, int | float | str]:
        with self._lock:
            start_mode = "unprepared" if self._request is None else self._request.start_mode.value
            acquisition_id = self._acquisition_id or "none"
        backend = type(self._backend)
        return {
            self._field_name("backend"): f"{backend.__module__}.{backend.__qualname__}",
            self._field_name("poll_period"): self._poll_period_s,
            self._field_name("completion_timeout"): self._completion_timeout_s,
            self._field_name("start_mode"): start_mode,
            self._field_name("acquisition_id"): acquisition_id,
        }

    def read_configuration(self) -> dict[str, dict[str, int | float | str]]:
        timestamp = time.time()
        return {
            key: {"value": value, "timestamp": timestamp}
            for key, value in self._configuration_values().items()
        }

    def describe_configuration(self) -> dict[str, DataKey]:
        result: dict[str, DataKey] = {}
        for key, value in self._configuration_values().items():
            suffix = key.removeprefix(f"{self.name}_")
            dtype = (
                "number"
                if isinstance(value, float)
                else "integer"
                if isinstance(value, int)
                else "string"
            )
            result[key] = {
                "source": self._source(f"configuration/{suffix}"),
                "dtype": dtype,  # type: ignore[typeddict-item]
                "shape": [],
            }
            if suffix in ("poll_period", "completion_timeout"):
                result[key]["units"] = "s"
        return result

    def configure(
        self, values: Mapping[str, Any]
    ) -> tuple[dict[str, dict[str, int | float | str]], dict[str, dict[str, int | float | str]]]:
        if values:
            raise UnsupportedCapabilityError(
                "This M6 slice has no runtime-configurable fields; use constructor configuration"
            )
        current = self.read_configuration()
        return current, current

    def export_retained_data(self, destination: str) -> None:
        text(destination, "destination")
        self._backend.export_retained_data(destination)

    def discard_retained_data(self, *, acquisition_id: str, reason: str) -> None:
        text(acquisition_id, "acquisition_id")
        text(reason, "disposition reason")
        with self._lock:
            if acquisition_id != self._acquisition_id:
                raise RetainedDataError("Discard requires the exact acquisition ID")
            if self._state not in _TERMINAL_STATES:
                raise RetainedDataError("Discard requires a terminal acquisition")
        self._backend.discard_retained_data(acquisition_id=acquisition_id, reason=reason)
        with self._lock:
            self._retained = None
            self._emitted = 0
            self._snapshot = None
            self._snapshot_timestamp = None


__all__ = ["AcquisitionBackend", "Keithley2460Device"]
