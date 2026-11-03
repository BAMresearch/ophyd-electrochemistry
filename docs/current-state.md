# Current project state

Updated: 2026-11-03. Branch: `mvp-sprint`. Plan: [MVP sprint plan](mvp-plan.md).

Keep this file short (see `AGENTS.md`). The detailed pre-sprint diary
(October–2 November bench narrative) is preserved in commit `e04ebc6`, in
`docs/validation.md` and in `docs/evidence/`.

## What works

- **Offline:** 1185 tests pass. Pure models/compiler, independent simulator,
  measurement schema v4, and a classic-ophyd `Keithley2460Device` Flyer running
  under a real RunEngine against the simulator (galvanostatic hold, current
  pulse train). Quickstart notebook runs on the simulator.
- **Sequences / run sheets:** finite step models, safety validation and battery
  protocol builders; Excel loader with full-row metadata and aggregated errors;
  template generator with four examples and the 16-run R5 pulse series.
- **Sequence Flyer:** separate `SequenceFlyer` / `run_sequence` path under a real
  RunEngine, wall-time simulator release, live readout/event streams, external
  start, timeout/abort drain and recovery. HDF5 writer and throttled Plotly live
  callback work offline; full-resolution plotting reads saved files. The file's
  UTC origin uses acquisition field timestamps: Bluesky 1.15.1 replaces collect
  event envelope times with host collection times.
  `RE.halt()` (no cleanup) still ends with output OFF and outcome `aborted`,
  but records after the last collect stay in the backend (not in the file).
- **Sequence simulation:** deterministic pure engine with OCV + R0 + R1‖C1,
  scheduled two-phase samples, CC/CV/rest, cutoff next/stop, repeat events and
  run-file-compatible readout chunks. Synthetic cell, not bench validated.
- **Sequencer compiler/replay (brief 07):** pure config/block/sample compilation,
  numeric runtime uploads, calibrated delay selection and incremental cutoff /
  readback replay. Golden S1 layouts and noisy simulator comparisons pass;
  runtime requires positive CV taper.
- **Sequencer hardware backend (O5, offline):** `Keithley2460SequenceBackend`
  installs the runtime, uploads under `pcall` (runtime errors come back as
  `ERR` lines), maps the clock by `eventlog.post` pings, polls status, event log
  and records through the replay, aborts with OFF confirmation and refuses to
  prepare over unread records of another session until `archive_retained`.
  Tested against the real runtime under Lua, including a RunEngine run; not
  yet run on the 2460 (B1).
- **Per-run notebook (O6):** Excel selection, request summary, synthetic preview,
  live plot and saved-file review; clean copies via `ophyd-electrochemistry-new-run`
  with explicit session limits. `BACKEND = "k2460"` selects the sequencer
  backend (confirmation gate before any I/O; not yet run on hardware). Save the
  executed notebook beside its HDF5 file.
- **Run files:** incremental HDF5 writer/reader with separate equipment/run
  metadata, compressed readouts and events; exclusive creation, per-append flush
  and explicit terminal outcomes. SWMR mode (HDF5 1.10 format): other
  processes read live snapshots while a run is written. After a killed kernel
  `read_run_file` reads all flushed data (outcome `incomplete`); other HDF5
  tools need the `repair_run_file` copy. Write run files to local disk.
- **On the 2460, via scripts (not via Bluesky):** PyVISA transport, identity/TSP
  checks, query-only inspection, packaged runtime install/replace, count-bounded
  current holds (runtime v14) with immediate or external START, buffered
  acquisition to 10,000 records at 1/0.1/0.01 NPLC (~24/207/821 Hz on the
  resistor), chunked retrieval, archive, abort, recovery.
- **Timing observed (resistor, 1 NPLC, v14):** START → buffered timestamp about
  45–52 ms, dominated by a 10 ms START polling loop and the measurement. Scoped
  observation, not a calibrated bound.

## What is broken or missing

- Timed-hold runtime v17 (`timed_hold.tsp`, `Keithley2460Backend`) fails after
  the first sample (`cadence_overrun`); superseded by the sequencer design in the
  plan. Do not develop it further.
- The hardware Bluesky path exists but has not run on the 2460; no
  voltage-source (CV) step on hardware yet.
- `runtime.tsp` sets `smu.source.offmode = smu.OFFMODE_NORMAL` — unsafe for cells.
  The sequencer must use HIGH-Z.
- Hard-coded commissioning limits (10 mA, 2 V, 60 s) are too small for cycling.
- No QueueServer profile.

## Before the next bench session

Rewire the digital I/O (decided 3 Nov): READY out line 1, BUSY out line 2,
step MARKER out line 4 (scope probe moves from line 1), START in line 6 (the
generator and its 1 kohm resistor move from line 3). Confirm the DB-9 pins.

## Last known hardware state (reconfirm before use)

As of 2026-11-03 ~12:40 CET, after B0: K2460 serial `04686198`, firmware 1.7.16a
(TSP, Lua 5.0.2), `169.254.113.151:5025`. Output OFF, source current 0 A,
off mode HIGH-Z for current and voltage, trigger model EMPTY, no runtime loaded
(the instrument is powered off overnight; runtime, buffers and off mode do not
survive that). `defbuffer1` holds 1,337 continuous-mode readings from today
(`trigger.continuous` is `CONT_AUTO`); event log read and empty. Front terminals,
4-wire, 100 Ω / 1 W resistor; trigger generator and scope off.

## Next steps

1. B1 on the next bench day: `bench/plan-b1.md` (rewire first, §0), runner
   `bench/b1_sequencer.py` (R1–R11 resistor, C1–C2 RC dummy; `--dry-run`
   compiles the matrix offline). Needs the RC dummy's values. O1–O6, O9, O10 are
   implemented offline.
2. Build the QueueServer profile (O7) using the sequence Flyer and run-sheet loader.

B0 and E1/E2 done (3 Nov): HIGH-Z per function, live buffer reads during a
run, 1M-record buffers; a function change (I<->V) turns the output off even
inside a model, same-function recalls keep it on. E3/E4: auto-zero-off timing
(4.83 ms at 0.1 NPLC), instrument-local voltage cutoff works, BRANCH_ON_EVENT
latches. S1/S2 (scope): prototype step layout runs as designed, external START
-> busy 65 us, markers lag the level change by 1.07 ms. See `docs/validation.md`.

## Information still needed from the user

Answers so far are recorded in the plan. Still open:

1. Cell capacity / active mass, voltage window, intended currents (C-rates).
2. Cycling: CC only or CC-CV; number of cycles.
3. Pulse amplitude(s).
4. Beamline trigger: signal type, level (5 V TTL?), polarity, pulse width,
   connector; whether the beamline can instead (or also) record a TTL from us.
5. Software start: what sends it (beamline control system, a person, a
   script) and over which channel; which clock beamline data are stamped with.
