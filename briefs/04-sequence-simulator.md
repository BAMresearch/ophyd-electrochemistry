# Brief 04 — pure sequence simulator with a cell model

Requires brief 01; brief 03 for the readout field names (`READOUT_FIELDS`).

## Goal

Off-day development of notebooks, plotting and the ophyd integration needs a
deterministic stand-in for the instrument that executes a `Sequence` with the
same semantics the instrument sequencer will have, producing records in the
`/readout` format of brief 03.

## Files

- `src/ophyd_electrochemistry/sequences/simulator.py`
- `tests/unit/test_sequence_simulator.py`

## Semantics (must match the planned instrument runtime)

- Steps run in `expanded()` order. Sample times come **only** from
  `sample_schedule(step)` (in `models.py`): for each `(count, interval)` phase,
  sample k is taken at the end of its k-th interval. A full step therefore lasts
  exactly `duration_s` and yields `step_records(step)` samples. Do not
  re-derive sample times from `Sampling` fields.
- After each sample: if V is outside `[limits.v_min_abs, limits.v_max_abs]` the
  run stops (outcome `stopped_cutoff`, event `stop`). Else if a step cutoff is
  met (`v_min`/`v_max` for CURRENT/REST, `i_abs_min` for VOLTAGE): event
  `cutoff`; `on_cutoff=NEXT` starts the next step at that time, `STOP` ends the
  run.
- VOLTAGE steps: current from the cell model, clamped to `±limits.i_max_abs`.
- An event `step_start` is emitted at each step start.
- Outcome `completed` when all steps finish.

## Cell model

```python
@dataclass(frozen=True, slots=True)
class CellModel:
    capacity_ah: float = 0.5e-3
    soc0: float = 0.5                  # 0..1
    r0_ohm: float = 20.0
    r1_ohm: float = 50.0
    c1_f: float = 0.2                  # tau = 10 s
    ocv_soc: tuple[float, ...] = ...   # SOC grid 0..1
    ocv_v: tuple[float, ...] = ...     # sloping 2.0 V -> 0.1 V region then a
                                       # low plateau ~0.08 V, hard-carbon-like
    noise_v: float = 50e-6
    # positive current raises SOC and voltage (matches the sign convention)
```

V = OCV(SOC) + I·R0 + V_RC, with V_RC from the R1‖C1 element integrated
exactly between samples (piecewise-constant current). SOC changes by
I·dt/(3600·capacity). Noise from `numpy.random.default_rng(seed)`.

## API

```python
@dataclass(frozen=True)
class SimulationResult:
    readout: dict[str, np.ndarray]   # READOUT_FIELDS
    events: list[tuple[float, int, int, str, str]]
    outcome: str
    final_soc: float

def simulate_sequence(sequence, limits, cell=CellModel(), *, seed=0) -> SimulationResult
def iter_simulated_chunks(result, chunk_records=500) -> Iterator[dict[str, np.ndarray]]
```

`source_status`/`measure_status` are 0 in simulation. `simulate_sequence` calls
`validate(sequence, limits)` first.

## Tests

Record count of a full bipolar sequence equals `expected_records`; the CC-to-
voltage step ends early at the cutoff with a `cutoff` event and the next step
starts; the absolute window stops the run; a zero-net-charge bipolar sequence
returns SOC within 1e-9 of the start (no noise); RC relaxation after a pulse
decays with tau = R1·C1 (fit within 2 %); determinism for equal seeds; a 0.1 s
pulse sequence and a cycling sequence both run under 2 s wall time.

## Out of scope

Ophyd/Bluesky integration, TSP, hardware, plotting.

## Commit message

`feat(sequences): deterministic sequence simulator with RC cell model`
