# Brief 09: review commits 1979525, 026c55a and c8f160f (review only)

These commits are already on `mvp-sprint` (written by Claude, 7 to 9 Nov).
Check that they are correct. **Do not change code or commit.** Report your
findings; Claude applies the fixes. Running the tests, ruff and mypy is
welcome.

Read: `AGENTS.md`, this brief, `git show <commit>` for each commit, and the
source files named below. The README rules on files and reading apply, except
that this brief does not ask for an implementation or a commit.

## Background (facts from the bench, not to be re-derived)

- Firmware 1.7.16a on the 2460 accepts trigger blocks 1 to 63 only. Block 64
  and above fail at upload with event 1130 (6 Nov). The packaged runtime's
  own arrays allow 255.
- A run holds at most 1,000,000 records (`compile_program`, runtime buffer).
- Decided 6 Nov: a sodiation pulse run ends when the voltage after a 5 min
  rest is at or below 5 mV. Under load the voltage may go to −3 V (session
  window). Decided 9 Nov: a `bipolar_pulse` row that samples every step at
  one rate is acceptable for this beamtime.

## Commit 1979525: refuse programs above 63 trigger blocks

- `SequencerProfile.max_blocks` default 255 → 63
  (`src/ophyd_electrochemistry/keithley/k2460/sequencer.py:51`). The profile
  check still allows up to 255; the error is raised at line ~469.
- `make_template` (`sequences/run_sheet.py`): the R5 series (0.1–100 s) is now a
  desodiation then a sodiation `pulse_train` row per pulse time, repeats from
  `log_repeats`. `example-pulse` (bipolar) gets `fast_window_s=1` so it
  compiles to 59 blocks. The template has 37 rows.
- `bench/b1_sequencer.py` `rc_cycle` (B1 test C1): single-phase 50 ms sampling,
  81 → 62 blocks. C1's checks (cutoffs v_max, i_abs_min, v_min; outcome
  `stopped_cutoff`) are unchanged.
- Tests: the 83-block golden layout test now compiles under
  `SequencerProfile(max_blocks=255)` and asserts the default profile refuses
  it. The short-pulse NPLC test and the `pulse` replay case use `pulse_train`
  instead of `bipolar_pulse`. The backend test helper `pulse_request` uses
  `bipolar_pulse_sequence(0.001, 0.1, 2, fast_window_s=1.0)` (59 blocks).

Check:
1. Is 63 enforced everywhere a program reaches the instrument (validate CLI,
   QueueServer plan, backend `prepare`, `bench/` runners)? Is there any path
   that compiles with a custom profile allowing more?
2. Do the changed tests still test what they did before (layout logic, NPLC
   choice, replay equivalence), or was coverage lost?
3. Does the C1 RC test still exercise what `bench/plan-b1.md` row C1 describes?
4. `bench/explore.py:39` keeps its own `MAX_BLOCKS = 63` with the comment "the
   compiler still allows more", which is now stale. Remove the guard or keep
   it?

## Commit 026c55a: documentation only

`docs/operator-guide.md` section 2, "How many repeats fit in one run": a table
of the largest `repeats` the compiler accepts for `bipolar_pulse` rows that
fit in 63 blocks (fast window covering pulse and rest, 1 s pre-rest), at
`fast_interval_s` 0.006 and 0.05.

Check:
5. Recompute a few table entries with `compile_program` (for example 1 s / 60 s
   → 49 at 6 ms, 409 at 50 ms; 0.1 s / 1 s → 2,716 at 6 ms). Do the stated
   formula (2 × (pulse + rest) / interval records per block) and the "about
   570 records per repeat" for the `hc01-desod-pulse` example hold?

## Commit c8f160f: `rest_end_v`, the relaxed-voltage end for `pulse_train`

- `pulse_train_sequence(..., rest_end_v=None)` (`sequences/builders.py`
  ~line 180–250, `REST_CHECK_S = 1.0`). With `rest_end_v` the steps are: pre-rest,
  pulse, rest of `rest_s − 1` s, then a 1 s REST "relaxed check" step with
  `v_min=rest_end_v` (current < 0, sodiation) or `v_max=rest_end_v`
  (current > 0) and `on_cutoff=STOP`. Repeat block (1, 3). The check samples at
  `interval_s`. It requires `rest_s > 2` and `interval_s <= 1`.
- The pulse has no step limits unless `v_min`/`v_max` (`v_low`/`v_high` in the
  sheet) are given, so under load only the session window applies.
- Run sheet: column `rest_end_v`, refused on other protocols, signed values
  allowed (`run_sheet.py` ~lines 72, 159, 188, 232).
- Tests: `test_sequences.py::test_pulse_train_ends_on_the_relaxed_voltage`,
  `test_sequence_simulator.py::test_relaxed_end_ignores_the_voltage_under_load`,
  `test_k2460_sequencer.py` replay case `relaxed`,
  `test_run_sheet.py::test_pulse_train_row_with_a_relaxed_end`.
- Not yet run on the instrument.

Check:
6. Compiler and runtime: does a one-sided limit on a REST step lower to the
   right limit blocks (what fills the missing side)? Does STOP from a step
   inside the repeat block end the run with output OFF and outcome
   `stopped_cutoff`, as the replay and simulator say?
7. Equality: the simulator treats a reading equal to a step limit as a cutoff
   (`test_absolute_window_is_inclusive_but_step_cutoffs_include_equality`). Do
   the compiled limit blocks on the 2460 do the same? Cite the reference
   manual (Rev. C) section if you can, or mark it unverified.
8. Edge cases: float `rest_s − 1` in sample schedules and record budgets; a
   check step whose `interval_s` does not divide 1 s; `fast_window_s` longer
   than `rest_s − 1`; a pulse that reaches the session window (absolute stop)
   versus the check's cutoff; `repeats` exhausted before the threshold
   (outcome `completed`).
9. Does `stopped_cutoff` let the QueueServer queue continue to the next row
   (needed for the two-row design: 1 min rests to an earlier threshold, then
   5 min rests to 5 mV)? Claude checked `sequences/flyer.py` `_finish` by
   reading, not with a queue test. Is a contract test worth adding?
10. Block budget: 57 blocks for 1 s pulses with 60 or 300 s rests, 63 for 10 s
   pulses (fast window 1 s, interval 0.25 s). Which documented settings would
   exceed 63, and does `oe-validate` report them clearly?

## Report

At most 30 lines. For each numbered check: OK, or the defect with file, line,
a concrete failing input and the suggested fix. List the checks you ran
(tests, ruff, mypy, scripts) with results. Separate defects from opinions.
