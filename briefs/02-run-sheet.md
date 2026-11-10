# Brief 02 — Excel run sheet: template and loader

Requires brief 01 (`ophyd_electrochemistry.sequences`) to be merged.

## Goal

During the beamtime each Excel row describes one run. A notebook loads the
sheet, picks a row by `measurement_id`, and gets a validated `Sequence` plus all
row values for metadata. Rarely changing settings (safety limits, ranges,
wiring) are not in the sheet; they are passed in as `SafetyLimits`.

## Files

- `src/ophyd_electrochemistry/sequences/run_sheet.py`
- `tests/unit/test_run_sheet.py`
- `pyproject.toml`: add optional extra `experiment = ["pandas>=2.2,<3",
  "openpyxl>=3.1,<4"]` and include it in the dev dependency group so tests run;
  run `uv lock`.

## Columns

First column `measurement_id` (str, required, unique, non-empty). Then:

| Column | Type | Used by | Notes |
|---|---|---|---|
| `protocol` | str | all | `bipolar_pulse`, `cycling`, `soc_position`, `rest` |
| `start_mode` | str | all | `immediate` (default) or `external` |
| `current_a` | float | pulse, cycling, soc | signed for `soc_position` |
| `pulse_s` | float | pulse | |
| `rest_s` | float | pulse, cycling, soc, rest | pulse default `max(pulse_s, 1)` |
| `repeats` | int | pulse | default `log_repeats(pulse_s)` |
| `pre_rest_s` | float | pulse | default 10 |
| `v_low`, `v_high` | float | cycling; optional pulse cutoffs | |
| `target_v` | float | soc | |
| `cycles` | int | cycling | |
| `cv_i_abs_min_a` | float | cycling | blank = no CV step |
| `max_step_s` | float | cycling, soc | |
| `fast_interval_s`, `fast_window_s`, `interval_s` | float | all | builder defaults when blank |
| `sample_id` | str | all | optional |
| `notes` | str | all | optional |

Unknown extra columns are allowed and preserved. Blank cells mean "use default";
a required value that is blank is an error.

## API

```python
@dataclass(frozen=True, slots=True)
class RunSpec:
    measurement_id: str
    sequence: Sequence
    start_mode: Literal["immediate", "external"]
    sample_id: str | None
    notes: str | None
    row_number: int                 # 1-based Excel row number (header = row 1)
    row: Mapping[str, object]       # every column as read (JSON-safe; NaN -> None)

def load_run_sheet(path, limits: SafetyLimits, *, sheet_name=0) -> dict[str, RunSpec]
def make_template(path, *, overwrite=False) -> Path
```

- `load_run_sheet` validates every row (including `validate(sequence, limits)`)
  and raises one `ValidationError` listing all bad rows as
  `row <n> (<measurement_id>): <column>: <problem>`.
- Duplicate or missing `measurement_id` is an error.
- `make_template` writes an `.xlsx` with the header, one example row per
  protocol, and the full R5 pulse series 0.1–100 s for one SOC
  (`current_a = 1e-4` as an obvious placeholder), plus a second sheet `README`
  describing the columns (from the table above). Refuses to overwrite unless
  `overwrite=True`.

## Tests

Template round trip (make → load gives 16 pulse runs + examples, all valid);
defaults applied; extra column preserved in `row`; error aggregation over
several bad rows; duplicate id; NaN → None; `start_mode` validation. Use
`tmp_path`.

## Out of scope

Notebook, plotting, hardware, HDF5.

## Commit message

`feat(sequences): Excel run-sheet template and loader`
