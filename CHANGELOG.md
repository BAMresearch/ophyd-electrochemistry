# Changelog

<!-- version list -->

## Unreleased

- Add a living user guide and simulator-backed quickstart notebook covering the
  three-layer configuration model, safe Ophyd lifecycle, paired I/V data,
  retained-data behavior and interactive Plotly post-run visualization. Derive
  the simulator run budget and host completion watchdog from the compiled
  request so longer pulse programs do not require manual timeout coordination.
- Revise the shared measurement schema to v2: replace the ambiguous combined
  status word and definition with independent source and measurement status
  values and definitions throughout typed records, simulator projection and the
  19-field Bluesky stream.
- Prepare runtime v13 and a guarded target timestamp proof. A uniquely tagged
  set of instrument-clock event markers brackets BUSY assertion and one
  digitize block; terminal retrieval exposes the absolute reading timestamp,
  NPLC and line frequency. The notebook combines these with a physical
  START-to-BUSY scope delay, archives every remotely consumed information event,
  and makes no timestamp-reference or hardware-gate claim before review.

- Add the first operational M6 classic-Ophyd Flyer over a typed acquisition
  backend. It provides real asynchronous Status objects, immediate/external
  kickoff semantics, background completion monitoring, confirmed abort/recovery,
  retained-data collection, snapshots and configuration metadata. Collection
  consumes bounded retryable offset chunks instead of materializing the entire
  retained acquisition; chunk size is the sole allowlisted mutable setting.
- Exercise that Flyer through a real Bluesky RunEngine for both start modes,
  full fixed-schema event emission, partial data after abort, no-START timeout,
  recovery and rejection of unsupported CV before output. The independent
  simulator is the only backend; raw 2460 adaptation remains blocked on the
  unresolved START/aperture timestamp mapping.
- Enable finite `CurrentPulseSequence` acquisitions in the Flyer. One START runs
  a complete locally timed pulse train; trigger-per-pulse operation is expressed
  as separately armed `count=1` acquisitions, not an unproven multi-edge mode.
- Make the independent simulator honor configured rising, falling and either-edge
  START detection, including directional pre-arm level checks and ignored return
  transitions after the one-shot wait. Report the backend edge selection as
  read-only Ophyd Device configuration.
- Add follower plan templates for one locally timed pulse train and separately
  armed external `count=1` shots. Repeated shots require unique raw destinations,
  export before exact-ID discard, and stop with retained data on failure.
- Add the first offline M5 data layer: immutable versioned V/I records with
  explicit electrical/timing origin, clock/aperture/source mapping, validated
  terminal buffer metadata, canonical checksums and deterministic offset chunks.
- Adapt frozen M2 records into that schema without weakening their synthetic
  provenance; retrying a lost offset-chunk reply no longer advances an implicit
  cursor. Target START/aperture mapping and shared-record projection remain open.
- Add a narrow M5 target adapter and TSP buffer proof: at most eight immediate
  1 NPLC readings in a 16-record fill-once buffer, raw paired source-readback/V
  fields plus statuses, deterministic offset retrieval, and explicit discard.
- Record three +1 mA four-wire readings on the nominal 100 ohm resistor. The
  retained retry matched exactly, mean V/I was 102.5112 ohm, warning/error
  counts were unchanged, and an independent session confirmed IDLE/OFF/0 A.
  Timestamps remain relative to the first reading and no hardware gate is
  promoted.
- Allow-list replacement of only the exact idle/output-off/0 A v4 volatile
  runtime, avoiding a reboot while retaining the loader's refusal to overwrite
  unknown scripts or globals.
- Add a versioned raw-K2460 archive with contiguous-chunk assembly, inner record
  and whole-payload checksums, allowlisted parsing, UTC/timestamp-origin metadata,
  and exclusive-create output that refuses to overwrite an existing archive.
- Exercise the runtime maximum of eight readings as deterministic 3+3+2 chunks.
  The checked archive reloaded exactly, the middle chunk retried identically,
  warning/error counts were unchanged, and independent retention/discard checks
  ended IDLE/OFF/0 A with an empty buffer.
- Clarify that eight readings is only the proof-runtime cap. An output-off
  `buffer.make(0, buffer.STYLE_STANDARD)` probe on firmware 1.7.16a allocated
  5,110,784 currently available records, then deletion plus garbage collection
  restored the prior buffer state without new warnings or errors.
- Replace the artificial eight-reading/16-record limits with runtime build v6:
  exact-sized fill-once allocation, a configurable 250,000-record host ceiling,
  a 5,000,000-record instrument-side backstop, and independently bounded
  4,096-record maximum transfers. Historical v5 archives remain readable.
- Allow-list safe in-place replacement of the exact v5 runtime only when its
  buffer is empty. On target, v6 allocated exactly 17 records, filled all 17,
  retrieved them as 10+7 chunks with deterministic retry, and preserved them
  through an independent IDLE/OFF/0 A audit before verified archival and
  explicit discard. Warning/error counts remained unchanged.

- Implement the M3 serialized, bounded optional PyVISA transport with explicit
  backend/resource/framing, strict 2460 identity/language validation, typed
  failures, session invalidation, and no retries of ambiguous mutations.
- Add a read-only commissioning command and notebook for identity, language and
  source-output state, plus mocked concurrency/framing/failure tests and initial
  raw-socket/VXI-11 output-OFF evidence. Hardware gate G05 remains NOT RUN.
- Add a guarded, bounded front-terminal four-wire resistor-smoke notebook and
  retain the two-point 100 µA/1 mA evidence. The uncalibrated load result does
  not promote any hardware acceptance gate.
- Record a six-point, 30-reading bipolar resistor sweep with correct polarity,
  no compliance trips, and fitted linearity/repeatability evidence; hardware
  gates remain NOT RUN.
- Add a guarded 20-reading buffer/retrieval and intentional host-exception
  cleanup notebook; retain complete hardware evidence for buffer bounds, record
  fields, timing, unchanged error count, OFF/0 A cleanup, and temporary deletion.
- Implement the M4 finite-current-hold proof: an exact-artifact-only TSP
  loader, versioned/digested packaged runtime, local finite/source-OFF and START
  timeout TriggerFlow paths, typed host adapter, and guarded preflight notebook.
- Record target TSP compile/load/idle evidence and harden firmware-1.7.16a upload:
  blank-free paced wire form, short digest-prefix name, compile barrier, clean
  globals, non-rerunning reuse, symbolic TSP enum parsing, and separate OFF-only
  initialization. No finite hold or source-output enablement was performed.
- Remove unsupported `math.huge` use found by the first target prepare attempt;
  the attempt failed before configuration or sourcing and force-safe confirmed
  output OFF with a programmed 0 A level.
- Track commanded READY/BUSY levels in the runtime status instead of reading
  output-configured digital pins, avoiding firmware warning 1808.
- Record two +1 mA, 0.2 V-limit, 250 ms immediate local holds on the nominal
  100 ohm resistor, including PREPARED/RUNNING/COMPLETE states, warning-free
  confirmation, and independent IDLE/OFF/0 A cleanup. No electrical samples or
  independent timing trace were collected.
- Record a programmatic abort during a 5 s maximum +1 mA hold, including
  RUNNING-to-ABORTED, OFF/0 A confirmation, idempotent repeated abort, recovery,
  the expected trigger-model-aborted warning, and zero errors. The 0.196 s host
  request-to-confirmation observation is not electrical cutoff-latency evidence.
- Fix software-tracked READY/BUSY status for TriggerFlow state changes, identify
  the corrected artifact as `m4-finite-current-hold-v2`, replace the responsive
  volatile runtime without reboot, and verify RUNNING/BUSY plus abort/idle-low
  states on the target without warning 1808.
- Harden external START after target firmware rejected trigger-mode input reads
  and returned symbolic digital-input levels: sample inactivity in digital mode,
  compare enums, restore trigger mode/edge, and report armed START as unknown.
  Runtime build v4 completed the unconnected-input two-second timeout path with
  READY asserted, output off in all polls, terminal flags low, no overruns, no
  new warning/error, and IDLE/OFF/0 A recovery.
- Verify programmatic abort from WAITING_START before timeout: READY asserted,
  BUSY/output low, ABORTED and OFF/0 A confirmation, idempotent repeated abort,
  IDLE recovery, one expected abort warning, and unchanged retained errors.
- Keep external ABORT disabled and G01/G02/G07 NOT RUN pending command-language
  transition, target compilation, READY-boundary traces, and failure testing.

- Require Bluesky >=1.15.1,<1.16 and update the lockfile to include the upstream
  fix for unretrieved failed-Status Futures at RunEngine teardown.
- Add a subprocess regression test that checks process-exit logging while
  preserving FailedStatus propagation, failed run outcome and retained partial data.

- Implement M2 independent virtual-clock simulation and typed fake transport:
  finite playback, READY/START/ABORT, local timeout, synthetic aperture averages,
  coherent snapshots, frozen chunks and checksummed retained-data export.
- Exercise independent reference traces, state/input races, timing/buffer/cutoff
  faults, uncertain shutdown, ambiguous replies and reconnect without command replay.
- Add an offline uv simulator example and clarify packaged TSP program scope;
  user-written TSP upload/execution remains outside the current scope.

- Implement M1: validated finite SI intent/configuration, first-class PRBS and
  arbitrary waveforms, coherent multisine generation and canonical request round trips.
- Add an explicit-profile pure compiler producing bounded finite IR with separate
  source/measurement schedules, aperture mapping, CV endpoints, resource budgets
  and generator/request/plan hashes. No network or executable TSP is generated.
- Include offline example and unit coverage for independent PRBS vectors, DFT
  tone recovery, cutoff latency, unsafe/unsupported requests and deterministic plans.
- Align exported contract revision with the documented 0.2 contract; retain the
  package's unreleased 0.0.0 semantic-release sentinel.
- Expand implementation contract to revision 0.2: first-class PRBS and generic
  finite arbitrary-waveform intent; multisine lowers through the shared mechanism.
- Specify deterministic generation, separate source/measurement timing, waveform
  provenance and M1/M2 acceptance; add G08 for hardware waveform fidelity/phase.
- No waveform Python models, generators, runtime or driver implementation added.
- Adopt uv/uvx setup, a committed cross-platform lockfile and dependency groups.
- Use a single dynamic version source and Python Semantic Release commit rules.
- Add reusable CI jobs and release preparation PRs based on MoDaCor/McSAS3.
- Build/tag the tested commit; configure optional PyPI Trusted Publishing.
- Normalize the development version to the unreleased `0.0.0` SemVer sentinel.

## 0.0.0.dev0 — 2026-10-02

- Document implementation contract revision 0.1.
- Define typed protocol/configuration/interface templates and module boundaries.
- Record source-supported assumptions and unresolved hardware validation gates.
- Add GitHub Actions, documentation build, package build, and RunEngine witness tests.
- Do not implement transport, runtime, simulator, or operational device behavior.
