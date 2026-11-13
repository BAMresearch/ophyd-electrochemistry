# Technical overview

For developers and reviewers: how the package is built, which component does
what, and how a run passes through them. Read the [user guide](../user/index.md)
first for the operator's view.

## Layers

```text
 Excel run sheet ──► run_sheet.py ──► Sequence (models.py, builders.py)
 session TOML ─────► queueserver.py ─► SafetyLimits, backend choice
                            │
   QueueServer RE Manager   │  qserver/startup: build_namespace()
   └ worker: RunEngine ─────┤  plan run_from_sheet ─► run_sequence (plans.py)
                            │        documents ─► RunFileWriter (HDF5), live plot
                            ▼
                 SequenceFlyer (flyer.py)            classic ophyd Flyer
                            │  SequenceBackend protocol (backend.py)
              ┌─────────────┴──────────────┐
 Keithley2460SequenceBackend        SimulatedSequenceBackend
 (sequence_backend.py)              (backend.py + simulator.py)
   │ compile_program / Replayer (sequencer.py, pure)
   │ PyVISA transport (transport.py), one owner
   ▼
 packaged TSP runtime on the 2460 (tsp/sequencer.tsp)
   trigger model: timing, cutoffs, markers, READY/BUSY, output OFF
```

The core is `SequenceFlyer`, a classic ophyd Flyer that runs one finite
`Sequence` on any `SequenceBackend`. Bluesky (the `run_sequence` plan and a
RunEngine) sits on top of it; the QueueServer startup sits on top of Bluesky
and turns run-sheet rows into queued plans. Below the Flyer, the 2460 backend
compiles the sequence into a trigger model that the packaged TSP runtime builds
and runs on the instrument. The simulator backend replaces everything below the
Flyer with a synthetic cell.

## Division of tasks

| Component | Code | Does | Does not |
|---|---|---|---|
| **TSP sequencer runtime** | `keithley/k2460/tsp/sequencer.tsp` | Checks every numeric entry against the configured limits; builds the configuration lists, the reading buffer and the trigger model; sets and reads back HIGH-Z; arms; on the instrument: timing, voltage cutoffs, step markers, READY/BUSY, output OFF at end, stop and abort; serves bounded buffer reads | Decide what to run; run arbitrary code (only its own `oe_seq_*` functions are called) |
| **Compiler and replay** | `keithley/k2460/sequencer.py` | Pure. `compile_program(sequence, limits, profile)` lowers a sequence to source/measure entries, at most 63 blocks and a sample plan, with delays from the commissioned timing table. `Replayer` walks the plan over incoming records: step and repeat indices, events, outcome, consistency checks | Any I/O |
| **2460 backend** | `keithley/k2460/sequence_backend.py`, `sequencer_runtime.py`, `transport.py` | Owns the VISA session; checks identity and serial; installs the runtime when its build differs; uploads under `pcall`; maps the instrument clock to UTC with `eventlog.post` pings; polls status, event log and records; aborts with OFF confirmation; refuses to prepare over unread records; archives them | Pace samples; retry an uncertain mutation |
| **Simulator backend** | `sequences/backend.py`, `sequences/simulator.py` | Same protocol; precomputes the run on a synthetic cell and releases it against wall time. See [Simulator](simulator.md) | Predict a real cell |
| **Ophyd Flyer** | `sequences/flyer.py` | Bluesky lifecycle (`prepare`, `kickoff`, `complete`, `collect`, `stop`, `abort`, `recover`); a monitor thread polls the backend every `poll_period_s` and queues readout pages; host-side timeouts (no START, run overdue); confirms output OFF at every terminal state | Know about Excel, sessions or the QueueServer |
| **Bluesky plan** | `sequences/plans.py` | `run_sequence`: prepare before RunStart (so an invalid run opens no run), metadata (row, sequence, limits, equipment), kickoff, `collect_while_completing`, cleanup that drains records on interruption | Validation beyond the request |
| **Callbacks** | `sequences/callbacks.py`, `run_file.py`, `viewer.py` | `RunFileWriter`: one SWMR HDF5 file per run (NeX-ish v2 under `/entry`; `run_file_fields.py` places, describes and gives units to every value; `read_run_file` returns one flat dict for v1 and v2), appended and flushed per page, terminal outcome; session text, `[sample]` and provenance stored, never read back into a run; `LiveSequencePlot`; read-only viewer helpers | Touch the instrument |
| **Models and builders** | `sequences/models.py`, `builders.py` | `Step`, `Sequence`, `RepeatBlock`, `SafetyLimits`, `validate`; protocol builders (`pulse_train_sequence`, `bipolar_pulse_sequence`, `cycling_sequence`, `soc_position_sequence`, Renard series) | Hardware limits other than the session's |
| **Run sheet** | `sequences/run_sheet.py` | Excel ⇄ `RunSpec`: each row through a builder, validated against the limits, all bad rows reported at once; the template with a README sheet | Hold limits or wiring |
| **Recovery** | `sequences/recovery.py` | `oe-recover-run`: recompiles a lost run's program from its file (`SequencerProfile.from_settings`), replays its `archive_retained` npz through `Replayer`, checks the file's readings and events are reproduced, writes a new file with the rest and provenance; outcome unchanged | Touch the instrument; mark a run completed |
| **Session and QueueServer** | `sequences/queueserver.py`, `qserver/` | `load_session` (TOML); `build_namespace` (RunEngine, writer, Flyer and the three plans; opening the environment secures HIGH-Z); `oe-validate`, `oe-enqueue`, `oe-archive-retained`; permissions allow only `run_from_sheet`, `recover_sequencer`, `archive_retained` | Per-run confirmation (the session is the confirmation) |
| **Notebooks** | `notebooks/k2460_run.ipynb`, `k2460_view.ipynb`, `sequences/notebook.py` | Per-run notebook (fallback without QueueServer; `oe-new-run`); read-only viewer | – |

## Where settings live

| Setting | Lives in | Set by |
|---|---|---|
| Protocol, levels, durations, thresholds, sampling, start mode, sample ID, notes | Run-sheet row | Operator, per run |
| Backend, instrument address and serial, terminals, data folder, voltage window, current limit, run bounds, poll period, START timeout | Session TOML | Operator, per environment; loading it is the confirmation |
| Digital lines, timing table (reading time per NPLC), ranges, compliance margin, block limit, `start_on_lowest_range` | `SequencerProfile` defaults in `sequencer.py` | Developer, from bench evidence |
| Firmware facts (63 blocks, Lua 5.0, 900-character lines) | `sequencer.py`, `sequencer_runtime.py` | Measured on the bench |

## One run, end to end

1. **Enqueue.** `oe-enqueue` loads the session and the sheet, validates the
   named rows, records the sheet's SHA-256 and adds one
   `run_from_sheet(run_sheet, measurement_id, sheet_sha256)` item per row.
2. **Load.** The worker's `run_from_sheet` refuses a sheet whose digest changed,
   reloads the row and validates it again against the session limits.
3. **Prepare** (before RunStart). The backend compiles the program, refuses
   unread records of another run, configures the instrument (HIGH-Z for both
   functions, terminals, lines), uploads entries and blocks in short checked
   calls, maps the clock and leaves the model ready with the output OFF.
4. **Kickoff.** Immediate start initiates the model. External start initiates
   it waiting for the START edge with READY high; the kickoff status then
   completes when the wait is armed, **before** sourcing begins.
5. **Run.** The trigger model times every sample and step on the instrument. The
   Flyer's monitor polls about once a second; the backend reads bounded chunks
   (at most 250 records per call), the `Replayer` assigns indices and events, and
   `collect_while_completing` emits pages that the `RunFileWriter` appends.
6. **End.** The model reaches its end or its stop path and switches the output
   OFF itself. The backend combines the final log entry and the replay into the
   outcome (`completed`, `stopped_cutoff`, `error` on any disagreement); the
   Flyer confirms the output is OFF; the run file gets its outcome.
7. **Interruption.** `pause immediate` or `abort` calls the Flyer's abort: the
   runtime aborts the model, switches the output OFF and confirms it; the Flyer
   drains the remaining records; the outcome is `aborted`. If the host dies, the
   finite model completes on its own and its records stay on the instrument
   until `archive_retained` saves them.

The sequencer's block layout, timing, cutoffs and runtime API are described in
[Sequencer runtime](sequencer.md).

## Safety layers

1. **Host validation:** every row against the session limits (`validate`), and
   compilation (ranges, sampling feasibility, block budget) in `oe-validate`,
   `oe-enqueue` and again at prepare, before any I/O.
2. **Runtime validation:** the TSP checks every uploaded value against the
   limits configured at prepare, so a host bug cannot upload an out-of-window
   level.
3. **Instrument-local cutoffs:** in current and rest steps the absolute
   window is a STOP branch after every voltage reading, and compliance sits
   0.1 V outside it. Voltage (CV) steps measure current: their level is inside
   the window by validation, compliance is `i_max_abs`, and the only branch is
   the taper cutoff.
4. **Output state:** HIGH-Z for both source functions; output OFF at end, stop
   and abort, confirmed by read-back (with a function-toggle fallback for a stale
   ON reading).
5. **Retained data:** prepare refuses while unread records of another run
   exist; `archive_retained` saves them first.
6. **One owner:** the RE Manager worker holds the only connection; the start
   script refuses a second manager.

## Repository map

| Path | Content |
|---|---|
| `src/ophyd_electrochemistry/sequences/` | Models, builders, run sheet, simulator, backends protocol, Flyer, plan, callbacks, run file, viewer, QueueServer and notebook helpers |
| `src/ophyd_electrochemistry/keithley/k2460/` | Sequencer compiler, runtime packaging, hardware backend, transport, `oe-diagnose`; `tsp/sequencer.tsp` |
| `qserver/` | RE Manager startup, permissions, example sessions, start script |
| `notebooks/` | `k2460_run.ipynb` (per run), `k2460_view.ipynb` (viewer); older notebooks belong to the archived design |
| `tests/` | See [Testing](testing.md) |
| `bench/` | Scripted bench runners, test matrices (`plan-*.md`) and session files used on hardware days |
| `briefs/` | Task and review briefs for Codex |
| `docs/` | This documentation; `docs/evidence/` holds the bench results |

## Older code in the package

The package still contains the earlier contract-revision-0.2 design (milestones
M1–M6): waveform intent and planning (`protocols.py`, `waveforms.py`,
`serialization.py`, `acquisition.py`, `keithley/k2460/compiler.py`,
`capabilities.py`), the virtual-clock simulator (`simulation/`), the
`Keithley2460Device` Flyer (`device.py`, `backend.py`, `projection.py`,
`measurement.py`) and the M4/v17 runtimes (`runtime.py`, `timed_runtime.py`,
`tsp/runtime.tsp`, `tsp/timed_hold.tsp`). The beamtime path does not use them,
except for the shared transport, the runtime install mechanism
(`RuntimeArtifact` in `runtime.py`), `state.py`, `exceptions.py`,
`validation.py` and `oe-diagnose`. `tsp/runtime.tsp` sets output-off mode
NORMAL and must never run with a cell connected. Their documentation is in the
[archive](../archive/index.md).
