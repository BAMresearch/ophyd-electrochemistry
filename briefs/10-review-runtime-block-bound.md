# Brief 10: review the runtime block bound in 264c63c (review only)

Follow-up to brief 09. **Do not change code or commit.** Report findings;
Claude applies fixes. The changed runtime is installed on the 2460 at the next
bench session, so this is the review that packaged TSP needs before it runs.

Read: `AGENTS.md`, this brief, `git show 264c63c`, and
`src/ophyd_electrochemistry/keithley/k2460/tsp/sequencer.tsp`,
`keithley/k2460/sequencer_runtime.py`, `keithley/k2460/sequence_backend.py`
(`__init__`, `prepare`, `install`/`load_runtime`).

The change: `oe_seq_begin` now checks `n_blocks` in 1..63 (was 1..255), because
firmware 1.7.16a rejects trigger blocks above 63 (event 1130). The backend
refuses a `SequencerProfile` above 63 and rechecks the block count before upload.

Check:
1. Is `oe_seq_begin` the only place in `sequencer.tsp` that bounds the block
   count? Do any later calls (block definitions, commit, `oe_seq_expect`
   checks) still assume 255, or index past 63?
2. Does the refusal leave the instrument as before: output OFF, no model
   armed, no buffer created or cleared, previous unread records untouched?
3. Is the runtime's install path (digest, ABI string, script name) affected,
   so that the backend re-installs the new runtime rather than reusing one with
   the old bound? Name anything in `docs/` or `bench/` that pins the old digest.
4. Are the new tests (`test_k2460_sequencer_runtime.py::test_begin_refuses_more_blocks_than_the_firmware_takes`,
   `test_k2460_sequence_backend.py::test_backend_refuses_programs_above_the_firmware_block_limit`)
   sufficient, or is a case missing?

Report: at most 15 lines, OK or defect (file, line, failing input, fix) per
check, plus checks run with results.
