"""Independent event scheduler over M1 IR, with a manually advanced local clock.

Never calls compiler sample/point mapping helpers or waveform generators. Values
are synthetic aperture averages of an ideal resistive circuit, not measurements.
"""

import heapq
import json
import math
import threading
from dataclasses import dataclass, replace
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Literal

from ..acquisition import AcquisitionRequest, StartMode
from ..exceptions import (
    AcquisitionAborted,
    ElectrochemistryError,
    RetainedDataError,
    ShutdownUnconfirmed,
    ValidationError,
)
from ..keithley.k2460 import Keithley2460Capabilities, Keithley2460Compiler, Keithley2460Config
from ..keithley.k2460.compiler import CompiledProgram
from ..serialization import canonical_json, sha256_json
from ..state import DeviceState
from ..validation import integer, number, positive, text

SIMULATION_SCHEMA = "ophyd-electrochemistry/simulation-v1"
SIMULATED_COMPLIANCE = 1  # Simulator flag, NOT a Keithley status bit.


@dataclass(frozen=True, kw_only=True)
class FakeCell:
    """Ideal voltage source plus series resistor; explicit synthetic parameters."""

    resistance_ohm: float
    open_circuit_voltage_v: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "resistance_ohm", positive(self.resistance_ohm, "resistance"))
        object.__setattr__(
            self, "open_circuit_voltage_v", number(self.open_circuit_voltage_v, "OCV")
        )

    def response(
        self, function: Literal["current", "voltage"], level: float, compliance: float
    ) -> tuple[float, float, int]:
        if function == "current":
            raw = self.open_circuit_voltage_v + level * self.resistance_ohm
            number(raw, "synthetic unconstrained voltage")
            voltage = min(compliance, max(-compliance, raw))
            current = (voltage - self.open_circuit_voltage_v) / self.resistance_ohm
        else:
            raw = (level - self.open_circuit_voltage_v) / self.resistance_ohm
            number(raw, "synthetic unconstrained current")
            current = min(compliance, max(-compliance, raw))
            voltage = self.open_circuit_voltage_v + current * self.resistance_ohm
        number(voltage, "synthetic voltage")
        number(current, "synthetic current")
        return voltage, current, SIMULATED_COMPLIANCE if abs(raw) > compliance else 0


@dataclass(frozen=True, kw_only=True)
class FaultInjection:
    source_deadline_point: int | None = None
    measurement_deadline_sample: int | None = None
    sensor_failure_tick: int | None = None  # Relative to actual START.
    buffer_capacity_records: int | None = None  # Overrides declared capacity downward.
    shutdown_confirmation_lost: bool = False

    def __post_init__(self) -> None:
        for field in (
            "source_deadline_point",
            "measurement_deadline_sample",
            "sensor_failure_tick",
        ):
            value = getattr(self, field)
            if value is not None:
                integer(value, field, minimum=0)
        if self.buffer_capacity_records is not None:
            integer(self.buffer_capacity_records, "buffer_capacity_records", minimum=0)
        if type(self.shutdown_confirmation_lost) is not bool:
            raise ValidationError("shutdown_confirmation_lost must be boolean")


@dataclass(frozen=True, kw_only=True)
class SourceTransition:
    tick: int  # Local clock since simulator creation.
    relative_tick: int
    logical_point: int
    repeat_index: int
    point_index: int
    cycle_index: int
    segment_index: int
    level: float


@dataclass(frozen=True, kw_only=True)
class SimulatedRecord:
    sample_index: int
    aperture_start_tick: int
    aperture_end_tick: int
    available_tick: int
    voltage_v: float
    current_a: float
    source_setpoint: float  # At aperture start, NOT the integrated measurement.
    source_function: Literal["current", "voltage"]
    first_logical_point: int
    last_logical_point: int
    repeat_index: int
    point_index: int
    cycle_index: int
    segment_index: int
    wholly_settled_in_one_dwell: bool
    simulation_status_bits: int


@dataclass(frozen=True, kw_only=True)
class TerminalOutcome:
    acquisition_id: str
    experiment_id: str
    success: bool
    cause: str
    terminal_tick: int
    start_tick: int | None
    source_points_executed: int
    records_retained: int
    waveform_complete: bool
    output_off_confirmed: bool


@dataclass(frozen=True, kw_only=True)
class Snapshot:
    record: SimulatedRecord
    acquired_tick: int  # Original aperture start on the local clock.
    latched_tick: int
    age_ticks: int
    state: DeviceState
    output: bool | None
    valid: bool = True


class SimulatedRuntime:
    """Typed simulator API, not ElectrochemistryDevice or a TSP emulator.

    Operations are serialized. advance_ticks drives the independent instrument
    clock even when its fake host transport is disconnected. No wall-clock sleeps.
    """

    def __init__(
        self,
        *,
        capabilities: Keithley2460Capabilities,
        config: Keithley2460Config,
        cell: FakeCell,
        faults: FaultInjection | None = None,
    ):
        if not isinstance(capabilities, Keithley2460Capabilities) or not isinstance(
            config, Keithley2460Config
        ):
            raise ValidationError("Explicit capabilities and configuration are required")
        if capabilities.evidence_kind != "simulation":
            raise ValidationError("Simulator requires an explicitly synthetic capability profile")
        if not isinstance(cell, FakeCell) or (
            faults is not None and not isinstance(faults, FaultInjection)
        ):
            raise ValidationError("Expected FakeCell and FaultInjection")
        self.capabilities, self.config, self.cell = capabilities, config, cell
        self.faults = faults or FaultInjection()
        self._prepared_faults = self.faults
        self.lock = threading.RLock()
        self.tick = 0
        self.state = DeviceState.IDLE
        self.output: bool | None = False
        self.ready = self.busy = False
        self.start_asserted = self.abort_asserted = False
        self._program: CompiledProgram | None = None
        self._request: AcquisitionRequest | None = None
        self._acquisition_id: str | None = None
        self._used_ids: set[str] = set()
        self._start_tick: int | None = None
        self._outcome: TerminalOutcome | None = None
        self._records: list[SimulatedRecord] = []
        self._trace: list[SourceTransition] = []
        self._events: list[tuple[int, int, int, str, int]] = []
        self._serial = 0
        self._snapshot: Snapshot | None = None
        self._emitted = 0
        self._disposed = True
        self._source_slots = 0
        self._compliance = 0.0
        self._response = (0.0, 0.0, 0)
        self._window: tuple[int, int, SourceTransition] | None = None
        self._average_v = self._average_i = 0.0
        self._window_bits = 0
        self._window_last_point = 0
        self._pending_record: SimulatedRecord | None = None
        self.dispositions: list[tuple[str, str]] = []

    @property
    def records(self) -> tuple[SimulatedRecord, ...]:
        with self.lock:
            return tuple(self._records)

    @property
    def source_trace(self) -> tuple[SourceTransition, ...]:
        with self.lock:
            return tuple(self._trace)

    @property
    def outcome(self) -> TerminalOutcome | None:
        with self.lock:
            return self._outcome

    def prepare(self, request: AcquisitionRequest, *, acquisition_id: str) -> CompiledProgram:
        with self.lock:
            text(acquisition_id, "acquisition_id")
            if self.state not in (DeviceState.IDLE, DeviceState.COMPLETE):
                raise ElectrochemistryError("Prepare requires IDLE or disposed COMPLETE")
            if not self._disposed:
                raise RetainedDataError("Explicitly discard retained acquisition before preparing")
            if acquisition_id in self._used_ids:
                raise ValidationError("Acquisition IDs cannot be reused in this simulator session")
            # Host-side M1 planning, before any mutation of simulated output/storage.
            program = Keithley2460Compiler(self.capabilities).compile(request, self.config)
            self.state = DeviceState.PREPARING
            self._program, self._request, self._acquisition_id = program, request, acquisition_id
            self._prepared_faults = self.faults
            self._used_ids.add(acquisition_id)
            self._records.clear()
            self._trace.clear()
            self._events.clear()
            self._snapshot = self._outcome = None
            self._start_tick = None
            self._emitted = self._source_slots = 0
            self._window = self._pending_record = None
            self._disposed = False
            p = request.program
            limit = (
                getattr(p, "voltage_limit_v", None)
                if program.source_function == "current"
                else getattr(p, "current_limit_a", None)
            )
            assert limit is not None
            self._compliance = limit
            self.output = False
            self.ready = self.busy = False
            self.state = DeviceState.PREPARED
            return program

    def kickoff(self) -> None:
        with self.lock:
            if self.state != DeviceState.PREPARED:
                raise ElectrochemistryError("Kickoff requires PREPARED")
            if self.start_asserted or (
                self.config.io.external_abort_enabled and self.abort_asserted
            ):
                raise ElectrochemistryError("Cannot arm with asserted START/ABORT")
            assert self._request is not None
            self.state = DeviceState.ARMING
            if self._request.start_mode == StartMode.EXTERNAL_TRIGGER:
                timeout = self._ceil_ticks(self.config.timing.external_start_timeout_s)
                self._schedule(self.tick + timeout, 0, "start_timeout")
                self.state = DeviceState.WAITING_START
                self.ready = (
                    True  # Wait installed before READY; no subsequent stale-event clearing.
                )
            else:
                self._start()

    def set_inputs(self, *, start: bool, abort: bool) -> None:
        """Logical asserted inputs; caller converts physical polarity/edge if needed.

        One atomic simulation batch gives ABORT precedence over coincident START.
        Separate calls preserve their explicit order, not a hardware simultaneity claim.
        """
        if type(start) is not bool or type(abort) is not bool:
            raise ValidationError("Logical inputs must be booleans")
        with self.lock:
            rising_start = start and not self.start_asserted
            self.start_asserted, self.abort_asserted = start, abort
            if (
                self.config.io.external_abort_enabled
                and abort
                and self.state
                in (DeviceState.PREPARED, DeviceState.WAITING_START, DeviceState.RUNNING)
            ):
                self._terminate("external_abort", success=False)
            elif rising_start and self.state == DeviceState.WAITING_START:
                self._start()

    def advance_ticks(self, ticks: int) -> None:
        integer(ticks, "ticks", minimum=0)
        with self.lock:
            target = self.tick + ticks
            while self._events and self._events[0][0] <= target:
                tick, _, _, kind, index = heapq.heappop(self._events)
                self._integrate(tick - self.tick)
                self.tick = tick
                self._event(kind, index)
            self._integrate(target - self.tick)
            self.tick = target

    def abort(self, *, reason: str = "programmatic") -> TerminalOutcome | None:
        text(reason, "abort reason")
        with self.lock:
            if self.state == DeviceState.ERROR:
                raise ShutdownUnconfirmed(
                    "Explicit recovery required; repeated abort cannot confirm OFF"
                )
            if self.state in (DeviceState.COMPLETE, DeviceState.ABORTED, DeviceState.IDLE):
                return self._outcome
            self._terminate(reason, success=False)
            if self._outcome is not None and not self._outcome.output_off_confirmed:
                raise ShutdownUnconfirmed("Simulated OFF acknowledgement lost")
            return self._outcome

    def completion(self) -> TerminalOutcome | None:
        """Nonblocking outcome query: pending None, success outcome, failure exception."""
        with self.lock:
            if self._outcome is None:
                return None
            if not self._outcome.output_off_confirmed:
                raise ShutdownUnconfirmed(self._outcome.cause)
            if not self._outcome.success:
                raise AcquisitionAborted(self._outcome.cause)
            return self._outcome

    def recover(self) -> None:
        with self.lock:
            if self.state == DeviceState.IDLE:
                return
            if self.state not in (DeviceState.ABORTED, DeviceState.ERROR):
                raise ElectrochemistryError("Recovery requires ABORTED or ERROR")
            if self.start_asserted or self.abort_asserted:
                raise ElectrochemistryError("Recovery requires inactive inputs")
            if self.faults.shutdown_confirmation_lost:
                raise ShutdownUnconfirmed("Simulated output cannot be verified")
            self.state = DeviceState.RECOVERING
            self.output = False
            self.ready = self.busy = False
            self._events.clear()
            self.state = DeviceState.IDLE
            # Preserve the original outcome, counts and shutdown uncertainty as evidence.

    def restore_shutdown_confirmation(self) -> None:
        """Test hook: restore the simulated OFF acknowledgement, without rearming."""
        with self.lock:
            self.faults = replace(self.faults, shutdown_confirmation_lost=False)

    def latch_snapshot(self) -> Snapshot:
        with self.lock:
            if not self._records:
                raise ElectrochemistryError("No complete buffered sample exists")
            assert self._start_tick is not None
            record = self._records[-1]
            acquired = self._start_tick + record.aperture_start_tick
            self._snapshot = Snapshot(
                record=record,
                acquired_tick=acquired,
                latched_tick=self.tick,
                age_ticks=self.tick - acquired,
                state=self.state,
                output=self.output,
            )
            return self._snapshot

    def read_snapshot(self) -> Snapshot:
        with self.lock:
            if self._snapshot is None:
                raise ElectrochemistryError("Latch a valid buffered snapshot first")
            return self._snapshot

    def collect_chunk(self, *, max_records: int) -> tuple[SimulatedRecord, ...]:
        integer(max_records, "max_records")
        with self.lock:
            if self._outcome is None:
                raise ElectrochemistryError("Only a frozen terminal buffer can be collected")
            result = tuple(self._records[self._emitted : self._emitted + max_records])
            self._emitted += len(result)
            return result

    def export_retained_data(self, destination: str) -> str:
        """Diagnostic JSON plus checksum; export/collection do NOT authorize overwrite."""
        with self.lock:
            if self._outcome is None or self._program is None:
                raise RetainedDataError("Export requires a frozen terminal acquisition")
            payload = canonical_json(
                {
                    "schema": SIMULATION_SCHEMA,
                    "synthetic": True,
                    "acquisition_id": self._acquisition_id,
                    "program": json.loads(self._program.canonical_program_json),
                    "request_sha256": self._program.request_sha256,
                    "program_sha256": self._program.program_sha256,
                    "timer_resolution_s": self._program.timer_resolution_s,
                    "clock": "virtual-integer-ticks-no-epoch-calibration",
                    "field_origin": "ideal-resistive-cell-aperture-average",
                    "cell": self.cell,
                    "faults_at_prepare": self._prepared_faults,
                    "faults_at_export": self.faults,
                    "source_trace": self._trace,
                    "records": self._records,
                    "outcome": self._outcome,
                }
            )
            digest = sha256_json(payload)
            Path(destination).write_text(
                canonical_json({"sha256": digest, "payload": json.loads(payload)}) + "\n",
                encoding="utf-8",
            )
            return digest

    def discard_retained_data(self, *, acquisition_id: str, reason: str) -> None:
        text(reason, "disposition reason")
        with self.lock:
            if acquisition_id != self._acquisition_id or self._outcome is None:
                raise RetainedDataError("Discard requires the exact terminal acquisition ID")
            if self._disposed:
                return
            self.dispositions.append((acquisition_id, reason))
            self._disposed = True  # Evidence remains readable until the next valid prepare.

    def _ceil_ticks(self, seconds: float) -> int:
        return int(
            (
                Decimal(str(seconds)) / Decimal(str(self.capabilities.timer_resolution_s))
            ).to_integral_value(rounding=ROUND_CEILING)
        )

    def _schedule(self, tick: int, priority: int, kind: str, index: int = 0) -> None:
        self._serial += 1
        heapq.heappush(self._events, (tick, priority, self._serial, kind, index))

    def _start(self) -> None:
        assert self._program is not None
        self._events.clear()
        self._start_tick = self.tick
        self.ready = False
        self.busy = self.output = True
        self.state = DeviceState.RUNNING
        self._schedule(self.tick, 40, "source", 0)
        self._schedule(self.tick + self._program.measurement.first_tick, 50, "open", 0)
        self._schedule(self.tick + self._program.source.duration_ticks, 30, "finish")
        if self.faults.sensor_failure_tick is not None:
            self._schedule(self.tick + self.faults.sensor_failure_tick, 0, "sensor_fault")
        # Execution confirmation includes installing the first source action.
        self.advance_ticks(0)

    def _capacity(self) -> int:
        override = self.faults.buffer_capacity_records
        return (
            self.capabilities.max_buffer_records
            if override is None
            else min(override, self.capabilities.max_buffer_records)
        )

    def _event(self, kind: str, index: int) -> None:
        if kind == "start_timeout":
            if self.state == DeviceState.WAITING_START:
                self._terminate("start_timeout", success=False)
            return
        if self.state != DeviceState.RUNNING:
            return
        assert self._program is not None and self._start_tick is not None
        program = self._program
        relative = self.tick - self._start_tick
        if kind == "finish":
            self._terminate("normal", success=True)
        elif kind == "sensor_fault":
            self._terminate("sensor_failure", success=False)
        elif kind == "source":
            if index == self.faults.source_deadline_point:
                self._terminate("source_deadline", success=False)
                return
            slots = self.capabilities.source_records_per_point
            if self._source_slots + slots + len(self._records) > self._capacity():
                self._terminate("buffer_exhausted", success=False)
                return
            point = index % len(program.source.steps)
            repeat = index // len(program.source.steps)
            step = program.source.steps[point]
            transition = SourceTransition(
                tick=self.tick,
                relative_tick=relative,
                logical_point=index,
                repeat_index=repeat,
                point_index=point,
                cycle_index=step.cycle_index,
                segment_index=step.segment_index,
                level=step.level,
            )
            try:
                self._response = self.cell.response(
                    program.source_function, step.level, self._compliance
                )
            except (ValidationError, OverflowError):
                self._terminate("sensor_failure", success=False)
                return
            self._trace.append(transition)
            self._source_slots += slots
            if not self.capabilities.local_cutoffs_use_measurements and self._cutoff(
                self._response[0]
            ):
                self._terminate("voltage_cutoff", success=False)
                return
            if index + 1 < program.source.logical_points:
                self._schedule(self.tick + step.dwell_ticks, 40, "source", index + 1)
        elif kind == "open":
            if index == self.faults.measurement_deadline_sample:
                self._terminate("measurement_deadline", success=False)
                return
            self._window = (index, relative, self._trace[-1])
            self._average_v = self._average_i = 0.0
            self._window_bits = 0
            self._window_last_point = self._trace[-1].logical_point
            self._schedule(self.tick + program.measurement.aperture_ticks, 10, "close", index)
        elif kind == "close":
            assert self._window is not None
            sample, start, first = self._window
            self._pending_record = SimulatedRecord(
                sample_index=sample,
                aperture_start_tick=start,
                aperture_end_tick=relative,
                available_tick=relative + program.measurement.overhead_ticks,
                voltage_v=self._average_v,
                current_a=self._average_i,
                source_setpoint=first.level,
                source_function=program.source_function,
                first_logical_point=first.logical_point,
                last_logical_point=self._window_last_point,
                repeat_index=first.repeat_index,
                point_index=first.point_index,
                cycle_index=first.cycle_index,
                segment_index=first.segment_index,
                wholly_settled_in_one_dwell=first.logical_point == self._window_last_point
                and start - first.relative_tick >= program.settling_ticks,
                simulation_status_bits=self._window_bits,
            )
            self._window = None
            self._schedule(self.tick + program.measurement.overhead_ticks, 20, "publish", index)
        elif kind == "publish":
            assert self._pending_record is not None
            if self._source_slots + len(self._records) + 1 > self._capacity():
                self._terminate("buffer_exhausted", success=False)
                return
            record = self._pending_record
            self._records.append(record)
            self._pending_record = None
            if self._cutoff(record.voltage_v):
                self._terminate("voltage_cutoff", success=False)
                return
            if index + 1 < program.measurement.record_count:
                next_tick = (
                    self._start_tick
                    + program.measurement.first_tick
                    + (index + 1) * program.measurement.period_ticks
                )
                self._schedule(next_tick, 50, "open", index + 1)

    def _cutoff(self, voltage: float) -> bool:
        assert self._program is not None
        cutoffs = self._program.effective_voltage_cutoffs_v
        return cutoffs is not None and (voltage <= cutoffs[0] or voltage >= cutoffs[1])

    def _integrate(self, ticks: int) -> None:
        if ticks > 0 and self._window is not None:
            assert self._program is not None
            # Normalize time first to avoid overflowing a representable average.
            weight = ticks / self._program.measurement.aperture_ticks
            self._average_v = math.fsum((self._average_v, self._response[0] * weight))
            self._average_i = math.fsum((self._average_i, self._response[1] * weight))
            self._window_bits |= self._response[2]
            self._window_last_point = self._trace[-1].logical_point

    def _terminate(self, cause: str, *, success: bool) -> None:
        assert self._acquisition_id is not None and self._request is not None
        self.state = DeviceState.ABORTING
        self.ready = False
        self._events.clear()  # Future source actions inhibited BEFORE OFF acknowledgement.
        self._window = self._pending_record = None  # No incomplete measurement fabricated.
        confirmed = not self.faults.shutdown_confirmation_lost
        self.output = False if confirmed else None
        self.busy = False
        self.state = (
            DeviceState.COMPLETE
            if success and confirmed
            else DeviceState.ABORTED
            if confirmed
            else DeviceState.ERROR
        )
        self._outcome = TerminalOutcome(
            acquisition_id=self._acquisition_id,
            experiment_id=self._request.experiment_id,
            success=success and confirmed,
            cause=cause if confirmed else f"shutdown_unconfirmed:{cause}",
            terminal_tick=self.tick,
            start_tick=self._start_tick,
            source_points_executed=len(self._trace),
            records_retained=len(self._records),
            waveform_complete=success,
            output_off_confirmed=confirmed,
        )
