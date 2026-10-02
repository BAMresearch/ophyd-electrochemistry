# Architecture and repository map

| Layer | Owns | Does not own |
|---|---|---|
| Bluesky plans | Run documents, finite lifecycle, cleanup, experiment correlation | Waveform timing |
| Classic ophyd Device/Flyer | Status objects, state reconciliation, cached snapshots, collection | QueueServer coordination |
| Protocol models | SI-valued electrochemical intent | TSP or hardware line numbers |
| 2460 compiler | Feasible discrete steps, bounds, storage estimates | Network access |
| Packaged TSP runtime | TriggerFlow, local timing, handshake and proven shutdown path | RunEngine documents |
| PyVISA transport | Locked bounded command/response operations | Experimental decisions |

## Module locations

| Path | Purpose/status |
|---|---|
| `src/ophyd_electrochemistry/protocols.py` | Immutable CV/hold/pulse models; validation still to implement |
| `src/ophyd_electrochemistry/acquisition.py` | Start mode and request independent of protocol |
| `src/ophyd_electrochemistry/interfaces.py` | Typed Device/Flyer contract |
| `src/ophyd_electrochemistry/keithley/k2460/config.py` | Hardware and timing policy configuration |
| `.../compiler.py`, `.../transport.py` | Pure compiler / transport protocols |
| `.../device.py`, `.../io.py` | Explicit placeholders; no operational code |
| `.../tsp/runtime.tsp` | Deliberately non-operational placeholder resource |
| `tests/contract/` | Real RunEngine operating a test-only lifecycle witness |
| `tests/simulator/` | Future independent fake transport/instrument |
| `tests/hardware/` | Future explicitly enabled bench tests |
| `docs/` | Contract, evidence, commissioning record, roadmap, ADRs |
| `.github/workflows/ci.yml` | Lint/typing/tests/docs/package checks |

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
