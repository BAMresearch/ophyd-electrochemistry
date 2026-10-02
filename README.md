# ophyd-electrochemistry

**Contract template, version 0.0.0.dev0 — no operational instrument driver yet.**

A Python library for instrument-owned electrochemistry acquisition through
classic ophyd and Bluesky. The first backend is the Keithley 2460 over Ethernet
using PyVISA and a versioned TSP/TriggerFlow runtime. Electrochemical timing and
the READY/BUSY/START/ABORT handshake belong on the instrument. Two independent
QueueServers may coordinate an operando experiment; the follower exclusively
owns the 2460 connection.

## Start here

- [Implementation contract](docs/implementation-contract.md): normative behavior.
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

## Development

Python 3.11–3.13 is the initial CI target. The template was locally checked on
Python 3.12 with Bluesky 1.14.6 and ophyd 1.11.2. Dependency ranges define the
intended support window; CI must establish compatibility for each environment.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
ruff check .
ruff format --check .
mypy src
pytest
mkdocs build --strict
python -m build
```

Start modes are explicit: `prepare(request)` leaves output OFF; immediate
`kickoff()` starts the finite program, while external `kickoff()` arms the
START wait. An external kickoff status completes when the wait is armed,
**before sourcing begins**. This project-specific meaning is tested through
Bluesky and documented prominently in [triggering](docs/triggering.md).

## Git and documentation

This snapshot includes a local Git repository on `main`, an initial commit,
and tag `contract-v0.1`. No remote repository has been created or configured.
The archive also contains the complete source tree, so it can be imported as a
new repository if its embedded history is not wanted.

All changes to public behavior must update the contract, evidence/acceptance
records, tests, and changelog together; see [CONTRIBUTING](CONTRIBUTING.md).
The runtime ABI has its own version and digest. The library version alone does
not establish runtime compatibility.

Licensing is an unresolved project-owner decision; see [LICENSE](LICENSE.md).
No upstream manuals or example implementations are redistributed.
