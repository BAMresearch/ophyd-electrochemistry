"""Typed sequence execution and wall-time release of a precomputed simulation."""

import json
import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

import numpy as np
from numpy.typing import NDArray

from ..exceptions import ElectrochemistryError, ValidationError
from ..state import DeviceState
from ..validation import integer, positive, text
from .models import SafetyLimits, Sequence, validate
from .run_sheet import RunSpec
from .simulator import CellModel, SimulationResult, simulate_sequence

# Kept independent of run_file so importing a Flyer needs no optional HDF5 package.
READOUT_FIELDS = (
    "t_s",
    "voltage_v",
    "current_a",
    "step_index",
    "repeat_index",
    "source_status",
    "measure_status",
)
SequenceEvent = tuple[float, int, int, str, str]


@dataclass(frozen=True)
class SequenceRequest:
    measurement_id: str
    sequence: Sequence
    limits: SafetyLimits
    start_mode: Literal["immediate", "external"] = "immediate"
    sample_id: str | None = None
    notes: str | None = None
    run_sheet_row: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "measurement_id", text(self.measurement_id, "measurement_id").strip()
        )
        if self.start_mode not in ("immediate", "external"):
            raise ValidationError("start_mode must be immediate or external")
        validate(self.sequence, self.limits)

    @classmethod
    def from_run_spec(cls, spec: RunSpec, limits: SafetyLimits) -> "SequenceRequest":
        return cls(
            spec.measurement_id,
            spec.sequence,
            limits,
            spec.start_mode,
            spec.sample_id,
            spec.notes,
            dict(spec.row),
        )


@dataclass(frozen=True)
class SequencePoll:
    readout: dict[str, NDArray[Any]]
    events: list[SequenceEvent]
    outcome: str | None
    detail: str = ""


class SequenceBackend(Protocol):
    """One owner; bounded polls are the only readout path."""

    def connect(self) -> None: ...
    def close(self) -> None: ...
    def inspect(self) -> tuple[DeviceState, bool | None]: ...
    def equipment(self) -> dict[str, str | int | float | bool]: ...
    def prepare(self, request: SequenceRequest) -> None: ...  # also from PREPARED
    def kickoff(self) -> None: ...
    def time_origin_epoch_s(self) -> float | None: ...
    def poll(self, max_records: int) -> SequencePoll: ...
    def abort(self, reason: str) -> None: ...
    def recover(self) -> None: ...


_DEFAULT_CELL = CellModel()


class SimulatedSequenceBackend:
    """Synthetic cell only; the host releases, but never paces, source points."""

    def __init__(
        self,
        cell: CellModel = _DEFAULT_CELL,
        *,
        speed: float = 1.0,
        seed: int = 0,
        external_start_delay_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or speed < 1:
            raise ValidationError("speed must be >= 1 (or infinity)")
        if math.isnan(speed):
            raise ValidationError("speed must be >= 1 (or infinity)")
        if not isinstance(cell, CellModel):
            raise ValidationError("cell must be a CellModel")
        integer(seed, "seed", minimum=0)
        if external_start_delay_s is not None:
            positive(external_start_delay_s, "external_start_delay_s", zero=True)
        self.cell, self.speed, self.seed = cell, speed, seed
        self._delay = external_start_delay_s
        self._clock, self._wall_clock = clock, wall_clock
        self._lock = threading.RLock()
        self._connected = False
        self._state = DeviceState.IDLE
        self._request: SequenceRequest | None = None
        self._result: SimulationResult | None = None
        self._start: float | None = None
        self._origin: float | None = None
        self._kickoff = 0.0
        self._record = self._event = 0
        self._abort_time: float | None = None
        self._detail = ""

    def connect(self) -> None:
        with self._lock:
            self._connected = True

    def close(self) -> None:
        with self._lock:
            if self._state in (DeviceState.RUNNING, DeviceState.WAITING_START):
                raise ElectrochemistryError("Cannot close an active simulation")
            self._connected = False

    def _check_connected(self) -> None:
        if not self._connected:
            raise ElectrochemistryError("Backend is not connected")

    def _auto_start(self) -> None:
        if (
            self._state == DeviceState.WAITING_START
            and self._delay is not None
            and self._clock() >= self._kickoff + self._delay
        ):
            # Anchor to the scheduled edge, even if the first poll comes later.
            self._begin(self._kickoff + self._delay)

    def inspect(self) -> tuple[DeviceState, bool | None]:
        with self._lock:
            self._auto_start()
            return self._state, self._state == DeviceState.RUNNING

    def equipment(self) -> dict[str, str | int | float | bool]:
        result: dict[str, str | int | float | bool] = {
            "backend": "simulator",
            "seed": self.seed,
            "speed": str(self.speed),
        }
        for key, value in asdict(self.cell).items():
            result[key] = json.dumps(value) if isinstance(value, tuple) else value
        return result

    def prepare(self, request: SequenceRequest) -> None:
        with self._lock:
            self._check_connected()
            # Re-preparing replaces a loaded sequence that was never kicked off
            # (e.g. the run failed to open); output is OFF throughout.
            if self._state not in (DeviceState.IDLE, DeviceState.COMPLETE, DeviceState.PREPARED):
                raise ElectrochemistryError("Prepare requires IDLE, PREPARED or COMPLETE")
            validate(request.sequence, request.limits)
            result = simulate_sequence(request.sequence, request.limits, self.cell, seed=self.seed)
            self._request, self._result = request, result
            self._record = self._event = 0
            self._start = self._origin = self._abort_time = None
            self._detail = ""
            self._state = DeviceState.PREPARED

    def _begin(self, moment: float) -> None:
        self._start = moment
        self._origin = self._wall_clock() - (self._clock() - moment)
        self._state = DeviceState.RUNNING

    def kickoff(self) -> None:
        with self._lock:
            self._check_connected()
            if self._state != DeviceState.PREPARED or self._request is None:
                raise ElectrochemistryError("Kickoff requires PREPARED")
            self._kickoff = self._clock()
            if self._request.start_mode == "external":
                self._state = DeviceState.WAITING_START
            else:
                self._begin(self._kickoff)

    def fire_start(self) -> None:
        with self._lock:
            if self._state == DeviceState.WAITING_START:
                self._begin(self._clock())

    def time_origin_epoch_s(self) -> float | None:
        with self._lock:
            self._auto_start()
            return self._origin

    def _virtual_time(self) -> float:
        if self._start is None:
            return 0.0
        if math.isinf(self.speed):
            return math.inf
        return max(0.0, self.speed * (self._clock() - self._start))

    def poll(self, max_records: int) -> SequencePoll:
        integer(max_records, "max_records")
        with self._lock:
            self._check_connected()
            self._auto_start()
            result = self._result
            if result is None or self._state == DeviceState.PREPARED:
                raise ElectrochemistryError("Poll requires kickoff")
            elapsed = self._abort_time if self._abort_time is not None else self._virtual_time()
            if self._state == DeviceState.WAITING_START:
                elapsed = -1.0
            available = int(np.searchsorted(result.readout["t_s"], elapsed, side="right"))
            end = min(available, self._record + max_records)
            readout = {
                key: values[self._record : end].copy() for key, values in result.readout.items()
            }
            self._record = end
            events = []
            while self._event < len(result.events) and result.events[self._event][0] <= elapsed:
                events.append(result.events[self._event])
                self._event += 1
            drained = self._record == len(result.readout["t_s"]) and self._event == len(
                result.events
            )
            outcome = None
            if drained and self._state != DeviceState.WAITING_START:
                if self._state == DeviceState.ABORTED:
                    outcome = "aborted"
                else:
                    self._state = DeviceState.COMPLETE
                    outcome = result.outcome
            return SequencePoll(readout, events, outcome, self._detail)

    def abort(self, reason: str) -> None:
        with self._lock:
            if self._state not in (DeviceState.WAITING_START, DeviceState.RUNNING):
                return
            assert self._result is not None
            elapsed = self._virtual_time()
            # Infinite speed means the whole simulation has already happened.
            if math.isinf(elapsed):
                elapsed = float(self._result.readout["t_s"][-1])
            self._abort_time = elapsed
            size = int(np.searchsorted(self._result.readout["t_s"], elapsed, side="right"))
            events = (
                [event for event in self._result.events if event[0] <= elapsed]
                if self._start is not None
                else []
            )
            step, repeat = events[-1][1:3] if events else (0, 0)
            events.append((elapsed, step, repeat, "abort", reason))
            self._result = SimulationResult(
                {key: values[:size] for key, values in self._result.readout.items()},
                events,
                "aborted",
                self._result.final_soc,
            )
            self._detail, self._state = reason, DeviceState.ABORTED

    def recover(self) -> None:
        with self._lock:
            if self._state not in (DeviceState.ABORTED, DeviceState.ERROR):
                raise ElectrochemistryError("Recover requires ABORTED or ERROR")
            self._state = DeviceState.IDLE
