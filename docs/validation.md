# Template validation record

Updated: 2026-10-09. Scope: contracts, transport/runtime commissioning, bounded
nominal-100-ohm tests, and a narrow target-buffer proof; not operational
sourcing or acquisition.

## M3 bounded transport and initial read-only commissioning

Implemented an optional, lazy-loaded PyVISA backend with explicit resource,
backend, LF/CRLF framing, finite open/I/O timeouts, bounded chunks and hard
command/response byte limits. Whole write/read transactions share one lock. Any
timeout, partial write, malformed or oversized reply invalidates the session.
Mutating writes are never retried and uncertain delivery raises a dedicated
ambiguity error. Arbitrary runtime upload remains disabled; M4 permits only the
exact packaged source/ABI/digest while in TSP mode with output confirmed OFF.

Thirty-two mocked transport tests cover concurrent callers, connection failure,
timeout classification, missing termination, non-ASCII/empty/oversized replies,
partial mutation writes, exact identity/language parsing, wrong model/mode,
session invalidation, evidence fields and the disabled runtime path. The complete
suite now has 212 passing tests on macOS/Python 3.12.13. Ruff lint/format, strict
mypy, locked dependency validation and strict MkDocs also pass.

The diagnostic was then run on 5 October 2026 using PyVISA 1.16.2 and pyvisa-py
0.8.1 against both explicit resources. Raw socket and VXI-11 returned identical
identity `KEITHLEY INSTRUMENTS,MODEL 2460,04686198,1.7.16a` and `SCPI` language.
Only manual-reviewed query-only `*IDN?`, `*LANG?`, and `:OUTPut:STATe?` commands
were sent. Both sessions reported source output `0` (OFF). Exact reports are retained in
`docs/evidence/k2460-2026-10-05-connectivity.json`.

This verifies initial reachability, framing, identity, and instrument-reported
output state only. The DUT was the user's 3 V coin cell rather than a characterized
dummy load; no source, measurement, transfer-abort/starvation, shutdown, timing,
electrical safety, isolation, or OFF-mode loading behavior was tested. All hardware
gates remain NOT RUN.

## Preliminary 100 ohm resistor smoke test

After the coin cell was removed, a user-supplied nominal 100 ohm resistor was
connected to the front terminals with four-wire sense. The user confirmed the
physical output indicator was off. The reviewed SCPI sequence configured current
source and voltage measurement without reset, used source readback and remote
sense, and imposed the instrument's minimum 0.2 V current-source voltage limit.

At 100 µA, source readback was 99.99917 µA and measured voltage was 10.27817 mV
(102.7826 ohm). At 1 mA, source readback was 0.9999969 mA and measured voltage
was 102.5312 mV (102.5315 ohm). Neither point tripped the limit. Output OFF was
confirmed after each point; cleanup programmed 0 A, and a separate query-only
session confirmed output `0` and programmed current `0`.

The guarded notebook and machine-readable record are
`notebooks/keithley_2460_resistor_smoke.ipynb` and
`docs/evidence/k2460-2026-10-05-resistor-smoke.json`. Because the resistor was
not calibrated, these values are plausibility evidence rather than accuracy
acceptance. No abort, disconnect, transfer fairness, timing, OFF-mode impedance,
or failure path was tested, so every hardware gate remains NOT RUN.

A follow-up bipolar sweep took five readings at each of six setpoints from
−1 mA to +1 mA. All 30 readings had the expected polarity and no limit trip.
Linear regression of four-wire voltage against measured current gave
102.502310 ohm slope, −33.020 µV intercept, R-squared 0.999999999914, and a
1.252 µV worst absolute residual. Output OFF was confirmed between points and
again from an independent session. The full record is
`docs/evidence/k2460-2026-10-05-resistor-bipolar-sweep.json`, with reproduction
in `notebooks/keithley_2460_resistor_bipolar_sweep.ipynb`; the same
uncalibrated-load and untested-failure-path limitations apply.

The buffer/cleanup procedure in `notebooks/keithley_2460_buffer_cleanup.ipynb`
was first exercised offline, then run against the resistor. It recovered all 20
five-field records and observed 0.999998004 mA mean source readback,
102.489834 mV mean voltage, and an 82.5695 ms mean timestamp interval. The
deliberate host exception, outer cleanup, and independent new session all
confirmed output OFF and 0 A. A unique temporary buffer was deleted and the two
pre-existing, user-reviewed LAN errors remained unchanged. Full data are in
`docs/evidence/k2460-2026-10-05-buffer-cleanup.json`. Process death, Ethernet
loss, and instrument-local watchdog behavior remain untested.

## M4 finite-hold runtime and target installation

Implemented the M4 proof runtime with ABI `oe-k2460-m4-hold-v1`. The
PyVISA loader rejects arbitrary source, verifies the exact packaged digest,
requires TSP and output OFF, serializes the complete `loadscript` transaction,
does not save to nonvolatile memory, verifies ABI/build afterward, and never
retries an ambiguous partial upload.

The packaged TSP constructs finite TriggerFlow paths for immediate current hold
and external START. Both normal and start-timeout terminal paths contain explicit
source-OFF and flag-deassertion blocks. Programmatic abort separately aborts the
model, disables its timer, switches output off, deasserts flags, and programs
0 A. Hard runtime bounds are 10 mA, 2 V, and 60 s; the host further applies the
explicit directional-current, voltage-envelope, and power limits. External
ABORT, measurement-derived cutoffs, acquisition buffers, general program
lowering, and ophyd integration are intentionally absent.

Offline unit tests cover artifact identity/digest, exact source framing,
arbitrary-source rejection, SCPI/output-ON rejection, existing digest reuse,
parameter/safety bounds, strict status parsing, command construction, and
abort/force-safe/recovery confirmation. The source structure is checked for
local TriggerFlow timing and explicit ON/OFF blocks without a blocking host/TSP
`delay()` call.

The target was changed to TSP from the front panel and rebooted. The exact
blank-free wire artifact compiled, installed in volatile memory, initialized,
and returned typed `idle` status with an empty trigger model and output OFF.
Reusing the matching script did not rerun its top level. No hold was prepared or
armed and no sourcing occurred. Evidence is retained in
`docs/evidence/k2460-2026-10-05-m4-runtime-install.json`.

Live diagnosis found firmware-specific failure modes for blank `loadscript`
messages, a 70-character full-digest script name, and overwriting globals from a
prior diagnostic script. The loader now uses the exact blank-free 301-line wire
form, a 30-character name containing a 96-bit digest prefix, paced writes, a
compile barrier, and a clean-global precondition. The host still verifies the
full SHA-256. External START's clear/initiate/READY boundary remains a specific
G01 race to measure; local process-kill, network-loss, timeout, abort latency,
and electrical OFF behavior remain G02/G07 evidence. No hardware gate is
promoted by installation alone.

Responsive obsolete runtimes were subsequently replaced without reboot by
requiring an idle/empty trigger model, output OFF and 0 A, issuing force-safe,
deleting the owned volatile script, setting its known globals to `nil`, and
verifying absence plus OFF/0 A before installing the new digest. Reboot remains
the recovery for a script that is stuck running and cannot accept cleanup.

The first target prepare exposed unsupported `math.huge` before arming or
sourcing; force-safe left output OFF and 0 A. With the dialect fix, a +1 mA,
0.2 V-limit, 250 ms immediate hold progressed through PREPARED, RUNNING local
delay block 3, COMPLETE terminal block 6, and recovery to IDLE/OFF/0 A. Warning
1808 from reading READY/BUSY output pins was removed by tracking commanded
levels in software. A second identical hold added no warnings or errors, and an
independent session confirmed the final safe state. See
`docs/evidence/k2460-2026-10-05-m4-immediate-hold.json`. No electrical sample or
independent timing trace was collected, so G01/G02/G07 remain NOT RUN.

On 2026-10-09, the same runtime and resistor setup exercised programmatic abort
during a 5 s maximum +1 mA hold. The host observed RUNNING/output-on at delay
block 3, then confirmed ABORTED/output-off, READY/BUSY deasserted, `smu.OFF`, and
0 A. Request-to-confirmation wall time was 0.196 s. Repeated abort remained
ABORTED without an additional warning; recovery returned IDLE/OFF/0 A. The
active abort added the expected “Trigger model path 1 has been aborted” warning
and no error. The retained record is
`docs/evidence/k2460-2026-10-09-m4-programmatic-abort.json`. This is a state-path
and command-idempotence result, not an independent electrical cutoff-latency
measurement, so G02/G07 remain NOT RUN.

The first trace revealed that software-tracked BUSY remained low while the
TriggerFlow model was RUNNING. Build `m4-finite-current-hold-v2` refreshes
logical READY/BUSY from runtime state without reading output-configured pins.
After safe in-place volatile replacement, the target reported RUNNING/BUSY 1 and
ABORTED/IDLE with both flags low, without warning 1808 or a new error.

The external no-START path then exposed two pre-arm firmware differences:
trigger-mode input state reads return `nil` and post errors, while a valid
digital-input read returns symbolic `digio.STATE_HIGH`, not numeric 1. Both
attempts failed before initiation and were independently left OFF/0 A. Build
`m4-finite-current-hold-v4` samples and symbolically compares START only in
digital-input mode, then restores trigger-input mode and reports START unknown.
With the DB-9 connector unconnected and falling-edge START, WAITING_START showed
READY 1/BUSY 0/output-off; 34 polls stayed off. The two-second local timeout was
observed after 2.047 s at block 15 with flags low, no overrun, and no new warning
or error; recovery returned IDLE/OFF/0 A. Evidence is retained in
`docs/evidence/k2460-2026-10-09-m4-start-timeout.json`. Polling does not prove
absence of a brief pulse or independently validate timing, so G01/G02 remain
NOT RUN.

Programmatic abort was then issued while the same unconnected-input model was
WAITING_START at block 5, before timeout and without a START edge. READY was 1,
BUSY/output were low, and abort confirmation after 0.199 s host wall time
reported ABORTED, flags low, `smu.OFF`, and 0 A. Repeated abort added no warning;
recovery returned IDLE/OFF/0 A. Only the expected active-abort warning was added
and errors remained at six. Evidence is retained in
`docs/evidence/k2460-2026-10-09-m4-waiting-start-abort.json`; electrical latency
and READY/START boundary timing remain untested.

A retained SCPI query-only preflight reached serial 04686198 at
2026-10-05T14:25:58Z and reconfirmed firmware 1.7.16a, SCPI mode, and output OFF.
No mutating command was sent. The result is retained in
`docs/evidence/k2460-2026-10-05-m4-preflight.json`; live work stopped before the
front-panel command-set change. Later TSP preflight and installation used
`TCPIP0::169.254.113.151::5025::SOCKET`.

The complete offline suite now has 230 passing tests on macOS/Python 3.12.13.
Ruff lint/format, strict source/script mypy, lock consistency, strict MkDocs,
notebook JSON/offline artifact execution, and package metadata checks are the
required final validation set for this increment.

## M5 measurement-record foundation

The first M5 slice was implemented offline on 9 October 2026. The public
`measurement-v1` models retain explicit electrical and timing origins, raw and
actual-START-relative timestamps, aperture/availability boundaries, source
setpoint, status bits and schedule association. Validation rejects nonfinite
values, invalid apertures, ambiguous mapping, source-function changes, timestamp
reference mismatches, record gaps and declared-capacity overruns.

Frozen buffers bind records to acquisition, request and compiled-program IDs.
Full-buffer and per-chunk SHA-256 values cover canonical JSON records. Chunk
retrieval is addressed by immutable offset rather than an advancing cursor; a
simulated lost reply can therefore be retried without skipping samples. The M2
adapter marks both electrical values and timing as synthetic and preserves an
empty no-START terminal outcome without inventing a START timestamp.

The narrow target increment adds an immediate-only, maximum-eight-reading TSP
path backed by a 16-record fill-once buffer. Three +1 mA records on the nominal
100 ohm front-terminal four-wire resistor contained source readback, measured
voltage, buffer-relative time and both status words. The same offset read was
identical on retry. Source status confirmed readback, four-wire sense and output
on during each aperture; measurement status identified front terminals and the
first-reading-in-group marker, with no questionable bit. Mean V/I was 102.5112
ohm. Warning/error counts were unchanged. Recovery and an independent session
confirmed IDLE/OFF/0 A while the records remained retained; after archiving,
explicit discard left the buffer empty and the source safe. The exact record is
[retained](evidence/k2460-2026-10-09-m5-buffer-proof.json).

Validated locally with Python 3.12.13: 266 tests pass. Ruff lint/format, strict
mypy, notebook JSON/code compilation, and strict MkDocs also pass. The target
proof does not map first-reading-relative timestamps to actual START, establish
capacity/no-wrap behavior, calibrate the resistor, export the shared archive, or
complete hardware gates G03/G04/G06.

## RunEngine failed-Status teardown fix

Reproduced the user's `Future exception was never retrieved` message on local
Python 3.12.14 with locked Bluesky 1.14.6, after all six existing RunEngine tests
reported success. Earlier test counts below did not detect this teardown logging
defect. The expected failed acquisition and retained partial data were correct;
the duplicate Future diagnostic was an upstream RunEngine issue.

Updated the dependency range to `bluesky>=1.15.1,<1.16` and lock to 1.15.1,
which includes [upstream fix #1972](https://github.com/bluesky/bluesky/pull/1972).
The new subprocess regression checks the unchanged abort/retention test through
process exit under asyncio debugging. It requires both expected failure semantics
and absence of unretrieved Future/Task messages; no runtime monkey patch or log
filter is used.

Confirmed the new regression fails with Bluesky 1.14.6 in an isolated environment
and passes with 1.15.1. Validated locally with Python 3.12.14: 180 tests pass,
including the new regression, Ruff lint/format, strict source/release-helper mypy,
locked dependency check, strict MkDocs, uv build and strict Twine metadata checks.
The project retains its 3.11–3.13 CI target; the user's macOS/Python 3.14.5 setup
was not reproduced locally. All hardware gates remain NOT RUN.

The fix `.tgz` is an overlay relative to M2; package version and contract revision
remain the unreleased `0.0.0` and `0.2` respectively.

## M2 implementation validation

This increment follows M1 and adds an independent event-clock runtime, ideal
synthetic circuit, typed transaction-locked fake transport and frozen retained
records. Both start modes, source/measurement clocks, input races, idempotent
abort/recovery and host reconciliation are implemented in simulation only.

Validated locally with uv 0.12.19 / Python 3.12.14:

- 179 tests pass: 67 new M2 simulator cases plus the prior 112 M1/contract/release
  cases. Only this local Python/platform combination was executed.
- Independent expected source traces and aperture integrals, including PRBS
  equal adjacent bits, multisine through arbitrary playback, pulse baselines,
  CV cycles/endpoints, full final dwells and exact/cross-edge apertures.
- READY/stale/held/coincident input races, local START timeout, abort during wait,
  playback/aperture/overhead, deadline/sensor/buffer/cutoff faults and compliance
  provenance. Complete/partial records survive shutdown and explicit recovery.
- Coherent immutable snapshots, bounded frozen chunks, digest-checked full
  diagnostic export, explicit retained-data disposition, ambiguous mutations,
  lost replies/chunks, reconnect without START replay and bounded polling.
- Ruff lint/format, strict source/release-helper mypy, locked dependency check,
  strict MkDocs, offline simulator example, uv wheel/sdist build and strict Twine
  metadata checks. The package still uses the unreleased `0.0.0` sentinel.

Simulation does not interpret TSP or implement the operational ophyd Device,
PyVISA framing, real clocks, analog settling, paired V/I measurements or physical
shutdown latency. Reviewed packaged TSP programs remain planned; user-written TSP
execution is outside scope. All hardware gates remain NOT RUN.

The M2 `.tgz` contains only changed/new repository-relative files after M1.
The M1 validation entry below records the prior increment's coverage.

## M1 implementation validation

This increment is relative to the uv/release and waveform-contract overlays.
M1 now has structurally validated immutable SI models/configuration, first-class
PRBS/arbitrary intent, pure coherent multisine generation, canonical request
round trips and explicit-profile bounded planning compilation.

Validated locally with uv 0.12.19 / Python 3.12.14:

- 112 tests: 100 M1 unit cases plus the prior twelve RunEngine/release cases.
  PRBS checks include full-vector fixtures independently calculated from polynomial
  bit recurrences for every order 2–10, cyclic state uniqueness/balance/correlation
  and fixed seed/phase vectors. Multisine recovery uses an independent DFT.
- Timing/feasibility tests cover CV endpoint/cycle joins, finite pulse/baseline
  dwell, independent aperture mapping, source/list/record/block/byte budgets,
  cutoff cadence, unsupported modes/abort, conservative cell/power limits and hashes.
- Strict source/release-helper typing, Ruff lint/format, locked dependency check,
  strict MkDocs and local Markdown target/anchor checks.
- Offline PRBS example, isolated uv wheel/sdist build, strict Twine metadata and
  packaged resource/unit-test/example inspection.

`hardware_ready=False` is returned for every compiled plan, including declared
bench profiles. Output is finite planning IR, not TSP/TriggerFlow. Source schedules
and cutoff/abort declarations are not measured physical evidence. Firmware-specific
lowering, transport, independent simulator, Device and data acquisition were not
implemented at M1; all bench gates remained NOT RUN. Other CI platforms/Python versions
are configured, not claimed as locally tested. No remote mutation was performed.

The update archive is `.tgz`, containing only changed/new repository-relative
files after the previous overlay, without Git history or generated/environment files.

## Waveform contract update — revision 0.2

This documentation-only increment is relative to the previously delivered
uv/semantic-release overlay. It adds planned first-class PRBS, finite arbitrary
waveforms and a pure multisine generator sharing arbitrary playback, with ADR
0002, provenance, M1/M2 acceptance and hardware gate G08.

Reviewed Tektronix's function-generation brief and official 10 ms pulse example.
Configuration-list waveform generation supports the architecture; PRBS/multisine
lowering is a design inference. The simple pulse example contains no measurement.
Neither source establishes our waveform timing, paired measurement, storage,
phase fidelity or abort latency. All hardware gates, including G08, are NOT RUN.

Validation: strict locked-uv MkDocs build, local Markdown targets/anchors and
contract/gate references, whitespace and changed-files archive contents.
No Python/API/lockfile/workflow changes or new waveform execution tests are
included. The prior twelve-test result below is from the uv update, not evidence
of newly implemented waveform support. No remote mutations were performed.

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
