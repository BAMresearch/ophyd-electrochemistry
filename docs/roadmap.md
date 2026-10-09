# Implementation roadmap

M1 pure models/generators and bounded planning compilation, M2 independent
simulation, M3 bounded transport, the narrow M4 finite-hold proof, and the first
offline M5 data-contract slice are implemented. A first M6 classic-Ophyd Flyer
slice now operates against the independent simulator backend. M4 now has target compile/load/idle plus narrow immediate-hold,
manual external-START, active-hold abort, START-wait abort, and no-START timeout state-path proofs,
while electrical timing and failure-path evidence remain open. Full G05 transport characterization and general
TSP/TriggerFlow lowering remain later work.
It is not an operational-hardware approval or a promise that every feature is
available on the instrument.

| Milestone | Deliverables | Acceptance / dependency |
|---|---|---|
| M1 — Pure validation/compiler | Finite SI models including first-class PRBS and arbitrary waveforms; deterministic PRBS/multisine generation; request/compiled hashes; range/resource budgets; CV and waveform timing rules | Independent PRBS vectors, coherent tone/peak tests, finite budgets, invalid requests rejected; no network code |
| M2 — Independent simulation | Fake transport/runtime with START/ABORT, partial buffers, state races, waveform playback and failure injection | Both start modes; repeated abort; failed shutdown; buffer retention; snapshot consistency; source/measurement timing and phase mapping |
| M3 — Transport characterization | **Implemented:** PyVISA backend, identity/firmware/language/output-state checks, read-only diagnostic, bounded framing/chunks/errors. **Open:** retrieval/abort fairness and complete target-unit G05 evidence | G05 on dummy load; no ambiguous command retries |
| M4 — Local runtime proof | **Implemented and target-tested:** exact-artifact TSP loader, minimal finite current hold, READY/BUSY/START model, local timeout, host adapter, target compile/load/idle, immediate and manual external-start holds, active-hold abort, START-wait abort, and no-START timeout state proofs. **Open:** electrical/timing, READY-boundary race, repeated-input and failure evidence | G01, G02, G07; output remains off before START; external-abort capability rejected until proven |
| M5 — Acquisition and data | **In progress:** shared typed V/I schema, immediate and external-start target acquisitions through 256 records, deterministic chunks and checksummed raw archive implemented. **Open:** START/aperture mapping, larger capacity/clock proof, repeated-input behavior and shared-record projection | G03, G04, G06; known physical origin, capacity and clock uncertainty |
| M6 — Classic ophyd Device/Flyer | **In progress:** operational `Keithley2460Device` for holds and current-pulse trains, with real Status objects, background completion, configuration metadata, bounded offset-chunk collection, terminal read/describe, stop/recover/unstage and explicit retained-data disposition over a typed backend. **Open:** target adapter, live running snapshot and hardware lifecycle validation | Simulator-backed Real RunEngine slice passes; target adapter depends on M5 START/aperture mapping |
| M7 — Protocol expansion | Commission finite arbitrary current playback, first-class PRBS and multisine through that shared path; CV/current cutoffs, cycling, pulse and voltage waveform variants | Each protocol proves limits, timing, abort latency and storage budget; waveform fidelity/phase requires G08 |
| M8 — Two-QueueServer integration | Separate startup environments, leader/follower plans, experiment linking, failure propagation | Timeouts, manual abort, missing START, leader/follower restarts, catalog correlation |

Current structurally validated models cover CV, voltage/current holds, current
pulses, `PRBSWaveform` and `ArbitraryWaveform`. A pure multisine generator retains
`MultisineSpec`. M1 expands the compiler result for independent source/measurement
timing, canonical hashes, scheduled aperture mapping and explicit capability budgets.
Planning, [M2 simulated execution](m2-simulator.md), [M3 bounded
communication](m3-transport.md), and the [M4 finite-hold proof](m4-runtime.md)
are implemented. The [M5 data layer](m5-data.md) is implemented offline and has
an explicitly synthetic simulator adapter. The [first M6 device slice](m6-device.md)
projects those retained records through a real RunEngine without claiming target
support. Live instrument execution so far
comprises guarded resistor sourcing, bipolar and buffer tests in SCPI mode plus
volatile TSP runtime installation, finite-hold state paths, and a 17-record
exact-sized TSP acquisition. There is no independent timing trace or operational
hardware-backed Ophyd device yet.
Charge/discharge cycling and voltage-pulse models remain extension work.
Chronoamperometry/chronopotentiometry can be
aliases/compositions of validated step/hold protocols rather than redundant APIs.

## M1/M2 waveform acceptance

- PRBS: independent known bit vectors for each supported generator/order;
  nonzero seed enforcement, maximal period, deterministic replay, full dwell of
  equal adjacent bits, finite sequence mean/commanded charge and exact repeats.
- Arbitrary: finite immutable lists, zero-order hold, full final dwell, no repeat
  endpoint duplication, independent measurement schedule and correct total budgets.
- Multisine: coherent integer bins, explicit phases, no DC/Nyquist/duplicate tones,
  summed peaks/envelope, mean/RMS/crest factor, achieved-frequency reporting and
  generation/schedule hashes. Reject unsafe or under-resolved requests.
- All: fail before output on capability/budget/timing errors; no silent clipping,
  resampling or streaming fallback. Independent simulation covers bit/list/repeat
  boundaries, aperture overlap, timing faults, protection cutoffs, retained partial
  data and abort during wait/playback/measurement. Simulated OFF is not G02 evidence.

M1 may compile against explicitly declared test capabilities. Actual 2460
capabilities remain evidence-gated; test fixtures must not become unverified
hardware defaults. See [waveform specification](waveforms.md).

## Decisions needed before commissioning

Record actual unit firmware/serial, VISA backend, DUT terminals/sense wiring,
cell-specific limits/off-mode behavior, leader logic interface, required worst-case
abort latency, and detector timing precision. These do not block M1/M2. Licensing
is resolved as BSD 3-Clause.

Packaged, reviewed TSP programs remain in scope for M4/M7. User-written TSP
upload/execution is outside the current scope; the simulator has no raw-script API.

## Completion rule

Every milestone updates contract coverage, tests, changelog, and bench evidence.
Do not promote an Open assumption to Supported from a successful simulated test.
