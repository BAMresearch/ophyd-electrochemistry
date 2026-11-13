# Brief 15: review `oe-recover-run`, the library form of your store-d recovery (review only)

**Do not change code or commit.** Report findings; Claude applies fixes. This
is the daily review of in-depth code changes (`AGENTS.md`).

Read: `AGENTS.md`, this brief, the **uncommitted** working-tree changes on
`mvp-sprint` (`git diff`, plus the new `src/ophyd_electrochemistry/sequences/recovery.py`,
`tests/unit/test_recovery.py`, `briefs/15-review-recover-run.md`), and your own
`/private/tmp/oe-store-d-recovery/recover_store_d.py`, which this generalises.
Ignore `runs.xlsx`, `dump.rdb`, `manuals/`.

What changed against your script:
- `SequencerProfile.from_settings` (`sequencer.py`) is the inverse of
  `SequencerProgram.settings()`; the range tables are not recorded, so the
  recompiled program's whole `settings()` must equal the file's equipment.
- `Replayer.predicted` exposes `running`/`completed`/`stop`; the prediction is
  noted, the outcome is kept. Outcomes `incomplete`, `error`, `aborted` are
  accepted (`hc-p0` and `ici-full-s` ended `error`); the measurement-ID check is gone.
- The file's events must be the replay's first events (time within 1 µs);
  times within 1 µs; other fields exact (NaN = NaN). Written without SWMR mode
  on a `repair_run_file` copy; the destination is removed on any failure.
- `oe-recover-run RUN ARCHIVE [--out]`, default `<id>-recovered_<stamp>.h5`.
- Real data (scratchpad outputs, not committed): `store-d` gives the same
  readout and events as your file; `ici-full-s` (+16,107 records, predicts
  stopped_cutoff) and `hc-p0` (+30,864, CV steps, predicts completed) recover.

Check:
1. **Wrong pairings get through?** An archive of a later or earlier run of the
   same sequence on the same instrument; one whose first records happen to
   match (a rest prefix of identical OCV readings?): is the prefix check
   strong enough when `prefix` is small (one record)? Should a minimum overlap
   or the `OE_T0`-based time origin (`time_origin_epoch_s`) be checked too?
2. **`first_record == 1` assumption.** `archive_retained` starts at
   `status.acked`; confirm from `sequence_backend.py` that a run the host lost
   is never partly acknowledged, including after `recover_sequencer`.
3. **Replay fidelity.** Can the live backend have written events the replay
   does not produce (instrument event-log errors, `abort`, host-side events)
   that make a valid pair refuse? Is that the right failure direction?
4. **`from_settings`.** Types after an HDF5 round trip (numpy bools/ints via
   `_plain`), NPLC key parsing (`reading_s_nplc_0p05`, `_1`), keys absent in
   older files (fall back to today's defaults: acceptable given the settings
   comparison?).
5. **File writing.** `h5py.File(target, "r+", libver=v110)` on a repaired copy,
   appending through `RunFile` without SWMR, the `run/recovery` dataset; the
   viewer index with two files of the same `measurement_id`.
6. **Docs.** `docs/user/beamtime.md` §6, `docs/user/reference.md`,
   `docs/technical/index.md` (Recovery row): accurate and sufficient?

Report: at most 15 lines, OK or defect (file, line, failing input, fix) per
check, plus checks run with results.
