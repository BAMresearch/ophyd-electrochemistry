# Data and the viewer

## Run files

Every run writes one HDF5 file to the session's `data_dir`, named
`<measurement_id>_<UTC start>.h5`. The file is written while the run goes on
and flushed after every read, so it can be opened at any time. Everything is
under `/entry`, and every value carries a `description` attribute and, where
it has one, `units` (NeXus style, so h5web, silx and HDFView show them;
`/entry/readout` opens as a voltage-against-time plot):

| Group | Content |
|---|---|
| `readout` | Every reading: `t_s` (s from the run's time zero), `voltage_v`, `current_a`, `step_index`, `repeat_index`, `source_status`, `measure_status` (the 2460's status bits, listed in the attribute) |
| `events` | `step_start`, `cutoff`, `stop`, `abort` and `error`, each with `t_s`, step, repeat and detail |
| `run` | Attributes: start and finish (UTC), `outcome`, `outcome_detail`. Datasets: start mode, notes, `time_origin_epoch_s` (UTC of `t_s = 0`, Unix epoch; NaN until the first reading), expected records and duration; JSON: the sequence, the session limits, the run-sheet row, other run metadata (`extra`); `recovery` after `oe-recover-run` |
| `sample` | `name` (the sample ID), `session_description` (the session file's header comment) and the session's `[sample]` table, with units from the key ([Sessions](sessions.md#the-sample-table)) |
| `instrument` | What the 2460 and its program used, in groups: `identity`, `runtime`, `clock`, `wiring`, `digital_io`, `limits`, `timing`, `program`, `source_1`…, `measure_1`…, `eventlog` (`simulator` for the simulator) |
| `session` | The session file as loaded (`text`, `path`, `sha256`) and its resolved settings |
| `provenance` | Bluesky `bluesky_uid` and `scan_id`, the run sheet's path and SHA-256, host, git revision and package versions |

Files written before 13 Nov 2026 (`schema` …`run-file.v1`) have `readout`,
`events`, `run` and a flat `equipment` group at the top level instead;
`read_run_file` reads both.

`outcome` is `completed`, `stopped_cutoff` (a threshold ended it; normal),
`aborted` or `error`. `incomplete` means the run is still being written, or its
writer crashed. Read files in Python with:

```python
from ophyd_electrochemistry.sequences.run_file import read_run_file

run = read_run_file(path)
run["readout"]["voltage_v"], run["events"]["kind"], run["run"]["outcome"]
run["run_sheet_row"], run["equipment"]  # equipment: the instrument groups, flattened
run["sample"], run["session"], run["provenance"]  # empty for v1 files
```

To refer to a run from a SAXS NeXus file, link its entry with a relative file
name, e.g. `saxs["entry/electrochemistry"] = h5py.ExternalLink("hc01-sodiate_20261201T101500Z.h5", "/entry")`.
Absolute time of a reading: `time_origin_epoch_s + t_s` (UTC, laptop clock).

Other HDF5 tools (HDF5 1.10 or later) read completed files directly. For a file
whose writer crashed, `repair_run_file` makes a copy they can open.

**Timing:** times come from the 2460's own reading timestamps. `t_s = 0` is an
event-log entry (`OE_T0`) that the trigger model writes as it starts: right
after the START edge for an external start (within about 0.1 ms on the scope,
3 Nov), or when the program starts for an immediate start. Its UTC time
(`time_origin_epoch_s`; `read_run_file` also gives `time_origin_utc`) comes from mapping the instrument clock to the laptop's
clock before the run; the offset and its uncertainty (about half a ping round
trip, typically under 1 ms on the direct cable) are stored as
`clock_offset_s` and `clock_uncertainty_s` in `instrument/clock`. The laptop clock
itself is only as good as its NTP. The MARKER pulse at each step start lets
beamline data be aligned to the steps.

## The viewer notebook

The viewer only reads run files. It never talks to the 2460 or the
QueueServer, so it can stay open during runs, and stopping it does not affect a
measurement. Run it on the laptop that writes the data.

1. Once per session, copy it next to the data so your settings do not change
   the repository copy:

   ```sh
   cp notebooks/k2460_view.ipynb ~/k2460-data/hc01/viewer.ipynb
   ```

2. Open the copy in VS Code (kernel: the repository's `.venv`) or JupyterLab
   (`uv run --locked --with jupyterlab jupyter lab ~/k2460-data/hc01/viewer.ipynb`
   from the repository folder).
3. In the first cell set `DATA_DIR` to the session's `data_dir`, and optionally
   `FOLLOW`, `COMPARE` and `X_AXIS`. Run the cells from the top.

| Section | What it shows | Settings |
|---|---|---|
| **Overview** | One row per run file, newest first: ID, sample, protocol, start (UTC), duration, readings, outcome, and counts of cutoffs, stops, aborts and errors. Rerun to refresh | `DATA_DIR` |
| **Follow a run** | Voltage and current against time for the run being written, redrawn every 2 s until it ends; before the first readings (e.g. waiting for START) a status line instead. Stop it with the notebook's stop button (■); the measurement continues | `FOLLOW` = a file name, or `None` for the newest |
| **One run at full view** | The run reading by reading, with cutoff, stop and abort markers and the events table | `X_AXIS` = `"time"` or `"charge"` |
| **Compare runs** | Voltage of several runs overlaid, each starting at zero | `COMPARE` = file names (default: the two newest finished runs); `X_AXIS` |

- **Charge** (`X_AXIS = "charge"`) is the integral of the measured current in
  mAh from the start of each run. Sodiation is a negative current, so its charge
  decreases.
- **Thinning:** long runs are thinned for display by keeping each section's
  highest and lowest point, so short pulses stay visible. Zoom in for detail;
  the file always holds every reading.
- An `incomplete` file may be a run in progress or a crashed one. Follow
  cannot tell them apart, so stop it by hand if the run is not advancing.
  Unreadable files are listed in the overview with the reason.

## Per-run notebooks

A notebook made with `oe-new-run` writes its run file next to itself. Save the
executed notebook there too, so the notebook (human record) and the HDF5 file
(machine record) stay together.
