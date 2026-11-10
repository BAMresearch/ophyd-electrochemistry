# ophyd-electrochemistry

Bluesky control of a Keithley 2460 SourceMeter for battery experiments:
galvanostatic and potentiostatic cycling, state-of-charge positioning, and
current pulses with rests. The instrument runs each sequence on its own (timing,
voltage cutoffs, step markers, output OFF at the end); the computer prepares,
starts and reads it. Built for operando beamtime measurements on
hard-carbon/Na coin cells, and usable on its own at the bench.

## What the package contains

```text
Excel run sheet + session file (limits)
        │
Bluesky QueueServer      queues one plan per run-sheet row
        │
Bluesky RunEngine        run_sequence plan ──► HDF5 run file, live plot
        │
SequenceFlyer            classic ophyd Flyer: prepare, kickoff, collect, abort
        │
2460 sequencer backend   ─or─   simulated backend (synthetic cell)
        │
TSP runtime on the 2460  trigger model: timing, cutoffs, markers, output OFF
```

- **At the core, an ophyd Flyer** (`SequenceFlyer`) that runs one finite step
  sequence on a backend and streams its readings into Bluesky documents.
- **On the instrument, a packaged TSP runtime.** The host compiles each
  sequence into a trigger model of at most 63 blocks; the 2460 times every
  sample, checks the voltage limits after every reading and switches the output
  OFF itself. The host never paces samples and never sends user-written TSP.
- **On top, Bluesky and the QueueServer.** A `run_sequence` plan and a
  QueueServer startup that turns rows of an Excel run sheet into queued runs,
  validated against the limits in a session file (TOML).
- **Data:** one HDF5 file per run with every reading, the events and the full
  settings, readable while it is written; a viewer notebook to follow and
  compare runs.
- **A simulator** with a synthetic cell that stands in for the instrument, so
  the whole stack runs and is tested without hardware.

Status: in development on the `mvp-sprint` branch for a beamtime in December
2026. The sequencer path runs on the 2460 (tested on a 100 Ω resistor and a
coin cell); it is not yet released on PyPI.

## Quickstart

Needs [uv](https://docs.astral.sh/uv/getting-started/installation/), git and
redis. uv installs Python 3.14 itself.

```sh
git clone -b mvp-sprint https://github.com/BAMresearch/ophyd-electrochemistry.git
cd ophyd-electrochemistry
uv sync --locked --extra experiment --extra visa --extra queueserver
source .venv/bin/activate
oe-template runs.xlsx
oe-validate runs.xlsx --session qserver/session-simulator.toml
```

The [Quickstart](docs/user/quickstart.md) continues from there: run the rows
through the QueueServer on the simulator and look at the data.

## Documentation

- [User guide](docs/user/index.md): quickstart, run sheets, sessions, the
  procedure at the beamtime, data and the viewer, command reference.
- [Technical documentation](docs/technical/index.md): how the package is
  built and which component does what, the sequencer runtime, the simulator,
  testing, development and releases.
- [Current state](docs/current-state.md) and [MVP plan](docs/mvp-plan.md).

Build the documentation site with `uv run --locked mkdocs build --strict`.

The package also still contains its earlier general design (contract revision
0.2: waveform models, the `Keithley2460Device` Flyer, the M4 runtime). The
beamtime path does not use it; see the [archive](docs/archive/index.md).

## License

[BSD 3-Clause](LICENSE.md). No Keithley manuals or example code are
redistributed.
