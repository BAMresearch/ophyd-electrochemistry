"""Where each run-file v2 value goes, with its units and meaning (pure, no HDF5).

``instrument_place(key, equipment)`` maps a flat equipment key (as the backend and
``SequencerProgram.settings()`` produce it) to ``(group, name, units, description)``
under ``/entry/instrument``. ``SAMPLE_UNITS`` gives the units of a ``[sample]`` key
by its suffix. Status bits: Keithley 2460 Reference Manual Rev. C,
``bufferVar.statuses`` (p. 14-43) and ``bufferVar.sourcestatuses``.
"""

import math
import re

from ..exceptions import ValidationError

_V, _A, _S = "V", "A", "s"

GROUPS: dict[str, str] = {
    "identity": "Which instrument (or simulator) ran the sequence",
    "runtime": "The packaged TSP sequencer runtime installed on the instrument",
    "clock": "Instrument clock against UTC, measured before the run",
    "wiring": "Measurement terminals and sense wiring",
    "digital_io": "Rear digital I/O lines (DB-9) used by the sequencer",
    "limits": "Instrument-local limits configured for the run (cutoffs and protection)",
    "timing": "Commissioned timing calibration used to plan the sampling",
    "program": "The compiled trigger-model program",
    "eventlog": "Instrument event-log entries seen before the run",
    "simulator": "Simulated cell model (simulator backend only)",
    "other": "Settings this version of the run-file layout does not describe",
}

_FIXED: dict[str, tuple[str, str, str]] = {
    "backend": ("identity", "", "backend that ran the sequence: k2460-sequencer or simulator"),
    "identity": ("identity", "", "*IDN? response of the instrument"),
    "serial_number": ("identity", "", "instrument serial number"),
    "firmware": ("identity", "", "instrument firmware version"),
    "line_frequency_hz": ("identity", "Hz", "power-line frequency (NPLC reference)"),
    "runtime_build": ("runtime", "", "build name of the packaged TSP runtime"),
    "runtime_sha256": ("runtime", "", "SHA-256 of the packaged TSP runtime source"),
    "sequencer_abi": ("runtime", "", "call interface between host and runtime"),
    "clock_offset_s": (
        "clock",
        _S,
        "instrument clock minus UTC; instrument time = time_origin_epoch_s + clock_offset_s + t_s",
    ),
    "clock_uncertainty_s": ("clock", _S, "half-width of the best measurement bracket"),
    "clock_method": ("clock", "", "how the clock offset was measured"),
    "terminals": ("wiring", "", "front or rear terminals"),
    "four_wire": ("wiring", "", "remote (four-wire) sense on"),
    "ready_line": ("digital_io", "", "line high while an external-start run waits for START"),
    "busy_line": ("digital_io", "", "line high from run start until the output is OFF"),
    "marker_line": ("digital_io", "", "line pulsed at the start of every step"),
    "start_line": ("digital_io", "", "input line whose edge starts an external-start run"),
    "start_edge": ("digital_io", "", "START edge that starts the run"),
    "external_start": ("digital_io", "", "run waited for START on start_line"),
    "v_min_abs": (
        "limits",
        _V,
        "lower bound of the window: in current and rest steps a reading below it "
        "stops the run with the output OFF",
    ),
    "v_max_abs": (
        "limits",
        _V,
        "upper bound of the window: in current and rest steps a reading above it "
        "stops the run with the output OFF",
    ),
    "i_max_abs": ("limits", _A, "largest current magnitude; the current limit of CV holds"),
    "vlimit_margin_v": (
        "limits",
        _V,
        "voltage-limit (compliance) margin beyond the larger of |v_min_abs|, |v_max_abs|",
    ),
    "block_overhead_s": ("timing", _S, "fixed time per trigger-model block"),
    "step_entry_s": ("timing", _S, "time to enter a step, spread over its samples"),
    "margin_s": ("timing", _S, "smallest delay kept free in each sample interval"),
    "capacity": ("program", "", "reading-buffer capacity in records (expected + 16)"),
    "block_count": ("program", "", "trigger-model blocks used"),
    "max_blocks": ("program", "", "trigger-model blocks available"),
    "max_call_chars": ("program", "", "longest runtime call sent, in characters"),
    "source_count": ("program", "", "source configurations (source_<n> groups)"),
    "measure_count": ("program", "", "measure configurations (measure_<n> groups)"),
    "output_off_mode": ("program", "", "output-off state; HIGH-Z isolates a cell"),
    "autozero": ("program", "", "measure autozero on"),
    "source_readback": ("program", "", "source value read back (not the setpoint)"),
    "start_on_lowest_range": (
        "program",
        "",
        "a leading 0 A rest starts on the lowest current range (no output-ON dip)",
    ),
    "pre_run_instrument_events": (
        "eventlog",
        "",
        "the last event-log entries before the run (number and message)",
    ),
    "seed": ("simulator", "", "noise seed"),
    "speed": ("simulator", "", "simulated time per wall-clock second"),
    "capacity_ah": ("simulator", "A h", "cell capacity"),
    "soc0": ("simulator", "", "initial state of charge (0..1)"),
    "r0_ohm": ("simulator", "ohm", "series resistance"),
    "r1_ohm": ("simulator", "ohm", "RC-element resistance"),
    "c1_f": ("simulator", "F", "RC-element capacitance"),
    "ocv_soc": ("simulator", "", "state-of-charge points of the OCV curve (JSON list)"),
    "ocv_v": ("simulator", "", "OCV curve voltages in V (JSON list)"),
    "noise_v": ("simulator", _V, "voltage noise (standard deviation)"),
}
_READING = re.compile(r"reading_s_nplc_(\d+(?:p\d+)?)")
_INDEXED = re.compile(r"(source|measure)_(\d+)_(\w+)")


def instrument_place(key: str, equipment: dict[str, object]) -> tuple[str, str, str, str]:
    """``(group, dataset name, units, description)`` of one equipment key."""
    if key in _FIXED:
        group, units, description = _FIXED[key]
        return group, key, units, description
    if match := _READING.fullmatch(key):
        nplc = match.group(1).replace("p", ".")
        return "timing", key, _S, f"time of one reading at {nplc} NPLC"
    if match := _INDEXED.fullmatch(key):
        kind, index, name = match.groups()
        group = f"{kind}_{index}"
        if kind == "source":
            mode = equipment.get(f"source_{index}_mode")
            unit = {"current": _A, "voltage": _V}.get(str(mode), "")
            other = {_A: _V, _V: _A}.get(unit, "")
            described = {
                "mode": ("", "source function: current or voltage"),
                "level": (unit, "source level when the entry is first applied"),
                "range": (unit, "source range"),
                "limit": (other, "limit (compliance) of the measured quantity"),
            }
        else:
            function = equipment.get(f"measure_{index}_function")
            unit = {"current": _A, "voltage": _V}.get(str(function), "")
            described = {
                "function": ("", "measure function: voltage or current"),
                "range": (unit, "measure range"),
                "nplc": ("", "integration time in power-line cycles"),
            }
        if name in described:
            units, description = described[name]
            return group, name, units, description
    return "other", key, "", "not described by this version of the run-file layout"


READOUT: dict[str, tuple[str, str]] = {
    "t_s": (_S, "seconds since the run's time origin (/entry/run/time_origin_epoch_s, UTC)"),
    "voltage_v": (
        _V,
        "cell voltage: measured in current-source steps, source readback in voltage steps",
    ),
    "current_a": (
        _A,
        "cell current: source readback in current-source steps, measured in voltage steps",
    ),
    "step_index": ("", "index of the step in /entry/run/sequence (from 0)"),
    "repeat_index": ("", "repetition of the repeat block (0 outside it)"),
    "source_status": (
        "",
        "2460 source status bits: 4 overvoltage protection, 8 readback, 16 overtemperature, "
        "32 source level limited (compliance), 64 four-wire sense, 128 output on",
    ),
    "measure_status": (
        "",
        "2460 measure status bits: 1 questionable, 8 front terminals, 16/32 limit 2 "
        "low/high, 64/128 limit 1 low/high, 256 first reading in a group",
    ),
}
EVENTS: dict[str, tuple[str, str]] = {
    "t_s": (_S, "seconds since the run's time origin"),
    "step_index": ("", "step the event belongs to"),
    "repeat_index": ("", "repetition of the repeat block"),
    "kind": ("", "step_start, cutoff, stop, abort or error"),
    "detail": ("", "step label, cutoff name or error message"),
}
RUN_ATTRS: dict[str, tuple[str, str]] = {
    "started_utc": ("", "file created (ISO 8601 UTC)"),
    "finished_utc": ("", "run concluded (ISO 8601 UTC); empty while running or after a crash"),
    "outcome": (
        "",
        "incomplete (running, or the writer stopped), completed, stopped_cutoff, aborted, error",
    ),
    "outcome_detail": ("", "cutoff, abort reason or error; recovery notes"),
}
RUN_DATASETS: dict[str, tuple[str, str]] = {
    "start_mode": ("", "immediate, or external (waited for START)"),
    "notes": ("", "the run-sheet row's notes"),
    "time_origin_epoch_s": (
        _S,
        "UTC time (Unix epoch) of t_s = 0, the runtime's OE_T0 event; NaN until known",
    ),
    "expected_records": ("", "records the sequence produces if it runs to the end"),
    "nominal_duration_s": (_S, "duration if the sequence runs to the end"),
    "sequence": ("", "the sequence (JSON)"),
    "safety_limits": ("", "the session's safety limits the run was validated against (JSON)"),
    "run_sheet_row": ("", "the run-sheet row as read (JSON)"),
    "extra": ("", "other RunStart metadata (JSON)"),
}
PROVENANCE: dict[str, tuple[str, str]] = {
    "bluesky_uid": ("", "RunStart document uid"),
    "scan_id": ("", "RunEngine scan_id"),
    "run_start_time": (_S, "RunStart time (Unix epoch, host clock)"),
    "plan_name": ("", "Bluesky plan"),
    "run_sheet_path": ("", "the run sheet the row came from"),
    "run_sheet_sha256": ("", "SHA-256 of the run sheet when it was enqueued"),
    "host": ("", "host name of the computer that wrote the file"),
    "platform": ("", "operating system"),
    "source_revision": ("", "git revision of the ophyd-electrochemistry checkout, if any"),
    "software": ("", "package versions (JSON)"),
}
SESSION: dict[str, tuple[str, str]] = {
    "format": ("", "toml (an OE_SESSION file) or notebook (a per-run notebook's settings)"),
    "path": ("", "the session file"),
    "sha256": ("", "SHA-256 of the session file bytes as loaded"),
    "text": ("", "the session file as loaded"),
    "settings": ("", "the resolved session settings (JSON)"),
}
SAMPLE_FIXED: dict[str, str] = {
    "name": "sample ID from the run sheet",
    "session_description": "the session file's header comment (cell, wiring)",
}
SAMPLE_UNITS: dict[str, str] = {
    "mg": "mg",
    "g": "g",
    "mah": "mA h",
    "uah": "uA h",
    "cm2": "cm2",
    "mm": "mm",
    "um": "um",
    "ul": "uL",
    "v": _V,
    "mv": "mV",
    "a": _A,
    "ma": "mA",
    "ua": "uA",
    "s": _S,
    "h": "h",
    "degc": "degC",
    "pct": "%",
}
_SAMPLE_KEY = re.compile(r"[a-z][a-z0-9_]{0,62}")


def sample_units(key: str) -> str:
    """Units of a ``[sample]`` value by its key suffix (``mass_mg`` -> ``mg``)."""
    _, sep, suffix = key.rpartition("_")
    return SAMPLE_UNITS.get(suffix, "") if sep else ""


def sample_key_problem(key: object) -> str | None:
    if not isinstance(key, str) or not _SAMPLE_KEY.fullmatch(key):
        return "keys must be lower-case names (a-z, 0-9, _; at most 63 characters)"
    if key in SAMPLE_FIXED or key == "description":
        return f"{key!r} is reserved"
    return None


def check_sample(table: object) -> dict[str, str | int | float | bool]:
    """Validate a ``[sample]`` table: flat, descriptive only, units by key suffix.

    Strings, finite numbers and booleans; no nested tables, arrays or dates.
    Nothing in a run reads these values: they are written to ``/entry/sample``.
    """
    if not isinstance(table, dict):
        raise ValidationError("[sample] must be a table")
    result: dict[str, str | int | float | bool] = {}
    for key, value in table.items():
        problem = sample_key_problem(key)
        if problem:
            raise ValidationError(f"[sample] {key!r}: {problem}")
        if isinstance(value, bool | int):
            if not isinstance(value, bool) and not -(2**63) <= value < 2**63:
                raise ValidationError(f"[sample] {key}: integer out of range")
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ValidationError(f"[sample] {key}: numbers must be finite")
        elif isinstance(value, str):
            if "\x00" in value:
                raise ValidationError(f"[sample] {key}: NUL character")
        else:
            raise ValidationError(
                f"[sample] {key}: only text, numbers and true/false "
                f"(no tables, arrays or dates); got {type(value).__name__}"
            )
        result[key] = value
    return result
