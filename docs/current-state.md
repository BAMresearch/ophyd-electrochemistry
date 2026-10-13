# Current project state

Updated: 2026-10-13

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
- The guarded timestamp-proof notebook and runtime v14 fixture are prepared,
  but the physical proof has not been completed.
- Runtime v14 and archive v3 implement immutable, query-verified K2460
  measurement profiles at 1, 0.1 and 0.01 NPLC with a fixed 0.2 V range,
  filter/autorange/relative off and one output-off autozero. The faster profiles
  remain uncommissioned on target hardware.
- The target runtime has completed immediate retained-buffer acquisition through
  10,000 records and external-start acquisition through 256 records, with
  deterministic chunk retry, archival, recovery, and independently matched
  fresh-session transfer.
- Measurement schema v2 exposes separate untouched `ec_source_status` and
  `ec_measurement_status` values with independent definitions. The obsolete
  combined status field is rejected.
- `VoltagePulseSequence` is available for immutable intent, canonical request
  encoding, pure compilation, and simulated execution. It remains outside the
  hardware-facing M6 default allowlist and has no commissioned target runtime.
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

The three v14 measurement profiles have each completed a 256-record immediate
resistor run at +1 mA. Observed paired-I/V rates were 24.497 Hz at 1 NPLC,
207.034 Hz at 0.1 NPLC and 821.183 Hz at 0.01 NPLC. Voltage population standard
deviations were 0.982, 275.822 and 302.250 microvolt respectively. These are
scoped observations for this firmware and bench, not guaranteed maxima or
accuracy claims; external-START/aperture timing remains unproven.

On 12 October the resistor/output-off setup was freshly reconfirmed. A v13
timestamp attempt started before an independent trace was available and ended
with `start_overrun=true`; it is retained only as rejected diagnostic evidence,
made no timing claim, and its one buffer record was explicitly discarded after
archival. A later v14 installation attempt failed while opening the VISA socket,
before any command reached the instrument.

Query-only connectivity was restored later on 12 October at
`169.254.113.151:5025`. Identity, serial number, firmware and TSP mode matched
the commissioned instrument. Read-only inspection found the exact v13 runtime
IDLE, the trigger model idle, output OFF, programmed level 0 A, an empty
16-record buffer, and zero unread warning/error events. The front panel had
shown `START input is asserted before arm` at 13:11:49, but the later inspection
found no active runtime or unread-event indication of that fault.

After explicit authorization, the guarded replacement path deleted only that
allowlisted empty v13 runtime and installed packaged v14 with SHA-256
`c4c40686bf2016f5c55c820b5d0bd34f78915dd10e6a77823d0a9cad245c6e82`.
The installation session and a fresh query-only session independently confirmed
v14 IDLE, trigger model idle, output OFF, programmed level 0 A, no retained
records, and zero unread warnings/errors. No acquisition was prepared or armed,
no output was enabled, and no reboot was needed. This live observation does not
replace fresh load/wiring confirmation before an energized run.

The nominal 100 ohm front-terminal four-wire resistor, physical output-OFF
indicator and a static-low 0.02 V START input were freshly confirmed for the
profile benchmark; immediate mode did not use START. Each v3 archive decoded
successfully, and a fresh session matched all 256 retained live records before
any authorized discard. All source statuses were 200; measurement status was
264 for the first record and 8 thereafter. Warning/error counts stayed zero and
every run finished output OFF at 0 A. The comparison and all archive checksums
are in
`docs/evidence/k2460-2026-10-12-measurement-profile-benchmark.json`.

Each profile's records were discarded only after separate user authorization.
A fresh session after the final discard verified v14 IDLE, trigger model idle,
output OFF, programmed level 0 A, an empty 256-record-capacity buffer, and zero
warnings/errors. That audit exposed a host lifecycle mismatch: the high-level
chunk reader rejected retrieval after recovery to IDLE even though retained
data remained readable through the runtime's own inactive-model/output-OFF
guard. The host now permits bounded reads from output-OFF IDLE while continuing
to reject active/prepared and output-on states. Focused offline tests pass; the
TSP artifact is unchanged.

Later on 12 October, an immediate +1 mA run at 0.01 NPLC filled an exact
10,000-record standard buffer without wrap. Acquisition took 12.181 s of host
wall time and the buffer-relative span was 12.17023136 s (821.595 observed
paired-I/V records/s). Bounded chunks of 4096, 4096 and 1808 records were
retried, archived, decoded and matched record-for-record from a fresh session.
Warning and error counts remained zero. After archive verification and explicit
user authorization, all 10,000 records were discarded. A fresh query-only
session confirmed v14 IDLE, trigger model idle, output OFF, programmed level
0 A, an empty 10,000-record-capacity buffer, and zero warnings/errors. The
archive, summary and discard record are
`docs/evidence/k2460-2026-10-12-capacity-10000-archive.json`,
`docs/evidence/k2460-2026-10-12-capacity-10000-evidence.json` and
`docs/evidence/k2460-2026-10-12-capacity-10000-discard.json`.

## User interface and quickstart

- `docs/user-guide.md` describes immutable per-run configuration, the Flyer
  lifecycle, paired I/V records, retained-data behavior, and preliminary result
  inspection.
- `docs/available-sequences.md` distinguishes current intent/planning/simulation
  coverage from default-Flyer and target support, and records phased plans for
  an example gallery, finite segment composition, waveform generators, and
  hardware commissioning.
- `notebooks/ophyd_electrochemistry_quickstart.ipynb` provides selectable
  unipolar-current, voltage-pulse, and charge-balanced alternating-current
  examples. It runs without importing the `examples` package or requiring an
  editable install. It locates the checkout, exposes `src/`, and defines its
  synthetic configuration and plan locally.
- The notebook uses Plotly for linked voltage/current plots, hover inspection,
  source-command display on the matching axis, and shaded pulse dwells.
- Configuration cells contain short field comments. `command_timeout_s` bounds
  one backend transaction, `external_start_timeout_s` bounds the local START
  wait, and `shutdown_timeout_s` bounds confirmation of abort/output OFF. None
  of these values represents acquisition duration.
- Simulator advancement and the host completion watchdog are derived from the
  compiled program duration. `prepare()` now returns validated local-completion
  and communication bounds plus request/program digests and record/capacity
  bounds. A reused Flyer replaces its effective watchdog for every successful
  request while retaining the constructor timeout only as a minimum. Explicit
  disposition clears stale bounds; rejected re-preparation preserves an earlier
  retained acquisition. The regression case with four 0.5 s periods and 0.2 s
  pulses produces 200 records at a 10 ms sample period and finishes with state
  COMPLETE and output off.
- Under the current synthetic profile, the minimum sample period is 3 ms
  (2 ms aperture plus 1 ms overhead). That is simulator behavior, not a
  hardware-rate claim.

The typed prepare-result and long-lived Flyer behavior are implemented for the
simulator backend. A future target adapter must supply its own reviewed bounds
from commissioned runtime behavior; it must not inherit synthetic planning
assumptions. Users do not need to enlarge communication or shutdown timeouts
merely because a pulse program is longer.

## Working tree and validation

At this checkpoint the runtime-v14/profile work is intentionally uncommitted.
It changes the K2460 runtime, archive, public submodule exports, unit tests,
guarded notebooks, user/developer documentation and changelog; it adds the
measurement-profile module and the rejected-attempt evidence JSON. The
quickstart notebook also restores its documented/tested default selector to
`current_pulse`. Always use current `git status` rather than this summary to
decide what must be preserved.

The latest full validation after adding the voltage and bipolar pulse examples
reported:

- 321 tests passed;
- Ruff lint and formatting passed;
- strict mypy passed;
- strict MkDocs passed;
- `git diff --check` passed; and
- the lockfile was resolved and checked after adding notebook dependencies.

Strict MkDocs, Ruff lint/format, strict mypy, and `git diff --check` were rerun
for the pulse-example update and passed.

The current complete offline suite reports 359 tests passed. Ruff lint and
formatting, strict source mypy, strict MkDocs, notebook JSON validation, and
`git diff --check` pass. Runtime v14 is installed on the target and all three
profiles now have scoped resistor-bench evidence; that hardware evidence is
separate from the offline suite.

## Next work

1. With an independent timing trace available, run/review the guarded hardware
   timestamp proof.
2. Use the timestamp result to define the real target-record projection and
   connect the prepared runtime backend to the M6 Flyer without inventing
   timestamp precision. Validate recovered-IDLE retrieval through that target
   adapter. Keep acquisition instrument-local and retrieve retained buffers only
   after the finite run.

When this checkpoint changes, replace stale statements here rather than
appending a chronological development diary. Put durable behavior in contracts,
bench results in evidence/validation, and historical narrative in the handover.
