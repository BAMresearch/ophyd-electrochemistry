# Brief 06 — per-run notebook template (simulator first)

Requires briefs 01–05 and the SWMR run file (`run_file.py` as committed).

## Goal

One Jupyter notebook per run. The operator copies the template for a run-sheet
row (or edits one ID), reviews a summary and a simulated preview, confirms
(hardware only), runs it with a live plot, and ends with the run file plotted.
The executed notebook, saved next to the HDF5 file, is the human record of the
run. Only the simulator backend exists; hardware arrives with O5, so the
notebook must make that switch a one-line setting without restructuring.

## Files

- `src/ophyd_electrochemistry/sequences/notebook.py` — helpers below
  (imports h5py/plotly/nbformat; not exported from `sequences`)
- `notebooks/k2460_run.ipynb` — the template
- `tests/contract/test_run_notebook.py`, `tests/unit/test_notebook_helpers.py`
- `pyproject.toml` / `uv.lock` — add `nbformat>=5.10,<6` to the `experiment`
  extra (it is already in the `notebook` group)

Read also: `sequences/backend.py`, `sequences/flyer.py`, `sequences/plans.py`,
`sequences/callbacks.py`, `sequences/run_sheet.py`, `sequences/run_file.py`,
`sequences/simulator.py`, `tests/contract/test_quickstart_notebook.py` (test pattern).

## `notebook.py`

```python
def run_summary(request: SequenceRequest) -> str
```
Plain-text block: measurement_id, protocol (from the run-sheet row, if any),
sample_id, notes, start_mode, number of expanded steps, nominal duration
(h:mm:ss), expected records, the safety limits, and the run-sheet row as
`key: value` lines. No hardware claims.

```python
def preview_figure(request: SequenceRequest, cell: CellModel = CellModel(), *, seed: int = 0) -> go.Figure
```
`simulate_sequence` + the same two-panel layout as the live plot (reuse
`callbacks._figure`; rename it to a public `sequence_figure` if you need to
import it, and keep the callbacks using it). Title says
"Preview — simulated cell, not a prediction".

```python
def require_confirmation(request: SequenceRequest, confirmed_id: str, *, hardware: bool) -> None
```
No-op for the simulator. For hardware, `confirmed_id.strip()` must equal
`request.measurement_id`, else `ValidationError` naming what the operator must
check (cell, wiring, voltage window, current limit, output-off mode HIGH-Z) and
the variable to set. (AGENTS.md tier 2: every energized cell run is confirmed.)

```python
def new_run_notebook(run_sheet, measurement_id, directory, limits, *, template=None) -> Path
```
Validate first: `load_run_sheet(run_sheet, limits)` must contain the ID (use
the session limits, never permissive ones). Copy the template (default:
`notebooks/k2460_run.ipynb` located relative to the repository; accept an
explicit path) with nbformat; set the settings cell's `MEASUREMENT_ID` and
`RUN_SHEET` values; clear all outputs; write `<directory>/<safe id>.ipynb`
(same `safe` rule as `RunFileWriter`). Refuse to overwrite. Locate the
settings cell by a `"tags": ["settings"]` cell-metadata tag and the two
assignments by line prefix; fail loudly if either is missing.

Also a console entry point `ophyd-electrochemistry-new-run` (in `pyproject.toml`
`[project.scripts]`) taking `run_sheet measurement_id directory` and the limits
as `--v-min --v-max --i-max --max-duration-s --max-records` (all required).

## Notebook cells (`notebooks/k2460_run.ipynb`)

Markdown cells are short and operator-facing. Code cells, in order:

1. **Settings** (tag `settings`): `MEASUREMENT_ID`, `RUN_SHEET`, `DATA_DIR`
   (default `Path.home() / "k2460-data"`; a markdown note: local disk, SWMR),
   `LIMITS = SafetyLimits(...)` (dummy-cell values, clearly marked to review),
   `BACKEND = "simulator"`, `SIM_SPEED = 10.0`, `CONFIRM_MEASUREMENT_ID = ""`.
2. **Load:** `runs = load_run_sheet(RUN_SHEET, LIMITS)`; show the run IDs with
   protocol and the selected one marked; `request =
   SequenceRequest.from_run_spec(runs[MEASUREMENT_ID], LIMITS)`; a clear error
   listing available IDs if missing.
3. **Review:** `print(run_summary(request))`; `preview_figure(request)` displayed.
4. **Device:** `BACKEND == "simulator"` → `SimulatedSequenceBackend(speed=SIM_SPEED)`;
   any other value raises `NotImplementedError("hardware backend arrives with O5")`.
   `flyer = SequenceFlyer(backend, name="k2460")`.
5. **Confirm:** `require_confirmation(request, CONFIRM_MEASUREMENT_ID, hardware=BACKEND != "simulator")`.
6. **Run:** new `RunEngine({})`; subscribe `RunFileWriter(DATA_DIR)` and
   `LiveSequencePlot()`; `outcome = RE(run_sequence(flyer, request))`; print
   outcome and `writer.path`. On `RunEngineInterrupted`/exceptions print what
   happened, the file path, `flyer.outcome`, and that the output was confirmed
   OFF (`flyer.output_enabled is False`), then re-raise.
7. **Result:** `plot_run_file(writer.path)`; a short event summary (counts of
   cutoff/stop/abort events, the final outcome, records written vs expected).
8. **Markdown "If something went wrong":** `flyer.recover()` after an abort;
   run files of crashed kernels are read by `read_run_file`, other tools need
   `repair_run_file`; how to start the next run (`ophyd-electrochemistry-new-run`).

The template is committed **without outputs**. `SIM_SPEED = 10` keeps a demo
run short; the notebook says to use `1.0` for realistic pacing.

## Tests

- Contract (pattern of `test_quickstart_notebook.py`, `exec` per code cell with
  source replacements): with a template run sheet from `make_template` in
  `tmp_path`, `DATA_DIR` in `tmp_path`, `SIM_SPEED = math.inf`, a short example
  ID: the notebook runs end to end; outcome `completed`; the run file exists and
  reads back with the expected record count; `flyer.output_enabled is False`;
  `LiveSequencePlot` uses a recording `render` (patch the display path, no
  IPython needed) and the preview/result figures are plotly Figures.
- Same notebook with `BACKEND = "k2460"` stops at the device cell with
  `NotImplementedError`, before any run file exists.
- The committed template has no outputs and exactly one `settings`-tagged cell.
- `new_run_notebook`: fills ID and sheet, clears outputs, refuses overwrite,
  rejects unknown IDs, safe file name; CLI round trip via `main([...])`.
- `require_confirmation`: simulator no-op; hardware mismatch/blank raises and
  names `CONFIRM_MEASUREMENT_ID`; exact (whitespace-stripped) match passes.
- `run_summary` contains the ID, duration and expected records.

## Out of scope

Hardware backend, QueueServer, any change to the Flyer/plan/callbacks beyond
the `_figure` rename, verifying rendering in a real Jupyter frontend (Claude
does that in review).

## Commit message

`feat(sequences): per-run notebook template, run helpers and new-run CLI`
