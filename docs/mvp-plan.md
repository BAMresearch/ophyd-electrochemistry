# MVP sprint plan

Started 2026-11-03. Experiment: two beamline days in the **first week of December 2026**;
software freeze Mon 30 Nov. Human-facing living plan: Claude Doc
"K2460 battery-pulse MVP — living plan". Codex task briefs: `briefs/`.
The beamline does not run Bluesky; we run our own stack next to it.

## Scope

**In:**

1. Reference galvanostatic (and potentiostatic) charge/discharge cycling with
   instrument-local voltage cutoffs (optionally a CV hold with current cutoff).
2. Bipolar square current pulses with zero-current rests, repeated N times for
   reproducibility, at several states of charge, for a Renard series of pulse times.
3. The user's own laptop runs the notebook and owns the 2460 (Ethernet link).
   Start by an external hardware trigger on a 2460 digital input, or by a
   software start whose source is not yet specified. The sequence runs
   instrument-locally either way; the start method only gates its first step.
   Step-start TTL markers and UTC-anchored buffer timestamps let the data be
   aligned with beamline data afterwards.
4. One Jupyter notebook per run, live plotting, run parameters read from an Excel
   run sheet (one row per run, `measurement_id` first). Slowly changing settings
   (limits, ranges, wiring, off-mode) live in the notebook.
5. Plans launchable through a single QueueServer that owns the 2460 (no
   leader/follower setup yet).

**Decided 2026-11-03:** the notebook runs its own RunEngine (simplest path)
and stays the beamline fallback. A QueueServer profile (O7) follows the notebook,
reusing the MOUSE stack (Redis + RE Manager already in use); whether the dress
rehearsal runs through it is decided at the 19–20 Nov gate. Python 3.14,
bluesky 1.16.x; take patch releases until the 30 Nov freeze. Tiled is optional
(the per-run HDF5 file is the primary record). Output is one
structured HDF5 file per run with separate groups for equipment settings, run
settings (including the full Excel row, optional sample IDs and notes) and
readouts. Bench days are normally Thu/Fri/Mon.

**Protocol estimates (user, 2026-11-03, may change):** two-electrode cells;
capacity and currents TBD. Pulse times 0.1–100 s (Renard series); rest = pulse
time, minimum 1 s; repeats log-interpolated from 100 at 0.1 s to 6 at 100 s; SOC set
and checked by voltage (CC to a target voltage, then rest).
Consequences:

- A full R5 series (16 pulse times) then takes about **2.9 h per SOC** and
  ~281k records (largest single run ~26k records).
- 0.1 s pulses need sub-10 ms sampling (0.1 NPLC ≈ 5 ms/reading on the resistor);
  100 s pulses at that rate would produce ~10⁶ records. Steps therefore get
  two-phase sampling (agreed): a fast interval for an initial window after the
  edge, then a slow interval. Proposed defaults 5 ms (0.1 NPLC) for 0.5 s, then
  100 ms (1 NPLC); confirm on the bench.

**Out (for now):** leader/follower QueueServers, PRBS/multisine/arbitrary
waveform playback, aperture/timestamp metrology beyond buffer timestamps,
voltage pulse trains, EIS.

## Design

### One step-sequence model for every protocol

A run is a finite `Sequence` of `Step`s, plus an optional repeated block:

| Step field | Meaning |
|---|---|
| `mode` | `current`, `voltage` or `rest` (rest = 0 A, output on, measures OCV) |
| `level` | A or V |
| `duration_s` | maximum step duration |
| `sample_interval_s` | measurement spacing within the step (slow phase) |
| `fast_interval_s`, `fast_window_s` | optional faster spacing for the first part of the step |
| `v_min`, `v_max` | (current/rest steps) voltage cutoffs |
| `i_abs_min` | (voltage steps) taper cutoff for CV holds |
| `on_cutoff` | `next` (end step, continue) or `stop` (end run, output OFF) |

Examples:

- Cycling: `{CC +I → v_max (next), [CV v_max → i_abs_min], rest, CC −I → v_min (next), rest} × n`.
- SOC positioning: `CC ±I for t (charge Q = I·t) or to a voltage`, then `rest`.
- Bipolar pulse block: `rest r0, {+I t_p, rest t_r, −I t_p, rest t_r} × n`.
- Renard series (R5: 1, 1.6, 2.5, 4, 6.3, 10, …; R10 for finer steps): one run
  per pulse time, generated as run-sheet rows or by a helper.

Global safety limits (absolute voltage window, max |I|, compliance, ranges,
off-mode HIGH-Z) are configured per notebook/session and every sequence is
validated against them before arming.

### Instrument side: one packaged "sequencer" TSP runtime

- The host calls one packaged function with the step table; the TSP builds a
  trigger model: per step a source-config change, a marker on a digital output
  line (step-start TTL for the beamline), a sample loop (delay → measure →
  limit branch → counter), and a counter branch for the repeated block.
  About 6 blocks per step; the 255-block limit allows ~35 distinct steps.
- Step duration is set by sample count; the sample delay is set to
  `interval − measured measurement time` per measurement profile. The true
  timing comes from buffer timestamps and is checked on the host, not policed in
  firmware. (The v17 timer/deadline approach is abandoned; its failure was very
  likely BRANCH_ON_EVENT latching the first deadline event.)
- Start: immediate on kickoff, or wait for the START digital line (v14 approach).
- Output-off mode HIGH-Z; output OFF at normal end, cutoff-stop and abort.
- **To verify against Rev. C before coding:** whether source configuration lists
  can switch source function (current↔voltage) within one trigger model; buffer
  reads while the model runs; HIGH-Z off-mode behaviour; max buffer size.

### Host side

- `Sequence`/`Step` models + validation + Excel run-sheet loader (pandas/openpyxl).
- `SequenceBackend` protocol with two implementations: the real 2460 sequencer
  backend and a Python simulator with a simple cell model
  (OCV(SOC) + R0 + R1‖C1) for off-day work and tests.
- The existing `Keithley2460Device` Flyer is reused where possible; it gains
  live chunk reads during the run (bounded reads, ~1 s cadence).
- Plans: `run_sequence(device, sequence, md)`; per-run metadata includes the
  Excel row. Data: Bluesky documents + an exported per-run file (format TBD).
- Notebook template: pick run by `measurement_id`, preview the sequence plot,
  run, live V/I/t plot, save. A QueueServer startup profile exposes the same
  device and plans.

## Work packages

Off-day (no hardware):

| # | Task | Status |
|---|---|---|
| O1 | `Step`/`Sequence` models, validation, protocol builders (cycling, bipolar pulse block, Renard helper) | Done 3 Nov (brief 01, reviewed) |
| O2 | Excel run-sheet template + loader | Done 3 Nov (brief 02, reviewed) |
| O3 | Simulator backend with cell model | Done 3 Nov (brief 04, reviewed; Flyer integration in O4) |
| O4 | Flyer integration with live chunk reads; `run_sequence` plan; live plot callback | Done 3 Nov (brief 05; simulator only) |
| O5 | Sequencer TSP + host backend, mocked-transport tests, offline Lua syntax check | todo |
| O6 | Per-run notebook template (simulator first) | Done 3 Nov (brief 06; simulator only) |
| O7 | QueueServer startup profile (MOUSE pattern: run-sheet rows → `run_from_sheet` queue items) + integration tests against the simulator | planned after O6; go/no-go at the 19–20 Nov gate |
| O9 | HDF5 writer (equipment / run settings incl. Excel row / readouts) | Done 3 Nov (brief 03, reviewed) |
| O10 | Run file in SWMR mode: live cross-process reads (notebook plot while a QueueServer worker writes); our reader opens SWMR so crashed files read back; `repair_run_file` for other tools | Done 3 Nov |
| O8 | Bench runner script that executes a test matrix and writes one evidence file | todo |

Bench days (in order):

| # | Load | Test matrix (summary) |
|---|---|---|
| B0 | resistor, output OFF throughout | `bench/feature_probe.py`: HIGH-Z off mode, mixed-function config list, BRANCH_ON_EVENT latch semantics, max buffer size |
| B1 | 100 Ω resistor, then RC dummy cell | Install sequencer; current/rest steps; bipolar pulse block; cutoff `next`/`stop` on the RC dummy; live reads during run; external START and software start; step-marker TTL on scope; HIGH-Z off state; measurement time per profile |
| B2 | RC dummy, then a sacrificial Na/hard-carbon cell | Notebook flow end to end; QueueServer flow; a multi-hour cycling run; pulse runs at two SOCs |
| B3 | Real cell, beamline-like setup | Full dress rehearsal from the Excel sheet, including trigger wiring |

## Open questions for the user

See `docs/current-state.md` → "Information needed".
