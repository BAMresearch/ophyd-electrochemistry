# ophyd-electrochemistry

**Development template — no operational instrument driver yet.**

A Python library for instrument-owned electrochemistry acquisition through
classic ophyd and Bluesky. The first backend is the Keithley 2460 over Ethernet
using PyVISA and a versioned TSP/TriggerFlow runtime. Electrochemical timing and
the READY/BUSY/START/ABORT handshake belong on the instrument. Two independent
QueueServers may coordinate an operando experiment; the follower exclusively
owns the 2460 connection.

## Start here

- [Implementation contract](docs/implementation-contract.md): normative behavior.
- [Waveform contract](docs/waveforms.md): planned first-class PRBS, arbitrary playback and multisine generation.
- [Assumptions and evidence](docs/assumptions.md): supported facts, corrections,
  and hardware acceptance gates.
- [Architecture](docs/architecture.md): ownership and repository map.
- [Triggering](docs/triggering.md): immediate/external lifecycle and four-line I/O.
- [Safety and recovery](docs/safety.md): shutdown confirmation and retained data.
- [Data contract](docs/data-contract.md): measurements, timestamps, and provenance.
- [Implementation roadmap](docs/roadmap.md): ordered work and acceptance criteria.
- [Validation record](docs/validation.md): checks performed on this template.

The `src/` tree contains inert immutable configuration models, typed interface
contracts, exceptions, and explicit implementation placeholders. It cannot
connect to or energize a sourcemeter. The tests use a **test-only lifecycle
witness**, not an instrument simulator or a proof of physical behavior.

Contract revision **0.2** includes first-class PRBS intent and a shared finite
arbitrary-waveform mechanism. Multisine is generated into that same representation
with explicit tones/phases and preserved provenance. These models/generators
enter M1; this update specifies them without adding executable waveform support.
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

Use `uv sync --locked --extra visa` when developing the future PyVISA backend.
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

Licensing is an unresolved project-owner decision; see [LICENSE](LICENSE.md).
No upstream manuals or example implementations are redistributed.
