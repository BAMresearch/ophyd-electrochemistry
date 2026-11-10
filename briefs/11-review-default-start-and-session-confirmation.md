# Brief 11: review the 1 µA start default and per-session confirmation (review only)

**Do not change code or commit.** Report findings; Claude applies fixes.

Read: `AGENTS.md` (tier 2), this brief, `git show 0b35831`, and
`keithley/k2460/sequencer.py` (`SequencerProfile`, `compile_program` around
`settle_first`), `sequences/queueserver.py`, `notebooks/k2460_run.ipynb`.
Background: `docs/technical/sequencer.md` §5 ("First step on the lowest range").

Change 1: `SequencerProfile.start_on_lowest_range` now defaults to True. A
leading 0 A rest outside the repeat block gets its own source entry on the
lowest current range; the next step recalls the shared range with the output
ON. Bench (9 Nov, `docs/evidence/k2460-2026-11-09-b1-*-dip.json`): output stays
ON through the range change, dip gone.

Change 2: per-run typed confirmation removed. `oe-enqueue` validates and queues;
`run_from_sheet(run_sheet, measurement_id, sheet_sha256)`; the notebook has no
confirm cell. Loading the session is the confirmation.

Check:
1. With the default on, can any sequence the builders or run sheet produce
   compile differently in a way that matters: a leading rest with a voltage
   cutoff, a leading rest inside or at the start of the repeat block, a
   voltage-mode first step, a sequence of one rest step, CC-CV cycling? Is the
   lowest range ever below what the rest's measurement or a cutoff needs?
2. Does the extra source entry change any limit: entry count, call length
   (900 characters), block count, `expected_records`, replay readback checks
   (status bit 32, tolerance against the 1 µA range at 0 A)?
3. Do the backend's `equipment()` / run-file metadata still describe the ranges
   correctly when step 0 uses a different range from the rest?
4. Change 2: is any confirmation path left behind (plan signatures, permissions
   YAML, `existing_plans_and_devices`, docs, bench scripts that queue items with
   `confirm_measurement_id`)? Does any test still pass only by accident?
5. Docs: do `docs/user/*.md` and `docs/technical/index.md` (commit after
   0b35831) state anything the code does not do? Name file and line.

Report: at most 15 lines, OK or defect (file, line, failing input, fix) per
check, plus checks run with results.
