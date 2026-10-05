# Changelog

<!-- version list -->

## Unreleased

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
