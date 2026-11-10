# Codex task briefs

Hand Codex one brief at a time:

> Read `AGENTS.md` and `briefs/<file>.md`, then implement the brief.

Rules that apply to every brief (Codex: follow these):

- Read only `AGENTS.md`, the brief, and the source files the brief names. Do not
  read `docs/validation.md`, `docs/evidence/`, the handover or other briefs.
- No hardware access. Do not modify files outside the brief's "Files" list
  unless a test or lint fix requires it; say so in the report if you do.
- Validate with `uv run --locked pytest -q`, `uv run --locked ruff check .`,
  `uv run --locked ruff format --check .` and `uv run --locked mypy src scripts` (see
  `docs/development.md`). If you add a dependency, run `uv lock` and commit the
  updated `uv.lock`.
- Commit on branch `mvp-sprint` with the message given in the brief (if your sandbox cannot commit, say so in the report). Do not push.
- Do not update `CHANGELOG.md`, `docs/current-state.md`, the user guide or the
  quickstart notebook; Claude does that at milestones.
- Finish with a report of at most 15 lines: files changed, tests added, checks
  run with results, and any deviation from the brief or open question.

| Brief | Task | Depends on |
|---|---|---|
| `01-sequence-models.md` | Step/Sequence models, validation, builders | — |
| `02-run-sheet.md` | Excel run-sheet template and loader | 01 |
| `03-hdf5-run-file.md` | Incremental HDF5 run file writer/reader | 01 |
| `04-sequence-simulator.md` | Pure sequence simulator with a cell model | 01 |
| `05-sequence-flyer.md` | Sequence Flyer, `run_sequence`, run-file writer, live plot | 01–04 |
| `06-run-notebook.md` | Per-run notebook template, helpers, new-run CLI | 01–05, SWMR run file |
| `07-sequencer-compiler.md` | Sequencer compiler (block table, runtime calls) and replay | 01–05, sequencer design |
| `09-review-blocks-and-relaxed-end.md` | Review only: 63-block limit, repeat table, `rest_end_v` (commits 1979525, 026c55a, c8f160f) | — |
| `10-review-runtime-block-bound.md` | Review only: runtime `oe_seq_begin` 63-block bound (264c63c), before bench install | 09 |
| `11-review-default-start-and-session-confirmation.md` | Review only: 1 µA start default and per-session confirmation (0b35831); docs accuracy | 10 |
| `12-review-follow-enqueue-b3.md` | Review only: enqueue compile (0cc63c3), Follow status (7ca66c0), B3 sheets and overnight plan (92e4881) | 11 |
