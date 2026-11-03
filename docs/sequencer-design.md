# Sequencer runtime design (O5)

Status: design, 3 Nov 2026. Based on bench evidence B0 and E1–E4 of 3 Nov
(`docs/validation.md`). Items marked **[B1]** are unverified and go into the B1
test matrix before the design is relied on.

## 1. Goal and constraints

Run any validated `Sequence` (steps, two-phase sampling, cutoffs, one repeat
block) instrument-locally on the 2460, with live reads, through the existing
`SequenceBackend` protocol (`sequences/backend.py`), so `SequenceFlyer`,
`run_sequence`, the run file and the notebook work unchanged.

Facts this design rests on (all measured on serial 04686198, firmware 1.7.16a):

| Fact | Source |
|---|---|
| TSP is Lua 5.0.2 (no `#`, no `%` operator) | B0 run 2 |
| Command lines of ~3 KB are rejected; ≤ 900 characters work | E1 |
| Off mode is stored **per source function**; power-on default NORMAL | B0 P2 + direct check |
| A source-function change turns the output OFF, also via `BLOCK_CONFIG_RECALL` inside a model; `BLOCK_SOURCE_OUTPUT ON` after it restores it (+24 ms) | E1, E2 A/B |
| Same-function recalls keep the output ON (+1.5 ms per recall) | E2 C |
| Set source function and limits before the measure function (else ranges clash and limits are clamped) | E1, E2 |
| Reading spacing, auto-zero OFF, 2 V range, 4-wire: 40.8 ms (1 NPLC), 4.83 ms (0.1), 1.24 ms (0.01); jitter ±20 µs; auto-zero ON doubles to triples it | E3 |
| `BRANCH_LIMIT_CONSTANT` (`LIMIT_OUTSIDE`) after a 1-reading measure block cuts off on the first sample past the window | E4 |
| `BRANCH_COUNTER N` = N passes in total | E4 |
| An unreachable block fails model validation at initiate (event 2709) | E4 first model |
| `BRANCH_ON_EVENT` latches events: unusable for per-sample deadlines | B0 P4A rerun |
| The buffer can be read in chunks while the model writes it; 1M-record buffers allocate | B0 P5, P6 |
| With HIGH-Z and 4-wire, nothing can be measured while the output is OFF | E1 (event 5078) |
| `smu.source.output` once read ON after OFF commands while the relay was open | after E1, not reproduced |

## 2. Overview

```
Sequence + SafetyLimits + profile
   │  host: compile_program()   (pure, tested)
   ▼
SequencerProgram: config entries, block table, sample plan
   │  host → runtime: short validated calls (oe_seq_*), each < 900 chars
   ▼
packaged sequencer.tsp: validates every entry/block, builds config lists,
buffer and trigger model; arms; runs instrument-locally (timing, cutoffs,
markers, output off at end/stop)
   │  host polls: oe_seq_read(offset, n) → bounded chunks
   ▼
host: replay() assigns step/repeat indices and events → SequencePoll
```

The host never paces samples. The model is finite by construction (sample
counts, repeat count), ends with the output OFF, and keeps running correctly if
the host disappears.

## 3. Instrument configuration at prepare

Set once per run, output OFF, before building lists:

- **Off mode HIGH-Z for both source functions**, read back for both; refuse to
  arm otherwise. Done at every prepare (power-off resets it).
- Terminals (front/rear) and sense (4-wire default) from the session config;
  sense is set inside every measure entry (it is per measure function).
- **Fixed ranges, autorange OFF**, chosen once per run so pulses never change
  range: current source range = smallest range ≥ max |I| of current steps;
  voltage measure range = smallest ≥ max(|v_min_abs|, |v_max_abs|); for voltage
  steps: voltage source range likewise, current measure range ≥ `i_max_abs`.
- Compliance: current steps `vlimit = max(|v_min_abs|, |v_max_abs|) + vlimit_margin_v`
  (default 0.1 V, so the instrument-local cutoff fires before compliance shapes
  the waveform); voltage steps `ilimit = i_max_abs`.
- Auto-zero OFF for all entries, `smu.measure.autozero.once()` at prepare.
- Readback ON (source value is measured, not the setpoint).
- Digital lines (session config, defaults from `Keithley2460Config`): marker
  output, busy output, START input. See §8.

## 4. Configuration lists

Two lists, rebuilt per run:

- `OE_SRC`: one entry per distinct source state `(mode, level)`. REST is current
  mode at 0 A. All current-mode entries share range and limit.
- `OE_MEAS`: one entry per distinct measure state `(function, nplc)`;
  function = voltage for current/rest steps, current for voltage steps.

A phase recalls `(OE_SRC[s], OE_MEAS[m])` with `BLOCK_CONFIG_RECALL`. Entries
are stored in the order "source settings, then measure settings" (E1/E2).

## 5. Trigger-model layout

Per step (one or two sampling phases):

| Block | Phase 1 | Phase 2 (if any) |
|---|---|---|
| `BLOCK_RESET_BRANCH_COUNT` (this phase's counter) **[B1]** | ✓ | ✓ |
| `BLOCK_CONFIG_RECALL OE_SRC[s], OE_MEAS[m]` | ✓ | ✓ (same s, phase-2 m) |
| `BLOCK_SOURCE_OUTPUT ON` — only if the step can follow a step of the other source function, or is the first step | if needed | – |
| `BLOCK_DIGITAL_IO` marker line high | ✓ | – |
| `BLOCK_DELAY_CONSTANT d` | ✓ | ✓ |
| `BLOCK_MEASURE_DIGITIZE buf, 1` | ✓ | ✓ |
| `BLOCK_DIGITAL_IO` marker line low (idempotent after the first sample) | ✓ | – |
| `BLOCK_BRANCH_LIMIT_CONSTANT` cutoff(s), see §7 | 1–2 | 1–2 |
| `BLOCK_BRANCH_COUNTER n → DELAY` | ✓ | ✓ |

About 16–18 blocks per two-phase step. Counter resets make re-entry correct
inside the repeat block and after a cutoff left a counter part-way **[B1: do
counters reset on completion; does the reset block exist under this name]**.

Around the steps:

- Prologue: `WAIT` on the START line (external start only), `BLOCK_LOG_EVENT`
  "OE_T0" (time origin, §8), busy line high.
- Repeat block: `RESET` of the repeat counter before the block's first step;
  `BRANCH_COUNTER count → first step of the block` after its last step.
- Normal end: `SOURCE_OUTPUT OFF`, marker and busy low. **Stop path** (separate
  blocks): `SOURCE_OUTPUT OFF`, lines low. The final block number reported by
  `trigger.model.state()` tells completed from stopped.
- No unreachable blocks (validation fails at initiate): the compiler emits the
  stop path only if some branch targets it, and never places blocks after an
  unconditional branch unless something branches there.

Budget: the bipolar pulse sequence (rest + 4 repeated two-phase steps) needs
about 90 blocks, CC-CV cycling about 110. The compiler rejects a program over
255 blocks with "split the run" (the model allows 30 steps, so this check, not
the step count, is the real limit).

## 6. Timing

A phase of `n` samples at effective interval `Δ` (from `sample_schedule`)
takes `DELAY d → MEASURE → limit/counter blocks` per sample:
`d = Δ − t_meas(nplc) − t_blocks`.

- NPLC per phase: the largest commissioned NPLC with
  `t_meas + t_blocks + margin ≤ Δ`, from a per-instrument timing table (E3,
  auto-zero OFF): 1 NPLC 40.8 ms, 0.1 NPLC 4.83 ms, 0.01 NPLC 1.24 ms;
  `t_blocks` ≈ 0.2 ms (E4 loop vs E3), margin 0.3 ms. **[B1: 0.5, 0.2 and 0.05
  NPLC, and `t_blocks` with marker and two limit blocks]**
- Consequence for the plan's proposal "5 ms at 0.1 NPLC": infeasible (4.83 +
  0.2 + 0.3 > 5). Options: fast interval 6 ms at 0.1 NPLC, or 5 ms at about
  0.05 NPLC (to be measured). See §12.
- Validation rejects a phase whose interval cannot be met at the smallest NPLC.
- Duration accuracy is whatever the timestamps show: a 0.1 ms per-sample error
  over 20,000 samples is 2 s. The host reports actual vs nominal step durations
  from timestamps; no timing claims beyond that.

## 7. Cutoffs

- Current and rest steps (reading = voltage):
  - `on_cutoff = STOP`: one block, `LIMIT_OUTSIDE [lo, hi] → stop path`, with
    `lo = v_min or v_min_abs`, `hi = v_max or v_max_abs`.
  - `on_cutoff = NEXT`: two blocks: `LIMIT_OUTSIDE [v_min_abs, v_max_abs] →
    stop path` first, then the step window `→ next step`. A reading outside the
    absolute window therefore always stops the run.
  - No step cutoffs: the absolute-window block only.
- Voltage steps (reading = current): `LIMIT_INSIDE [−i_abs_min, i_abs_min] →
  next/stop` (CV taper). The sourced voltage is within the absolute window by
  validation; compliance is `i_max_abs`.
- "Next step" inside the repeat block is the next step of the block, or the
  repeat counter after its last step.
- Between samples the instrument does not watch the voltage; the compliance
  limit is the only protection there. This is stated in the user docs.

## 8. Start, markers, busy line, time base

- START: `immediate` arms and runs; `external` waits on the START input line
  (`digio.MODE_TRIGGER_IN`, `trigger.digin[line].edge`, as in runtime v14). The output stays OFF while waiting.
- Marker line: high at every step start (each repeat too), low after the step's
  first sample; pulse width = first sample interval (≥ ~1.5 ms). Busy line: high
  from START to output OFF. Both are TTL outputs for the beamline.
- Time base: every reading has instrument `seconds + fractionalseconds`. t = 0
  is the `OE_T0` event-log entry written right after START by
  `BLOCK_LOG_EVENT, trigger.LOG_INFO1, "OE_T0"` (same clock; in-model log events
  were used in the 2 Nov timestamp proof), so `t_s = reading_time − t0` for both
  start modes.
- UTC mapping (`measurement.ClockMapping`): at prepare the host brackets
  `eventlog.post("OE_PING", …)` between two host UTC readings; the event's
  timestamp gives the offset with uncertainty ≈ half the round trip (~1 ms on
  the link-local cable) **[B1: `eventlog.post` exists and is timestamped]**.

## 9. Readout and live polling

- Buffer: one `STYLE_STANDARD` buffer of `expected_records + 16`, rebuilt per
  run, never cleared while it holds unacknowledged records.
- `oe_seq_read(first, count)` prints one line via `printbuffer` with, per
  record: seconds, fractional seconds, reading, source value, status, source
  status; at most 500 records (~36 KB) per call. `format.asciiprecision` is set
  to 15 so the host sees the values the limit blocks compared.
- `poll(max_records)` loops `oe_seq_read` up to `max_records`, and also reads
  `trigger.model.state()` and new event-log entries (instrument errors become
  `error` events).
- **Replay** (`replay(program, records)`, pure): walks the sample plan with the
  same cutoff rules as §7 (and the simulator), assigning `step_index`,
  `repeat_index`, `step_start`/`cutoff`/`stop` events and the outcome. At the
  end it must agree with the record count and the final block (completed vs
  stop path); disagreement is outcome `error` with a detail, never silent.
  Checks: source readback within tolerance of the step level when not in limit
  (status bit 32); sample spacing within tolerance of `Δ`.
- Mapping to `READOUT_FIELDS`: current/rest steps `voltage_v = reading`,
  `current_a = source value`; voltage steps the other way round; statuses
  copied.

## 10. Safety and failure handling

- Output-OFF confirmation (`oe_seq_off()`): abort the model, `smu.source.output
  = OFF`, then require `smu.source.output == smu.OFF`; if it still reads ON,
  change the source function and back (forces off, event 5073) and read again;
  still ON → `ShutdownUnconfirmed`. Used after every run, abort and recovery.
- Abort: `oe_seq_abort(reason)` = the confirmation above; reports records
  written so far; the host drains them (`SequenceFlyer.abort` already does).
- Host death: the finite model completes and turns the output OFF itself.
- Retained data: `prepare` refuses while the previous buffer has records not yet
  read by the host, unless an explicit discard naming that run is given.
- Never auto-retry a mutation whose delivery is uncertain; a timed-out query
  invalidates the session and recovery starts with inspection (existing
  transport behaviour).
- Every line the host sends is a call to a packaged runtime function with
  numeric/whitelisted arguments, < 900 characters. No user TSP.

## 11. Runtime API (`tsp/sequencer.tsp`, ABI `oe-k2460-seq-v1`)

| Function | Does |
|---|---|
| `oe_seq_status()` | one line: runtime state, model state, final block, output, buffer n, acknowledged offset, error count |
| `oe_seq_configure(...)` | §3 settings; HIGH-Z both functions with read-back; digital lines; limits stored for entry validation |
| `oe_seq_begin(n_src, n_meas, n_blocks, capacity, discard_id)` | refuses with unread records; deletes old lists/buffer; creates new |
| `oe_seq_source(i, mode, level)` / `oe_seq_measure(i, func, nplc)` | validated against configured limits; stores entry i |
| `oe_seq_block(i, kind, a, b, c, d)` | whitelist: recall, output, digio, delay, measure, limit_outside, limit_inside, counter, reset_counter, wait_start, log_event, branch_always; checks targets and indices |
| `oe_seq_arm()` | verifies model complete, HIGH-Z, output OFF, posts nothing to the output; initiates |
| `oe_seq_read(first, count)` | §9 |
| `oe_seq_ack(offset)` | host has archived records up to offset |
| `oe_seq_abort(reason)` / `oe_seq_off()` | §10 |
| `oe_seq_ping()` | `eventlog.post` for clock mapping |

Installed like the M4 runtime: digest-named script, output OFF, ABI/build
check, allow-listed in `transport.load_runtime`.

## 12. Host modules and tests

- `keithley/k2460/sequencer.py` (pure): `SequencerProfile` (ranges, lines,
  margins, timing table), `compile_program(sequence, limits, profile)`,
  `replay(program, records)`. Tests: golden block tables for pulse, cycling,
  CC-CV; block budget; no unreachable blocks; every emitted call < 900 chars;
  **replay equals the simulator** (simulate, strip indices, replay, compare);
  replay detects inconsistent data.
- `keithley/k2460/tsp/sequencer.tsp`: Lua 5.0 only; an offline lint rejects
  `#`, `%`, `goto`, integer division and lines over 900 characters.
- `keithley/k2460/sequence_backend.py`: `Keithley2460SequenceBackend`
  implementing `SequenceBackend`; tests against a fake transport that models
  the runtime's replies, including a stale-ON output reading and a timed-out
  query.
- Notebook: `BACKEND = "k2460"` selects it; `require_confirmation` already gates
  cell runs.

## 13. B1 verification (resistor, then RC dummy cell)

1. `BLOCK_RESET_BRANCH_COUNT` name/behaviour; counter state after natural
   completion and after a cutoff exit; repeat block of 3 × 2 steps.
2. `SOURCE_OUTPUT ON` while already on (no glitch, no error).
3. Timing table: 0.5/0.2/0.05 NPLC; `t_blocks` with marker + two limit blocks;
   nominal vs actual step durations over a 5-minute pulse run.
4. `eventlog.post` timestamps; `BLOCK_LOG_EVENT` in the prologue; clock offset.
5. Marker and busy lines on the scope; external START from the generator.
6. Live poll at 200 Hz for 5 minutes (0.1 s pulses), host CPU and lag.
7. Abort mid-run; host-kill mid-run (model completes, output OFF); stale-ON
   attribute check after a function switch inside a model.
8. RC dummy: CC to cutoff (NEXT and STOP), CV taper cutoff, HIGH-Z off state at
   a STOP (voltage after output OFF recorded by a separate DMM or by the next
   run's first reading).

## 14. Decisions needed from the user

1. Fast sampling: 6 ms at 0.1 NPLC, or 5 ms at ~0.05 NPLC (noisier)?
2. Digital lines: which line carries step markers to the beamline, which is
   busy, which is START (defaults: marker 1, busy 2, START 3)?
3. `vlimit_margin_v` above the absolute window (default 0.1 V)?
