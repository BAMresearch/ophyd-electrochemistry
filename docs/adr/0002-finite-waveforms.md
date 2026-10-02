# ADR 0002 — First-class PRBS and shared finite waveform playback

Date: 2026-10-02. Status: accepted intended design; hardware capabilities unverified.
Contract revision: 0.2. Extends ADR 0001 without changing its lifecycle/ownership.

## Context

Operando battery experiments require binary broadband excitation and configurable
multitone excitation alongside CV/holds/pulses. Reconstructing an unlabelled list
loses PRBS generator/phase information needed to reproduce and correlate a run.
Separate execution paths for each waveform would duplicate timing, shutdown,
buffering and provenance behavior.

Tektronix's [function-generation brief](https://www.tek.com/en/documents/technical-brief/equipping-source-measure-units-with-function-generation-using-tsp-technology)
establishes configuration-list waveform generation as an architectural option
for the 2460. Our PRBS/multisine use of that boundary is a design inference;
the brief is not acceptance of our requested timing or safe-abort requirements.

## Decision

- Add first-class `PRBSWaveform` intent with explicit two levels, bit dwell,
  supported versioned maximal-length LFSR convention, nonzero seed and repeats.
- Add `ArbitraryWaveform` with finite uniformly timed current/voltage levels,
  zero-order-hold semantics, fixed source function and bounded repetitions.
- Implement multisine as a deterministic pure generator retaining `MultisineSpec`
  and lowering into `ArbitraryWaveform`. Start with coherent integer-bin tones,
  explicit peak amplitudes/phases and conservative combined-envelope validation.
- Use one prepared instrument-local playback/compiler/runtime path and inherit
  compliance, cutoffs, both start modes, abort, frozen-buffer collection and recovery.
- Separate source timing, electrical measurement/aperture and detector exposure.
  Retain intent, generated/compiled schedules and actual timing/phase evidence.

Current-driven excitation is the first commissioning priority. Voltage variants
share intent/execution semantics but require their own capability evidence.

## Consequences

M1 now includes deterministic waveform generation and budget validation; M2
includes independent playback/timing/fault simulation. Existing placeholder
compiler fields must evolve before these programs are exposed. No source
classes or operational support are added by this contract change.

Reject unsupported timing/budgets rather than clipping, silently resampling or
streaming over Ethernet. Unbounded playback, arbitrary tap polynomials,
nonuniform dwells, source-function changes and implicit source-OFF segments
are deferred. Downstream impedance/spectral analysis is separate from this driver.

G08 records waveform fidelity/phase evidence, alongside G02/G03/G04/G06. A
successful pure generator or simulator never establishes 10 ms source/measurement
performance, calibrated EIS or detector synchronization. The published 0.1
contract baseline/tag remains historical; this revision does not create a Git tag
or change the independent package version/runtime ABI.
