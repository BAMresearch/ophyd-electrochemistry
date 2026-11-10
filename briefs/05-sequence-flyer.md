# Brief 05 — sequence Flyer, `run_sequence` plan, run-file writer and live plot

Requires briefs 01–04.

## Goal

Run a `Sequence` under a real Bluesky `RunEngine` with live data: the Flyer
emits new records about once a second while the run is in progress, a callback
writes them to the brief-03 HDF5 run file as they arrive, and a second callback
redraws a V/I-vs-time plot. The only backend in this brief is the simulator
(brief 04), with its records released in wall time. The hardware backend
(brief O5, later) implements the same `SequenceBackend` protocol.

Do **not** reuse or modify `keithley/k2460/device.py` (`Keithley2460Device`) or
`acquisition.py`; they are tied to the older `AcquisitionRequest` programs.
You may read `device.py` for the threading/Status patterns
(`_start_worker`, `complete()` monitor, `stop()`); copy what fits.

## Files

- `src/ophyd_electrochemistry/sequences/backend.py` — request, protocol, simulated backend
- `src/ophyd_electrochemistry/sequences/flyer.py` — `SequenceFlyer`
- `src/ophyd_electrochemistry/sequences/plans.py` — `run_sequence`
- `src/ophyd_electrochemistry/sequences/callbacks.py` — `RunFileWriter`, `LiveSequencePlot`, `plot_run_file`
- `src/ophyd_electrochemistry/sequences/run_file.py` — add `set_time_origin` only
- `src/ophyd_electrochemistry/sequences/__init__.py` — export backend, flyer, plan names (not callbacks)
- `pyproject.toml` / `uv.lock` — add `plotly>=6,<7` to the `experiment` extra
- `tests/unit/test_sequence_flyer.py`, `tests/unit/test_sequence_callbacks.py`, `tests/unit/test_run_file.py`

Read also: `sequences/models.py`, `sequences/simulator.py`, `sequences/run_file.py`,
`sequences/run_sheet.py` (`RunSpec` only), `state.py`, `exceptions.py`.

## `backend.py`

```python
@dataclass(frozen=True)
class SequenceRequest:
    measurement_id: str
    sequence: Sequence
    limits: SafetyLimits
    start_mode: Literal["immediate", "external"] = "immediate"
    sample_id: str | None = None
    notes: str | None = None
    run_sheet_row: Mapping[str, object] = field(default_factory=dict)
    # __post_init__: nonempty stripped measurement_id, start_mode check,
    # validate(sequence, limits).

    @classmethod
    def from_run_spec(cls, spec: RunSpec, limits: SafetyLimits) -> "SequenceRequest": ...

@dataclass(frozen=True)
class SequencePoll:
    readout: dict[str, NDArray]          # READOUT_FIELDS, possibly zero-length
    events: list[tuple[float, int, int, str, str]]   # (t_s, step, repeat, kind, detail)
    outcome: str | None                  # None while not terminal; else a run-file outcome
    detail: str = ""

class SequenceBackend(Protocol):
    def connect(self) -> None: ...
    def close(self) -> None: ...
    def inspect(self) -> tuple[DeviceState, bool | None]: ...   # (state, output on?)
    def equipment(self) -> dict[str, str | int | float | bool]: ...  # flat, RunFile rules
    def prepare(self, request: SequenceRequest) -> None: ...  # -> PREPARED, output OFF
    def kickoff(self) -> None: ...   # immediate -> RUNNING; external -> WAITING_START, output OFF
    def time_origin_epoch_s(self) -> float | None: ...  # UTC epoch of t_s = 0; None before start
    def poll(self, max_records: int) -> SequencePoll: ...  # next unread records/events, in order
    def abort(self, reason: str) -> None: ...   # -> ABORTED, output OFF
    def recover(self) -> None: ...   # ABORTED/ERROR -> IDLE
```

`poll` returns records and events in order without gaps or repeats, at most
`max_records` records per call. It is the only data path; there is no separate
"read everything at the end".

### `SimulatedSequenceBackend(cell=CellModel(), *, speed=1.0, seed=0, external_start_delay_s=None, clock=time.monotonic, wall_clock=time.time)`

- `prepare`: re-validate, run `simulate_sequence` once and keep the result.
- Virtual time after start = `speed · (clock() − start)`; `speed=math.inf`
  releases everything at once (tests). `poll` releases records and events with
  `t_s ≤` virtual time. It becomes terminal (state COMPLETE, output OFF,
  outcome = the simulation's outcome) once virtual time has passed the last
  record/event and everything has been returned.
- External start: WAITING_START until `fire_start()` is called (any thread), or
  automatically `external_start_delay_s` wall seconds after kickoff if set.
  `time_origin_epoch_s()` is `wall_clock()` at the start moment.
- `abort(reason)`: from WAITING_START/RUNNING, append an `("abort", reason)` event
  at the current virtual time, drop records after it (they never happened),
  outcome `aborted`; output OFF. Abort when idle/terminal is a no-op.
- Output is on only in RUNNING. `inspect` reports it.
- `equipment()`: `backend="simulator"`, `seed`, `speed` as text (inf is not
  JSON-safe), the scalar cell fields, and `ocv_soc`/`ocv_v` as JSON text.

## `flyer.py` — `SequenceFlyer(ophyd.Device)`

`SequenceFlyer(backend, *, name, poll_period_s=1.0, chunk_records=2000, start_timeout_s=None, completion_margin_s=60.0)`

- `stage`: `backend.connect()`, require output confirmed OFF (`ShutdownUnconfirmed`
  otherwise) and a non-active state. `unstage`: stop if active, confirm OFF, close.
- `prepare(request) -> Status`: requires staged and IDLE/COMPLETE; calls
  `backend.prepare`, then checks `inspect() == (PREPARED, False)`. Stores the
  request and `equipment` (property) for the plan.
- `kickoff() -> Status`: finishes when the state is RUNNING, or WAITING_START
  with output OFF (external start), or already COMPLETE.
- `complete() -> Status`: starts one monitor thread. Every `poll_period_s` it
  drains `backend.poll(chunk_records)` until a short read, appends to an
  internal pending buffer under a lock, and finishes the status when the poll
  is terminal and drained:
  - outcome `completed` or `stopped_cutoff` → success, after confirming
    `(COMPLETE, False)`; `stopped_cutoff` is a programmed instrument cutoff, not
    a host error. The `outcome` and `outcome_detail` properties hold it.
  - outcome `aborted`/`error` → `AcquisitionAborted(detail)`.
  - deadlines (wall time): `start_timeout_s` (None = wait forever) while
    WAITING_START; `nominal_duration_s(sequence) + completion_margin_s` after
    the start. On expiry abort with reason `host_completion_timeout` and fail.
    (The simulator therefore requires `speed ≥ 1`; validate that.)
- `collect()`: yields and clears everything pending; legal in any state after
  kickoff, including ABORTED (so the plan can drain after an abort).
  Two streams, distinguished by disjoint key sets (bluesky groups legacy
  collect events by key set; verify with a RunEngine test):
  - `readout`: keys exactly `READOUT_FIELDS`.
  - `sequence_events`: keys `event_t_s`, `event_step_index`, `event_repeat_index`,
    `event_kind`, `event_detail`.
  - Event `time` = `time_origin_epoch_s + t_s` (both streams). `describe_collect`
    returns both streams with dtype/shape/units (`source` = `"sequence:<field>"`).
- `abort(reason) -> Status`: backend abort, confirm `(ABORTED, False)`, then a
  final drain into pending; fail the completion status. `stop(success=False)`:
  abort and wait (bounded); never labels an interrupted run successful.
  `pause` → stop; `resume` raises `UnsupportedCapabilityError`.
- `recover() -> Status`, `read_configuration`/`describe_configuration` (backend
  kind, poll period, chunk size), `trigger`/`read`/`describe` may be minimal
  (no step scans with this device).
- One backend call at a time (operation lock); abort may wait at most one
  bounded poll.

## `plans.py`

```python
def run_sequence(flyer, request: SequenceRequest, *, md=None, flush_period_s=1.0):
    """stage → prepare → open_run(md) → kickoff → collect_while_completing → close_run → unstage."""
```

- Prepare **before** `open_run`, so validation/loading errors open no run and
  create no file.
- Start-document metadata (user `md` may add keys; a collision with these
  raises `ValidationError`): `measurement_id`, `sample_id`, `notes`,
  `start_mode`, `sequence` (`to_dict`), `safety_limits` (`to_dict`),
  `run_sheet_row`, `equipment`, `expected_records`, `nominal_duration_s`,
  `plan_name="run_sequence"`.
- Use `bps.collect_while_completing([flyer], [flyer], flush_period=flush_period_s)`.
- Wrap the body (inside the run) in `bpp.finalize_wrapper` whose final plan
  does `bps.stop(flyer)` (no-op when terminal) and one last `bps.collect(flyer,
  return_payload=False)`, so records and the `abort` event reach the documents
  after a Ctrl-C/`RE.abort()`. The finalizer must not mask the original error.
- Return the flyer's `outcome`.

## `callbacks.py` (imports h5py/plotly; not exported from `sequences`)

### `RunFileWriter(directory)` — a `bluesky.callbacks.core.CallbackBase`

- `start`: only for `plan_name == "run_sequence"`. Creates
  `<directory>/<safe measurement_id>_<UTC YYYYmmddTHHMMSSZ>.h5` via
  `RunFile.create` (decode `sequence`/`safety_limits` with `sequence_from_dict`
  / `limits_from_dict`); `safe` = characters outside `[A-Za-z0-9._-]` → `_`.
  `path` attribute is set. Errors propagate (the RunEngine then aborts before
  kickoff — test this).
- `event_page` for `readout`: one `append_readout` per page; on the first page
  `set_time_origin(time[0] − t_s[0])`. For `sequence_events`: `append_event` per
  row. Also handle single `event` documents (unpack to a page).
- `stop`: outcome = `aborted` if `exit_status == "abort"`, `error` if `"fail"`
  (detail = stop `reason`), else `stopped_cutoff` if any `stop` event was
  written, else `completed`. `finalize`, then `close`. Close also when anything
  above raises.

### `run_file.py` addition

`RunFile.set_time_origin(epoch_s: float)`: once per file, writes `/run` attrs
`time_origin_epoch_s` (float) and `time_origin_utc` (ISO 8601, µs, `Z`); raises
if already set or finalized. Readers see them in `read_run_file(...)["run"]`.

### `LiveSequencePlot(*, title=None, min_redraw_s=2.0, max_points=20000, render=None)`

- Accumulates `readout` pages; two stacked plotly subplots sharing x: voltage
  (V) and current (A) vs time since start (s). Step-start events are not drawn
  (too many); `cutoff`/`stop`/`abort` events become vertical lines with labels.
- Redraws at most every `min_redraw_s`, and always at `stop`. Display-only
  decimation to `max_points` per trace by stride (the file keeps everything).
- `render(fig)` is injectable for tests. The default uses one
  `IPython.display.display(fig, display_id=True)` handle and `handle.update(fig)`
  afterwards (works from the RunEngine's thread; no ipywidgets). Import IPython
  lazily.
- Title: `measurement_id` from the start document plus the current outcome.

### `plot_run_file(path) -> plotly Figure`

Same layout, built from `read_run_file`, full resolution.

## Tests (`RunEngine({})`, simulator with `speed=math.inf` unless stated)

1. Full bipolar pulse run: `readout` stream record count == `expected_records`,
   values equal `simulate_sequence(...)` for the same seed/cell; events stream
   has all `step_start` events in order; return value `completed`.
2. Live: `speed` chosen so a ~3 s wall run (e.g. a 30 s sequence at `speed=10`)
   with `flush_period_s=0.2` produces **more than one** `readout` event page.
3. `RunFileWriter`: file has all records, events, the run-sheet row, equipment,
   `time_origin_*` and outcome `completed`; a `soc_position`/cutoff-`STOP`
   sequence gives `stopped_cutoff` with the run reported to the caller.
4. Abort mid-run (`RE.abort()` from a timer thread, or a plan that raises after
   some collects): output OFF confirmed, run file outcome `aborted` with the
   `abort` event and all records up to it, completion status failed, flyer can
   `recover` and run again.
5. External start: kickoff finishes while WAITING_START with output OFF;
   `fire_start()` from a thread lets the run complete; `start_timeout_s` expiry
   aborts with `host_completion_timeout`.
6. A pre-existing/unwritable run-file path makes the run fail before kickoff
   (backend never left PREPARED/output never on).
7. Invalid request (sequence outside limits) fails in prepare; no start document.
8. `LiveSequencePlot` with a recording `render`: redraw throttling (fake clock
   or `min_redraw_s=0` vs large), final redraw at stop, decimation cap,
   cutoff line present. `plot_run_file` round-trips a written file.
9. `md` key collision raises; `SequenceRequest.from_run_spec` on a template row.

No test may sleep longer than ~5 s in total across this file set.

## Out of scope

Hardware backend and TSP, QueueServer, the per-run notebook (brief 06),
sequence preview plots, ipywidgets/FigureWidget.

## Commit message

`feat(sequences): sequence Flyer with live collection, run_sequence plan and run-file/plot callbacks`
