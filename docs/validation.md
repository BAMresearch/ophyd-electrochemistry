# Template validation record

Date: 2026-10-02. Scope: contract template, not operational 2460 behavior.

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
