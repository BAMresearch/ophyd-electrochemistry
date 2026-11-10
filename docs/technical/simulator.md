# Simulator and fakes

Everything above the backend runs without an instrument. There are two kinds of
stand-in, for different purposes.

## The simulated device (`SimulatedSequenceBackend`)

`sequences/backend.py` implements the same `SequenceBackend` protocol as the
2460 backend, so the Flyer, the plan, the run file, both notebooks and the
QueueServer run unchanged on it. Select it with `backend = "simulator"` in a
session file (`qserver/session-simulator.toml`) or `BACKEND = "simulator"` in
the per-run notebook.

- At `prepare`, it validates the request and computes the whole run with
  `simulate_sequence` (`sequences/simulator.py`). From `kickoff`, `poll`
  releases the precomputed readings and events as wall time passes, scaled by
  `speed` (10 = ten times real time; `math.inf` releases everything at once,
  which the tests use).
- External start: `external_start_delay_s` supplies a synthetic START, or
  `fire_start()` does so on demand.
- `abort` cuts the release at the current time with outcome `aborted`;
  `inspect` reports the same `DeviceState` values and output state as the
  hardware backend.

### The cell model

`CellModel` is an equivalent circuit: open-circuit voltage as a function of
state of charge, a series resistance R0 and one R1‖C1 element, plus Gaussian
noise. Defaults: 0.5 mAh, R0 20 Ω, R1 50 Ω, C1 0.2 F, an OCV curve with a low
plateau rising to 2 V, 50 µV noise. Positive current raises the state of
charge and the voltage, as in the sequence convention.

`simulate_sequence` runs the sequence in virtual time with the same sampling
schedule (`sample_schedule`), the same cutoff rules and the same repeat
handling as the instrument program. It produces the readout fields and events
of a real run, so the simulator also serves as an independent reference for
the compiler: the replay of a simulated run must reproduce the simulator's
indices, events and outcome.

The curve is illustrative, **not** a fitted hard-carbon model. The per-run
notebook's preview uses the same simulation and says so on the plot.

## Fakes of the 2460 for backend tests

The hardware backend is tested without hardware by running the **real
packaged TSP** (`tsp/sequencer.tsp`) in Lua through `lupa`, against a mock of
the instrument API (`tests/unit/test_k2460_sequencer_runtime.py`, `MOCK`). The
mock reproduces behaviour measured on the bench: off mode stored per source
function, a function change forcing the output OFF, a stale ON reading after an
OFF command. `tests/unit/test_k2460_sequence_backend.py` extends it with an
event log, a reading buffer and a Python stand-in for the trigger model that
fills the buffer from the simulator. The backend's transport is replaced by one
that executes every line it sends in that Lua state, so the tests exercise the
actual upload, `pcall` error reporting, status parsing, reads, replay, abort and
recovery paths.

A lint (`lint_lua50`) checks the packaged source for constructs the 2460's
Lua 5.0.2 lacks (`#`, `%`, `goto`, integer division) and for lines over 900
characters.

## Older simulator

`ophyd_electrochemistry.simulation` is the virtual-clock runtime and fake
transport of the archived M2 design (waveform playback for `Keithley2460Device`).
It is tested in `tests/simulator/` and is not used by the beamtime path; see the
[archive](../archive/m2-simulator.md).
