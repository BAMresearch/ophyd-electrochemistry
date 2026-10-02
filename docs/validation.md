# Template validation record

Date: 2026-10-02. Scope: contract template, not operational 2460 behavior.

## uv and release-workflow update

Baseline: GitHub `main` commit `f8e5e555228d8700fa08f1f23782237fd0bb0688`.
Compared the current McSAS3/MoDaCor pyprojects and reusable release/CI workflows.
Validated with uv 0.12.19 and Python 3.12.14:

- `uv lock --check` and locked environment creation/sync.
- Ruff lint/formatting and strict mypy checks for source and release helper.
- Twelve tests: the six original RunEngine contract checks plus six isolated
  Git/metadata release-gating tests. These never push or publish.
- Strict MkDocs build; isolated uv wheel/sdist build and strict Twine checks.
- Actionlint 1.7.12, invoked with `uvx --from actionlint-py==1.7.12.25 actionlint`,
  validates all six workflows and local reusable-workflow references.
- Disposable-checkout release preparation: actual Python Semantic Release 10.7.0
  stamps `0.1.0`, inserts its changelog section and permits a fresh matching lock.
  A local `v0.1.0` tag plus a subsequent `fix:` commit correctly previews `0.1.1`.
  Later commits with an already-tagged version do not qualify for republishing.

No remote mutation, Actions run, release creation, or publishing was performed.
The matrix targets Python 3.11–3.13 on Linux and Python 3.12 on macOS/Windows;
those additional environments are configured, not claimed as locally tested.
PyPI setup and the optional release-PR token remain repository-owner configuration.
The supplied overlay has only changed/new files at repository-relative paths.

## Source review

The official datasheet, official 2460 pulse example, Tektronix's maintained
command API, Bluesky hardware protocols, ophyd architecture, QueueServer
introduction, and PyVISA resource documentation were reviewed. The full 2460
reference PDF was unavailable to the tools used; exact runtime/manual checks
remain a hardware implementation prerequisite. See [assumptions](assumptions.md).

## Local validation environment

Python 3.12; Bluesky 1.14.6; ophyd 1.11.2; event-model 1.24.0.
The package remains a contract/interface template with no operational hardware
driver. CI targets Python 3.11–3.13; only Python 3.12 was run locally.

## Checks

- Lint and formatting check for source, examples, and tests.
- Strict type check for the 13 source modules.
- Six contract checks through a real RunEngine/test-only witness: both start
  modes, partial-event/event-page document validation, acquisition failure
  versus successful abort, UNKNOWN output on failed shutdown, and normal/aborted
  follower-plan finalization with retained readings.
- Strict documentation build and wheel/source-distribution build.
- Distribution resource inspection for typed package marker and TSP placeholder.

These checks validate API consumption and template consistency. The witness
encodes intended behavior; it is not independent proof of instrument timing,
electrical shutdown, firmware behavior, or safety. No hardware tests were run.

The initial local Git commit and `contract-v0.1` tag version the baseline. No
remote repository, GitHub Actions execution, deployment, or package publication
was performed. All listed checks passed locally; six tests passed. The deliberately
failed acquisition test also produced an upstream RunEngine `FailedStatus`
future diagnostic while its expected exception, finalizer, data, and stop-document
assertions passed. Do not confuse that intentional failure path with hardware
acceptance.
