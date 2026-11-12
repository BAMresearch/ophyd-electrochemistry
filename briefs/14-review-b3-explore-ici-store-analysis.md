# Brief 14: review the B3 series runner, ICI and storage sheets, and the analysis, 12 Nov (review only)

**Do not change code or commit.** Report findings; Claude applies fixes. This
is the daily review of in-depth code changes (`AGENTS.md`).

Read: `AGENTS.md`, this brief, and the **uncommitted** working-tree changes on
`mvp-sprint`: `git diff` (`bench/explore.py`, `bench/b3_sheets.py`,
`bench/b2_monitor.py`, `tests/unit/test_bench_b2.py`, `tests/unit/test_bench_b3.py`)
plus the new files `bench/b3_analysis.py`, `tests/unit/test_bench_b3_analysis.py`,
`bench/b3/session-b3-explore.toml`, `bench/plan-b3-explore.md`. Ignore `runs.xlsx`,
`dump.rdb`, `manuals/`. All of this ran on a real cell on 12 Nov (results in
`docs/validation.md`, B3). `store-d` from `b3_sheets.py` is running overnight now.

1. **`bench/explore.py --series b2|b3`.** `Series` dataclass; `CFG` is a module
   global replaced in `main()`. Check that no path still uses the B2 values
   when `--series b3` is given (defaults evaluated at import, helpers called by
   `authorize`/`prepare`/`run`, the tests' fixtures). The current cap depends on
   pulse length (`i_max_long_a` above `long_pulse_s`): is the boundary right for
   `pulse_s == long_pulse_s`, for `bipolar_pulse` and `soc_position` rows, and
   for a `pre_rest_s`? Net-charge range `net_uah`; `reference_ohm(directory,
   direction, v_pre)` now only compares references whose first pre-pulse voltage
   is within `REFERENCE_DV` (0.05 V): can it return no reference and silently
   disable the factor-2 stop rule? Do the B3 values match `plan-b3-explore.md`
   and `session-b3-explore.toml` (±2.9 V rule inside a ±3 V window, 10.4 mA,
   7200 s)?
2. **`bench/b3_sheets.py`.** `ici_rows` (`pulse_train`, 120 s at ±C/2, 10 s
   rests, 80 repeats as a bound, ends on `v_high`/`v_low` under load) and
   `store_rows` (`soc_position`, +C/20 to `target_v` 2.5 V, `max_step_s` 30 h,
   30 min rest, 10 s interval). Confirm from `builders.py`/`run_sheet.py` that a
   positive current with `target_v` above the start voltage cuts off on v_max,
   and what happens if the cell starts above 2.5 V. 30.5 h bound against the
   session's `max_duration_s` 144000. The current check loop in `main()`
   against each sheet's session.
3. **`bench/b2_monitor.py --bench`.** File name and `test_matrix` now follow
   `--bench`; anything else B2-specific in `evidence()` or `run_checks()`?
4. **`bench/b3_analysis.py`** (moved from ad-hoc scripts; produced the numbers in
   `docs/validation.md` and `docs/beamtime-concept.md`). Check the ICI
   definitions against Lacey 2017 / Chien et al. 2023: R from the last reading
   under load to the first pause reading; k from V against √(t − t_last_loaded)
   over 0.1–1 s, divided by −I; the time origin is the last loaded reading, not
   the true switch (up to one sample earlier: how large is the bias in k at
   6 ms?). `load()` of an npz (`seconds`, `fraction`, `reading`, `source`) —
   correct for current-mode records only; is that guarded enough? Charge by the
   rectangle rule over uneven spacing (6 ms / 1 s); the hysteresis interpolation
   on reversed sodiation arrays (monotonic?); `eta_at` when a pulse starts at
   index 0; the Butler–Volmer fit bounds.
5. **Incident, no code (network cable out, 13:31 UTC).** During `ici-full-s` a
   status query timed out, the transport invalidated the session, unstage raised
   `ShutdownUnconfirmed` and the plan failed without sending an abort; the 2460
   finished alone. The RE Manager was then killed with the link down, so the
   2460 kept the dead session and timed out `*IDN?` from new ones until the user
   connected to the dead-socket port 5030 (Reference Manual p. 2-22) after the
   run had ended. Confirm from `transport.py`/`sequence_backend.py` that nothing
   could be sent after the timeout. Should `oe-diagnose` or the docs offer the
   5030 step (read-only otherwise)? Is it known to be safe while a trigger model
   runs (the manual does not say; mark unverified)?

Report: at most 15 lines, OK or defect (file, line, failing input, fix) per
check, plus checks run with results.
