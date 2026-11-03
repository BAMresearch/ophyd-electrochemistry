# Brief 07 — sequencer compiler and replay (pure Python)

Requires briefs 01–05. Design: `docs/sequencer-design.md` (read §3–§9 and §12).
The instrument runtime already exists and is not part of this brief:
`keithley/k2460/tsp/sequencer.tsp` and `keithley/k2460/sequencer_runtime.py`
(Claude). Do not modify them, the transport or their tests. Your block codes
and `oe_seq_*` call signatures must match `sequencer.tsp` exactly; read it to
check. Claude writes the hardware backend on top of your module afterwards.

## Goal

Turn a validated `Sequence` + `SafetyLimits` into a `SequencerProgram`: the
configuration entries, the trigger-model block table and the sample plan, plus
the exact runtime calls that upload it. And the reverse: `Replayer` assigns
step/repeat indices, events and the outcome to the readings coming back, with
the same cutoff rules the instrument applies. Everything here is pure and
deterministic; no I/O.

## Files

- `src/ophyd_electrochemistry/keithley/k2460/sequencer.py`
- `tests/unit/test_k2460_sequencer.py`

Read: `docs/sequencer-design.md`, `keithley/k2460/tsp/sequencer.tsp`, `sequences/models.py`, `sequences/simulator.py`,
`sequences/backend.py` (`READOUT_FIELDS`), `keithley/k2460/config.py` (line numbers).

## Profile

```python
@dataclass(frozen=True)
class SequencerProfile:
    marker_line: int = 1          # Keithley2460Config.ready
    busy_line: int = 2
    start_line: int = 3
    vlimit_margin_v: float = 0.1
    # Auto-zero OFF, 2 V range, 4-wire, 50 Hz line (bench E3/T4, 3 Nov 2026):
    reading_s: Mapping[float, float] = {1: 0.04084, 0.5: 0.02083, 0.2: 0.00884,
                                        0.1: 0.004827, 0.05: 0.002825, 0.01: 0.001238}
    block_overhead_s: float = 0.00014   # marker + limits + counter per sample (T4)
    step_entry_s: float = 0.00132       # reset + recall + marker per step change (S1)
    margin_s: float = 0.0003
    max_blocks: int = 255
    max_call_chars: int = 900
    voltage_ranges_v: tuple[float, ...] = (0.2, 2, 7, 10, 20, 100)
    current_ranges_a: tuple[float, ...] = (1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 0.1, 1, 4, 5, 7)
```

## Instrument settings (design §3)

- Current/rest steps: one current-source range = smallest ≥ max |level| of
  current steps (≥ the 1 µA range); `vlimit = max(|v_min_abs|, |v_max_abs|) +
  vlimit_margin_v`; voltage measure range = smallest ≥ `vlimit`.
- Voltage steps: voltage-source range = smallest ≥ max |level| of voltage steps;
  `ilimit = i_max_abs`; current measure range = smallest ≥ `i_max_abs`.
- No range large enough → `ValidationError`.

## Program

```python
@dataclass(frozen=True)
class SourceEntry:  mode: Literal["current", "voltage"]; level: float; range: float; limit: float
@dataclass(frozen=True)
class MeasureEntry: function: Literal["voltage", "current"]; range: float; nplc: float
@dataclass(frozen=True)
class Block:        kind: BlockKind; args: tuple[float, ...]   # numbers only
@dataclass(frozen=True)
class PhasePlan:    count: int; interval_s: float; nplc: float; delay_s: float
                    measure_entry: int; measure_block: int
@dataclass(frozen=True)
class StepPlan:     step_index: int; mode: StepMode; level: float; source_entry: int
                    phases: tuple[PhasePlan, ...]
                    window: tuple[float, float] | None   # LIMIT_OUTSIDE (current/rest)
                    taper: float | None                  # LIMIT_INSIDE |I| (voltage)
                    on_cutoff: CutoffAction; abs_window_first: bool
                    start_block: int
@dataclass(frozen=True)
class SequencerProgram:
    sequence: Sequence; limits: SafetyLimits; profile: SequencerProfile
    external_start: bool
    sources: tuple[SourceEntry, ...]; measures: tuple[MeasureEntry, ...]
    blocks: tuple[Block, ...]; steps: tuple[StepPlan, ...]
    repeat: RepeatBlock | None; capacity: int        # expected_records + 16
    def runtime_calls(self) -> list[str]: ...
    def settings(self) -> dict[str, str | int | float | bool]: ...  # flat, RunFile equipment rules

def compile_program(sequence, limits, profile=SequencerProfile(), *, external_start=False) -> SequencerProgram
```

Entries are 1-based and de-duplicated: sources by `(mode, level)` (REST =
current 0 A), measures by `(function, nplc)`.

### Block kinds (codes are part of the runtime ABI — do not renumber)

| Code | `BlockKind` | args | Runtime meaning |
|---|---|---|---|
| 1 | `RECALL` | src, meas | `BLOCK_CONFIG_RECALL "OE_SRC", src, "OE_MEAS", meas` |
| 2 | `OUTPUT` | on (1/0) | `BLOCK_SOURCE_OUTPUT` |
| 3 | `DIGIO` | pattern, mask | `BLOCK_DIGITAL_IO` (bit n−1 = line n) |
| 4 | `DELAY` | seconds | `BLOCK_DELAY_CONSTANT` |
| 5 | `MEASURE` | count | `BLOCK_MEASURE_DIGITIZE` into the run buffer |
| 6 | `LIMIT_OUTSIDE` | lo, hi, target, measure_block | `BLOCK_BRANCH_LIMIT_CONSTANT, LIMIT_OUTSIDE` |
| 7 | `LIMIT_INSIDE` | lo, hi, target, measure_block | same, `LIMIT_INSIDE` |
| 8 | `COUNTER` | count, target | `BLOCK_BRANCH_COUNTER` (count = passes in total) |
| 9 | `RESET_COUNTER` | counter_block | `BLOCK_RESET_BRANCH_COUNT` |
| 10 | `WAIT_START` | – | `BLOCK_WAIT` on the START line, `CLEAR_ENTER` |
| 11 | `LOG` | code (1 T0, 2 END, 3 STOP, 4 DONE) | `BLOCK_LOG_EVENT` "OE_T0" … "OE_DONE" |
| 12 | `ALWAYS` | target | `BLOCK_BRANCH_ALWAYS` |

### Layout (design §5; block numbers are 1-based)

- Prologue: `WAIT_START` (external only), `LOG T0`, `DIGIO busy high`.
- Per step, per phase: phase 1 = `RESET_COUNTER c1`, `RECALL`, [`OUTPUT 1`],
  `DIGIO marker high`, `DELAY`, `MEASURE 1`, `DIGIO marker low`, limit block(s),
  `COUNTER n1 → DELAY`; phase 2 = `RESET_COUNTER c2`, `RECALL` (same source,
  phase-2 measure entry), `DELAY`, `MEASURE 1`, limit block(s), `COUNTER n2 → DELAY`.
- `OUTPUT 1` on the first step, and on any step whose source mode (REST counts
  as current) differs from a possible predecessor: the previous step; for the
  repeat block's first step also the block's last step.
- Limits: current/rest with `on_cutoff=STOP`: one `LIMIT_OUTSIDE [lo, hi] → stop`
  with lo = `v_min` or `v_min_abs`, hi = `v_max` or `v_max_abs`. With `NEXT` and a
  step cutoff: `LIMIT_OUTSIDE [v_min_abs, v_max_abs] → stop` first, then the step
  window `→ next`. No step cutoffs: the absolute block only. Voltage steps:
  `LIMIT_INSIDE [−i_abs_min, i_abs_min] → next/stop` if `i_abs_min` is set, else
  no limit block. "next" = the next step's `start_block`, or for the repeat
  block's last step the repeat `COUNTER`.
- Repeat: `RESET_COUNTER r` right before the block's first step; after its last
  step `COUNTER count → block's first step start`.
- End: `OUTPUT 0`, `DIGIO 0 (marker|busy)`, `LOG END`; then, only if some block
  targets the stop path: `ALWAYS → done`, stop path `OUTPUT 0`, `DIGIO 0`,
  `LOG STOP`, and `done` = `LOG DONE`. No unreachable blocks; assert it.
- Delay per phase: `d = Δ − reading_s[nplc] − block_overhead_s − step_entry_s / n`
  with NPLC = the largest table entry giving `d ≥ margin_s`. None → `ValidationError`
  naming the step and the shortest feasible interval.
- More than `max_blocks` blocks → `ValidationError` ("split the run").

### Runtime calls (one string each, every one < `max_call_chars`)

```
oe_seq_begin(n_src, n_meas, n_blocks, capacity)
oe_seq_source(i, mode, level, range, limit)      -- mode 1 current, 2 voltage
oe_seq_measure(i, func, range, nplc)             -- func 1 voltage, 2 current
oe_seq_block(i, code, a, b, c, d)                -- unused args 0
oe_seq_commit()
```
Numbers via `repr(float(x))` / `str(int)`; no other text. Order: begin,
sources, measures, blocks, commit.

## Replay

```python
class Replayer:
    def __init__(self, program: SequencerProgram) -> None: ...
    def feed(self, t_s, reading, source_value, status, source_status) -> tuple[dict[str, NDArray], list[SequenceEvent]]
    def finish(self, logged: Literal["end", "stop"] | None) -> tuple[str, str]   # (outcome, detail)
```

- `feed` takes equal-length arrays (any chunking, order preserved), walks the
  sample plan and returns `READOUT_FIELDS` arrays plus `(t_s, step, repeat,
  kind, detail)` events: `step_start` (t = previous reading's t, 0 for the
  first), `cutoff` (detail `v_min`/`v_max`/`i_abs_min`/`abs_window`), `stop`.
- Cutoff tests as the instrument: `LIMIT_OUTSIDE` fires if `x < lo or x > hi`;
  `LIMIT_INSIDE` if `lo <= x <= hi`; absolute block before the step block.
- Mapping: current/rest `voltage_v = reading, current_a = source_value`;
  voltage steps the reverse. `source_status`, `measure_status` copied.
- Readback check (not in limit, i.e. `source_status & 32 == 0`): current steps
  `|source − level| > 0.002·range + 0.002·|level|` → one `error` event per step
  execution ("readback mismatch, n samples"); same for voltage steps on volts.
- Records beyond the plan, or after a stop → `error` event and outcome `error`.
- `finish`: predicted completed + logged "end" → `completed`; predicted stop +
  logged "stop" → `stopped_cutoff`; anything else → `error` with a detail naming
  both. `finish(None)` (aborted elsewhere) → `aborted`.

## Tests

1. Golden layouts: a bipolar pulse sequence (builders, two-phase sampling, STOP
   cutoffs, repeat), a CC-CV cycling sequence (NEXT cutoffs, voltage step,
   repeat) and an external-start variant: block lists match hand-checked
   expectations (assert on kinds and every branch target), no unreachable block,
   OUTPUT 1 exactly where required.
2. The S1 bench layout: rest + (+4.9 mA, 0, −4.9 mA, 0)×3 at 6.25 ms gives the
   same structure as `bench/marker_start_s1.py` (block count 53, same targets).
3. Delays: 6 ms at 0.1 NPLC → d = 6 − 4.827 − 0.14 − 1.32/n ms; 0.1 s pulse with
   two-phase sampling picks 0.1 NPLC (fast) and 1 NPLC (slow); 2 ms interval →
   0.01 NPLC; 1 ms → `ValidationError`.
4. Ranges/limits for windows 0–2.5 V, |I| ≤ 5 mA etc.; no range → error.
5. Budget error over 255 blocks; every runtime call < 900 chars and matches
   `^oe_seq_[a-z]+\([-0-9.e, ]*\)$`.
6. **Replay equals the simulator**: for pulse, cycling (with cutoffs NEXT and
   STOP firing) and CC-CV sequences, `simulate_sequence` (noise on) → records
   (reading/source per step mode) → `Replayer` in random chunk sizes →
   `step_index`, `repeat_index`, event kinds/steps and outcome equal the
   simulation (use limits where no sample sits exactly on a boundary).
7. Replay errors: extra records, readback mismatch, logged/predicted mismatch.

## Out of scope

TSP, transport, backend, Flyer/notebook changes, hardware.

## Commit message

`feat(k2460): sequencer compiler and replay`
