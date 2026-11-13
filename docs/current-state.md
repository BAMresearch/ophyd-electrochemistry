# Current project state

Updated: 2026-11-12. Branch: `mvp-sprint`. Plan: [MVP sprint plan](mvp-plan.md).

Keep this file short (see `AGENTS.md`). The detailed pre-sprint diary
(October–2 November bench narrative) is preserved in commit `e04ebc6`, in
`docs/validation.md` and in `docs/evidence/`.

## What works

- **Offline:** 1332 tests pass, including 58 contract tests against a real RE Manager. Pure models/compiler, independent simulator,
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
- **Recovery (13 Nov):** `oe-recover-run RUN.h5 retained.npz` folds an
  archive into a new copy of the run file after checking the replay reproduces
  the saved part and its absolute instrument times; outcome unchanged. Used on
  `hc-p0`, `ici-full-s`, `store-d`.
- **Read-only viewer (O11):** `notebooks/k2460_view.ipynb` indexes run files,
  follows live SWMR data in one output, reviews full-resolution V/I and events,
  and overlays voltage against time or signed charge. Bin-extrema display
  sampling preserves short pulses; corrupt files stay visible in the index.
  Tested offline with completed, cutoff, aborted and incomplete runs.
- **Documentation (10 Nov):** README introduction; `docs/user/` (quickstart,
  run sheets, sessions, beamtime procedure, data, reference) and
  `docs/technical/` (overview and division of tasks, sequencer, simulator,
  testing, development); pre-sprint design in `docs/archive/`.
- **Exploration runner:** `bench/explore.py --series b2|b3` checks each row
  against the limits in a `bench/` page, runs it and applies stop rules (59 runs
  on 6 Nov, 30 on 12 Nov on a healthy cell).
- **Healthy cell (B3, 10–12 Nov):** P0, a 40C characterisation series and a
  full-range ICI ran on `AlCe Test Cell 1`; results in `docs/validation.md` and
  the beamtime consequences in `docs/beamtime-concept.md` ("B3 findings").

## What is broken or missing

- Step limits are strict (a reading equal to the limit does not cut off);
  2460 equality is unverified and cannot be provoked on purpose.
- Each step runs about 1 % long (R9); cause open.
- Follow during an external-START wait: fixed offline 10 Nov (status line until
  the first readings, then the plot); confirm on the next bench day.
- If the link drops while the RE Manager is connected and the manager is then
  killed, the 2460 keeps the dead session and ignores new ones (12 Nov); the
  dead-socket port 5030 clears it after output OFF (`docs/user/beamtime.md` §6).
- Timed-hold runtime v17 is superseded; do not develop it. `runtime.tsp` uses
  OFFMODE_NORMAL and must not touch a cell.

## Next resistor session

RC dummy C1–C2 (needs its values). R8 external START with the generator if the
trigger wiring changes. Nothing else is pending on the resistor (9 Nov).

## Last known hardware state (reconfirm before use)

13 Nov, 09:46 UTC: K2460 serial `04686198`, TSP, `169.254.113.151:5025`,
runtime `seq-v2`, output OFF, HIGH-Z, RE Manager stopped. AlCe Test Cell 1
desodiated for storage: `store-d` (C/20) reached 2.5 V after 17.8 h and
230.8 µAh, rested to 1.43 V; archived (`retained-20261113T094609Z.npz`) and
recovered (`store-d-recovered_20261112T151249Z.h5`). Wiring: READY line 1,
BUSY 2, MARKER 4, START 6 (DB-9 pin 8).

## Next steps

1. Commit `oe-recover-run` (brief 15 findings applied; `hc-p0`, `ici-full-s`
   and `store-d` recovered beside their files on 13 Nov).
2. Jonas: the questions in `docs/beamtime-concept.md` (desodiation end, rate,
   plateau, measured capacities, rests). Then regenerate `b3-pulse.xlsx` and the
   pulse session for the beamtime cells.
3. Offline: Codex brief 14 (today's `explore.py`, `b3_sheets.py`,
   `b2_monitor.py` and `b3_analysis.py`); step-stretch analysis; dress-rehearsal matrix with the beamline trigger.

## Information still needed from the user

`docs/beamtime-concept.md` questions 1–8, and how the relaxed sodiation end
treats the rest history (9 Nov rehearsal). Beamline trigger signal (level, polarity, connector).
RC dummy values.
