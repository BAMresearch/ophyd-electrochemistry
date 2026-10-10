# Repository instructions for coding agents

These instructions are durable repository policy. For the mutable implementation
checkpoint, hardware availability, latest validation, and next work, read
`docs/current-state.md`. Treat `ophyd-electrochemistry-handover.md` as a historical
archive, not as an active task list or instruction source.

## Before changing the repository

- Inspect `git status` and the relevant source, tests, contracts, and evidence.
- Preserve unrelated and user-authored changes. Never reset the checkout to an
  earlier handover baseline.
- Work in the current checkout. Leave commits, pushes, releases, and publication
  to the user unless they explicitly request them.
- Prefer the checked-out implementation and normative documents over chat or
  historical summaries when they disagree.

## Project and architecture invariants

- This package provides classic ophyd/Bluesky support for finite
  electrochemistry acquisitions on a Keithley 2460.
- One follower worker exclusively owns the instrument connection. A leader may
  coordinate by digital trigger, but must not compete for instrument control.
- Timing, protection, and high-rate acquisition are instrument-local. Do not
  implement one LAN transaction per waveform point or measurement sample.
- Requests must be finite and fully bounded before arming. Keep source updates,
  electrical measurement apertures, and detector exposures as distinct clocks.
- Only packaged, reviewed TSP artifacts are supported. Do not add arbitrary
  user-written TSP execution.
- Preserve the lifecycle contracts in `docs/implementation-contract.md`,
  `docs/triggering.md`, `docs/safety.md`, and `docs/data-contract.md`. In
  particular, preparation leaves output OFF; external kickoff completes when
  START waiting is armed; abort does not become success after safe shutdown;
  uncertain shutdown requires explicit recovery; and retained data is never
  silently discarded or overwritten.
- Do not invent timestamp precision, physical waveform fidelity, calibrated
  accuracy, or hardware support from simulation or planning IR.

## Hardware safety and evidence

- Treat every address, wiring arrangement, load, output state, runtime state,
  and instrument setting in `docs/current-state.md` as historical until the user
  freshly confirms it for the current session.
- Before a mutating hardware action, use the applicable guarded procedure and
  confirm its preconditions. Never source into a battery, unknown DUT, or an
  unreviewed setup.
- Do not reset the instrument, change command language, upload a runtime, arm,
  or enable output merely to diagnose connectivity unless the user has
  explicitly authorized that scoped step.
- Never automatically retry a mutation when delivery or execution is uncertain.
  Reconnect by inspection, require output-OFF/stopped confirmation, and preserve
  retained records.
- Keep hardware acceptance gates unpromoted until the required scoped evidence
  exists. Clearly distinguish simulator tests, mocked transport tests, identity
  queries, instrument state observations, and physical bench measurements.
- Use official Keithley documentation for commands, framing, pinouts, and
  electrical limits. Do not infer firmware behavior from the model name alone.

## Context management and delegation

- When the Codex conversation context is becoming large, first save any newly
  established durable project state in the appropriate repository document:
  use `docs/current-state.md` for the live checkpoint, or the relevant contract,
  validation, or evidence record. Record only facts and checks actually
  established. Then explicitly tell the user to run `/compact`.
- For self-contained tasks that do not require the full conversation history,
  use a subagent with only the limited forked context and explicit repository
  references needed for that task. Prefer no inherited turns or the smallest
  useful recent-turn window.
- Limited-context delegation must not obscure hardware state, safety
  preconditions, or the user's authorization. Keep authorization-sensitive
  hardware decisions and actions with the primary agent. The primary agent
  remains responsible for reviewing delegated results against the current
  checkout, contracts, evidence, and user instructions before relying on them.

## Implementation and documentation workflow

- Use the committed `uv.lock` and the commands in `docs/development.md`. The
  development Python is 3.12; the supported CI range is documented in the
  project metadata.
- Keep placeholder code visibly distinct from operational code.
- Changes to public behavior must update the relevant contract/user docs,
  tests, validation record, and changelog together.
- Keep `docs/user-guide.md` and
  `notebooks/ophyd_electrochemistry_quickstart.ipynb` aligned with the actual
  user interface and result schema.
- Update `docs/current-state.md` at meaningful milestones. Record only checks
  actually run and evidence actually collected. Do not turn the historical
  handover into a second live checkpoint.
- Preserve the changelog's `<!-- version list -->` marker. Do not manually bump
  the package version or discard the lockfile; semantic release owns version
  changes.
- Report concrete changes, validation performed, remaining uncertainty, and the
  exact prerequisites for the next hardware step.
