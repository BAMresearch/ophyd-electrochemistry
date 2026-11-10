# Repository instructions for coding agents

Durable repository policy. The live checkpoint and next tasks are in
`docs/current-state.md`; the sprint plan is in `docs/mvp-plan.md`. Read those two
first and nothing else unless the task needs it. `ophyd-electrochemistry-handover.md`
and the long history in `docs/validation.md` / `docs/evidence/` are archives.

## Priorities

The goal is a usable Bluesky/QueueServer interface for Keithley 2460 battery
experiments (galvanostatic/potentiostatic cycling and bipolar current pulses with
rests on Na/hard-carbon cells) by the experiment date in `docs/mvp-plan.md`.
Work only on items in that plan. Do not add new waveform types, metrology studies
or planning abstractions that the plan does not call for; propose them instead.

## Architecture invariants

- One process owns the instrument connection.
- Timing, cutoffs and protection run instrument-local (packaged TSP / trigger
  model). The host never paces individual source points or samples. Bounded
  periodic buffer reads during a run (for live plotting) are allowed.
- Requests are finite and validated against configured limits before arming.
- Only packaged, reviewed TSP is executed. No arbitrary user TSP.
- Preparation leaves output OFF; an aborted run is never reported as success;
  retained instrument data is archived before it is discarded or overwritten.
- Do not claim timing precision or accuracy beyond what buffer timestamps and
  recorded bench evidence show.

## Hardware safety (tiered)

Tier 1 — **resistor / passive dummy load** (resistor, RC dummy cell):
- At the start of a bench session the user confirms address, load, wiring and
  limits once, and authorizes a **test matrix** (e.g. `bench/plan-YYYY-MM-DD.md`).
  Within that matrix the agent may install the packaged runtime, prepare, arm,
  run, archive and then discard proof buffers without asking per run.
- Stop and ask on any unexpected error, uncertain output state, or a step outside
  the authorized matrix. Never auto-retry a mutation whose delivery is uncertain;
  reconnect by inspection and confirm output OFF first.

Tier 2 — **batteries / electrochemical cells**:
- Confirmation is per **environment**, not per run or plan (user, 10 Nov). The
  environment is a session file (`OE_SESSION` TOML, or the per-run notebook's
  settings cell): it fixes the instrument, terminals and operational limits
  (voltage window, current limit, duration, records), and the cell and wiring
  are named in its header. Loading it (the user starts the RE Manager with it,
  or names it for the agent to load) is the confirmation. Within it any run that
  validates against its limits may run; there is no typed per-run confirmation.
- The agent never writes, widens or edits a cell environment's limits or set-up
  description and then loads it on its own: a new or changed cell environment
  needs the user to say it matches what is connected.
- An exploration series (`bench/explore.py`, a page in `bench/`) adds stop rules
  and a time limit on top of an environment; the user starts it with the
  runner's `authorize`. On a stop rule or an unexpected state, stop and report:
  no retry, reset or power cycle.
- Output-off mode must be HIGH-Z for cells; never NORMAL/ZERO (they force the
  terminals toward 0 V and discharge or sodiate the cell).
- Voltage cutoffs must be instrument-local. Never source into an unknown DUT.

Always: do not reset the instrument or change its command language without
asking. Use official Keithley 2460 documentation (Reference Manual Rev. C) for
commands and limits; mark anything unverified as such.

## Bench days vs off days

Hardware is available only on scheduled bench days. On off days, work on tasks
that need no instrument (simulator, plans, QueueServer, notebook, tests, TSP code
review). Batch all hardware questions into the next bench-day test matrix and
scripted runners, so a bench day is spent executing, not designing.

## Documentation discipline

- `docs/current-state.md` stays under ~120 lines: what works, what's broken,
  next steps, last bench state. Replace stale text; never append a diary.
- Bench results go into one evidence file per test matrix (script output), plus
  a short entry in `docs/validation.md`.
- Update user docs, quickstart notebook and changelog at milestones, not per edit.

## Development workflow

- Use `uv` with the committed `uv.lock` and the commands in `docs/technical/development.md`
  (Python 3.14). Run tests, ruff and mypy before reporting work done.
- Inspect `git status` first; preserve unrelated user changes.
- Work happens on the `mvp-sprint` branch. Leave pushes, merges and releases to
  the user. Do not bump versions manually; preserve the changelog
  `<!-- version list -->` marker.
- Report concrete changes, checks actually run, and remaining uncertainty.
