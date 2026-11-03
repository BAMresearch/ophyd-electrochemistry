"""Serialized resistor-commissioning backend for the packaged timed-hold candidate.

This adapter never installs a runtime or changes command language. Its timing
envelope is a software watchdog input pending bench commissioning, not measured
output-off latency, aperture placement, or a hardware-readiness claim.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Protocol, TypeVar

from ...acquisition import AcquisitionRequest, PreparedAcquisition, StartMode
from ...exceptions import (
    AcquisitionAborted,
    ElectrochemistryError,
    IncompatibleInstrumentError,
    RetainedDataError,
    ShutdownUnconfirmed,
    TransportError,
    TransportProtocolError,
    UnsupportedCapabilityError,
    ValidationError,
)
from ...measurement import MeasurementSchema, RecordChunk, RetainedBuffer, RetainedBufferMetadata
from ...protocols import GalvanostaticHold
from ...serialization import canonical_json, canonical_request_json, request_sha256, sha256_json
from ...state import DeviceState
from ...validation import integer, number, positive, text
from .config import Keithley2460Config
from .measurement_profiles import K2460MeasurementProfile, resolve_measurement_profile
from .projection import project_current_hold_buffer
from .runtime import (
    RuntimeBufferInfo,
    RuntimeState,
    RuntimeStatus,
    RuntimeTransport,
    _parse_nonnegative_integer,
)
from .timed_runtime import TimedHoldPlan, TimedHoldRuntimeController, timed_packaged_runtime
from .transport import VisaTransportConfig, parse_command_language, parse_identity

_T = TypeVar("_T")
_STOPPED = {
    RuntimeState.IDLE,
    RuntimeState.COMPLETE,
    RuntimeState.START_TIMEOUT,
    RuntimeState.ABORTED,
    RuntimeState.ERROR,
}
_STATE_MAP = {
    RuntimeState.IDLE: DeviceState.IDLE,
    RuntimeState.PREPARED: DeviceState.PREPARED,
    RuntimeState.WAITING_START: DeviceState.WAITING_START,
    RuntimeState.RUNNING: DeviceState.RUNNING,
    RuntimeState.COMPLETE: DeviceState.COMPLETE,
    RuntimeState.START_TIMEOUT: DeviceState.ERROR,
    RuntimeState.ABORTED: DeviceState.ABORTED,
    RuntimeState.ERROR: DeviceState.ERROR,
}
_ARCHIVE_SCHEMA = "ophyd-electrochemistry/k2460-timed-hold-export-v1"


class BackendTransport(RuntimeTransport, Protocol):
    """One connection owned exclusively by this backend and its controller."""

    config: VisaTransportConfig

    def connect(self) -> None: ...

    def close(self) -> None: ...


class Keithley2460Backend:
    """Finite timed current holds on a separately commissioned resistive setup.

    ``commissioning_resistor=True`` is an explicit scope opt-in, not confirmation
    of physical wiring or permission to energize hardware. The candidate is
    restricted to reviewed firmware 1.7.16a and the exact packaged timed runtime.
    Runtime installation and fresh physical safety checks are separate steps.
    """

    hardware_ready = False

    def __init__(
        self,
        transport: BackendTransport,
        config: Keithley2460Config,
        *,
        source_range_a: float,
        measurement_profile: K2460MeasurementProfile = K2460MeasurementProfile.NPLC_1_2V,
        expected_serial_number: str | None = None,
        commissioning_resistor: bool = False,
    ) -> None:
        if commissioning_resistor is not True:
            raise UnsupportedCapabilityError("Timed backend requires commissioning_resistor=True")
        if not isinstance(config, Keithley2460Config):
            raise ValidationError("config must be Keithley2460Config")
        if not isinstance(transport.config, VisaTransportConfig):
            raise ValidationError("Backend transport requires explicit VISA session limits")
        if transport.config.resource_name != config.visa_resource:
            raise ValidationError(
                "Transport resource differs from the reviewed device configuration"
            )
        self.config = config
        self._session_config = transport.config
        self._artifact = timed_packaged_runtime()
        self._transport = transport
        self._controller = TimedHoldRuntimeController(transport, config)
        self._source_range_a = positive(source_range_a, "source_range_a")
        resolve_measurement_profile(measurement_profile)
        self._profile = measurement_profile
        self._serial = (
            None
            if expected_serial_number is None
            else text(expected_serial_number, "expected_serial_number")
        )
        self._lock = threading.RLock()
        self._connected = False
        self._identity: str | None = None
        self._line_frequency_hz: int | None = None
        self._state = DeviceState.IDLE
        self._output: bool | None = None
        self._needs_recovery = False
        self._shutdown_attempted = False
        self._failure: str | None = None
        self._cause: str | None = None
        self._request: AcquisitionRequest | None = None
        self._plan: TimedHoldPlan | None = None
        self._prepared: PreparedAcquisition | None = None
        self._acquisition_id: str | None = None
        self._frozen: RetainedBuffer | None = None
        self._extent: RuntimeBufferInfo | None = None

    def _io(self, operation: Callable[[], _T]) -> _T:
        try:
            return operation()
        except TransportError:
            self._needs_recovery = True
            self._state, self._output = DeviceState.ERROR, None
            if self._request is not None and self._cause is None:
                self._failure = self._failure or "transport_failure"
            raise

    def _require_connected(self) -> None:
        if not self._connected:
            raise ElectrochemistryError("Backend must be connected")

    def _verify_connection(self, *, allow_prepared: bool = False) -> RuntimeStatus:
        identity = parse_identity(self._io(lambda: self._transport.query("*IDN?")))
        if identity.firmware_version != "1.7.16a":
            raise IncompatibleInstrumentError("Timed candidate requires reviewed firmware 1.7.16a")
        if self._serial is not None and identity.serial_number != self._serial:
            raise IncompatibleInstrumentError("Instrument serial number differs from configuration")
        language = parse_command_language(self._io(lambda: self._transport.query("*LANG?")))
        if language != "TSP" or self._transport.command_language != "TSP":
            raise IncompatibleInstrumentError("Timed candidate requires existing TSP language")
        frequency_response = self._io(lambda: self._transport.query("print(localnode.linefreq)"))
        try:
            frequency = number(float(frequency_response), "instrument line frequency")
        except (ValueError, ValidationError) as exc:
            raise TransportProtocolError("Invalid instrument line-frequency readback") from exc
        if frequency not in (50, 60):
            raise IncompatibleInstrumentError("Timed candidate requires 50 or 60 Hz line frequency")
        artifact = self._artifact
        build = self._io(lambda: self._transport.query("print(oe_m4_runtime_build)"))
        present = self._io(lambda: self._transport.query(f"print({artifact.script_name} ~= nil)"))
        if build != artifact.build or present != "true":
            raise IncompatibleInstrumentError(
                "Exact packaged timed runtime must already be installed"
            )
        status = self._io(self._controller.status)
        self._require_stopped_off(status, allow_prepared=allow_prepared)
        self._identity = identity.raw
        self._line_frequency_hz = int(frequency)
        return status

    @staticmethod
    def _require_stopped_off(status: RuntimeStatus, *, allow_prepared: bool = False) -> None:
        if (
            status.output_enabled
            or (
                status.state not in _STOPPED
                and not (allow_prepared and status.state == RuntimeState.PREPARED)
            )
            or status.model_state
            not in {
                "trigger.STATE_IDLE",
                "trigger.STATE_ABORTED",
                "trigger.STATE_FAILED",
                "trigger.STATE_EMPTY",
                "trigger.STATE_BUILDING",
            }
        ):
            raise ShutdownUnconfirmed(
                "Operation requires a stopped runtime and confirmed output OFF"
            )

    def connect(self) -> None:
        with self._lock:
            self._io(self._transport.connect)
            status = self._verify_connection()
            self._connected = True
            self._observe(status)

    def close(self) -> None:
        with self._lock:
            if not self._connected:
                return
            self._require_stopped_off(self._io(self._controller.status))
            self._io(self._transport.close)
            self._connected = False

    def _observe(self, status: RuntimeStatus) -> tuple[DeviceState, bool | None]:
        if self._request is not None and status.state in _STOPPED - {RuntimeState.IDLE}:
            first_terminal_observation = self._cause is None
            if first_terminal_observation:
                self._cause = self._io(self._controller.terminal_cause)
            if status.state != RuntimeState.COMPLETE:
                self._failure = self._failure or self._cause or status.state.value
            elif self._cause != "normal_completion":
                self._failure = self._failure or self._cause or "unresolved_terminal_cause"
            if (
                first_terminal_observation
                and status.state == RuntimeState.COMPLETE
                and (status.timer_overrun or status.start_overrun)
            ):
                self._failure = self._failure or "trigger_overrun"
            if status.model_state == "trigger.STATE_FAILED":
                self._failure = self._failure or "runtime_model_failed"
        if self._needs_recovery:
            self._state, self._output = DeviceState.ERROR, None
        else:
            self._state = _STATE_MAP[status.state]
            self._output = status.output_enabled
            if status.state == RuntimeState.COMPLETE and self._failure is not None:
                self._state = DeviceState.ERROR
        return self._state, self._output

    def inspect(self) -> tuple[DeviceState, bool | None]:
        with self._lock:
            self._require_connected()
            return self._observe(self._io(self._controller.status))

    def start_edge(self) -> str:
        return self.config.io.start_edge

    def _require_no_unread_errors(self) -> None:
        count = self._io(
            lambda: _parse_nonnegative_integer(
                self._transport.query("print(eventlog.getcount(eventlog.SEV_ERROR))"),
                "unread error count",
            )
        )
        if count:
            raise ElectrochemistryError("Unread instrument errors prevent preparation or arming")

    def configuration_metadata(self) -> dict[str, int | float | str]:
        """Return cached provenance only; never query or mutate an instrument."""
        with self._lock:
            return {
                "hardware_ready": 0,
                "timing_status": "uncommissioned",
                "instrument_identity": self._identity or "unknown",
                "runtime_build": self._artifact.build,
                "runtime_sha256": self._artifact.sha256,
                "measurement_profile": self._profile.value,
                "line_frequency_hz": self._line_frequency_hz or -1,
                "request_json": (
                    canonical_request_json(self._request)
                    if self._request is not None and self._prepared is not None
                    else "unprepared"
                ),
                "plan_json": (
                    canonical_json(self._plan)
                    if self._plan is not None and self._prepared is not None
                    else "unprepared"
                ),
                "config_json": canonical_json(self.config),
                "transport_config_json": canonical_json(self._session_config),
            }

    def prepare(self, request: AcquisitionRequest, *, acquisition_id: str) -> PreparedAcquisition:
        with self._lock:
            self._require_connected()
            if self._needs_recovery:
                raise ShutdownUnconfirmed("Explicit recovery is required before preparation")
            if not isinstance(request, AcquisitionRequest):
                raise ValidationError("prepare requires AcquisitionRequest")
            if not isinstance(request.program, GalvanostaticHold):
                raise UnsupportedCapabilityError("Timed candidate supports only GalvanostaticHold")
            self._require_no_unread_errors()
            text(acquisition_id, "acquisition_id")
            if self._prepared is not None:
                raise RetainedDataError(
                    "Explicit disposition of the previous acquisition is required"
                )
            status = self._io(self._controller.status)
            self._require_stopped_off(status)
            info = self._io(self._controller.buffer_info)
            if info.record_count:
                raise RetainedDataError("Unknown retained records cannot be adopted or overwritten")
            if status.state not in (RuntimeState.IDLE, RuntimeState.COMPLETE):
                raise ElectrochemistryError("Preparation requires IDLE or disposed COMPLETE")
            hold = request.program
            plan = TimedHoldPlan(
                current_a=hold.current_a,
                source_range_a=self._source_range_a,
                voltage_limit_v=hold.voltage_limit_v,
                duration_s=hold.duration_s,
                sample_period_s=hold.sample_period_s,
                lower_cutoff_v=max(
                    self.config.safety.voltage_min_v,
                    hold.lower_cutoff_v
                    if hold.lower_cutoff_v is not None
                    else self.config.safety.voltage_min_v,
                ),
                upper_cutoff_v=min(
                    self.config.safety.voltage_max_v,
                    hold.upper_cutoff_v
                    if hold.upper_cutoff_v is not None
                    else self.config.safety.voltage_max_v,
                ),
                start_mode=request.start_mode,
                measurement_profile=self._profile,
            )
            plan.validate_config(self.config)
            artifact = self._artifact
            digest = sha256_json(
                canonical_json(
                    {
                        "schema": "k2460-timed-hold-plan-v1",
                        "plan": plan,
                        "config": self.config,
                        "runtime_sha256": artifact.sha256,
                    }
                )
            )
            # Bind context before mutation: an uncertain prepare must never be
            # treated as permission to adopt or discard an unrelated buffer.
            self._request, self._plan, self._acquisition_id = request, plan, acquisition_id
            self._failure, self._cause = None, None
            self._shutdown_attempted = False
            prepared_status = self._io(lambda: self._controller.prepare_timed_hold(plan))
            self._require_no_unread_errors()
            if prepared_status.state != RuntimeState.PREPARED or prepared_status.output_enabled:
                self._needs_recovery = True
                self._state, self._output = DeviceState.ERROR, None
                raise ShutdownUnconfirmed("Preparation did not confirm PREPARED/output OFF")
            actual = self._io(self._controller.buffer_info)
            if actual.record_count or actual.capacity_records < plan.measurement_count:
                self._needs_recovery = True
                self._state, self._output = DeviceState.ERROR, None
                raise RetainedDataError(
                    "Prepared runtime buffer has an unexpected extent or capacity"
                )
            if actual.capacity_records > self.config.buffer.capacity_ceiling_records:
                self._needs_recovery = True
                self._state, self._output = DeviceState.ERROR, None
                raise RetainedDataError("Prepared buffer exceeds configured capacity ceiling")
            self._prepared = PreparedAcquisition(
                request_sha256=request_sha256(request),
                program_sha256=digest,
                expected_records=plan.measurement_count,
                capacity_records=actual.capacity_records,
                completion_bound_s=(
                    plan.duration_s
                    + plan.measurement_budget_s
                    + self.config.timing.poll_period_s
                    + self.config.timing.shutdown_timeout_s
                    + (
                        self.config.timing.external_start_timeout_s
                        if request.start_mode == StartMode.EXTERNAL_TRIGGER
                        else 0
                    )
                ),
                # A terminal observation may use status, cause, extent and a
                # final Flyer inspection. Each VISA query has write+read I/O.
                communication_margin_s=4
                * max(
                    self.config.timing.command_timeout_s,
                    2 * self._session_config.io_timeout_ms / 1000,
                ),
            )
            self._extent, self._frozen = None, None
            self._observe(prepared_status)
            return self._prepared

    def kickoff(self) -> None:
        with self._lock:
            self._require_connected()
            if self._needs_recovery:
                raise ShutdownUnconfirmed("Explicit recovery is required before kickoff")
            if self._prepared is None or self._state != DeviceState.PREPARED:
                raise ElectrochemistryError("Kickoff requires a successfully prepared acquisition")
            self._require_no_unread_errors()
            status = self._io(self._controller.arm)
            self._observe(status)
            if status.state in _STOPPED:
                self._shutdown_terminal_if_needed(status)
                if self._failure is not None:
                    raise AcquisitionAborted(self._failure)

    def _shutdown_terminal_if_needed(self, status: RuntimeStatus) -> RuntimeStatus:
        if status.output_enabled:
            # One safe-shutdown action within run cleanup, never a retry of
            # the acquisition mutation or an inspection side effect.
            self._failure = self._failure or "terminal_output_on"
            if not self._shutdown_attempted:
                self._shutdown_attempted = True
                status = self._io(self._controller.abort)
                self._observe(status)
        self._require_stopped_off(status)
        return status

    def completion(self) -> object | None:
        with self._lock:
            self._require_connected()
            if self._prepared is None:
                raise ElectrochemistryError(
                    "Completion requires a successfully prepared acquisition"
                )
            status = self._io(self._controller.status)
            self._observe(status)
            if self._needs_recovery:
                raise ShutdownUnconfirmed(
                    "Explicit recovery is required after uncertain communication"
                )
            if status.state not in _STOPPED:
                return None
            status = self._shutdown_terminal_if_needed(status)
            if self._failure is not None:
                raise AcquisitionAborted(self._failure)
            if status.state != RuntimeState.COMPLETE and self._cause != "normal_completion":
                raise ElectrochemistryError("Acquisition is stopped without a successful outcome")
            info = self._io(self._controller.buffer_info)
            if (
                info.record_count != self._prepared.expected_records
                or info.capacity_records != self._prepared.capacity_records
            ):
                self._failure = "incomplete_or_changed_buffer"
                self._state = DeviceState.ERROR
                raise RetainedDataError("Normal completion has an unexpected retained extent")
            return self._cause or "normal_completion"

    def abort(self, *, reason: str = "programmatic") -> object | None:
        with self._lock:
            self._require_connected()
            text(reason, "abort reason")
            if self._needs_recovery:
                raise ShutdownUnconfirmed("Inspect and explicitly recover uncertain hardware first")
            status = self._io(self._controller.status)
            if status.state == RuntimeState.COMPLETE:
                self._observe(status)
                self._require_stopped_off(status)
                return self._cause
            if status.state == RuntimeState.ABORTED and not status.output_enabled:
                self._observe(status)
                return self._failure
            self._failure = self._failure or reason
            self._cause = self._cause or reason
            self._shutdown_attempted = True
            status = self._io(self._controller.abort)
            self._observe(status)
            self._require_stopped_off(status)
            return self._failure

    def recover(self) -> None:
        with self._lock:
            # Reopen for inspection only. Recovery never uploads, force-safes,
            # resets or retries a possibly delivered acquisition mutation.
            self._io(self._transport.connect)
            status = self._verify_connection(allow_prepared=True)
            self._connected = True
            self._require_stopped_off(status, allow_prepared=True)
            self._observe(status)  # latch failed outcome before runtime recovery
            if self._request is not None and self._cause is None:
                self._failure = self._failure or "recovered_without_terminal_outcome"
                self._cause = self._failure
            recovered = self._io(self._controller.recover)
            if recovered.state != RuntimeState.IDLE or recovered.output_enabled:
                raise ShutdownUnconfirmed("Recovery did not confirm IDLE/output OFF")
            self._needs_recovery = False
            self._observe(recovered)

    def _freeze(self) -> RetainedBuffer:
        self._require_connected()
        if self._needs_recovery:
            raise ShutdownUnconfirmed("Explicit recovery is required before retained-data transfer")
        if self._prepared is None or self._plan is None or self._acquisition_id is None:
            raise RetainedDataError("Unknown retained records cannot be adopted by this backend")
        status = self._io(self._controller.status)
        self._require_stopped_off(status)
        self._observe(status)
        if status.state == RuntimeState.IDLE and self._cause is None:
            raise RetainedDataError(
                "No terminal outcome is associated with the prepared acquisition"
            )
        info = self._io(self._controller.buffer_info)
        if (
            info.capacity_records != self._prepared.capacity_records
            or info.record_count > self._prepared.expected_records
            or (
                info.record_count and (info.start_index != 1 or info.end_index != info.record_count)
            )
        ):
            raise RetainedDataError("Retained buffer extent differs from the prepared acquisition")
        if self._extent is not None and info != self._extent:
            raise RetainedDataError("Frozen retained extent changed; refusing stale cached data")
        if self._frozen is not None:
            return self._frozen
        chunks = []
        offset = 0
        while True:
            chunk = self._io(
                partial(
                    self._controller.read_buffer_chunk,
                    offset=offset,
                    max_records=self.config.buffer.transfer_chunk_records,
                )
            )
            if chunk.offset != offset or chunk.total_records != info.record_count:
                raise RetainedDataError("Transferred chunk differs from the frozen retained extent")
            chunks.append(chunk)
            if chunk.final:
                break
            if not chunk.records:
                raise RetainedDataError("Empty chunk appeared before retained end")
            offset = chunk.next_offset
        self._require_stopped_off(self._io(self._controller.status))
        if self._io(self._controller.buffer_info) != info:
            raise RetainedDataError("Retained extent changed during transfer")
        normal = self._failure is None and self._cause == "normal_completion"
        self._frozen = project_current_hold_buffer(
            chunks=tuple(chunks),
            acquisition_id=self._acquisition_id,
            request_sha256=self._prepared.request_sha256,
            program_sha256=self._prepared.program_sha256,
            capacity_records=info.capacity_records,
            expected_records=self._prepared.expected_records,
            terminal_cause=self._failure or self._cause or status.state.value,
            waveform_complete=normal,
            source_setpoint_a=self._plan.current_a,
        )
        self._extent = info
        return self._frozen

    def retained_schema(self) -> MeasurementSchema:
        with self._lock:
            return self._freeze().schema

    def retained_metadata(self) -> RetainedBufferMetadata:
        with self._lock:
            return self._freeze().metadata

    def read_record_chunk(self, *, offset: int, max_records: int) -> RecordChunk:
        with self._lock:
            integer(max_records, "max_records")
            if max_records > self.config.buffer.transfer_chunk_records:
                raise ValidationError("max_records exceeds configured transfer ceiling")
            return self._freeze().chunk(offset=offset, max_records=max_records)

    def export_retained_data(self, destination: str) -> str:
        with self._lock:
            text(destination, "destination")
            retained = self._freeze()
            payload = {
                "schema": _ARCHIVE_SCHEMA,
                "instrument_identity": self._identity,
                "line_frequency_hz": self._line_frequency_hz,
                "measurement_settings": resolve_measurement_profile(self._profile),
                "runtime": self._artifact,
                "request": self._request,
                "plan": self._plan,
                "config": self.config,
                "transport_config": self._session_config,
                "retained": retained,
            }
            digest = sha256_json(canonical_json(payload))
            encoded = canonical_json({"sha256": digest, "payload": payload})
            with Path(destination).open("x", encoding="utf-8") as stream:
                stream.write(encoded + "\n")
            return digest

    def discard_retained_data(self, *, acquisition_id: str, reason: str) -> None:
        with self._lock:
            text(reason, "disposition reason")
            if self._acquisition_id is None or acquisition_id != self._acquisition_id:
                raise RetainedDataError("Discard requires the exact owned acquisition ID")
            self._freeze()
            info = self._io(lambda: self._controller.discard_buffer(reason=reason))
            if info.record_count:
                raise RetainedDataError("Disposition did not confirm an empty runtime buffer")
            self._request = None
            self._plan = None
            self._prepared = None
            self._acquisition_id = None
            self._frozen, self._extent = None, None
            self._failure, self._cause = None, None


__all__ = ["Keithley2460Backend"]
