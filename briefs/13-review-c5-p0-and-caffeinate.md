# Brief 13: review the B3 C/5 P0 and the stay-awake fix, 10–11 Nov (review only)

**Do not change code or commit.** Report findings; Claude applies fixes. This
is the daily review of in-depth code changes (`AGENTS.md`).

Read: `AGENTS.md`, this brief, and `git show a17b4d8 ad2a4ca`. `hc-p0` from
a17b4d8 has been running alone on a real cell since 10 Nov 14:59:20 UTC, so
findings on it matter for the morning steps and the next cells.

1. **`a17b4d8` C/5 P0 as one `cycling` row** (`bench/b3_sheets.py`,
   `bench/b3/session-b3-p0.toml`, `bench/plan-b3.md`). Two cycles starting with
   desodiation, ±142 µA between 5 mV and 2.5 V, CV at 5 mV to 14 µA (≤ 1 h),
   30 min rests, CC steps ≤ 8 h. Check: the first +I half ends at its first
   reading when the cell is at or above 2.5 V (`cc_step` v_max, the runtime's
   `LIMIT_OUTSIDE` branch); on the bench the cell read 2.25 V OCV and the step
   ended at the first reading. If a CC sodiation ends on its 8 h time limit
   above 5 mV, what does the CV step at 5 mV do (current limited by the
   session's 200 µA)? Is that acceptable, or should the CC timeout stop the
   run (`on_cutoff` / a different action)? The 36 h row bound against the
   session's 40 h and 1,000,000 records; current and measure ranges for 142 µA
   and the CV hold; anything in the compile that differs for `start_with`
   desodiation.
2. **`ad2a4ca` caffeinate** (`qserver/start-k2460-re-manager.sh`). Foreground:
   `caffeinate -w $$` before `exec`; detached: `-w` on the setsid child (the
   group leader). Does caffeinate always exit with the manager (kill script,
   `qserver manager stop`, a crash)? Can it keep a stale assertion, or match
   the `pgrep -f bin/start-re-manager` checks in either script?
3. **The 10 Nov sleep incident** (no commit; log in the description of
   ad2a4ca and `docs/current-state.md`). The Mac idle-slept 63 min into
   `hc-p0`; on wake `eventlog.getcount` failed with ConnectionResetError, the
   transport invalidated the session, unstage raised `ShutdownUnconfirmed`
   and the plan failed. Confirm from `transport.py`, `sequence_backend.py` and
   `flyer.py` that after the reset nothing was sent (no abort, no output OFF,
   no reconnect) and that the run file records a failure, not success. With
   a live connection instead (sleep shorter than the 2460's TCP timeout), what
   would the error path have sent? The queue put `hc-p0` back at its front:
   is there a cheap guard so a failed or interrupted sequence item cannot be
   started again by a later `qserver queue start`?

Report: at most 15 lines, OK or defect (file, line, failing input, fix) per
check, plus checks run with results.
