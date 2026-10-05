# ophyd-electrochemistry — Codex handover

Prepared 5 October 2026 for continued development in VS Code.

Repository: <https://github.com/BAMresearch/ophyd-electrochemistry>

## Start here

**M1 (models and compiler) and M2 (independent simulation) are implemented. M3 (real transport characterization) is next. There is no operational 2460 driver yet, and every hardware acceptance gate remains NOT RUN.**

This document transfers decisions and validation from the preceding chat. Inspect the actual local checkout before changing anything: its source, contract documents, Git status and any applicable `AGENTS.md` take precedence over an assumed chat baseline. The user has been unpacking incremental update archives and uploading changes independently; do not assume GitHub or the local checkout contains every delivered update.

The user’s macOS checkout was `/Users/bpauw/Code/ophyd-electrochemistry`. VS Code is already open on the repository. Work directly in that checkout, preserve unrelated changes, and leave commits, pushes and publishing to the user unless subsequently instructed otherwise.

### Starter prompt to paste into Codex

```text
Continue development of BAMresearch/ophyd-electrochemistry using
ophyd-electrochemistry-handover.md as context. First read applicable AGENTS.md,
inspect Git status and the current checkout, and read the contract, roadmap,
assumptions, M1/M2 documentation and hardware acceptance record named below.
Confirm that M1, M2 and the Bluesky 1.15.1 failed-Status fix are present, then
run the locked-uv baseline checks. Preserve existing user changes.

Implement the offline parts of M3: a bounded, serialized PyVISA transport,
mocked transport tests, and an explicit read-only commissioning diagnostic.
Use actual 2460 documentation to establish commands and framing. Do not
guess firmware capabilities, reset the instrument, upload a runtime or turn
on its output. Ambiguous mutations must not be automatically retried.

We also discussed a minimal optional single-QueueServer commissioning harness
alongside M3. Inspect the proposal in this handover and incorporate it into
the plan; it has not been implemented. Keep M8 for two-server integration.
Do not expand this into a full beamline service stack.

Complete useful offline implementation and verification before requesting
the missing hardware resource, backend and characterized setup needed for
live diagnostics. Report exactly what was tested and leave hardware gates
NOT RUN until their required evidence exists. Packaged, reviewed TSP programs
remain in scope; arbitrary user-written TSP execution remains out of scope.
Do not push or publish without further instructions.
```

## Project purpose and decisions

Build a classic ophyd/Bluesky interface for finite electrochemistry acquisitions on a Keithley 2460. A follower worker owns one instrument connection. A separate leader may coordinate an experiment through digital triggering; it must not compete for instrument control.

Use `uv`/`uvx`, `pyproject.toml`, a committed `uv.lock`, and semantic release. CI/release structure follows BAM’s McSAS3/MoDaCor conventions where practical. The package version `0.0.0` is an unreleased bootstrap sentinel; semantic release owns future version changes. Current implementation contract revision is **0.2**.

Support both finite uploaded waveform descriptions and packaged, reviewed TSP programs in the eventual implementation. PRBS is a first-class waveform; generic finite arbitrary playback is the shared mechanism for multisine. These are currently models, compiled planning schedules and simulated execution—not an instrument upload API. There is no user-written TSP API.

### Documents and source to read

All paths below are relative to the repository root.

| Read first | Purpose |
|---|---|
| `docs/implementation-contract.md`, `docs/architecture.md` | API, ownership and system boundaries |
| `docs/m1-compiler.md`, `docs/waveforms.md` | Implemented models, compilation and waveform semantics |
| `docs/m2-simulator.md` | Implemented simulation behavior and limits |
| `docs/triggering.md`, `docs/safety.md`, `docs/data-contract.md` | Lifecycle, shutdown, retained data and provenance |
| `docs/assumptions.md`, `docs/hardware-acceptance.md` | Unverified claims and evidence gates |
| `docs/roadmap.md`, `docs/development.md`, `docs/validation.md` | Milestones, commands and historical verification |

Then inspect `src/ophyd_electrochemistry/{interfaces,state,acquisition,protocols,waveforms,serialization}.py`, `keithley/k2460/{config,capabilities,compiler,transport}.py`, `simulation/`, `examples/follower.py`, and the tests. Placeholder files must remain visibly distinguishable from operational code.

## What exists now

| Area | Implemented status |
|---|---|
| M1 models | Immutable finite SI models: potentiostatic/galvanostatic holds, CV, current pulse sequences, PRBS, arbitrary waveforms; multisine generator with retained specification |
| M1 compiler | Pure bounded compilation against explicit capabilities: timing quantization, limits, resource budgets, independent source/measurement schedules, aperture mapping and canonical provenance hashes |
| M2 simulation | Independent event-clock runtime, ideal synthetic circuit, typed transaction-locked fake transport, digital START/ABORT handling, faults, snapshots, retained records and diagnostic export |
| Bluesky tests | Real RunEngine with a small test-only lifecycle witness; document/lifecycle contracts and failure cleanup tested |
| Real transport | `keithley/k2460/transport.py` defines a protocol only; no implemented PyVISA backend |
| Instrument runtime | `tsp/runtime.tsp` is deliberately nonoperational; `io.py` and `device.py` are placeholders |
| Device/Flyer | Full operational adapter is M6; the M2 simulator does not yet implement it |
| QueueServer | No dependency, startup environment, Redis harness or integration tests yet |

Important M1 details:

- `AcquisitionRequest` contains a program, experiment ID and start mode. The compiler requires explicit safety, digital I/O, timing and capability configuration; synthetic profiles are not hardware defaults.
- PRBS uses deterministic finite right-shift Fibonacci/XOR generation, orders 2–10, a nonzero seed and exact repeats. Equal adjacent bits retain separate full dwells. Generator identity is `fibonacci-xor-right-lsb-v1`.
- Arbitrary waveforms use finite uniform zero-order hold, one source function, exact repeats and a full final dwell. There is no host streaming or extra endpoint. Multisine uses coherent integer bins and explicit phases/amplitudes; reject invalid tones or unsafe envelopes instead of clipping or silently normalizing.
- Source updates, electrical measurement apertures and detector exposures are different clocks. A 10 Hz detector does not resolve 10 ms source bits. Do not claim calibrated EIS or physical waveform fidelity from the generator.
- Every `CompiledProgram` has `hardware_ready=False`. Output is finite planning IR, **not** firmware-specific TSP/TriggerFlow. Declared timing, cutoff latency and abstract resource budgets still need hardware proof.
- Current source is the initial commissioning priority. Voltage variants require their own capability evidence. Cycling and voltage-pulse models remain extension work.

Important M2 details:

- Uses a separate integer event scheduler, not wall-clock sleeps or the compiler’s sample-mapping implementation. Polling does not define source timing.
- Models an ideal voltage source plus series resistance with immediate analog transitions and aperture-averaged V/I. It is not a TSP emulator, a VISA framing emulator or a physical shutdown/settling proof.
- Exercises stale/held/coincident input races, missing START, aborts, deadlines, sensor/buffer/cutoff faults, lost shutdown acknowledgment, disconnects, lost replies and explicit recovery.
- Complete records survive faults; incomplete measurements are dropped. Frozen chunks and checksummed full diagnostic export retain provenance. Collection is not durable archive acknowledgment. New preparation requires explicit disposition of the previous acquisition’s retained data.
- `FakeTransport` serializes typed transactions. Lost mutation replies do not authorize replay. A disconnected host reports UNKNOWN; reconnect requires inspection. It never automatically re-arms an acquisition.

## Contracts the next implementation must preserve

1. **Timing and protection are local to the instrument.** No network transaction for every waveform point. Requests are finite and fully budgeted before arming.
2. **Prepare leaves output OFF and flags down.** External kickoff confirms the runtime is armed and READY, before actual START; output remains OFF while waiting. Immediate kickoff confirms execution. This external kickoff meaning is an intentional contract choice.
3. **Completion describes the acquisition.** Successful shutdown after interruption does not turn an aborted acquisition into success. `complete()` is not a graceful-stop request.
4. **Unconfirmed shutdown means ERROR/UNKNOWN.** Fail pending statuses, retain records and require explicit recovery that verifies OFF/stopped. Recovery does not reset, resume, re-arm or erase data automatically.
5. **Cleanup is bounded and preserves history.** Stop/unstage are idempotent after a terminal outcome. Pausing an active acquisition aborts it; there is no automatic resume/replay.
6. **Data has physical origin and provenance.** Snapshot values come from coherent existing measurements and retain their timestamps; no fabricated fresh zero before the first measurement. Buffers must not wrap or overwrite. Correlate uncertain delivery by acquisition identity/sample index.
7. **Hardware claims require scoped evidence.** Firmware, range, source mode, load, measurement settings and required latency matter. A command slope constraint does not establish analog slew. Simulated OFF does not establish electrical OFF.
8. **Wiring is still unverified.** Logical example line assignments are not a DB9 pinout. Leader logic voltage compatibility and the external-abort path must be measured/documented; a trigger event is not automatically a preemptive emergency stop.

## Latest RunEngine warning and fix

The user ran six RunEngine tests on macOS/Python 3.14.5. All passed, but process teardown printed `Future exception was never retrieved`, with an intentional `AcquisitionAborted`/`FailedStatus` traceback.

The same diagnostic was reproduced on Linux/Python 3.12.14 with Bluesky 1.14.6. The latest update changes the dependency to **`bluesky>=1.15.1,<1.16`**, locks **1.15.1**, and adds `tests/contract/test_runengine_failure_logging.py`. The subprocess regression checks the unchanged abort/partial-data case through process exit with asyncio debugging. It fails on 1.14.6 and passes on 1.15.1. Upstream fix: <https://github.com/bluesky/bluesky/pull/1972>.

Do not suppress the warning or patch the RunEngine locally. The failed acquisition, failed stop document and retained partial data remain expected behavior. Verify both the dependency/lock and the regression are present in the actual checkout.

Latest recorded local validation: **180 tests passed** on Python 3.12.14 with uv 0.12.19; Ruff lint/format, mypy, locked dependencies, strict MkDocs, wheel/sdist build and strict Twine checks passed. This handover does not claim these checks were rerun for document generation. CI targets Python 3.11–3.13 on Linux and 3.12 on macOS/Windows. The user’s Python 3.14 environment was not reproduced locally; it is outside the current CI matrix.

### Baseline commands

```bash
uv python install
uv sync --locked --python 3.12
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy src scripts
uv run --locked mkdocs build --strict
uv lock --check
uv run --locked python -m examples.compile_program
uv run --locked python -m examples.simulate_program
```

For M3 dependencies use `uv sync --locked --python 3.12 --extra visa`. The existing extra contains PyVISA and pyvisa-py. Confirm platform/backend suitability rather than assuming an installed vendor VISA library. Use `uv build` and `uv run --locked twine check --strict dist/*` when package contents change.

## M3: the next concrete milestone

Start with an offline-testable transport and explicit **read-only, output-OFF diagnostic**. On 5 October 2026, the user supplied the sourcemeter IP address **`169.254.77.125`**. Reachability, the VISA resource, identity and firmware have not yet been verified. Run the first network checks from the user's local machine, not a remote chat workspace; no bench evidence has been recorded here.

### Implement and verify offline first

- A bounded serialized PyVISA transport, with explicit resource/backend configuration, documented termination/framing, finite timeouts, bounded response/chunk sizes and useful typed errors.
- Identity, model, serial, firmware and TSP-mode checks grounded in the actual 2460 reference documentation. Reject incompatible or ambiguous responses. Do not infer capabilities from a model name alone.
- Transaction tests for concurrent access, timeouts, partial/overlong responses, protocol errors and ambiguous replies. Never automatically retry a mutation whose execution is uncertain. Bound retrieval so later abort handling cannot be starved.
- A diagnostic entry point that performs only reviewed queries, records raw responses plus backend/dependency versions and unit identity, and does not reset, change output settings, load TSP or source a waveform. A query must not be assumed side-effect-free without checking its semantics.
- Update assumptions, development instructions, changelog and validation records. Add evidence references only when evidence exists.

The full reference PDF was unavailable during prior review. Existing official examples and Tektronix’s maintained command API support the design direction but do not establish exact runtime semantics. Obtain and consult the correct manual for this unit/firmware before writing hardware commands.

### Inputs still needed for live work

| Input | Current knowledge |
|---|---|
| Network address | User supplied `169.254.77.125`; local reachability not yet checked |
| Verified VISA resource | Unknown; do not guess SOCKET/INSTR resource or port |
| VISA backend and platform installation | Unknown |
| Actual model, serial, firmware and active command language | Not read from the instrument |
| Dummy load/source, terminals and sense wiring | Not recorded |
| Cell-specific compliance, cutoffs, power limits and OFF mode | Not recorded for hardware |
| Required abort/cutoff latency and timing tolerances | Must be declared for the bench setup |
| Leader digital interface and pinout | Unverified |

An identity probe supplies part of **G05**; it does not pass G05’s partial-transfer, retrieval-abort and starvation requirements. Some of those tests need later operational runtime support. Keep this boundary explicit rather than claiming all M3 hardware acceptance from successful connection.

### Later milestones

| Milestone | Remaining implementation/evidence |
|---|---|
| M4 | Minimal finite local hold, READY/BUSY/START, instrument-local timeouts; G01/G02/G07 |
| M5 | Real V/I field origins, aperture mapping, retained-buffer export, capacity and clock correlation; G03/G04/G06 |
| M6 | Operational classic ophyd Device/Flyer; real RunEngine with simulator, then bench |
| M7 | Commission arbitrary current playback, PRBS/multisine and expanded protocols; G08 and protocol-specific limits |
| M8 | Two independent QueueServers, leader/follower coordination and failure propagation |

Every G01–G08 gate remains **NOT RUN**. Use `docs/hardware-acceptance.md` for required raw traces, buffers, setup, tolerances and scoped results. Two servers produce separate run UIDs linked by experiment ID, not a shared Bluesky run.

## QueueServer proposal: discussed, not implemented

The user suggested using QueueServer earlier for RunEngine startup, orderly shutdown, environment configuration and commissioning plans. The recommendation was a **minimal optional single-server harness alongside M3**, retaining M8 for two-server operation. This refinement is not yet reflected in implemented dependencies or the roadmap; treat it as a proposal to incorporate into the next plan.

Use uv for the Python environment/dependencies; QueueServer manages the worker/process and plan namespace. Begin with one local RE Manager, Redis, CLI and version-controlled startup/configuration. Explicitly construct/configure the RunEngine and subscribers in startup code; verify the chosen QueueServer version’s APIs. Keep direct pytest/RunEngine tests for debugging. A GUI, HTTP service or catalog deployment is not required for the initial harness.

Initially expose simulation/read-only commissioning plans appropriate to the existing interfaces. The M2 simulator is not yet a Flyer: do not pretend it already supports the final acquisition plan. Real sourcing must wait for the local runtime and shutdown proof.

An idle worker’s `environment_close` and forceful `environment_destroy` are different operations; killing a worker cannot prove the instrument stopped. A native shutdown-script hook has not been established in this project. Use explicit bounded cleanup and instrument-local finite shutdown, and verify behavior under disconnect/worker death. Failed/aborted plans may return to the queue front: restarting the queue must not silently replay an interrupted electrochemistry acquisition. Require explicit review, data disposition and a new acquisition identity before rerunning.

Primary references to recheck against the selected version:

- <https://blueskyproject.io/bluesky-queueserver/installation.html>
- <https://blueskyproject.io/bluesky-queueserver/cli_tools.html>
- <https://blueskyproject.io/bluesky-queueserver/startup_code.html>
- <https://blueskyproject.io/bluesky-queueserver/using_queue_server.html>

## Delivery history and release conventions

The preceding chat delivered these incremental overlays, in order:

1. `ophyd-electrochemistry-uv-semantic-release-update.zip`
2. `ophyd-electrochemistry-waveform-contract-update.zip`
3. `ophyd-electrochemistry-m1-update.tgz`
4. `ophyd-electrochemistry-m2-update.tgz`
5. `ophyd-electrochemistry-runengine-fix.tgz`

The last overlay updates `CHANGELOG.md`, `docs/{assumptions,development,validation}.md`, `pyproject.toml`, `uv.lock`, and adds the RunEngine logging regression. Check those files first if the warning persists. Archives contain only changed/new repository-relative files, without an outer repository directory or Git/environment/build artifacts. Use **`.tgz`** for any further archive deliveries; do not archive all changes relative to the original Git HEAD if earlier overlays are already installed.

The original remote baseline was `f8e5e555228d8700fa08f1f23782237fd0bb0688` (`enh: initial framework`). It is historical context, not a target to reset or check out.

CI uses six workflows under `.github/workflows/`: `ci.yml`, `tests.yml`, `build.yml`, `docs.yml`, `release-pr.yml`, `release.yml`. Release preparation creates a `release/semantic-release` PR; publishing checks the exact tested version/changelog/SHA. Conventional `feat:`/`enh:` commits produce minor changes and `fix:`/`perf:` patch changes under the configured pre-1.0 policy. Preserve the changelog’s `<!-- version list -->` marker. Do not manually increment `__version__` or discard the lockfile.

PyPI publishing remains opt-in with repository-owner trusted-publisher configuration; licensing is unresolved. The optional release-PR token and GitHub-generated PR check behavior are documented in the repository. No remote Actions execution, release or publication was performed in this chat.

## What to report at the next milestone

Give the user the concrete changes, the checks actually run, any missing local overlay or environment mismatch, and the exact evidence needed for the next live step. Distinguish mock tests, real identity queries and operational bench measurements. Preserve unverified capability gates and retained data rather than reporting simulated behavior as hardware support.
