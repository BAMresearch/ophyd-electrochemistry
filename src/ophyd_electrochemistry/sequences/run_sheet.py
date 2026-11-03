"""Excel run sheets for finite sequences; loading never accesses hardware.

Install the ``experiment`` extra to read or create workbooks. Limits, wiring,
output-off mode and hardware approval belong to the notebook/session.
"""

import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal

from ..exceptions import ValidationError
from .builders import (
    DEFAULT_FAST_INTERVAL_S,
    bipolar_pulse_sequence,
    cycling_sequence,
    log_repeats,
    renard_series,
    rest_step,
    soc_position_sequence,
    two_phase_sampling,
)
from .models import SafetyLimits, Sampling, Sequence, validate


@dataclass(frozen=True, slots=True)
class RunSpec:
    measurement_id: str
    sequence: Sequence
    start_mode: Literal["immediate", "external"]
    sample_id: str | None
    notes: str | None
    row_number: int
    row: Mapping[str, object]


# These descriptions form the template's description row and README sheet. They
# say what each value does, so a reader can check their understanding of it.
_COLUMNS = {
    "measurement_id": "Unique run name (text). Rows whose first cell starts with # are skipped.",
    "protocol": "What to run: bipolar_pulse, cycling, soc_position or rest.",
    "start_mode": "immediate: start when the notebook arms the run. external: wait for the "
    "hardware start trigger. Blank = immediate.",
    "current_a": "Current in A; positive current raises the cell voltage. bipolar_pulse: "
    "amplitude, applied as +I then -I (or -I then +I, see start_with). cycling: magnitude "
    "for both directions. "
    "soc_position: signed, the sign sets the direction.",
    "pulse_s": "bipolar_pulse: duration of each +I and -I pulse in s.",
    "start_with": "charge (positive current, voltage rises) or discharge (negative current; "
    "for hard carbon against Na: sodiation). bipolar_pulse: sign of the first pulse in each "
    "block. cycling: the first half-cycle. Blank = charge.",
    "rest_s": "Zero-current rest in s. bipolar_pulse: after each pulse (blank = larger of "
    "pulse_s and 1). cycling: after each half-cycle (blank = 600). soc_position: after "
    "reaching target_v. rest: total duration.",
    "repeats": "bipolar_pulse: number of pulse / rest / opposite pulse / rest blocks. Blank = "
    "100 at 0.1 s "
    "down to 6 at 100 s.",
    "pre_rest_s": "bipolar_pulse: rest before the first pulse in s. Blank = 10.",
    "v_low": "Lower voltage threshold in V; the 2460 watches the voltage, it does not hold "
    "it. cycling: the downward step ends here. bipolar_pulse (optional): the run stops "
    "if a pulse reaches it. Not the absolute safety limit (set in the notebook).",
    "v_high": "Upper voltage threshold in V; the 2460 watches the voltage, it does not hold "
    "it. cycling: the upward step ends here (and the CV hold, if any, holds this "
    "voltage after the charge). bipolar_pulse (optional): the run stops if a pulse reaches "
    "it. Not the absolute safety limit (set in the notebook).",
    "target_v": "soc_position: constant current until the cell voltage reaches this value "
    "in V, then rest_s at zero current.",
    "cycles": "cycling: number of cycles; each starts with the half set by start_with.",
    "cv_i_abs_min_a": "cycling: after a constant-current step, hold its end voltage until "
    "|current| falls to this value in A (at most 3600 s); which step is set by cv_after. "
    "Blank = no voltage hold.",
    "cv_after": "cycling with cv_i_abs_min_a: hold after the charge (at v_high) or after the "
    "discharge (at v_low). Blank = charge.",
    "max_step_s": "Time limit in s for each constant-current step; the step ends then "
    "even if its voltage was not reached. cycling: blank = 86400. soc_position: required.",
    "fast_interval_s": "Sampling interval in s during the first fast_window_s of every "
    "step. bipolar_pulse: blank = 0.006. Other protocols: fast sampling only if set.",
    "fast_window_s": "Length in s of the fast-sampling period at each step start. "
    "bipolar_pulse: blank = 0.5.",
    "interval_s": "Sampling interval in s after the fast period. Blank = 0.1 for "
    "bipolar_pulse, 1 otherwise.",
    "sample_id": "Optional sample name; stored with the data.",
    "notes": "Optional free text; stored with the data.",
}
_PROTOCOLS = {"bipolar_pulse", "cycling", "soc_position", "rest"}
_TEXT = ("measurement_id", "protocol", "start_mode", "start_with", "cv_after", "sample_id", "notes")
_REQUIRED = {
    "bipolar_pulse": ("current_a", "pulse_s"),
    "cycling": ("current_a", "v_low", "v_high", "cycles"),
    "soc_position": ("current_a", "target_v", "max_step_s", "rest_s"),
    "rest": ("rest_s",),
}
_NUMERIC = tuple(column for column in _COLUMNS if column not in _TEXT)


def _json_cell(value: object) -> object:
    """Keep raw values, normalizing blanks and Excel date/time values for JSON."""
    if value is None or (isinstance(value, str) and value == ""):
        return None
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if not math.isfinite(value):
            raise ValidationError("must be finite for JSON metadata")
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, (str, int, float, bool)):
        return value
    raise ValidationError(f"unsupported Excel cell type {type(value).__name__}")


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _parse_row(row: Mapping[str, object]) -> tuple[dict[str, object], list[str]]:
    parsed = dict(row)
    errors = []
    for column in _TEXT:
        value = row.get(column)
        if _blank(value):
            parsed[column] = None
        elif not isinstance(value, str):
            errors.append(f"{column}: must be a string")
        else:
            # Stray spaces typed in Excel must not create a distinct ID or protocol.
            parsed[column] = value.strip()
    for column in ("measurement_id", "protocol"):
        if parsed.get(column) is None:
            errors.append(f"{column}: required value is blank")
    protocol = parsed.get("protocol")
    if isinstance(protocol, str):
        if protocol not in _PROTOCOLS:
            errors.append(f"protocol: must be one of {', '.join(sorted(_PROTOCOLS))}")
        else:
            for column in _REQUIRED[protocol]:
                if _blank(row.get(column)):
                    errors.append(f"{column}: required value is blank")
    start = parsed.get("start_mode")
    if start is None:
        parsed["start_mode"] = "immediate"
    elif isinstance(start, str) and start not in ("immediate", "external"):
        errors.append("start_mode: must be immediate or external")
    for column in ("start_with", "cv_after"):
        choice = parsed.get(column)
        if isinstance(choice, str) and choice not in ("charge", "discharge"):
            errors.append(f"{column}: must be charge or discharge")
    for column in _NUMERIC:
        value = row.get(column)
        if _blank(value):
            parsed[column] = None
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{column}: must be a finite number")
        elif not math.isfinite(value):
            errors.append(f"{column}: must be finite")
        elif column in ("cycles", "repeats"):
            if value < 1 or int(value) != value:
                errors.append(f"{column}: must be a positive integer")
            else:
                parsed[column] = int(value)
        else:
            parsed[column] = float(value)
            if column not in ("current_a", "v_low", "v_high", "target_v"):
                if value < 0 or (column != "cv_i_abs_min_a" and value == 0):
                    sign = "nonnegative" if column == "cv_i_abs_min_a" else "positive"
                    errors.append(f"{column}: must be {sign}")
    return parsed, errors


def _build_sequence(row: Mapping[str, object]) -> Sequence:
    # _parse_row has already checked the Excel types. Typed accessors keep the
    # builders free of Excel-specific coercions (integral floats, blank cells).
    def value(column: str, default: float | None = None) -> float:
        result = row.get(column)
        if result is None:
            result = default
        assert isinstance(result, (float, int)) and not isinstance(result, bool)
        return float(result)

    def count(column: str, default: int = 1) -> int:
        result = row.get(column)
        if result is None:
            return default
        assert isinstance(result, int)
        return result

    def optional(column: str) -> float | None:
        return None if row.get(column) is None else value(column)

    def choice(column: str) -> str:
        result = row.get(column)
        return "charge" if result is None else str(result)

    protocol = row["protocol"]
    if protocol == "bipolar_pulse":
        pulse_s = value("pulse_s")
        return bipolar_pulse_sequence(
            value("current_a"),
            pulse_s,
            count("repeats", log_repeats(pulse_s)),
            rest_s=optional("rest_s"),
            pre_rest_s=value("pre_rest_s", 10),
            v_min=optional("v_low"),
            v_max=optional("v_high"),
            fast_interval_s=value("fast_interval_s", DEFAULT_FAST_INTERVAL_S),
            fast_window_s=value("fast_window_s", 0.5),
            interval_s=value("interval_s", 0.1),
            start=choice("start_with"),
        )
    interval = value("interval_s", 1)
    if protocol == "cycling":
        sequence = cycling_sequence(
            value("current_a"),
            value("v_low"),
            value("v_high"),
            count("cycles"),
            rest_s=value("rest_s", 600),
            cv_i_abs_min=optional("cv_i_abs_min_a"),
            max_step_s=value("max_step_s", 86400),
            start=choice("start_with"),
            cv_after=choice("cv_after"),
            interval_s=interval,
        )
    elif protocol == "soc_position":
        sequence = soc_position_sequence(
            value("current_a"),
            value("target_v"),
            max_s=value("max_step_s"),
            rest_s=value("rest_s"),
            interval_s=interval,
        )
    else:
        sequence = Sequence((rest_step(value("rest_s"), Sampling(interval)),))
    if row.get("fast_interval_s") is not None or row.get("fast_window_s") is not None:
        sequence = replace(
            sequence,
            steps=tuple(
                replace(
                    step,
                    sampling=two_phase_sampling(
                        step.duration_s,
                        value("fast_interval_s", DEFAULT_FAST_INTERVAL_S),
                        value("fast_window_s", 0.5),
                        interval,
                    ),
                )
                for step in sequence.steps
            ),
        )
    return sequence


def _error_column(message: str, protocol: object) -> str:
    aliases = {
        "sampling.fast_interval_s": "fast_interval_s",
        "sampling.interval_s": "interval_s",
        "i_abs_min": "cv_i_abs_min_a",
        "v_min": "v_low",
        "v_max": "v_high",
        "i_max_abs": "current_a",
        "max_s": "max_step_s",
        "level": "target_v" if protocol == "soc_position" else "current_a",
        "duration_s": "rest_s" if protocol == "rest" else "max_step_s",
    }
    for field in sorted(set(_COLUMNS) | set(aliases), key=len, reverse=True):
        if field in message:
            return aliases.get(field, field)
    # Budget/effective-interval failures concern the sequence built by this row.
    return "protocol"


def load_run_sheet(
    path: str | Path, limits: SafetyLimits, *, sheet_name: str | int = 0
) -> dict[str, RunSpec]:
    """Load and validate every row, reporting all bad rows in one exception.

    Empty rows and rows whose ``measurement_id`` starts with ``#`` (such as the
    template's description row) are skipped, preserving Excel row numbers. IDs must be stored as
    text in Excel (e.g. ``001``); literal strings such as ``NA`` remain strings.
    Unknown columns are preserved. Dates/times become ISO strings in metadata.
    """
    import pandas as pd  # type: ignore[import-untyped]

    if not isinstance(limits, SafetyLimits):
        raise ValidationError("limits must be SafetyLimits")
    frame = pd.read_excel(
        path,
        sheet_name=sheet_name,
        header=None,
        dtype=object,
        keep_default_na=False,
        engine="openpyxl",
    )
    if frame.empty:
        raise ValidationError("row 1 (<header>): measurement_id: missing header")
    records = list(frame.itertuples(index=False, name=None))
    headers = records[0]
    header_errors = []
    if headers[0] != "measurement_id":
        header_errors.append("measurement_id: must be the first column")
    for column in ("measurement_id", "protocol"):
        if column not in headers:
            header_errors.append(f"{column}: required column is missing")
    if any(not isinstance(h, str) or not h.strip() for h in headers):
        header_errors.append("header: column names must be non-empty strings")
    if len(set(headers)) != len(headers):
        header_errors.append("header: duplicate column names")
    if header_errors:
        raise ValidationError("\n".join(f"row 1 (<header>): {e}" for e in header_errors))

    def comment(record: tuple[object, ...]) -> bool:
        return isinstance(record[0], str) and record[0].strip().startswith("#")

    ids = Counter(
        record[0].strip()
        for record in records[1:]
        if isinstance(record[0], str) and not comment(record)
    )
    errors: list[str] = []
    runs: dict[str, RunSpec] = {}
    for row_number, record in enumerate(records[1:], start=2):
        if comment(record):
            continue
        row = {}
        row_errors = []
        for column, cell in zip(headers, record, strict=True):
            try:
                row[column] = _json_cell(cell)
            except ValidationError as exc:
                row_errors.append(f"{column}: {exc}")
        if not row_errors and all(_blank(cell) for cell in row.values()):
            continue
        parsed, parse_errors = _parse_row(row)
        row_errors.extend(parse_errors)
        measurement_id = parsed.get("measurement_id")
        if isinstance(measurement_id, str) and ids[measurement_id] > 1:
            row_errors.append("measurement_id: duplicate identifier")
        if not row_errors:
            try:
                sequence = _build_sequence(parsed)
                validate(sequence, limits)
            except ValidationError as exc:
                row_errors.append(f"{_error_column(str(exc), parsed['protocol'])}: {exc}")
        if row_errors:
            label = "<missing>" if _blank(measurement_id) else str(measurement_id)
            errors.extend(f"row {row_number} ({label}): {e}" for e in row_errors)
            continue
        assert isinstance(measurement_id, str)
        start_mode = parsed["start_mode"]
        sample_id, notes = parsed["sample_id"], parsed["notes"]
        assert start_mode in ("immediate", "external")
        assert sample_id is None or isinstance(sample_id, str)
        assert notes is None or isinstance(notes, str)
        runs[measurement_id] = RunSpec(
            measurement_id,
            sequence,
            "external" if start_mode == "external" else "immediate",
            sample_id,
            notes,
            row_number,
            row,
        )
    if errors:
        raise ValidationError("\n".join(errors))
    return runs


def make_template(path: str | Path, *, overwrite: bool = False) -> Path:
    """Write examples and one SOC's R5 pulse series; currents are placeholders.

    The workbook is a starting point, not approval to energize a cell. It must
    be reviewed and validated with the actual session's SafetyLimits.
    """
    import pandas as pd
    from openpyxl.comments import Comment  # type: ignore[import-untyped]
    from openpyxl.styles import Alignment, Font  # type: ignore[import-untyped]

    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        raise ValueError("template path must end in .xlsx")
    examples = [
        dict(measurement_id="example-pulse", protocol="bipolar_pulse", current_a=1e-4, pulse_s=1),
        dict(
            measurement_id="example-cycling",
            protocol="cycling",
            current_a=1e-4,
            v_low=0.1,
            v_high=2,
            cycles=1,
            max_step_s=600,
            rest_s=10,
        ),
        dict(
            measurement_id="example-soc",
            protocol="soc_position",
            current_a=1e-4,
            target_v=1,
            max_step_s=600,
            rest_s=10,
        ),
        dict(measurement_id="example-rest", protocol="rest", rest_s=10),
    ]
    for pulse_s in renard_series(0.1, 100):
        examples.append(
            dict(
                measurement_id=f"soc01-pulse-{pulse_s:g}s",
                protocol="bipolar_pulse",
                current_a=1e-4,
                pulse_s=pulse_s,
                sample_id="SOC01-placeholder",
            )
        )
    for example in examples:
        example["notes"] = "PLACEHOLDER: review current, voltage window and limits before use."
    descriptions = dict(_COLUMNS)
    descriptions["measurement_id"] = "# " + descriptions["measurement_id"]
    readme = [
        ("Run sheet", "One row per run. Blank cells use defaults; required blanks are errors."),
        (
            "Description row",
            "Row 2 says what each column does. It starts with # and is skipped; start any "
            "other row's measurement_id with # to skip that run.",
        ),
        (
            "Safety",
            "Current 1e-4 A and voltages are placeholders; review before use. "
            "Pass SafetyLimits from the notebook. Wiring, ranges and HIGH-Z stay in the session.",
        ),
        (
            "Metadata",
            "Extra columns are allowed and retained. Store IDs as text. "
            "Dates/times are saved as ISO strings; blank cells become null.",
        ),
        (
            "Sampling",
            "For non-pulse protocols, either fast field enables two-phase sampling; "
            "the other defaults to 0.006 s / 0.5 s. Short steps use only fast sampling.",
        ),
        *_COLUMNS.items(),
    ]
    with path.open("wb" if overwrite else "xb") as stream:
        with pd.ExcelWriter(stream, engine="openpyxl") as writer:
            pd.DataFrame([descriptions, *examples], columns=list(_COLUMNS)).to_excel(
                writer,
                sheet_name="Runs",
                index=False,
            )
            pd.DataFrame(readme, columns=["Column / topic", "Description"]).to_excel(
                writer,
                sheet_name="README",
                index=False,
            )
            for sheet in writer.book.worksheets:
                sheet.auto_filter.ref = sheet.dimensions
                for cell in sheet[1]:
                    cell.font = Font(bold=True)
                for cells in sheet.iter_rows(min_row=2):
                    for cell in cells:
                        cell.alignment = Alignment(vertical="top", wrap_text=True)
            runs_sheet, readme_sheet = writer.book["Runs"], writer.book["README"]
            # Size Runs columns by header and values; the description row wraps
            # and scrolls away, so each header also carries it as a hover note.
            widest_description = 0.0
            for column in runs_sheet.columns:
                values = [column[0], *column[2:]]
                width = min(40, max(24, *(len(str(cell.value or "")) + 2 for cell in values)))
                runs_sheet.column_dimensions[column[0].column_letter].width = width
                widest_description = max(widest_description, len(str(column[1].value)) / width)
                column[1].font = Font(italic=True, color="595959")
                column[0].comment = Comment(_COLUMNS[column[0].value], "ophyd-electrochemistry")
            runs_sheet.row_dimensions[2].height = 15 * (math.ceil(widest_description) + 1)
            runs_sheet.freeze_panes = "B2"
            for cell in runs_sheet["A"][2:]:
                cell.number_format = "@"
            readme_sheet.freeze_panes = "A2"
            readme_sheet.column_dimensions["A"].width = 18
            readme_sheet.column_dimensions["B"].width = 100
    return path
