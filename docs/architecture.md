# Architecture and repository map

| Layer | Owns | Does not own |
|---|---|---|
| Bluesky plans | Run documents, finite lifecycle, cleanup, experiment correlation | Waveform timing |
| Classic ophyd Device/Flyer | Status objects, state reconciliation, cached snapshots, collection | QueueServer coordination |
| Protocol models | SI-valued electrochemical intent | TSP or hardware line numbers |
| 2460 compiler | Feasible discrete steps, bounds, storage estimates | Network access |
| Packaged TSP runtime | TriggerFlow, local timing, handshake and proven shutdown path | RunEngine documents |
| PyVISA transport | Locked bounded command/response operations | Experimental decisions |
| M2 simulator | Independent event clock, fake circuit/host link, fault and retention tests | Firmware interpretation or physical evidence |

## Module locations

| Path | Purpose/status |
|---|---|
| `src/ophyd_electrochemistry/protocols.py` | Immutable structurally validated CV, hold, current/voltage pulse models and program union |
| `src/ophyd_electrochemistry/waveforms.py` | PRBS/arbitrary/multisine intent and deterministic pure generators |
| `src/ophyd_electrochemistry/serialization.py` | Canonical request JSON/hash and validating allowlisted decoder |
| `src/ophyd_electrochemistry/acquisition.py` | Start mode and request independent of protocol |
| `src/ophyd_electrochemistry/interfaces.py` | Typed Device/Flyer contract |
| `src/ophyd_electrochemistry/keithley/k2460/config.py` | Hardware and timing policy configuration |
| `.../capabilities.py`, `.../compiler.py` | Explicit capability profiles and concrete pure planning compiler |
| `.../transport.py` | Implemented serialized, bounded optional PyVISA transport |
| `.../commissioning.py` | Implemented read-only identity/language/output-state diagnostic and JSON report |
| `.../device.py` | First operational classic-Ophyd Flyer over a typed retained-buffer backend; simulator-backed only until target timestamp mapping is resolved |
| `.../io.py` | Placeholder for the still-uncommissioned external-abort path |
| `.../runtime.py`, `.../tsp/runtime.tsp` | Offline-reviewed M4 finite-current-hold proof; exact packaged artifact only, not an acquisition backend |
| `src/ophyd_electrochemistry/simulation/` | Implemented independent virtual-clock runtime and typed fake transport |
| `tests/contract/` | Real RunEngine operating both the original minimal witness and the simulator-backed M6 Flyer |
| `tests/unit/` | M1 models, waveform properties/independent vectors, budgets, timing and serialization |
| `tests/simulator/` | Independent reference traces, aperture integrals, state races, faults and retention |
| `tests/hardware/` | Future explicitly enabled bench tests |
| `examples/follower.py` | Single-acquisition, pulse-train and archive-before-rearm repeated-shot plans |
| `docs/` | Contract, evidence, commissioning record, roadmap, ADRs |
| `.github/workflows/ci.yml` | Lint/typing/tests/docs/package checks |

## Waveform extension in revision 0.2

The intent layer now includes `PRBSWaveform` and `ArbitraryWaveform`. A pure
multisine generator retains `MultisineSpec` and emits `ArbitraryWaveform`; all
waveforms lower to the same finite instrument-local execution representation.
The program union and `CompiledProgram` now include waveform provenance,
independent source/measurement timing, logical indices and planning budgets.
The finite IR is not TSP/TriggerFlow or a hardware capability approval. The
[M4 proof runtime](m4-runtime.md) implements only one bounded current hold and
does not lower general M1 programs; [M1](m1-compiler.md) and
[M2](m2-simulator.md) document planning/simulation boundaries.

PRBS remains a first-class public intent even if internally expanded into a list.
Deterministic generators may run on the host before preparation; they never drive
point-by-point network timing. Large schedules use associated checksummed exports,
not an array duplicated in every electrical event. The existing four-line I/O
and separate-QueueServer run model remain applicable. See [waveforms](waveforms.md)
and [ADR 0002](adr/0002-finite-waveforms.md).

## Two QueueServers

Both workers have their own RunEngine, run UID, document subscriptions, and
catalog writes. Leader detector `primary` and follower `electrochemistry` are
therefore in **different runs**, joined by `experiment_id` and recorded run UIDs.
The driver also supports a single-RunEngine deployment, where both streams can
belong to one run. Do not promise one shared run in the two-server deployment.

Leader/follower is a facility orchestration pattern, not a QueueServer mode
established by the sources reviewed. Startup/configuration must isolate control
ports, Redis queue state/namespaces, catalogs, and device ownership. Coordination
timeouts and abort propagation are facility integration work outside the driver.

The follower prepares, kicks off, then waits on `complete()` while its background
monitor services hardware state. The leader waits for READY, arms detectors,
sends START, and confirms execution. BUSY falling alone is not proof of success;
retrieve follower outcome. Very short BUSY pulses may be missed by polling.
