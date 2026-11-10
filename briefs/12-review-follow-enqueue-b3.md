# Brief 12: review the day's code, 10 Nov (review only)

**Do not change code or commit.** Report findings; Claude applies fixes. This
is the daily review of in-depth code changes (`AGENTS.md`).

Read: `AGENTS.md`, this brief, and `git show 0cc63c3 7ca66c0 92e4881`.

1. **`0cc63c3` enqueue compiles rows** (`sequences/queueserver.py`,
   `compile_for_2460`, `queue_items`, `validate_rows`). Your brief-11 finding 5.
   Does `queue_items` now refuse every row that `prepare` would refuse for a
   compile reason (blocks, ranges, sampling)? Is anything compiled with
   different arguments from the backend's `prepare` (profile, external start,
   limits)? Is compiling for simulator sessions a problem anywhere (tests,
   notebook, contract tests)?
2. **`7ca66c0` Follow status line** (`sequences/viewer.py`, `follow`). Context:
   on 5 Nov a plot first drawn empty never redrew in VS Code during an
   external-START wait; the cause was not reproduced. Check the state machine:
   status shown only while `incomplete` with zero readings; the status cleared
   and the plot created once readings exist; a run that ends with zero readings
   (aborted while waiting, START timeout) shows a final plot and returns its
   outcome; timeout and KeyboardInterrupt still return the last outcome.
   Is there a better-founded explanation of the original symptom in the code
   (`_run_figure` with empty data, `uirevision`, display updates)?
3. **`92e4881` B3 sheets and plan** (`bench/b3_sheets.py`, `bench/b3/*.toml`,
   `bench/plan-b3.md`). This prepares a tier-2 run on a real cell overnight.
   Check: P0 current sign and size (−C/10), 5 mV target, 14 h bound and the
   session's 100 µA covering it; the CV variant's hold current capped by
   `i_max_abs`; pulse rows' signs, relaxed ends and repeat bounds; every row
   fits 63 blocks and 1,000,000 records across the CLI range (5–40C,
   0.05–1 mAh, `--external`). In the plan: is killing the RE Manager
   (`pkill -KILL -f bin/start-re-manager`) while `hc-p0` runs safe with respect
   to the backend (no abort sent, no half-written command), and does the
   morning path (`oe-diagnose`, `oe-archive-retained`, then a new RE Manager
   with another session) leave nothing that could restart or abort a run?

Report: at most 15 lines, OK or defect (file, line, failing input, fix) per
check, plus checks run with results.
