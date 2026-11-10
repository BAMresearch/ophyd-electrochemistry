# Run sheets

A run sheet is an Excel workbook with one row per run. `oe-template runs.xlsx`
writes one example of every protocol and an R5 series of pulse rows; the README
sheet and the header tooltips describe every column. Only the settings of a run
belong in the sheet. Limits, wiring and the instrument belong in the
[session file](sessions.md).

## The essentials

- `measurement_id`: a unique name; it also names the run file. Put `#` in front
  to skip a row.
- `protocol`: one of

  | Protocol | What it does |
  |---|---|
  | `rest` | Zero current for `rest_s`; measures the open-circuit voltage |
  | `soc_position` | Constant current until `target_v` (or `max_step_s`), optionally a voltage hold, then a rest |
  | `pulse_train` | A rest, then `repeats` × (pulse, rest), all pulses in one direction |
  | `bipolar_pulse` | A rest, then `repeats` × (pulse, rest, opposite pulse, rest) |
  | `cycling` | `cycles` × (charge to `v_high`, rest, discharge to `v_low`, rest), optional CV hold |

- **Sign:** a positive current raises the cell voltage. For hard carbon against
  Na, **sodiation is a negative current**. `pulse_train` and `soc_position`
  take a signed `current_a`. In `start_with` and `cv_after`, write `sodiation`
  or `desodiation`; `discharge` and `charge` mean the same.
- `start_mode`: `immediate`, or `external` to wait for a START pulse on the
  2460's digital input (see [At the beamtime](beamtime.md#external-start)).
- Sampling: each step samples every `fast_interval_s` (default 6 ms for pulses)
  for its first `fast_window_s`, then every `interval_s`.

## Examples

For a 0.6 mAh cell (1C = 600 µA). The values are illustrative, not final:

| measurement_id | protocol | settings |
|---|---|---|
| hc01-sodiate | soc_position | current_a −6e-5, target_v 0.005, max_step_s 50400, rest_s 1800, interval_s 1 |
| hc01-desod-pulse | pulse_train | current_a 6e-3 (10C), pulse_s 1, rest_s 60, repeats 540, fast_window_s 1, interval_s 0.25, v_high 0.2, start_mode external |
| hc01-sod-pulse | pulse_train | current_a −6e-3, pulse_s 1, rest_s 300, repeats 540, fast_window_s 1, interval_s 0.25, rest_end_v 0.005 |
| ref01-cycle | cycling | current_a 3e-5, v_low 0.005, v_high 0.2, cycles 1, start_with desodiation, cv_i_abs_min_a 6e-6, cv_after sodiation, rest_s 1800, max_step_s 72000, interval_s 1 |

## Ending a pulse run

A pulse train stops when a pulse reaches `v_high` or `v_low`, so `repeats` is
only an upper bound. The 2460 checks that threshold on the voltage **under
load**, which at high rates includes a large IR drop.

`rest_end_v` ends the run on the **relaxed** voltage instead. The last second
of each rest becomes a separate check, and the run stops when the voltage there
is below `rest_end_v` (sodiation) or above it (desodiation); a reading exactly
equal to it does not stop the run. Under load, only `v_low` / `v_high` (if set)
and the session window apply, so the session window must allow the excursion
(down to −3 V for sodiation). The agreed sodiation end is 5 mV after a 5 min
rest (`hc01-sod-pulse`). For long rests only near the end, use two rows: one
with 1 min rests and an earlier `rest_end_v`, then one with 5 min rests and
`rest_end_v` 0.005. The queue goes on to the next row after such a stop.

## How much fits in one run

The 2460 builds each run as a *trigger model* of at most 63 blocks. Repeats do
not cost blocks (the instrument counts them itself), but every step and every
change of sampling rate does. `oe-validate` reports the block count and refuses
a row that does not fit. In practice:

- `pulse_train`, `soc_position`, `rest` and single cycles always fit.
- A `bipolar_pulse` row fits only when `fast_window_s` is at least `pulse_s`
  and `rest_s`, so every step samples at one rate. Otherwise use one
  `pulse_train` row per direction.

A run holds at most 1,000,000 readings. A fitting `bipolar_pulse` row uses
2 × (⌈`pulse_s` / `fast_interval_s`⌉ + ⌈`rest_s` / `fast_interval_s`⌉)
readings per repeat (368 for 0.1 s / 1 s at 6 ms). The largest `repeats`
accepted, with a 1 s `pre_rest_s`:

| pulse_s / rest_s | fast_interval_s 0.006 | fast_interval_s 0.05 |
|---|---|---|
| 0.1 s / 1 s | 2,716 | 22,726 |
| 1 s / 1 s | 1,496 | 12,499 |
| 1 s / 10 s | 272 | 2,272 |
| 1 s / 60 s | 49 | 409 |
| 10 s / 60 s | 42 | 357 |
| 60 s / 60 s | 24 | 208 |
| 100 s / 100 s | 14 | 124 |

At 6 ms the buffer is full after about 1.7 h of running, at 50 ms after about
14 h. `fast_interval_s` must be below `interval_s`; for 0.1 s or coarser, also
raise `interval_s` (for example to 0.25). A `pulse_train` row samples fast
only for the first `fast_window_s` of each pulse and rest, so it reaches far
more repeats: `hc01-desod-pulse` above uses about 570 readings per repeat.
Every run must also fit the session's `max_duration_s` and `max_records`.

## Validate before the beamtime

```sh
oe-validate runs.xlsx --session my-session.toml          # every row
oe-validate runs.xlsx hc01-sod-pulse --session my-session.toml
```

One line per row: protocol, start mode, duration, maximum readings, trigger
blocks and integration time (NPLC). Fix every `FAIL` line. Validation needs no
instrument.

**Do not edit the sheet after its runs are queued:** a queued run refuses to
start if the file has changed since it was enqueued (it compares a checksum).
Edit, then enqueue again.
