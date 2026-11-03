# Current project state

Updated: 2026-11-03. Branch: `mvp-sprint`. Plan: [MVP sprint plan](mvp-plan.md).

Keep this file short (see `AGENTS.md`). The detailed pre-sprint diary
(October–2 November bench narrative) is preserved in commit `e04ebc6`, in
`docs/validation.md` and in `docs/evidence/`.

## What works

- **Offline:** 527 tests pass. Pure models/compiler, independent simulator,
  measurement schema v4, and a classic-ophyd `Keithley2460Device` Flyer running
  under a real RunEngine against the simulator (galvanostatic hold, current
  pulse train). Quickstart notebook runs on the simulator.
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
- No hardware-backed Bluesky path, no voltage-source mode on hardware, no
  instrument-local voltage cutoffs tested, no live data reads during a run.
- `runtime.tsp` sets `smu.source.offmode = smu.OFFMODE_NORMAL` — unsafe for cells.
  The sequencer must use HIGH-Z.
- Hard-coded commissioning limits (10 mA, 2 V, 60 s) are too small for cycling.
- No QueueServer profile, no Excel run sheet, no per-run notebook.

## Last known hardware state (historical — reconfirm before use)

As of 2026-11-02: K2460 serial `04686198`, firmware 1.7.16a, TSP mode,
`169.254.113.151:5025`. Runtime v17 installed (SHA-256 `68b25b2e…b521`), IDLE,
output OFF, 0 A. Front terminals, 4-wire, 100 Ω / 1 W resistor. Runtime buffer
holds **one retained run-05 reading** (archived in
`docs/evidence/k2460-2026-11-02-timed-v17-run-05*.json`). `defbuffer1` holds
12,867 older records — do not clear without asking. Equipment power-down was
not confirmed.

## Next steps

1. Off-day work O1–O3 of the plan: sequence models, Excel loader, simulator.
2. Check the Rev. C manual items listed under "To verify" in the plan, then O5
   (sequencer TSP + backend).
3. Prepare the bench-day B1 test matrix and runner (O8).

Bench test B0 (`bench/feature_probe.py`, output OFF only) is ready to run on
the next bench session; it writes its own evidence file.

## Information still needed from the user

Answers so far are recorded in the plan. Still open:

1. Cell capacity / active mass, voltage window, intended currents (C-rates).
2. Cycling: CC only or CC-CV; number of cycles.
3. Pulse amplitude(s); how to trim the ~7.6 h-per-SOC pulse series.
4. Start: TTL source and logic levels; what "Ethernet start" means (our notebook
   vs. a beamline control-system message, and which protocol); whether the
   beamline wants a TTL marker from the 2460 at step starts.
5. Experiment dates.
