# Brief 08 — run viewer notebook (read-only)

Requires briefs 01–06 and O7 (QueueServer). Decision (3 Nov): a viewer notebook
for the beamtime; no Grafana or web service for now.

## Goal

Under the QueueServer, the live plot runs in the worker, which has no display.
Operators need a separate, read-only notebook that:

1. lists every run in a data directory;
2. follows the run being written, updating in place;
3. shows one run at full resolution;
4. overlays several runs, voltage against time or against charge (the
   material scientists compare pulsed and non-pulsed (de)sodiation; see
   `docs/beamtime-concept.md`).

It only reads HDF5 files written by `RunFileWriter`. It never connects to the
instrument or the RE Manager, and it never writes or repairs a file.

## Files

- `src/ophyd_electrochemistry/sequences/viewer.py` (new)
- `notebooks/k2460_view.ipynb` (new, clean: no outputs or execution counts)
- `tests/unit/test_viewer.py`, `tests/contract/test_view_notebook.py`
- `docs/operator-guide.md`: short "Watching runs" paragraph in step 8 (replace
  the `plot_run_file` hint)

Read: `sequences/run_file.py` (`read_run_file`, the `incomplete` outcome,
SWMR), `sequences/callbacks.py` (`sequence_figure`, `plot_run_file`,
`LiveSequencePlot._display`), `tests/contract/test_run_notebook.py` (how a
notebook is executed in tests), `docs/operator-guide.md`.

## API (`sequences/viewer.py`, `experiment` extra only)

```python
def run_index(data_dir: str | Path) -> pandas.DataFrame
```
- One row per `*.h5` directly in `data_dir`, newest first.
- Columns: `file`, `measurement_id`, `sample_id`, `protocol` (from
  `run_sheet_row`), `start_utc`, `duration_s` (last `t_s`, 0 if none),
  `records`, `outcome` (`incomplete` while written or after a crash),
  `cutoffs`, `stops`, `aborts`, `errors`, `detail`.
- An unreadable file becomes a row with outcome `unreadable` and the
  exception text in `detail`; it never raises for one bad file.

```python
def charge_mah(readout: Mapping[str, NDArray]) -> NDArray
```
Cumulative charge ∫ I dt in mAh: trapezoidal, from the first record, signed (a
negative current makes it fall). Same length as the readout.

```python
def decimate(x: NDArray, y: NDArray, max_points: int) -> NDArray  # sorted indices
```
- At most `max_points` indices; all of them when `len(x) <= max_points`.
- Keeps the first and last sample, and the minimum and maximum of `y` in each
  of `max_points // 2` equal-count bins. A 0.1 s pulse inside a 10 h run must
  stay visible.
- Plain stride decimation, which `sequence_figure(max_points=...)` uses today,
  can drop whole pulses; do not use it here.

```python
def run_figure(path, *, x: Literal["time", "charge"] = "time", max_points: int = 20_000) -> go.Figure
```
- The `sequence_figure` layout (voltage above current), decimated per trace
  with `decimate`.
- Title: measurement ID, outcome and record count.
- `x="charge"` uses `charge_mah` on the x axis.

```python
def overlay_figure(paths, *, x: Literal["time", "charge"] = "charge", max_points: int = 20_000) -> go.Figure
```
- One voltage trace per run, labelled by measurement ID (and sample ID if set).
- Each run's x starts at 0.

```python
def follow(path, *, period_s: float = 2.0, max_points: int = 20_000, timeout_s: float | None = None,
           display=None, sleep=time.sleep) -> str
```
- Shows `run_figure(path)` in a single notebook output and replaces it in place
  every `period_s`.
- Use the raw-MIME-bundle `display_id` technique of `LiveSequencePlot._display`;
  plain `fig.show()` appends a new figure each time.
- Returns the outcome once it is no longer `incomplete`, or when `timeout_s`
  passes. KeyboardInterrupt (the notebook's stop button) returns cleanly.
- `display` and `sleep` are injectable for tests.

```python
def latest_run(data_dir) -> Path | None
```
The newest `*.h5` file in `data_dir`.

Numbers come from the file only. Do not claim more timing precision than
`t_s` has.

## Notebook `notebooks/k2460_view.ipynb`

The settings cell is tagged `settings`:

```python
DATA_DIR = Path.home() / "k2460-data"
FOLLOW = None              # measurement file name, or None for the newest run
COMPARE = []               # file names to overlay
X_AXIS = "charge"          # or "time"
```

Sections, each a markdown cell and one short code cell:
1. Overview: `run_index(DATA_DIR)`.
2. Follow: `follow(...)` on `FOLLOW` or `latest_run`. Say that the cell keeps
   running until the run ends; use the stop button to leave it early.
3. One run at full view: `run_figure`, plus the events table.
4. Compare: `overlay_figure(COMPARE or the two newest finished runs)`.

Top markdown: the notebook is read-only, safe to run while the QueueServer
writes, and the data directory must be on local disk (SWMR).

## Tests

- Build run files offline with `RunFileWriter` under a RunEngine on the
  simulator, as `test_sequence_flyer.py` does. Include one completed run, one
  with a STOP cutoff, and one aborted run.
- An "in progress" file: `RunFile.create(...)` with appended chunks, not
  finalized, so its outcome is `incomplete`.
- A corrupt file: random bytes named `*.h5`.
- `run_index`:
  - row values, newest first;
  - an in-progress file reads as `incomplete`;
  - the corrupt file becomes an `unreadable` row with the others intact.
- `charge_mah`:
  - a constant 1 mA for 3600 s gives 1 mAh at the end;
  - the sign follows the current;
  - an empty readout gives an empty array.
- `decimate`:
  - never more than `max_points` indices;
  - the first and last index are kept;
  - a single-sample spike inside 10^6 points is kept;
  - all indices are returned when the data are short;
  - the indices are sorted and unique.
- `run_figure` and `overlay_figure`: trace counts, axis titles, x starting at 0,
  points per trace ≤ `max_points`.
- `follow`:
  - a writer thread appends to an in-progress file and then finalizes it;
  - with a fake display and fake sleep, there is one display call followed by
    updates, the call returns the final outcome, and `timeout_s` returns
    `incomplete`.
- Contract test: execute the notebook cells (as `test_run_notebook.py` does)
  against a temporary data directory with the files above. Monkeypatch
  `Figure.show`/display; `FOLLOW` points at a finished run, so `follow`
  returns at once.
- The template is clean: no outputs, exactly one settings cell.

## Out of scope

Grafana or any web service, CSV export, per-pulse statistics, changes to the
writer, run-file schema or callbacks (except importing `sequence_figure`), and
any instrument access.

## Commit message

`feat(viewer): read-only run viewer notebook and helpers`
