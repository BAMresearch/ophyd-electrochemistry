# Brief 16: review the plan for a "NeX-ish" run file v2 (plan review only)

**Do not change code or commit.** Review this plan and answer the questions at
the end; Claude implements after your findings. Read `AGENTS.md`,
`src/ophyd_electrochemistry/sequences/run_file.py`, `callbacks.py` (the
`RunFileWriter`), `plans.py` (`run_sequence` metadata), `queueserver.py`
(`Session`, `load_session`, `run_from_sheet`), `recovery.py`, and
`docs/user/data.md`.

## Goal (user, 13 Nov)

Make the per-run HDF5 file self-describing for people and for later linking from a
NeXus file of the SAXS measurements:

- everything under one `/entry` group;
- equipment settings in logical subgroups instead of 53 flat attributes;
- `units` and `description` on every value where they apply, NeXus style;
- a `sample` subgroup;
- **store every piece of metadata available at run time** that might help later.

"NeX-ish": NeXus conventions where they cost little, not NeXus compliance.

## What exists (schema `ophyd-electrochemistry.run-file.v1`)

```
/                 attrs: schema, measurement_id, created_utc, package_version
/equipment        53 flat attrs (identity, clock, timing table, lines, source_1_*, ...)
/run              attrs: start_mode, sample_id, notes, started_utc, finished_utc, outcome,
                  outcome_detail, time_origin_epoch_s, time_origin_utc
                  datasets (JSON): sequence, safety_limits, run_sheet_row, extra; [recovery]
/readout          t_s, voltage_v, current_a, step_index, repeat_index, source_status,
                  measure_status (chunked, gzip, @units)
/events           t_s, step_index, repeat_index, kind, detail
```

The file is written in SWMR mode. Every object is created before `swmr_mode = True`.
During the run only these change: appends to `readout`/`events`, and `run` attributes
(time origin, outcome, finished). All readers go through `read_run_file`, which returns
a flat dict, except `recovery.py`, which opens `run`, `readout` and `events` by path.

Available at write time but **not stored today**:

- start document: `uid`, `scan_id`, `time`, `plan_name`, `expected_records`,
  `nominal_duration_s`;
- the QueueServer item: sheet path and `sheet_sha256`;
- the session:
  - its TOML path and full text, whose header comment names the cell, wiring and
    confirmation;
  - backend, resource, expected serial, terminals, poll period, START timeout;
- software versions: Python, bluesky, ophyd, h5py, HDF5, numpy, queueserver;
- host name.

## Proposed layout (schema `ophyd-electrochemistry.run-file.v2`)

```
/  @schema=...v2  @default="entry"  @creator="ophyd-electrochemistry"
   @creator_version  @HDF5_Version  @h5py_version  @file_time
/entry                       @NX_class=NXentry  @default="readout"
   title, measurement_id                           (scalar datasets, @description)
   start_mode                                      (scalar dataset)
   /run                     mutable status as ATTRIBUTES (SWMR):
                              started_utc, finished_utc, outcome, outcome_detail,
                              time_origin_epoch_s (@ -> see units note), time_origin_utc
                            @descriptions = JSON {attr name: text}, @units = JSON {attr: unit}
      sequence, safety_limits, run_sheet_row, extra   (JSON string datasets, @description)
      [recovery]                                       (JSON, written by oe-recover-run)
   /sample                  @NX_class=NXsample
      name                    = run-sheet sample_id ("" if absent)
      description             = run-sheet notes
      session_description     = the session file's header comment (cell, wiring)
      <key>                   = the session TOML's optional [sample] table (decided, Q4)
   /instrument              @NX_class=NXinstrument
      /identity   identity, serial_number, firmware, line_frequency_hz, backend
      /runtime    runtime_build, runtime_sha256, sequencer_abi
      /clock      clock_offset_s, clock_uncertainty_s, clock_method
      /connection resource, terminals, four_wire, start_edge, poll_period_s,
                  start_timeout_s, expected_serial          (from the session)
      /digital_io ready_line, busy_line, marker_line, start_line, external_start
      /timing     reading_s_nplc_<x> (one dataset each, or one table: Q3), block_overhead_s,
                  step_entry_s, margin_s, vlimit_margin_v
      /limits     v_min_abs, v_max_abs, i_max_abs, max_duration_s, max_records
      /program    capacity, block_count, max_blocks, max_call_chars, output_off_mode,
                  autozero, source_readback, start_on_lowest_range, source_count,
                  measure_count, expected_records, nominal_duration_s
      /source_<n> mode, level, range, limit       /measure_<n> function, range, nplc
      /other      any key the table does not know (never fails)
   /readout                 @NX_class=NXdata  @signal="voltage_v"  @axes="t_s"
      t_s (@units s, @start = time_origin_utc once known), voltage_v (V), current_a (A),
      step_index, repeat_index, source_status, measure_status  — each @description
   /events                  t_s (@units s), step_index, repeat_index, kind, detail — @description
   /provenance              bluesky_uid, scan_id, plan_name, run_sheet_path,
                            run_sheet_sha256, session_path, session_sha256, session_text,
                            host, software (JSON {package: version}), package_version
```

Every scalar in `instrument`, `sample` and `provenance` is a scalar dataset (UTF-8 for
text) with `@description`, and `@units` where physical (`V`, `A`, `s`, `Hz`; none
for counts, flags, NPLC, line numbers). They are written once in `RunFile.create`,
before SWMR mode. The `run` status stays as attributes because it changes during the
run under SWMR (outside SWMR's guarantees for datasets). Its descriptions and units
are group attributes holding JSON.

## Implementation steps

1. **Metadata table in `run_file.py`**:
   - `FIELDS: {key: (group, units, description)}` for every key `SequencerProgram.settings()`
     and `Keithley2460SequenceBackend.equipment()` produce today (53, from a real file);
   - patterns for `source_<n>_*`, `measure_<n>_*` and `reading_s_nplc_*`;
   - unknown keys go to `instrument/other`.
   A unit test enumerates the keys of a compiled program plus a mocked backend and
   fails on any key without an entry, so new settings get documented.
2. **`RunFile.create`** gains `sample: Mapping | None`, `provenance: Mapping | None`
   and `connection: Mapping | None`. It writes the v2 tree; values are validated as
   today (`_equipment`).
3. **`read_run_file`** detects v1/v2 by `schema`. It returns the same flat dict as
   today, with `equipment` flattened from `instrument/*` minus `connection`, plus
   new keys `sample`, `connection` and `provenance`, empty for v1. So the viewer,
   the bench scripts, `oe-recover-run` and `from_settings` keep working, and v1
   files stay readable.
4. **Writers.**
   - `run_sequence` passes `uid`/`scan_id` from the start document. `RunFileWriter`
     reads them from the doc.
   - `run_from_sheet` adds the sheet path and SHA-256, and the session path, text and
     SHA-256 (`load_session` keeps `source_path` and `source_text` on `Session`,
     excluded from equality and the constructor).
   - The connection fields come from the session.
   - **`[sample]` table in the session file (user, 13 Nov).** It is optional and
     descriptive only: strings, numbers and booleans, flat, with the unit as a key
     suffix (`mass_mg`, `capacity_mah`, `area_cm2`). `load_session` stops refusing it
     (it refuses unknown top-level tables today), validates its types and keeps it on
     `Session.sample`. No code reads it for limits, compilation or validation; it is
     only written to `/entry/sample`. The suffix gives `@units` (`_mg` → `mg`); a key
     without a known suffix gets no units. `oe-enqueue` and the start script print it.
     The per-run notebook's settings cell gets an optional `SAMPLE = {...}`.
   - The per-run notebook passes its settings cell as `session_text`.
   - Versions and host are collected in `RunFileWriter`.
5. **`recovery.py`.**
   - Open groups through a helper that knows both layouts.
   - Keep the source's layout (a v1 file recovers to v1).
   - Store `recovery` under `/entry/run` in v2.
6. **Tests.**
   - v2 round trip, every value with its description and units.
   - v1 fixture still read: a small v1 file generated by today's writer, committed
     under `tests/data/`.
   - SWMR live-reader test on v2.
   - Recovery on v1 and v2.
   - The completeness test from step 1.
   - Simulator QueueServer contract tests unchanged.
   - Check one file in h5web or HDFView (NXdata plot of V against t).
7. **Docs.** `docs/user/data.md` (layout and how to open it), the technical index
   row, and a changelog entry at the milestone.
8. **Bench.** One real 2460 run (resistor or a cell at rest) writes v2 before the
   freeze (30 Nov), read back and recovered once.

Estimate: about one day, including tests; then a Codex review of the code.

## Questions for Codex

1. **`NX_class` attributes.** `NXentry`, `NXsample`, `NXinstrument`, `NXdata` help
   h5web/silx/NeXus readers (h5web auto-plots `NXdata`). They also invite NeXus
   validators that will complain, for example about the missing `NXentry/definition`
   and about non-NeXus names. Is it better to:
   - use them only where the group really follows the base class (`NXdata` for
     `readout`; `NXentry`; `NXsample` with `name`/`description`), or
   - leave them out entirely?
2. **SWMR.**
   - Is creating about 70 small scalar datasets and nested groups before
     `swmr_mode = True` fine? Any limits on object count or metadata cache?
   - Is setting `t_s@start` (ISO 8601) after SWMR starts acceptable? It is the same
     kind of update as today's time-origin attributes.
3. **Timing table.** One dataset per NPLC (`reading_s_nplc_0p1`), or one 2-column
   compound or two arrays (`nplc`, `reading_s`)? The flat key must round-trip
   for `SequencerProfile.from_settings`.
4. **Sample table (decided: `[sample]` in the session file, step 4).** Under
   AGENTS.md tier 2 the session is the environment the user confirms, and the agent
   may not edit a cell environment's set-up description and load it itself. That
   covers `[sample]` too, so a wrong or stale table is the user's to fix.
   - Is the suffix-to-units rule sound, or should it be an explicit
     `[sample.units]` table?
   - Should the table be validated beyond types (for example, refuse nested tables
     or arrays)?
   - Does anything in the plan let `[sample]` affect a run?
5. **Storing the session text.** It holds the instrument address and limits; any
   reason not to store it verbatim? The data stay local.
6. **Linking from a SAXS NeXus file.**
   - Is `/entry` plus `NXdata` enough for an external link (`h5py.ExternalLink`
     to `/entry/readout`)?
   - Should we add an absolute UTC time axis dataset, or is `t_s@start` plus
     `time_origin_epoch_s` the NeXus-preferred way?
7. **Anything stored today that this layout loses**, or anything available at run
   time not listed above.
8. **Risk before the 30 Nov freeze**: anything in this plan you would cut or defer.

Report: at most 25 lines; a numbered answer per question, then defects in the plan
(step, problem, fix).
