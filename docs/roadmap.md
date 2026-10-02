# Implementation roadmap

The contract is ready for implementation of pure models and simulator work.
It is not an operational-hardware approval or a promise that every feature is
available on the instrument.

| Milestone | Deliverables | Acceptance / dependency |
|---|---|---|
| M1 — Pure validation/compiler | Finite SI models, serialization/hash, range/resource budgets, discrete CV endpoint/timing rules | Unit tests reject unsafe/unsupported values; no network code |
| M2 — Independent simulation | Fake transport/runtime with START/ABORT, partial buffers, state races, failure injection | Both start modes; repeated abort; failed shutdown; buffer retention; snapshot consistency |
| M3 — Transport characterization | PyVISA backend, identity/firmware/TSP checks, bounded framing/chunks/errors | G05 on dummy load; no ambiguous command retries |
| M4 — Local runtime proof | Minimal finite hold plus READY/BUSY/START and local timeout | G01, G02, G07; output remains off before START; external-abort capability rejected if unproven |
| M5 — Acquisition and data | Measured/readback V/I record schema, timing, frozen-buffer export | G03, G04, G06; known physical origin, capacity and clock uncertainty |
| M6 — Classic ophyd Device/Flyer | Status/state monitor, config metadata, read/describe, complete/collect, stop/unstage | Real RunEngine + simulator; then all relevant bench gates |
| M7 — Protocol expansion | CV, current hold/cutoffs, charge/discharge cycles, current/voltage pulse variants | Each protocol proves limits, timing, abort latency, and storage budget |
| M8 — Two-QueueServer integration | Separate startup environments, leader/follower plans, experiment linking, failure propagation | Timeouts, manual abort, missing START, leader/follower restarts, catalog correlation |

Initial public models cover CV, voltage/current holds, and current pulses.
Charge/discharge cycling and voltage-pulse models are documented extension work;
do not present them as implemented. Chronoamperometry/chronopotentiometry can be
aliases/compositions of validated step/hold protocols rather than redundant APIs.

## Decisions needed before commissioning

Record actual unit firmware/serial, VISA backend, DUT terminals/sense wiring,
cell-specific limits/off-mode behavior, leader logic interface, required worst-case
abort latency, detector timing precision, and licensing. These do not block M1/M2.

## Completion rule

Every milestone updates contract coverage, tests, changelog, and bench evidence.
Do not promote an Open assumption to Supported from a successful simulated test.
