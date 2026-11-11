# Current project state

Updated: 2026-11-10. Branch: `mvp-sprint`. Plan: [MVP sprint plan](mvp-plan.md).

Keep this file short (see `AGENTS.md`). The detailed pre-sprint diary
(October–2 November bench narrative) is preserved in commit `e04ebc6`, in
`docs/validation.md` and in `docs/evidence/`.

## What works

- **Offline:** 1320 tests pass, including 58 contract tests against a real RE Manager. Pure models/compiler, independent simulator,
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
  runtime requires positive CV taper. A leading 0 A rest starts on the 1 µA
  range (`start_on_lowest_range`, default since 10 Nov): removes the output-ON
  dip seen on cells (up to −220 mV, 9 Nov DC1–DC3).
- **Sequencer hardware backend (O5, offline):** `Keithley2460SequenceBackend`
  installs the runtime, uploads under `pcall` (runtime errors come back as
  `ERR` lines), maps the clock by `eventlog.post` pings, polls status, event log
  and records through the replay, aborts with OFF confirmation and refuses to
  prepare over unread records of another session until `archive_retained`.
  Ran on the 2460 in B1 and B2 (resistor), and on a coin cell.
- **Per-run notebook (O6):** Excel selection, request summary, synthetic preview,
  live plot and saved-file review; clean copies via `oe-new-run`
  with explicit session limits. `BACKEND = "k2460"` selects the sequencer
  backend (ran on the 2460 in Q10); its settings cell is the run's environment. Save the
  executed notebook beside its HDF5 file.
- **QueueServer (O7):** `qserver/startup` + session TOML (`OE_SESSION`): RE
  Manager worker owns the instrument; queue items are run-sheet rows with the
  sheet's SHA-256 (refused if the sheet changed); `oe-enqueue` validates and
  queues without a per-run confirmation: loading the session is the
  confirmation (10 Nov; `oe-validate` checks a sheet first); only
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
- **Documentation (10 Nov):** README introduction; `docs/user/` (quickstart,
  run sheets, sessions, beamtime procedure, data, reference) and
  `docs/technical/` (overview and division of tasks, sequencer, simulator,
  testing, development); pre-sprint design in `docs/archive/`.
- **Exploration runner:** `bench/explore.py` checks each row against the limits
  in a `bench/` page, runs it and applies stop rules (59 runs on 6 Nov).

## What is broken or missing

- Step limits are strict (a reading equal to the limit does not cut off);
  2460 equality is unverified and cannot be provoked on purpose.
- Each step runs about 1 % long (R9); cause open.
- Follow during an external-START wait: fixed offline 10 Nov (status line until
  the first readings, then the plot); confirm on the next bench day.
- The explore reference stop rule compares across voltages.
- Timed-hold runtime v17 is superseded; do not develop it. `runtime.tsp` uses
  OFFMODE_NORMAL and must not touch a cell.

## Next resistor session

RC dummy C1–C2 (needs its values). R8 external START with the generator if the
trigger wiring changes. Nothing else is pending on the resistor (9 Nov).

## Last known hardware state (reconfirm before use)

10 Nov, 16:08 UTC: K2460 serial `04686198`, TSP, `169.254.113.151:5025`,
runtime `seq-v2`, HIGH-Z. **`hc-p0` running alone** on AlCe Test Cell 1 (front
terminals) since 14:59:20 UTC, about 17–19 h. The Mac idle-slept at 16:00; on
wake the read failed (2460 had reset the connection), so the plan failed without
reaching the 2460 and its run file ends at 16:02. RE Manager killed 16:08;
`hc-p0` is back at the queue front (remove, never start). Wiring: READY line 1,
BUSY 2, MARKER 4, START 6 (DB-9 pin 8).

## Next steps

1. Healthy cell (AlCe Test Cell 1, 0.712 mAh): after P0 (C/5 to 5 mV with CV,
   to 2.5 V, to 5 mV with CV, one row) `oe-diagnose`, `oe-archive-retained`;
   then the draft beamtime pulses (`bench/plan-b3.md`; tier 2).
2. Offline: Codex review brief 13; step-stretch analysis;
   B3 dress-rehearsal matrix with the beamline trigger once its signal is known;
   explore reference stop rule.
3. Beamtime run sheet: regenerate `b3-pulse.xlsx` with Jonas's answers.

## Information still needed from the user

`docs/beamtime-concept.md`: whether desodiation runs also end at the relaxed
limit, and how the relaxed end treats the rest history (9 Nov rehearsal); the
rate, cell count. Beamline trigger signal (level, polarity, connector).
RC dummy values.
