# Current project state

Updated: 2026-11-07. Branch: `mvp-sprint`. Plan: [MVP sprint plan](mvp-plan.md).

Keep this file short (see `AGENTS.md`). The detailed pre-sprint diary
(October–2 November bench narrative) is preserved in commit `e04ebc6`, in
`docs/validation.md` and in `docs/evidence/`.

## What works

- **Offline:** 1298 tests pass, including 57 contract tests against a real RE Manager. Pure models/compiler, independent simulator,
  measurement schema v4, and a classic-ophyd `Keithley2460Device` Flyer running
  under a real RunEngine against the simulator (galvanostatic hold, current
  pulse train). Quickstart notebook runs on the simulator.
- **Sequences / run sheets:** finite step models, safety validation and battery
  protocol builders (unipolar `pulse_train`, bipolar pulses, cycling, SOC
  positioning, rest); compiles to at most 63 trigger blocks (firmware limit),
  so `bipolar_pulse` fits only with `fast_window_s` covering pulse and rest; Excel loader with full-row metadata and aggregated
  errors; `sodiation`/`desodiation` accepted for the directions.
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
  Ran on the 2460 in B1 and B2 (resistor), and on a coin cell.
- **Per-run notebook (O6):** Excel selection, request summary, synthetic preview,
  live plot and saved-file review; clean copies via `oe-new-run`
  with explicit session limits. `BACKEND = "k2460"` selects the sequencer
  backend (confirmation gate before any I/O; ran on the 2460 in Q10). Save the
  executed notebook beside its HDF5 file.
- **QueueServer (O7):** `qserver/startup` + session TOML (`OE_SESSION`): RE
  Manager worker owns the instrument; queue items are run-sheet rows with the
  sheet's SHA-256 (refused if the sheet changed); `oe-enqueue` asks for a
  typed confirmation per hardware run (`oe-validate` checks a sheet first); only
  `run_from_sheet`, `recover_sequencer` and `archive_retained` permitted.
  `qserver/start-k2460-re-manager.sh` starts it; `environment open` sets HIGH-Z.
  Ran on the 2460 in B2, including host-crash recovery and a 300k-record soak.
- **Run files:** incremental HDF5 writer/reader with separate equipment/run
  metadata, compressed readouts and events; exclusive creation, per-append flush
  and explicit terminal outcomes. SWMR mode (HDF5 1.10 format): other
  processes read live snapshots while a run is written. After a killed kernel
  `read_run_file` reads all flushed data (outcome `incomplete`); other HDF5
  tools need the `repair_run_file` copy. Write run files to local disk.
- **Read-only viewer (O11):** `notebooks/k2460_view.ipynb` indexes run files,
  follows live SWMR data in one output, reviews full-resolution V/I and events,
  and overlays voltage against time or signed charge. Bin-extrema display
  sampling preserves short pulses; corrupt files stay visible in the index.
  Tested offline with completed, cutoff, aborted and incomplete runs.
- **Exploration runner:** `bench/explore.py` checks each row against the limits
  in a `bench/` page, runs it and applies stop rules (59 runs on 6 Nov).

## What is broken or missing

- `pulse_train` tests limits during the pulse. The agreed end, at or below 5 mV
  after a 5 min rest (`docs/beamtime-concept.md`), is not built.
- Each step runs about 1 % long (R9); cause open. The voltage dip at output ON
  (−10 to −323 mV on the cell) is unexplained.
- Follow does not refresh a run waiting for external START.
- The explore reference stop rule compares across voltages.
- Timed-hold runtime v17 is superseded; do not develop it. `runtime.tsp` uses
  OFFMODE_NORMAL and must not touch a cell.

## Next resistor session

Rerun R4–R7 (not rerun since the replay fix). Power-cycle, `environment open`,
read back HIGH-Z (first real test of NORMAL to HIGH-Z). Test a stop on a rest
step before relying on the relaxed-voltage end. RC dummy C1–C2 if available.

## Last known hardware state (reconfirm before use)

6 Nov, about 14:30 UTC: K2460 serial `04686198`, firmware 1.7.16a, TSP,
`169.254.113.151:5025`, powered off, so runtime, buffers and HIGH-Z are gone and
off mode is NORMAL until `environment open`. Wiring: READY line 1, BUSY 2, MARKER 4,
START 6 (DB-9 pin 8). Coin cell `Richard-HC400-rev` disconnected, about 48 µAh still
sodiated, 1.79 V and falling when last read.

## Next steps

1. Offline: relaxed-voltage end for pulse runs; the
   two-row design for long rests near the end; output-ON and step-stretch analysis.
2. Next resistor session (above), then K4/K5-style trains on a healthy cell.
3. Beamtime run sheet once the open questions are answered.

## Information still needed from the user

`docs/beamtime-concept.md`: whether desodiation runs also end at the relaxed
limit, the rate, cell count. Beamline trigger signal (level, polarity, connector).
RC dummy values. A healthy cell: this one tests the method only.
