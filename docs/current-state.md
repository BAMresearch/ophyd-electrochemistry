# Current project state

Updated: 2026-10-10

This is the concise, mutable resumption checkpoint for ongoing development.
Repository-wide working rules live in the root-level `AGENTS.md`. The original
root-level `ophyd-electrochemistry-handover.md` is an archival account of the
5 October starting point and is not an active task list. The checkout,
contracts, tests, and evidence remain authoritative.

## Implementation checkpoint

- M1–M5 and the first simulator-backed M6 classic-ophyd Flyer slice are
  implemented.
- The real Keithley 2460 adapter is deliberately blocked on the external-START
  to measurement-aperture timestamp proof and a reviewed projection from target
  records into the fixed M5 event schema.
- The guarded timestamp-proof notebook and runtime v13 fixture are prepared,
  but the physical proof has not been completed.
- The target runtime has completed immediate and external-start retained-buffer
  acquisitions through 256 records, deterministic chunk retry, archival,
  recovery, and explicit discard.
- Measurement schema v2 exposes separate untouched `ec_source_status` and
  `ec_measurement_status` values with independent definitions. The obsolete
  combined status field is rejected.
- The project uses the BSD 3-Clause License.

## Hardware and evidence snapshot

The most recently reported instrument was a Keithley 2460 at
`169.254.113.151`. The last described load was a nominal 100 ohm resistor on the
front terminals in four-wire mode. These are historical facts, not permission
or confirmation for a future hardware operation. Reconfirm the address, VISA
resource, load, terminal/sense wiring, physical output indicator, runtime state,
and relevant event/error log before live work.

Completed evidence includes read-only connectivity, resistor plausibility and
bipolar sweeps, bounded buffer retrieval/cleanup, packaged volatile TSP runtime
installation and replacement, immediate and external-start finite holds,
programmatic abort, and retained-buffer archival/recovery. Consult
`docs/validation.md`, `docs/hardware-acceptance.md`, and `docs/evidence/` for the
scope and limitations of each result.

A 256-record run at 1 NPLC measured a mean raw paired-record interval of
40.8326 ms, approximately 24.49 Hz. This is a scoped hardware observation, not
a maximum-rate claim. Reviewed 0.1 and 0.01 NPLC paired-I/V profiles still need
implementation and bench characterization for cadence, noise, status integrity,
and pulse-edge mapping.

## User interface and quickstart

- `docs/user-guide.md` describes immutable per-run configuration, the Flyer
  lifecycle, paired I/V records, retained-data behavior, and preliminary result
  inspection.
- `notebooks/ophyd_electrochemistry_quickstart.ipynb` runs without importing the
  `examples` package or requiring an editable install. It locates the checkout,
  exposes `src/`, and defines its synthetic configuration and plan locally.
- The notebook uses Plotly for linked voltage/current plots, hover inspection,
  commanded-current display, and shaded pulse dwells.
- Configuration cells contain short field comments. `command_timeout_s` bounds
  one backend transaction, `external_start_timeout_s` bounds the local START
  wait, and `shutdown_timeout_s` bounds confirmation of abort/output OFF. None
  of these values represents acquisition duration.
- Simulator advancement and the host completion watchdog are derived from the
  compiled program duration. The regression case with four 0.5 s periods and
  0.2 s pulses produces 200 records at a 10 ms sample period and finishes with
  state COMPLETE and output off.
- Under the current synthetic profile, the minimum sample period is 3 ms
  (2 ms aperture plus 1 ms overhead). That is simulator behavior, not a
  hardware-rate claim.

The production direction is for each prepared target acquisition to expose a
reviewed worst-case completion bound so a long-lived Flyer can update its
watchdog for that request. Users should not need to enlarge communication or
shutdown timeouts merely because a pulse program is longer.

## Working tree and validation

At this checkpoint, the quickstart/UI work was intentionally uncommitted and
modified `CHANGELOG.md`, `README.md`, `docs/user-guide.md`,
`docs/validation.md`, the quickstart notebook, `pyproject.toml`, `uv.lock`, and
`tests/contract/test_quickstart_notebook.py`. This context split additionally
adds `AGENTS.md` and this file, changes the archival handover header, and links
this page from the documentation navigation and README. Always use current
`git status` rather than this list to decide what must be preserved.

The latest full validation before this context split reported:

- 315 tests passed;
- Ruff lint and formatting passed;
- strict mypy passed;
- strict MkDocs passed;
- `git diff --check` passed; and
- the lockfile was resolved and checked after adding notebook dependencies.

After this context split, strict MkDocs and `git diff --check` were rerun and
passed. The full test suite was not rerun because the split changes only
documentation and agent guidance.

## Next work

1. With the 2460 and resistor setup available, reconfirm the complete safe bench
   state and run/review the guarded hardware timestamp proof.
2. Use the result to define the real target-record projection and connect the
   prepared runtime backend to the M6 Flyer without inventing timestamp
   precision.
3. Add immutable, reviewed measurement profiles such as 1, 0.1, and 0.01 NPLC
   with fixed range, filter, and autozero policy; benchmark them on the resistor.
4. Keep acquisition instrument-local and retrieve retained buffers only after
   the finite run.

When this checkpoint changes, replace stale statements here rather than
appending a chronological development diary. Put durable behavior in contracts,
bench results in evidence/validation, and historical narrative in the handover.
