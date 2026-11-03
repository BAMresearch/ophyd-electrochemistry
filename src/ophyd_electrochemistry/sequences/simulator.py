"""Deterministic offline sequence execution; no instrument I/O or wall-clock pacing.

This synthetic cell is for exercising software, not predicting a real cell.
Positive current raises SOC. OCV is linearly interpolated, with endpoint values
outside the grid; SOC itself is not clipped, so charge remains conserved.
"""

import math
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from ..exceptions import ValidationError
from ..validation import integer, number, positive
from .models import (
    CutoffAction,
    SafetyLimits,
    Sequence,
    Step,
    StepMode,
    expanded,
    expected_records,
    sample_schedule,
    validate,
)


@dataclass(frozen=True, slots=True)
class CellModel:
    """OCV + R0 + R1||C1, initially relaxed (zero RC voltage).

    The default curve has a low plateau and a sloping region up to 2 V,
    oriented so positive current raises both SOC and voltage, as in the sequence
    convention. It is illustrative, not a fitted hard-carbon OCV curve.
    """

    capacity_ah: float = 0.5e-3
    soc0: float = 0.5
    r0_ohm: float = 20.0
    r1_ohm: float = 50.0
    c1_f: float = 0.2
    ocv_soc: tuple[float, ...] = (0.0, 0.2, 0.5, 0.8, 1.0)
    ocv_v: tuple[float, ...] = (0.08, 0.085, 0.1, 0.8, 2.0)
    noise_v: float = 50e-6

    def __post_init__(self) -> None:
        for name in ("capacity_ah", "r0_ohm", "r1_ohm", "c1_f"):
            positive(getattr(self, name), name)
        number(self.soc0, "soc0")
        if not 0 <= self.soc0 <= 1:
            raise ValidationError("soc0 must be in [0, 1]")
        positive(self.noise_v, "noise_v", zero=True)
        if (
            not isinstance(self.ocv_soc, tuple)
            or not isinstance(self.ocv_v, tuple)
            or len(self.ocv_soc) < 2
            or len(self.ocv_soc) != len(self.ocv_v)
        ):
            raise ValidationError(
                "ocv_soc/ocv_v must be equal-length tuples with at least 2 points"
            )
        for name in ("ocv_soc", "ocv_v"):
            for value in getattr(self, name):
                number(value, name)
        if self.ocv_soc[0] != 0 or self.ocv_soc[-1] != 1:
            raise ValidationError("ocv_soc must span 0..1")
        if any(b <= a for a, b in zip(self.ocv_soc, self.ocv_soc[1:], strict=False)):
            raise ValidationError("ocv_soc must be strictly increasing")


@dataclass(frozen=True)
class SimulationResult:
    readout: dict[str, NDArray[Any]]
    events: list[tuple[float, int, int, str, str]]
    outcome: str
    final_soc: float


def _cutoff(step: Step, voltage: float, current: float) -> str:
    if step.mode == StepMode.VOLTAGE:
        if step.i_abs_min is not None and abs(current) <= step.i_abs_min:
            return "i_abs_min"
    else:
        if step.v_min is not None and voltage <= step.v_min:
            return "v_min"
        if step.v_max is not None and voltage >= step.v_max:
            return "v_max"
    return ""


def _hold_voltage(
    drive: float, rc_v: float, interval: float, cell: CellModel, i_max: float
) -> tuple[float, float]:
    """Exact R0 + R1||C1 response to a held voltage over one interval.

    ``drive`` is setpoint minus OCV (OCV frozen within the interval). While the
    required current exceeds ``i_max`` the source holds the clamped current
    (compliance) until the RC voltage lets it leave compliance, then holds the
    voltage. Returns the RC voltage at the interval end and the charge in C.
    """
    r0, r1 = cell.r0_ohm, cell.r1_ohm
    tau_s = r1 * cell.c1_f
    charge_c = 0.0
    current = (drive - rc_v) / r0
    if abs(current) > i_max:
        held = math.copysign(i_max, current)
        # RC voltage at which (drive - rc) / R0 falls back to the clamped current.
        ratio = (drive - held * r0 - held * r1) / (rc_v - held * r1)
        leave_s = -tau_s * math.log(ratio) if 0 < ratio < 1 else math.inf
        if leave_s >= interval:
            rc_v = held * r1 + (rc_v - held * r1) * math.exp(-interval / tau_s)
            return rc_v, held * interval
        rc_v, charge_c, interval = drive - held * r0, held * leave_s, interval - leave_s
    # Held voltage: RC relaxes to the R1/(R0+R1) share of the drive with the
    # faster time constant of R0 in parallel with R1.
    final = drive * r1 / (r0 + r1)
    tau_v_s = tau_s * r0 / (r0 + r1)
    response = -math.expm1(-interval / tau_v_s)
    rc_integral = final * interval + (rc_v - final) * tau_v_s * response
    charge_c += (drive * interval - rc_integral) / r0
    rc_v += (final - rc_v) * response
    return rc_v, charge_c


_DEFAULT_CELL = CellModel()


def simulate_sequence(
    sequence: Sequence, limits: SafetyLimits, cell: CellModel = _DEFAULT_CELL, *, seed: int = 0
) -> SimulationResult:
    """Execute in virtual time, checking cutoffs after retaining each sample.

    In voltage mode the RC response and charge are exact for the held voltage
    with current compliance at the session limit (OCV frozen within each
    interval; see ``_hold_voltage``). The sample reports the current that holds
    the setpoint at the interval end, so the measured voltage equals the
    setpoint (plus noise) outside compliance. A held-current shortcut would be
    unstable for intervals longer than about tau·2·R0/(R0+R1).
    Noise affects measured voltage and its cutoffs, never the internal state.
    """
    validate(sequence, limits)
    if not isinstance(cell, CellModel):
        raise ValidationError("cell must be a CellModel")
    rng = np.random.default_rng(seed)
    size = expected_records(sequence)
    # Match run_file.READOUT_FIELDS without importing its optional h5py dependency.
    readout: dict[str, NDArray[Any]] = {
        name: np.zeros(size, dtype="f8" if index < 3 else "i4")
        for index, name in enumerate(
            (
                "t_s",
                "voltage_v",
                "current_a",
                "step_index",
                "repeat_index",
                "source_status",
                "measure_status",
            )
        )
    }
    events: list[tuple[float, int, int, str, str]] = []
    soc, rc_v = cell.soc0, 0.0
    time_s, used = 0.0, 0
    outcome = "completed"
    charge_as = 3600 * cell.capacity_ah
    tau_s = cell.r1_ohm * cell.c1_f
    for step_index, repeat_index, step in expanded(sequence):
        events.append((time_s, step_index, repeat_index, "step_start", step.label))
        step_start = time_s
        phase_start = 0.0
        cut = ""
        for count, interval in sample_schedule(step):
            # expm1 retains precision for intervals much shorter than tau.
            response = -math.expm1(-interval / tau_s)
            for k in range(1, count + 1):
                if step.mode == StepMode.VOLTAGE:
                    drive = step.level - float(np.interp(soc, cell.ocv_soc, cell.ocv_v))
                    rc_v, charge_c = _hold_voltage(drive, rc_v, interval, cell, limits.i_max_abs)
                    soc += charge_c / charge_as
                    # The source holds the setpoint against the OCV change too.
                    drive = step.level - float(np.interp(soc, cell.ocv_soc, cell.ocv_v))
                    current = (drive - rc_v) / cell.r0_ohm
                    current = max(-limits.i_max_abs, min(limits.i_max_abs, current))
                else:
                    current = step.level
                    soc += current * interval / charge_as
                    rc_v += (current * cell.r1_ohm - rc_v) * response
                voltage = float(np.interp(soc, cell.ocv_soc, cell.ocv_v)) + current * cell.r0_ohm
                voltage += rc_v + rng.normal(0.0, cell.noise_v)
                time_s = step_start + phase_start + k * interval
                readout["t_s"][used] = time_s
                readout["voltage_v"][used] = voltage
                readout["current_a"][used] = current
                readout["step_index"][used] = step_index
                readout["repeat_index"][used] = repeat_index
                used += 1
                if voltage < limits.v_min_abs or voltage > limits.v_max_abs:
                    outcome = "stopped_cutoff"
                    events.append(
                        (time_s, step_index, repeat_index, "stop", "absolute voltage window")
                    )
                    break
                cut = _cutoff(step, voltage, current)
                if cut:
                    events.append((time_s, step_index, repeat_index, "cutoff", cut))
                    if step.on_cutoff == CutoffAction.STOP:
                        outcome = "stopped_cutoff"
                        events.append((time_s, step_index, repeat_index, "stop", cut))
                    break
            if cut or outcome != "completed":
                break
            phase_start += count * interval
        if outcome != "completed":
            break
        if not cut:
            time_s = step_start + step.duration_s
    return SimulationResult(
        {name: values[:used].copy() for name, values in readout.items()}, events, outcome, soc
    )


def iter_simulated_chunks(
    result: SimulationResult, chunk_records: int = 500
) -> Iterator[dict[str, NDArray[Any]]]:
    """Yield ordered, bounded readout slices, including the final partial chunk."""
    integer(chunk_records, "chunk_records")
    for start in range(0, len(result.readout["t_s"]), chunk_records):
        yield {
            name: values[start : start + chunk_records] for name, values in result.readout.items()
        }
