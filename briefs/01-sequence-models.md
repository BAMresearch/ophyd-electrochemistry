# Brief 01 — step-sequence models, validation and builders

## Goal

A small, pure (no I/O, no hardware) model of a finite run as a sequence of
steps. Every experiment protocol for the December beamtime (cycling, SOC
positioning, bipolar current pulses with rests) must be expressible with it, and
it must map cleanly onto an instrument trigger model later (one repeated block,
at most 30 steps). It is new and independent of the existing
`waveforms.py`/compiler code; do not import or modify those.

## Files

- `src/ophyd_electrochemistry/sequences/__init__.py` (public exports)
- `src/ophyd_electrochemistry/sequences/models.py`
- `src/ophyd_electrochemistry/sequences/builders.py`
- `tests/unit/test_sequences.py`
- Reuse `ophyd_electrochemistry.exceptions.ValidationError` for all errors.

## Conventions

- SI units: s, V, A. Positive current flows out of the 2460 HI terminal into the
  cell's + terminal and raises the cell voltage.
- All models are frozen dataclasses (`slots=True`), validated in `__post_init__`
  for type, finiteness and sign; cross-object checks live in `validate()`.

## Models (`models.py`)

```python
class StepMode(StrEnum): CURRENT = "current"; VOLTAGE = "voltage"; REST = "rest"
class CutoffAction(StrEnum): NEXT = "next"; STOP = "stop"

@dataclass(frozen=True, slots=True)
class Sampling:
    interval_s: float                    # slow-phase spacing, > 0
    fast_interval_s: float | None = None # optional fast spacing at the step start
    fast_window_s: float = 0.0           # duration of the fast phase, >= 0
    # fast_interval_s and fast_window_s > 0 must be given together;
    # fast_interval_s < interval_s.

@dataclass(frozen=True, slots=True)
class Step:
    mode: StepMode
    level: float          # A (CURRENT), V (VOLTAGE); must be 0.0 for REST
    duration_s: float     # maximum step duration, > 0
    sampling: Sampling
    v_min: float | None = None      # CURRENT/REST: end step when V <= v_min
    v_max: float | None = None      # CURRENT/REST: end step when V >= v_max
    i_abs_min: float | None = None  # VOLTAGE only: end step when |I| <= i_abs_min
    on_cutoff: CutoffAction = CutoffAction.NEXT
    label: str = ""
    # VOLTAGE steps may not set v_min/v_max; CURRENT/REST may not set i_abs_min.

@dataclass(frozen=True, slots=True)
class RepeatBlock:
    first: int   # index of the first repeated step (inclusive)
    last: int    # index of the last repeated step (inclusive), >= first
    count: int   # total number of executions of the block, >= 1

@dataclass(frozen=True, slots=True)
class Sequence:
    steps: tuple[Step, ...]          # 1..30 steps
    repeat: RepeatBlock | None = None
    name: str = ""

@dataclass(frozen=True, slots=True)
class SafetyLimits:
    v_min_abs: float          # absolute window, always enforced by the instrument
    v_max_abs: float          #   as a STOP cutoff (run ends, output OFF)
    i_max_abs: float          # max |current| sourced or allowed (compliance in V mode)
    max_duration_s: float     # upper bound on nominal sequence duration
    max_records: int          # upper bound on expected record count
    min_interval_s: float = 0.005   # smallest allowed sampling interval
```

Functions in `models.py`:

- `validate(sequence, limits) -> None`: CURRENT `|level| <= i_max_abs`; VOLTAGE
  level within `[v_min_abs, v_max_abs]`; step cutoffs inside the absolute window;
  `v_min < v_max` when both set; repeat indices in range; every interval
  `>= min_interval_s`; `nominal_duration_s <= max_duration_s`;
  `expected_records <= max_records`. Error messages name the step index and field.
- `expanded(sequence) -> list[tuple[int, int, Step]]`: `(step_index,
  repeat_index, step)` in execution order (repeat_index 0 outside the block).
- `step_records(step) -> int`: samples if the step runs its full duration:
  `ceil(min(fast_window, duration)/fast_interval) + ceil(max(0, duration -
  fast_window)/interval)`; document the formula in the docstring.
- `nominal_duration_s(sequence) -> float` and `expected_records(sequence) -> int`:
  sums over `expanded()`; upper bounds because cutoffs can end steps early.
- `to_dict(obj)` / `sequence_from_dict(d)`, `limits_from_dict(d)`: JSON-safe
  round trip (enums as strings) for metadata and HDF5 storage.

## Builders (`builders.py`)

- `renard_series(t_min, t_max, series=5) -> list[float]`: preferred-number
  values (R5: 1.0, 1.6, 2.5, 4.0, 6.3; R10: 1.0, 1.25, 1.6, 2.0, 2.5, 3.15, 4.0,
  5.0, 6.3, 8.0) times powers of ten, inclusive of both ends when they are in
  the series. `renard_series(0.1, 100)` gives 16 values from 0.1 to 100.
- `log_repeats(t, t_lo=0.1, n_lo=100, t_hi=100, n_hi=6) -> int`: log-log
  interpolation, rounded, clamped to `[n_hi, n_lo]`. Must give 100 at 0.1 s,
  39 at 1 s and 6 at 100 s.
- `two_phase_sampling(duration_s, fast_interval_s=0.005, fast_window_s=0.5,
  interval_s=0.1) -> Sampling`: if `duration_s <= fast_window_s`, the whole step
  is fast.
- `cc_step(current_a, target_v, max_duration_s, sampling, on_cutoff=NEXT) -> Step`:
  CURRENT; sets `v_max=target_v` if current > 0, else `v_min=target_v`.
- `rest_step(duration_s, sampling) -> Step`.
- `cv_step(voltage_v, i_abs_min, max_duration_s, sampling) -> Step`.
- `bipolar_pulse_sequence(current_a, pulse_s, repeats, *, rest_s=None,
  pre_rest_s=10.0, v_min=None, v_max=None, fast_interval_s=0.005,
  fast_window_s=0.5, interval_s=0.1, name="") -> Sequence`:
  steps `[pre-rest, +I pulse, rest, −I pulse, rest]` with `RepeatBlock(1, 4,
  repeats)`; `rest_s` defaults to `max(pulse_s, 1.0)`; `current_a > 0`; optional
  `v_min`/`v_max` are per-step cutoffs with `on_cutoff=STOP` on the pulse steps;
  sampling from `two_phase_sampling` per step.
- `cycling_sequence(current_a, v_low, v_high, cycles, *, rest_s=600.0,
  cv_i_abs_min=None, cv_max_s=3600.0, max_step_s=86400.0, start="charge",
  interval_s=1.0, name="") -> Sequence`: one cycle = CC to `v_high`,
  [CV at `v_high` until `cv_i_abs_min`], rest, CC to `v_low`, rest (order
  swapped for `start="discharge"`), repeated `cycles` times via `RepeatBlock`.
- `soc_position_sequence(current_a, target_v, *, max_s, rest_s, interval_s=1.0)
  -> Sequence`: CC (sign chosen from the current voltage direction is not
  knowable offline, so the caller passes a signed current) to `target_v`, then
  rest.

## Tests (`tests/unit/test_sequences.py`)

Cover: every validation error path; `expanded` order with a repeat block;
record/duration formulas (including a 0.1 s pulse that is all-fast); the R5
series list from 0.1 to 100 s; `log_repeats` anchor values; a bipolar sequence
for 0.1 s × 100 repeats has nominal duration 220 s + pre-rest; dict round trip
equality for all builders; frozen/immutable behaviour.

## Out of scope

Hardware, simulation, Excel, HDF5, ophyd/Bluesky integration, docs.

## Commit message

`feat(sequences): step-sequence models, validation and protocol builders`
