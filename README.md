# ophyd-electrochemistry

**M1–M3 and the first M5 data slice implemented; narrow M4 state paths and a three-record M5 target-buffer proof completed — no operational driver yet.**

A Python library for instrument-owned electrochemistry acquisition through
classic ophyd and Bluesky. The first backend is the Keithley 2460 over Ethernet
using PyVISA and a versioned TSP/TriggerFlow runtime. Electrochemical timing and
the READY/BUSY/START/ABORT handshake belong on the instrument. Two independent
QueueServers may coordinate an operando experiment; the follower exclusively
owns the 2460 connection.

## Start here

- [Implementation contract](docs/implementation-contract.md): normative behavior.
- [Waveform contract](docs/waveforms.md): first-class PRBS, arbitrary-waveform intent and multisine generation.
- [M1 compiler](docs/m1-compiler.md): implemented API, offline example, timing rules and planning limits.
- [M2 simulator](docs/m2-simulator.md): virtual playback, triggering, faults and retained evidence.
- [M3 transport](docs/m3-transport.md): bounded PyVISA transactions and read-only commissioning.
- [M4 runtime](docs/m4-runtime.md): packaged finite-hold TSP proof and live-test boundary.
- [M5 data records](docs/m5-data.md): typed measurement origins, mapping and deterministic retained-buffer chunks.
- [Assumptions and evidence](docs/assumptions.md): supported facts, corrections,
  and hardware acceptance gates.
- [Architecture](docs/architecture.md): ownership and repository map.
- [Triggering](docs/triggering.md): immediate/external lifecycle and four-line I/O.
- [Safety and recovery](docs/safety.md): shutdown confirmation and retained data.
- [Data contract](docs/data-contract.md): measurements, timestamps, and provenance.
- [Implementation roadmap](docs/roadmap.md): ordered work and acceptance criteria.
- [Validation record](docs/validation.md): checks performed on this template.

The `src/` tree contains structurally validated immutable models, deterministic
waveform generators, canonical serialization, a pure bounded planning compiler,
an independent virtual-clock runtime/fake transport, a bounded PyVISA backend,
an immutable M5 measurement/retention schema, and a narrow packaged M4/M5
finite-hold and target-buffer proof runtime. It has no operational
acquisition or ophyd sourcing driver. Guarded notebooks limit live commissioning
to the documented nominal 100 ohm resistor setup. Tests cover M1, M2, mocked
transport/runtime behavior and a separate **test-only RunEngine lifecycle
witness**; they do not establish general physical behavior.

Contract revision **0.2** includes first-class PRBS intent and a shared finite
arbitrary-waveform mechanism. Multisine is generated into that same representation
with explicit tones/phases and preserved provenance. These models/generators
and pure planning now exist, with simulated execution; instrument playback remains future work.
Source updates, electrical measurement and detector exposure have separate
timing requirements. Supported waveform rates remain hardware acceptance gates.

## QuickStart with uv

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) first.
The project pins Python 3.12 for development, supports Python 3.11–3.13, and
commits `uv.lock`. `uv sync` creates `.venv` and installs the package in editable
mode; no manual activation or pip bootstrap is needed.

```bash
git clone https://github.com/BAMresearch/ophyd-electrochemistry.git
cd ophyd-electrochemistry
uv python install
uv sync --locked
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy src scripts
uv run --locked mkdocs build --strict
uv build
uv run --locked twine check --strict dist/*
```

In VS Code, open this directory and select its `.venv` Python interpreter.
For an existing checkout, start at `uv python install`. Inspect package intent
without hardware using:

```bash
uv run --locked python -c "from examples.request import request; print(request)"
```

Compile a PRBS plan with an explicit synthetic profile, without hardware:

```bash
uv run --locked python -m examples.compile_program
```

The result reports `hardware_ready: false`. The example's timings/limits are
test fixtures, not instrument specifications or cell-safe defaults. See the
[M1 guide](docs/m1-compiler.md) for the public compiler and serialization APIs.

Run that plan through the independent simulator, without hardware:

```bash
uv run --locked python -m examples.simulate_program
```

See the [M2 guide](docs/m2-simulator.md) for simulation APIs, synthetic data origin,
failure injection and retention. The M4 loader accepts only the exact packaged
finite-hold TSP artifact; user-written TSP upload/execution remains out of scope.

Use `uv sync --locked --extra visa` for the PyVISA backend. The safe initial
command requires an explicit VISA resource/backend and sends only `*IDN?`,
`*LANG?`, and the language-appropriate source-output state query:

```bash
uv run --locked --extra visa ophyd-electrochemistry-k2460-diagnose \
  --resource 'TCPIP0::169.254.77.125::5025::SOCKET' \
  --backend '@py'
```

See the [M3 guide](docs/m3-transport.md) before connecting hardware. It records
identity, framing and instrument-reported output-OFF evidence but does not prove
electrical isolation or pass G05.
The guarded `notebooks/keithley_2460_resistor_smoke.ipynb` procedure is only for
the stated front-terminal, four-wire, nominal 100 ohm load; it must not be run on
a battery or unknown DUT.
The guarded `notebooks/keithley_2460_buffer_cleanup.ipynb` reproduces the
completed 20-reading buffer/retrieval and ordinary host-exception cleanup test.
Its hardware confirmation remains off by default.
The guarded `notebooks/keithley_2460_m4_runtime.ipynb` reproduces TSP preflight,
volatile runtime installation, immediate hold, programmatic abort, and the
unconnected-input no-START timeout with independent opt-in flags. Read the [M4
guide](docs/m4-runtime.md) before proceeding.
The guarded `notebooks/keithley_2460_m5_buffer.ipynb` reproduces the bounded
three-reading +1 mA target-buffer proof and keeps acquisition, audit, and
destructive discard behind separate opt-in flags. It is only for the confirmed
front-terminal four-wire nominal 100 ohm resistor setup.
For a lean runtime environment, use `uv sync --locked --no-default-groups`.
Development tools are dependency groups, not published package extras.

`uvx` runs standalone tools in isolation. For example, preview the next release
without changing files, tags, or GitHub:

```bash
uvx --from python-semantic-release==10.7.0 semantic-release version --print
```

Project tests need the project environment, so run them with `uv run`, not
isolated `uvx pytest`. See [development and releases](docs/development.md) for
locking, commit conventions, CI, and one-time repository setup.

Start modes are explicit: `prepare(request)` leaves output OFF; immediate
`kickoff()` starts the finite program, while external `kickoff()` arms the
START wait. An external kickoff status completes when the wait is armed,
**before sourcing begins**. This project-specific meaning is tested through
Bluesky and documented prominently in [triggering](docs/triggering.md).

## Git and documentation

The canonical repository is
[BAMresearch/ophyd-electrochemistry](https://github.com/BAMresearch/ophyd-electrochemistry).
Chat development updates are overlays containing only changed/new files with
repository-relative paths. Unpack into your checkout, review the diff, and commit
through your normal workflow. An update never replaces `.git` or `.venv`.

All changes to public behavior must update the contract, evidence/acceptance
records, tests, and changelog together; see [CONTRIBUTING](CONTRIBUTING.md).
The package version has one source, `src/ophyd_electrochemistry/__init__.py`.
Python Semantic Release prepares the version/changelog/lockfile in a PR; CI tags
the merged and tested commit. The initial `0.0.0` is a bootstrap sentinel, not a
released operational driver. The contract revision and runtime ABI/digest are
independent of the package version.

This project is licensed under the [BSD 3-Clause License](LICENSE.md). No
upstream manuals or example implementations are redistributed.
