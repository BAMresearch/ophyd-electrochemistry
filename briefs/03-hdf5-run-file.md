# Brief 03 — incremental HDF5 run file

Requires brief 01 (`ophyd_electrochemistry.sequences`).

## Goal

One HDF5 file per run, written incrementally while the run is in progress (so a
crash keeps everything received so far), with equipment settings, run settings
and readouts in separate groups. Files are never silently overwritten.

## Files

- `src/ophyd_electrochemistry/sequences/run_file.py`
- `tests/unit/test_run_file.py`
- `pyproject.toml`: add `h5py>=3.11,<4` to the `experiment` extra (create the
  extra if brief 02 has not yet); include it in the dev group; run `uv lock`.

## Layout (schema `ophyd-electrochemistry.run-file.v1`)

```
/                 attrs: schema, measurement_id, created_utc, package_version
/equipment        attrs: flat str/int/float/bool settings (model, serial,
                  firmware, resource, runtime_build, runtime_sha256, ranges,
                  nplc, offmode, terminals, sense, line_frequency_hz, ...)
/run              attrs: start_mode, sample_id, notes, started_utc,
                  finished_utc, outcome, outcome_detail
  sequence        scalar str dataset: JSON of to_dict(sequence)
  safety_limits   scalar str dataset: JSON of to_dict(limits)
  run_sheet_row   scalar str dataset: JSON of the full Excel row (or "{}")
  extra           scalar str dataset: JSON of any further caller metadata
/readout          1-D resizable, chunked, gzip-compressed datasets, equal length:
  t_s (f8)              time since sequence start
  voltage_v (f8), current_a (f8)
  step_index (i4), repeat_index (i4)
  source_status (i4), measure_status (i4)
                  each dataset has a `units` attr ("s", "V", "A", "" ...)
/events           1-D resizable compound or parallel datasets: t_s, step_index,
                  repeat_index, kind (str: "step_start", "cutoff", "stop",
                  "abort", "error"), detail (str)
```

## API

```python
READOUT_FIELDS: tuple[str, ...]  # the seven names above, in order

class RunFile:
    @classmethod
    def create(cls, path, *, measurement_id, equipment: Mapping, sequence,
               limits, start_mode, sample_id=None, notes=None,
               run_sheet_row: Mapping | None = None,
               extra: Mapping | None = None) -> "RunFile"
        # refuses an existing path (FileExistsError); writes started_utc
    def append_readout(self, chunk: Mapping[str, ArrayLike]) -> None
        # all seven fields, equal lengths; flushes after each append
    def append_event(self, t_s, step_index, repeat_index, kind, detail="") -> None
    def finalize(self, outcome: str, detail: str = "") -> None
        # outcome in {"completed", "stopped_cutoff", "aborted", "error"}
        # writes finished_utc; further appends raise
    def close(self) -> None   # also context manager; close without finalize
                              # leaves outcome="incomplete"

def read_run_file(path) -> dict  # {"attrs", "equipment", "run", "sequence",
                                 #  "safety_limits", "run_sheet_row", "extra",
                                 #  "readout": {name: np.ndarray}, "events": ...}
```

Non-scalar or non-JSON-safe equipment values raise `ValidationError`. JSON uses
`sort_keys=True`. Use h5py SWMR-compatible settings where simple
(`libver="latest"`) so a notebook can read the file during the run; document it.

## Tests

Create → several appends → finalize → `read_run_file` equals inputs; refuse
overwrite; unequal chunk lengths rejected; append after finalize rejected;
unfinalized file reads back with outcome `incomplete`; sequence JSON round-trips
via `sequence_from_dict`. Use `tmp_path`.

## Out of scope

Bluesky document export, live plotting, hardware.

## Commit message

`feat(sequences): incremental HDF5 run file`
