# Template validation record

Updated: 2026-11-03. Scope: contracts, transport/runtime commissioning, bounded
nominal-100-ohm tests, a narrow target-buffer proof, and the first operational
simulator-backed M6 Flyer; not operational hardware sourcing or acquisition.

Source/wheel builds and strict Twine checks passed. All 31 packaged source/
resource files matched the checkout; isolated wheel v14/v17 reconstruction and
source-distribution fixture byte equality passed. All **53 November evidence
JSON files** parsed. The software check record is
`evidence/k2460-2026-11-02-timed-v17-software-validation.json`
(repository-relative path). These checks do not override the failed hardware
cadence result.

## S1/S2 sequencer-shaped model with scope (3 November 2026)

Same tier-1 set-up; ±4.9 mA (±0.50 V, user asked for >= 0.5 V on the scope).
`bench/marker_start_s1.py` builds a 53/54-block prototype of the design's step
layout: 13 steps (rest, then +4.9 mA / 0 A / -4.9 mA / 0 A x 3), 16 samples per
step at 6.25 ms, 0.1 NPLC, marker on line 1, busy on line 2, START on line 3.

- S1 (immediate, twice; logged): 208/208 readings, normal end block, levels
  ±0.5022 V, sample spacing 6.2575 ms, +1.32 ms per step change, first reading
  36.8 ms after t = 0 (HIGH-Z output-on), total 1348 ms for 1300 nominal.
  Scope (user): 13 markers, widths 6.24–6.29 ms; the resistor voltage step comes
  1.07 ms before its marker; busy high 1.349 s, falling 101.07 ms after the last
  marker.
- S2 (external START from a 0.01 Hz square wave, 28 mV / 4.5 V via 1 kohm; waited
  30.3 s): identical run. Scope (user): START -> busy 65 us, START -> first marker
  30.66 ms, START -> first voltage step 131.31 ms.

Evidence: [`k2460-2026-11-03-scope-s1-immediate.json`](evidence/k2460-2026-11-03-scope-s1-immediate.json),
[`k2460-2026-11-03-scope-s1-readings.json`](evidence/k2460-2026-11-03-scope-s1-readings.json),
[`k2460-2026-11-03-scope-s1-external.json`](evidence/k2460-2026-11-03-scope-s1-external.json),
[`k2460-2026-11-03-scope-s2-readings.json`](evidence/k2460-2026-11-03-scope-s2-readings.json).

## Preliminary B1 checks T1–T5 on the resistor (3 November 2026)

Same tier-1 set-up, 2 mA, after a power cycle (HIGH-Z set for both functions).
`bench/b1_prelim.py`; output confirmed OFF after every test.

- T1 (output OFF, counted with log events): an inner `BRANCH_COUNTER` restarts
  when its loop is re-entered after completing; `BLOCK_RESET_BRANCH_COUNT`
  exists.
- T2: `eventlog.post` entries are timestamped; instrument clock minus host UTC
  +4,167.75 s, spread 0.18 ms over 5 pings, round trip 1.7 ms. The first reading
  came 40.9 ms after an in-model `BLOCK_LOG_EVENT` at 1 NPLC: reading timestamps
  mark the end of the measurement.
- T3: a second `SOURCE_OUTPUT ON` while on adds 0.2 ms and nothing else. The
  stale `smu.source.output` = ON reproduced after CC -> CV (no ON block) -> CC ->
  OFF; front panel OFF; a function toggle cleared it. T3b: with an ON block after
  every function switch the output stays on (status 200) and the attribute reads
  OFF afterwards.
- T4 (auto-zero OFF): reading spacing 0.5 NPLC 20.83 ms, 0.2 NPLC 8.84 ms,
  0.05 NPLC 2.83 ms; sequencer-shaped loop (marker + two limit blocks + counter)
  period 4.968 ms at 0.1 NPLC with zero delay (overhead 0.14 ms); a 6 ms loop
  ran at 6.002 ms (5.989–6.027 ms), a 5 ms loop at 0.05 NPLC at 5.001 ms.
- T5: 6,000 records of a 200 Hz run read live in 29 chunks (<= 28 KB, 17 ms per
  read), contiguous.

A first run stopped after T3 on the stale reading (the script then required
`smu.source.output == OFF` without the toggle fallback); its T1/T2 output was
not saved and the tests were rerun. Evidence:
[`k2460-2026-11-03-b1-prelim.json`](evidence/k2460-2026-11-03-b1-prelim.json).

## E3/E4 measurement timing and limit-branch cutoff on the resistor (3 November 2026)

Same tier-1 authorization and set-up as E1/E2 (current source only, <= 4 mA).

- E3, reading spacing / standard deviation at 0.41 V (2 V range, 4-wire), auto-zero
  ON vs OFF: 1 NPLC 82.6 / 40.8 ms (0.9 / 0.7 uV); 0.1 NPLC 10.6 / 4.83 ms
  (5.2 / 4.1 uV); 0.01 NPLC 3.38 / 1.24 ms (11.8 / 6.7 uV). Jitter about
  +-20 us. The planned 5 ms fast phase at 0.1 NPLC needs auto-zero OFF.
- E4: `BLOCK_BRANCH_LIMIT_CONSTANT` with `LIMIT_OUTSIDE [-1, 0.3] V` after a
  one-reading measure block did not fire at 0.205 V and fired on the first
  0.410 V sample, jumping to a 0 A rest (output stayed on). Instrument-local
  voltage cutoffs work. `BLOCK_BRANCH_COUNTER N` gives N passes in total (the
  first included). A first E4 model with an unreachable block after
  `BRANCH_ALWAYS` failed validation at initiate (event 2709, output never on).

Evidence: [`k2460-2026-11-03-timing-cutoff-e3-e4.json`](evidence/k2460-2026-11-03-timing-cutoff-e3-e4.json)
(E3; its E4 is the failed first model) and
[`k2460-2026-11-03-timing-cutoff-e4.json`](evidence/k2460-2026-11-03-timing-cutoff-e4.json);
script `bench/timing_cutoff_e3.py`.

## E1/E2 energized config-list recall on the resistor (3 November 2026)

Tier 1, user-authorized: 100 ohm four-wire resistor (front), |I| <= 5 mA, voltage
limit 1 V, digital line 2 ("busy") as marker, HIGH-Z for both functions. Output
on for at most about 2.5 s per model; output confirmed OFF after every run.

- **A source-function change turns the output OFF**, also inside a running
  trigger model via `BLOCK_CONFIG_RECALL` (source status lost `STAT_OUTPUT`,
  200 -> 72; direct `smu.source.func` change logs event 5073). A following
  `BLOCK_SOURCE_OUTPUT ON` restores it: CC 4 mA (0.41 V) -> CV 0.30 V (2.93 mA)
  with 24 ms extra between readings. CC -> CV steps need that re-enable block
  and briefly leave the cell at open circuit (HIGH-Z).
- **Same-function recalls keep the output ON** (CC +4 mA -> 0 A -> -2 mA, status
  200 throughout, about 1.5 ms extra per recall): pulse and rest steps, all in
  current mode, can share one config list.
- Setup order matters: set the source function and its limits before the
  measure function; a measure range set while measure and source functions are
  equal is refused and clamps the source limit (events 1128, 5076/5077). E1 hit
  this (phase 1 ran in compliance at 2.05 mA) and is superseded by E2.
- At 1 NPLC, 4-wire, the reading spacing was 82.6 ms (default settings,
  auto-zero presumably on); the sequencer must set auto-zero and measure its
  real spacing per profile.
- Command lines of about 3 KB are rejected ("Input buffer overrun", -363); lines
  up to 900 characters worked. With HIGH-Z and 4-wire, no measurement is
  possible while the output is OFF (event 5078).
- After E1, `smu.source.output` read ON (and stayed ON after explicit OFF
  commands) while a measurement reported the output as off and the front panel
  showed OFF; a source-function change cleared it. Not reproduced in E2. Open
  for B1: verify output state independently of that attribute.

Evidence: [`k2460-2026-11-03-function-switch-e1.json`](evidence/k2460-2026-11-03-function-switch-e1.json),
[`k2460-2026-11-03-function-switch-e2.json`](evidence/k2460-2026-11-03-function-switch-e2.json);
scripts `bench/function_switch_probe.py`, `bench/function_switch_e2.py`.

## B0 output-OFF firmware feature probe (3 November 2026)

`bench/feature_probe.py` on serial 04686198, firmware 1.7.16a, with a 100 ohm
four-wire resistor connected and the output OFF throughout. The instrument had
been powered off overnight: its power-on events report `defbuffer1`/`defbuffer2`
0 % filled, so the 12,867 unretrieved `defbuffer1` records of 2 November were
lost to the power-off, and the v17 runtime was no longer loaded. Run 1 stopped in
P6 (a probe bug: two reply lines for one query) and run 2 in P6 (Lua 5.0.2 has no
`#` operator); both left the output OFF. Run 3 completed with no event-log errors.
Findings:

- Output-off mode is stored **per source function**; power-on default NORMAL.
  HIGH-Z is settable for both (direct check). The sequencer must set and verify
  HIGH-Z for current and voltage at every prepare.
- One source configuration list holds current and voltage entries, and
  `BLOCK_CONFIG_RECALL` with a source and a measure list switches both functions
  inside one trigger model (readings Volt/Amp, then Amp/Volt). Output OFF only;
  switching with output ON is a B1 item.
- **`BRANCH_ON_EVENT` latches:** once the timer event has occurred, the branch
  block takes the branch on every later pass, even after a `WAIT` consumed the
  event; an event before the block's first pass is also seen. This confirms the
  suspected v17 failure (a deadline branch firing on every sample). The first
  P4A run used `BRANCH_COUNTER 1`, which never re-enters the loop (see E4), and
  was misread; the rerun is
  [`k2460-2026-11-03-feature-probe-p4a-rerun.json`](evidence/k2460-2026-11-03-feature-probe-p4a-rerun.json).
- A 1,000,000-record reading buffer can be allocated.
- A reading buffer can be read in chunks while the trigger model measures into
  it: 26 reads during RUNNING, contiguous through 2,000 records (about 3.4 ms per
  reading at 0.01 NPLC, output OFF).

Evidence: [`k2460-2026-11-03-feature-probe.json`](evidence/k2460-2026-11-03-feature-probe.json)
(run 3 and the direct off-mode check) and
[`k2460-2026-11-03-feature-probe-run1-partial.json`](evidence/k2460-2026-11-03-feature-probe-run1-partial.json).

## Timed-hold candidate software and installation

Latest scoped diagnostic, **run-05**, used unchanged v17 with +5 mA / 1 V /
1 s duration and **1 s spacing (one reading)**. It completed normally with
4.9842773005 mA / 512.48002052 mV, statuses 200/8, after run-04's single partial
reading was checksum-verified, freshly matched and explicitly dispositioned.
All five events were archived; no errors. Recovery and an independent connection
verified IDLE/EMPTY/OFF/0 A, one retained record/capacity 16, unchanged default
extents and zero unread events. New export checksums and raw/projected equality
passed. The instrument initiation-to-idle event span was 1.00257588 s, not a
physical-duration measurement. User reported trigger-setting uncertainty; no
scope values/capture are confirmed. Immediate mode ignores generator START.
Repeated cadence remains failed and no hardware gate is promoted. The user was
told they can power down equipment; physical shutdown is unconfirmed. Evidence:
`evidence/k2460-2026-11-02-timed-v17-run-05-review.json` and linked run/export.
The focused 63 timed-runtime tests passed; package/runtime bytes were unchanged.

Latest checkout validation after the host padding fix: **527 tests passed**, Ruff
lint/format (94 files), strict mypy (29 source/helper files), strict MkDocs and
whitespace checks. V17 wire source passed Lua 5.0.2 syntax compilation. The
firmware block-list fixture and inert Lua graph checker are included in source
distributions; these checks do not establish instrument event semantics.

**Preceding hardware result: first energized v17 hold incomplete.** The
+5 mA / 1 V / 1 s / 0.1 s case collected one of ten requested readings and took
the local `cadence_overrun` OFF path. The retained record is 4.983975552 mA /
512.47859001 mV, statuses 200/8. The user reports the scope did not trigger;
there is no new physical pulse-duration evidence. All five events (three
information, two range warnings, zero errors) were archived. Recovery and a fresh
connection verified IDLE/EMPTY/OFF/0 A, one retained record/capacity 16, unchanged
default-buffer extents and zero unread events. Fresh raw equality, export payload
SHA-256 and file checksum passed. See `evidence/k2460-2026-11-02-timed-v17-run-04-review.json`
and its linked source/export. V17 is not accepted for timed holds. Event-consumption
semantics require review; an inert graph recorder cannot validate them.

V17's preceding OFF preparation was blocked by a whitespace-sensitive host parser;
that was corrected against the actual firmware block list without changing TSP
bytes. That attempt issued no arm and recovered with zero records before the
same authorized single capture proceeded. Installation/raw attempt evidence use
`timed-v17-install`, `timed-v17-run-03` and `timed-v17-run-04` filenames.

Shared measurement v4, pure raw-record projection and the separate timed-resistor
backend are implemented with explicit unknown START, physical clock resolution,
I/V windows, availability and program mapping. The new backend uses one serialized
connection, exact artifact/firmware checks, independently programmed nominal
hold duration/sample spacing, terminal bounded transfer, checksummed export,
explicit disposition and failure-preserving recovery. The new 2 V / 1 NPLC profile
is uncommissioned and is rejected by the unchanged v14 count-proof API.

Offline checks before installation passed 516 tests, Ruff lint/format, strict
mypy (29 source/helper files), strict MkDocs and whitespace checks. Source and
wheel distributions built and passed strict Twine checks. Wheel source/resource
equality and isolated v14/v15 artifact reconstruction passed. Both wire artifacts
passed standalone Lua 5.0.2 syntax compilation; this does not validate TSP commands
or firmware event/timing semantics. The Lua version/grammar check used the
[official Lua 5.0 reference](https://www.lua.org/manual/5.0/manual.html), consistent
with Keithley's documented Lua 5.0.2 engine. Base v14 digest remains unchanged.
A subsequent host-only kickoff-fault cleanup refinement passed 91 focused tests
and strict mypy; it did not change the packaged TSP bytes.

The subsequent full host-cleanup suite passed 517 tests. After the rejected v15
preparation below, corrected v16 passed **521 tests**, strict mypy (29 files),
Ruff format (94 files) and standalone Lua 5.0.2 syntax compilation before v16 installation. Tests cover measurement-range-before-compliance ordering, minimum
profile-compatible compliance, retained-data rejection during exact v15
replacement, and nonconsuming error checks before preparation and arm.

With fresh front-four-wire 100 ohm / 1 W resistor and physical-OFF confirmation,
the user explicitly authorized only old run08 disposition, exact v14-to-v15
replacement, consuming event archival and independent safe-state verification.
Two current sessions matched the old raw record/absolute timestamp to its verified
archive before the authorized discard. V15 installed at SHA-256
`d8d3bcdb948dde40be53f71d83a87df2679528e3bc81d3b73c43f035999e0712`.
The installation session and an independent query-only session verified exact
identity, IDLE/stopped/OFF/0 A, no proof records (capacity 0 before allocation),
zero unread events and preserved default-buffer counts/extents/capacities. No
installation events occurred. No acquisition was prepared or armed and no source,
cadence, cutoff or protection gate is promoted. Evidence is
`evidence/k2460-2026-11-02-timed-v15-preflight.json` and
`evidence/k2460-2026-11-02-timed-v15-install.json`.

V16 was then explicitly authorized, installed and independently verified safely.
It prepared the requested 1 V compliance correctly, but initiate failed with nine
2709 missing-block events for the immediate graph's external-only blocks. A
separate fresh stopped/OFF inspection archived nine errors and two 5076 warnings,
then performed the already authorized recovery. Independent final queries
verified IDLE/EMPTY/OFF/0 A, zero readings/capacity 16, preserved defaults and zero
unread events. See the v16 install/run-02/recovery evidence files. No acquisition
gate passed. Before installation, corrected v17 passed 526 tests, Ruff lint, strict mypy and
Lua 5.0.2 syntax compilation passed. An inert Lua recorder executed both mode
constructors, verifying 29/39 contiguous reachable blocks and four/five explicit
terminal-OFF paths; it reproduced an unreachable block in v16 immediate mode.
Fixture: `tests/fixtures/timed_hold_graph.lua`. This does not establish firmware
cadence, physical output duration, measurement timing or cutoff latency.

The separately authorized first +5 mA / 1 V / 1 s / 0.1 s attempt failed during
output-OFF preparation: 5077 rejected the source limit while measurement range
was still 0.2 V; 5076 then reported automatic limit 0.212 V at the new 2 V range.
Readback mismatch prevented arm. No output-ON command was issued and no readings
were collected. Both events were archived under explicit consent; unread counts
became zero. Recovery verified runtime IDLE/output OFF/programmed 0 A, model
BUILDING (stopped), proof buffer count 0/capacity 16, unchanged defaults. The
fresh session confirmed IDLE/OFF/BUILDING before an overly strict model-IDLE
assertion stopped the remaining checks. Evidence:
`evidence/k2460-2026-11-02-timed-v15-run-01.json`. This promotes no acquisition gate.


The proposed first energized case is +5 mA / 1 V compliance / 1 s / 0.1 s spacing,
10 expected records, 10 mA range and `NPLC_1_2V`. At nominal 100 ohm this predicts
0.5 V and 2.5 mW, not measured accuracy. The user selected specified duration/sample
spacing, requested a stronger CH4 signal and confirmed 100 ohm / 1 W; a separate
scoped run/setup question is pending. All later gates remain subject to the
[timed-hold acceptance matrix](timed-hold-commissioning.md).

## Scope observation-node clarification

Latest corrected-scope repeat **2026110208** completed after explicit fresh
confirmation of CH4 1 Mohm/DC/1x at 50 mV/div with 0–200 mV visible, unchanged
load/direct cable/START setup, physical output OFF and Single waiting on BUSY
rising at 20 ms/div with 10–20 ms pretrigger. Authorization covered only the
archived/matched run07 record discard, one +1 mA / 0.2 V / 1 NPLC repeat,
consuming event archival and recovery. Query-only preflight, archive checksum
and independent old-record equality passed before discard. The run reached
WAITING_START/READY high/BUSY low/output OFF and completed at block 17 with both
overrun flags false. Reading: **1.0000127368 mA / 102.48923302 mV**, statuses 200/8.
Nine events (seven information, two mode-change warnings 1811, no errors) were
archived. Four unique monotonic markers give BUSY bracket 27.50 us, paired block
41.01070 ms and reading 0.11516 ms before POST_MEASURE. Fresh-session reading/
timestamp matching and retained-IDLE retrieval passed; final IDLE/OFF/0 A,
one new retained record, default-buffer counts/extents/capacities preserved
(contents not compared). The scope subsequently showed very noisy but roughly
0/100/0 mV, qualitatively compatible with the instrument reading. User-reported
START-to-BUSY rise/fall are 6.162(1)/51.461(1) ms (45.299 ms BUSY high), and
START-to-CH4 rise/fall 6.8(1)/50.3(1) ms (43.5 ms nominal crossing interval).
CH4 rise is nominally 0.638 ms after BUSY rise and fall 1.161 ms before BUSY
fall. Parentheses are interpreted as 1 us on BUSY and 0.1 ms on CH4, without a
complete uncertainty budget. Transition durations are not accurately measurable,
only order-of-2-ms estimates. The conditional marker interval is
49.49278–49.52228 ms from START to buffered timestamp, not a field integration
window. No fresh sample rate/record length or raw trace was supplied; no
calibrated amplitude correction or individual aperture is established and no
gate is promoted. Evidence: `evidence/k2460-2026-11-02-timestamp-attempt-08.json`
and its checksum-linked review. Strict MkDocs, evidence JSON parsing and
`git diff --check` passed for this documentation/evidence update.

The user then selected **basic finite holds with timing explicitly uncalibrated**
and stated rough calibration **on the order of 10 ms** is sufficient. This is a
usability requirement, not a measured accuracy claim or new hardware-action
authorization. No further identical timing repeats are proposed. Next work is
coherent unknown-actual-START contract support, a limited real current-hold
backend, offline lifecycle/projection validation and a predefined scoped
acceptance set. Fine independent I/V aperture calibration is outside that first
version; existing adapter gates remain unchanged until the implementation and
contract revision. User reports and conditional arithmetic are retained in
`evidence/k2460-2026-11-02-timestamp-attempt-08-scope-analysis.json`; accepted
scope is `evidence/k2460-2026-11-02-initial-interface-scope.json`. No hardware
commands were sent for these updates. Strict docs, evidence JSON parsing and
whitespace checks passed.

Earlier repeat **2026110207** used user-confirmed direct unattenuated CH4
banana/BNC wiring (centre HI, shield LO), 1 Mohm/DC/1x, same four-wire resistor
and START setup, physical output OFF and scope already waiting Single on BUSY
rising at 20 ms/div with 10–20 ms pretrigger. Explicit authorization covered
only run06 proof-record discard, one +1 mA / 0.2 V / 1 NPLC acquisition, event
archival and recovery. Query-only preflight verified IDLE/OFF/0 A; archive
checksum and fresh old-record matching passed before that discard. The run
armed WAITING_START with output OFF and completed with both overrun flags false.
It measured **1.0000149487 mA / 102.50234604 mV**, raw statuses 200/8. Nine events
(seven information, two warnings 1811, no errors) were archived, with four
unique monotonic run-tagged markers. BUSY bracket is 27.54 us, measurement block
41.01130 ms and reading timestamp 0.10578 ms before POST_MEASURE. Fresh-session
reading/timestamp matching and retained-IDLE retrieval passed; final
IDLE/OFF/0 A with the new proof record retained and all default-buffer extents
unchanged. Default-buffer contents were not compared. The user subsequently
reported CH4 overload/wrong setting and requested a repeat: no scope baseline,
amplitude, edge delay or rise/fall time is accepted from run07. The exact wrong
setting/overload type is unspecified; a tentative approximately 100 mV later
observation has no uncertainty or valid trace. The original instrument evidence
remains archived and independently matched. The report is in
`evidence/k2460-2026-11-02-timestamp-attempt-07-scope-status.json`; corrected
settings/readiness and explicit run07 disposition/event-archival permission
were subsequently received for completed run08 above.
No new hardware command was sent for this capture-status update. The run06
analog amplitude discrepancy has no established cause or
correction. No aperture or timing gate is promoted. Raw evidence and the
checksum-linked review are `evidence/k2460-2026-11-02-timestamp-attempt-07.json`
and `timestamp-attempt-07-review.json` in the same directory. Strict MkDocs and
`git diff --check` passed for this documentation/evidence update.

Run **2026110206** subsequently completed after explicit fresh setup and scoped
discard/run/event-archival authorization. The read-only connection retry
verified IDLE/OFF/0 A and unchanged buffers; no reset or power cycle was needed.
The prior run05 archive checksum and independently retrieved record matched
before authorized discard. The one +1 mA / 0.2 V / 1 NPLC run reached
WAITING_START with output OFF, then completed with both overrun flags false.
It measured 1.0000148322 mA and 102.57840157 mV, raw statuses 200/8. Nine events
were archived (seven information, two mode-change warnings 1811, no errors).
Four run-tagged markers are unique and monotonic: BUSY bracket 26.82 us,
measurement block 41.02950 ms, reading 0.11530 ms before POST_MEASURE. Fresh-session
reading/timestamp matching and retained-IDLE retrieval passed; final state is
IDLE/OFF/0 A with one new proof record retained and all default-buffer extents
unchanged. Contents of default buffers were not compared. The user confirmed
CH3 at physical START pin3 and CH4 across the resistor with LO intentionally
earthed, 1 Mohm/DC/1x and scope already waiting on BUSY before arm. No individual
integration window is established.
See `evidence/k2460-2026-11-02-timestamp-attempt-06.json` and its checksum-linked
`timestamp-attempt-06-review.json` companion. No gate is promoted.

The subsequent scope report gives START-to-BUSY rise 8.740(1) ms and fall
54.055(1) ms (45.315 ms BUSY high), with all edges visible at 10 MSa/s,
20.0 ms/div and 2 Mpts. These actual settings imply a 200 ms record, superseding
the nominal 100 ms setup report. Interpreting each delay parenthetical as 1 us,
the conditional marker calculation is 52.08702–52.11584 ms from START to the
buffered timestamp; thresholds, coverage and full scope error budget remain
unverified. CH4 was reported as a noisy 29(1) mV plateau, with 0–100% rise
0.90(1) ms and 100–0% fall 0.93(1) ms. These are preserved as reported, not
relabelled 10–90% times or START-to-CH4 delays. The analog amplitude is unresolved
against the instrument's 102.5784 mV reading: baseline versus excursion, actual
input/coupling/attenuation, probe switch and connection nodes need checking.
No cause or analog source timing/settling is established. The checksum-linked
`evidence/k2460-2026-11-02-timestamp-attempt-06-scope-analysis.json` retains the
verbatim report and conditional arithmetic; no raw trace is in the repository.
No hardware commands were sent for this reduction. Strict MkDocs and
`git diff --check` passed for the documentation/evidence update.

In a later follow-up the user tentatively called 29 mV an absolute plateau,
reported START-to-CH4 rising 9.6(1) ms and falling 52.7 ms, then replaced CH4's
probe with banana wiring and requested a repeat. The original capture's nominal
CH4 crossing interval is 43.1 ms, with rise 0.860 ms after BUSY rise and fall
1.355 ms before BUSY fall. The rising parenthetical is interpreted as 0.1 ms;
falling uncertainty and threshold confirmation are absent, and the amplitude
discrepancy remains unresolved. These are provisional observations, not
calibrated source timing or aperture evidence. The separate
`evidence/k2460-2026-11-02-timestamp-attempt-06-scope-followup.json` links the
unchanged original reports. New direct-cable node/input confirmation and explicit
retained-run06 disposition/event-archival permission were subsequently received;
run07 is recorded above. No hardware commands were sent for the follow-up
arithmetic itself. Strict MkDocs and `git diff --check` passed.

Latest user report replaces the probes with Tektronix P2220 at 1x and moves CH3
to the Keithley side of the START series resistor. Official P2220 instructions
establish 1x compensation for 1 Mohm inputs and nominal 6 MHz bandwidth; actual
probe/channel response and a new pin-side trace have not been measured. The
CH4 resistor-only connection is documented in the calibration procedure; its
physical installation, settings, physical output OFF and capture readiness were
subsequently explicitly confirmed for run06 above. The initial run-06 preflight timed out opening the established
resource before any identity/state query. Its evidence contains no instrument
observations. The subsequent read-only retry and authorized acquisition are
recorded above; the failed preflight caused no hardware mutation.

The user subsequently identified Agilent 54831B / P060-S, CH1 READY / CH2 BUSY /
CH3 generator output before the 1 kohm START resistor, and DUT LO connected only
to FORCE LO/SENSE LO with no earth bond. The
[wiring report](evidence/k2460-2026-11-02-scope-wiring-clarification.json) qualifies
all earlier generator-edge-to-BUSY/reading calculations: no measured series-path
propagation/threshold correction establishes their physical Keithley START-pin
reference. Original raw run/review/analysis files are retained unchanged. The
BUSY-high difference cancels a common reference offset, subject to the original
scope error budget. No new pin-side trace or analog DUT measurement was obtained,
no hardware command was sent, and no timing gate was promoted. P060-S input and
compensation specifications/compatibility remain unverified. Strict MkDocs and
`git diff --check` passed for this documentation/evidence clarification.

## Independent field timing — shared measurement v3

`MeasurementRecord` now carries separate voltage/current `MeasurementAperture`
values, each with timing origin and optional boundary uncertainty. An unproved
window or availability stays `None`. Aperture timestamp references select a
known electrical field; buffer-storage and unresolved references select none.
Known storage timestamps must follow both known windows and precede known
complete-record availability. Unknown field timing requires unknown schedule
mapping. Synthetic and physical origins cannot be mixed as hardware evidence.
Digests include field timing and uncertainty. Shared v1/v2 IDs are rejected
rather than silently relabelled as v3; raw Keithley archive decoding is unchanged.

The simulator explicitly supplies identical synthetic windows with zero virtual
uncertainty. The fixed 25-field Bluesky projection exposes independent windows,
origins and uncertainties, encoding unknown numeric timing as `-1` and unknown
origin as `unknown`. Descriptor configuration identifies timestamp reference and
field. User guide, quickstart, example, contracts and changelog are aligned.

Validation: **379 tests passed**; Ruff lint/format, strict mypy (`src scripts`),
strict MkDocs and `git diff --check` passed. Tests exercise sequential/differing
windows, unresolved and storage references, unknown timing, false synthetic
provenance, storage before integration finishes, independent retained-buffer
checksums, Event Model documents and executed quickstart variants. The offline
simulator example completed 635 synthetic records with schema v3 and
`hardware_ready=False`. Packaged runtime SHA-256 stayed
`c4c40686bf2016f5c55c820b5d0bd34f78915dd10e6a77823d0a9cad245c6e82`.

The [calibration design](timestamp-aperture-calibration.md) specifies independent
electrical phase scans, per-field analysis and evidence rejection criteria.
The existing constant-resistor setup cannot identify either aperture from
START/BUSY alone. Exact scope/channel/probe details, safe electrical coupling
and grounding, and a feasible stimulus remain prerequisites. No new executable
hardware fixture was packaged, uploaded or armed, no instrument commands were
sent for this software increment, and no hardware gate or real-adapter support
was promoted. The last physical observation remains run `2026110205` at
IDLE/OFF/0 A with one retained proof reading and unchanged default buffers.

## Per-request completion watchdog

Operational backend preparation now returns a validated immutable
`PreparedAcquisition`, binding request/program digests, expected/capacity record
bounds and operational timing. The simulator compiler defines its local
completion bound as achieved quantized duration plus the complete configured
local START wait for external-start requests, separately reports the configured
one-transaction communication margin, and applies the configured buffer ceiling.
`Keithley2460Device` verifies the request digest and recomputes the effective
host watchdog after every successful prepare as the larger of its constructor
minimum and the prepared bound plus the larger of the communication margin or
two host polling periods. Explicit disposition clears prior timing values.

Contract tests cover immediate and external-start arithmetic, a run delayed
beyond a deliberately undersized constructor minimum, replacement across
successive short and long requests, malformed prepare results, and clearing a
previous value after failed preparation. Unit tests reject nonfinite or
nonpositive prepared bounds, malformed digests/counts and programs exceeding a
configured buffer ceiling. Additional contract tests reject a result for a
different request, cross-check retained request/program/capacity metadata, and
show that rejected re-preparation leaves an earlier retained acquisition
collectable under its original identity. The living quickstart now relies on
this prepare contract rather than passing a manually derived per-request timeout
to the device. These are offline simulator/host-contract results; a future
target adapter must provide independently reviewed bounds from its own
commissioned runtime. The complete offline suite reports 359 passing tests.

## Recovered-IDLE retained-buffer retrieval

The host controller now permits bounded `read_buffer_chunk()` calls after
`recover()` has safely returned the runtime to output-OFF IDLE. This matches the
retention contract and the instrument-side guard, which already permits reads
only while the trigger model is inactive and output is off. PREPARED,
WAITING_START, RUNNING and output-on IDLE are rejected before querying buffer
metadata or records. Retrieval remains offset-bounded, deterministic and
non-destructive; IDLE does not alter the original acquisition outcome or imply
durable archival.

Focused tests exercise a recover-then-read sequence with retained records and
verify each rejected state. The guarded M5 notebook's independent audit now
rereads every post-recovery chunk from a fresh connection and compares the
records with the checked archive. This is an offline host-lifecycle correction;
the TSP artifact and hardware capability claims are unchanged. The complete
offline suite reports 334 passing tests; Ruff lint/format, strict mypy, strict
MkDocs and `git diff --check` also pass.

## Measurement profiles commissioned on the resistor

Runtime v14 adds three versioned K2460 commissioning profiles at 1, 0.1 and
0.01 NPLC. Each fixes the voltage measurement range at 0.2 V, disables
autorange, filtering and relative measurement, and performs one autozero while
output is off before leaving autozero disabled. The host sends only an
allowlisted integer selector and queries back the applied code, NPLC, range and
four switch states before accepting PREPARED. `CurrentHoldAcquisitionProof`
rejects strings, arbitrary settings and voltage limits above the fixed range.

Raw archive v3 records the versioned profile identity. Its allowlisted parser
continues to read v2 and v1 archives as the original 1 NPLC policy. Focused
runtime/archive tests pass for all selectors, query-back mismatch, timestamp
proof pinning, checksums and legacy decoding. The profile selector was added to
the guarded M5 notebook; the timestamp notebook is explicitly pinned to 1 NPLC.
The complete offline suite reports 329 passing tests; Ruff lint/format, strict
source mypy, strict MkDocs and `git diff --check` also pass.

The first intended v13-to-v14 target replacement did not begin: two attempts to
open the VISA raw socket failed before any command was sent. Connectivity was
later restored without rebooting. Read-only preflight found the exact v13
runtime IDLE, trigger model idle, output OFF, programmed level 0 A, an empty
buffer, and zero unread warnings/errors. After explicit authorization, the
guarded replacement path installed the exact packaged v14 artifact with SHA-256
`c4c40686bf2016f5c55c820b5d0bd34f78915dd10e6a77823d0a9cad245c6e82`.
The installation session and an independent query-only session both confirmed
v14 IDLE, trigger model idle, output OFF, 0 A, no retained records, and zero
unread warnings/errors. No acquisition was prepared or armed during installation.

All three profiles were then exercised with immediate 256-reading acquisitions
at +1 mA and a 0.2 V limit on the front-terminal four-wire nominal 100 ohm
resistor. The fixed 0.2 V range and queried profile settings matched before each
arm. Results were:

| NPLC | Mean interval | Mean rate | Voltage population SD | Mean V/I |
|---:|---:|---:|---:|---:|
| 1 | 40.8206 ms | 24.497 Hz | 0.982 µV | 102.4960 Ω |
| 0.1 | 4.83012 ms | 207.034 Hz | 275.822 µV | 102.5000 Ω |
| 0.01 | 1.21776 ms | 821.183 Hz | 302.250 µV | 102.5332 Ω |

Every run produced exactly 256 strictly ordered timestamps, source status 200
for every record, and the documented measurement-status pattern 264 for the
first record then 8. Warning/error counts remained zero and output OFF/0 A was
confirmed after each run. Each v3 archive decoded successfully and a fresh
session matched all retained live records to it before any authorized discard.
Each instrument buffer was then discarded only after separate user
authorization; a final fresh session confirmed v14 IDLE, trigger model idle,
output OFF, 0 A, an empty buffer, and zero warnings/errors.
The complete comparison and archive checksums are retained in
[`k2460-2026-10-12-measurement-profile-benchmark.json`](evidence/k2460-2026-10-12-measurement-profile-benchmark.json).

These rates characterize paired source-readback/voltage acquisition on this
firmware and setup; they are not guaranteed maxima. The uncalibrated resistor
does not establish absolute accuracy, and first-reading-relative buffer time
does not establish external START or aperture timing. No timestamp or
operational Ophyd-adapter hardware gate is promoted by this benchmark.

## 10,000-record fill-once and transfer proof

The same confirmed resistor setup completed an immediate +1 mA acquisition at
0.01 NPLC into an exact 10,000-record standard fill-once buffer. Host-observed
acquisition time was 12.181 s; buffer-relative timestamps increased strictly
across 12.17023136 s, corresponding to 821.595 paired I/V records/s. The full
extent was 1–10,000 with no wrap. Retrieval used bounded 4096+4096+1808-record
chunks, and retrying the first chunk was identical.

The v3 archive decoded equal to the in-memory record set. Before recovery, a
fresh VISA session reread and matched every retained record. Source status was
200 throughout; measurement status was 264 on the first record and 8 thereafter.
Warning/error counts remained zero. After recovery retained all records at
IDLE/OFF/0 A, explicit user authorization permitted discard; another fresh
session confirmed an empty 10,000-record-capacity buffer and the same safe state.
See the [archive](evidence/k2460-2026-10-12-capacity-10000-archive.json),
[summary](evidence/k2460-2026-10-12-capacity-10000-evidence.json), and
[discard record](evidence/k2460-2026-10-12-capacity-10000-discard.json).

This is strong bounded fill-once/no-wrap and multi-chunk transfer evidence, but
it does not establish the configured 250,000-record ceiling, calibrated
accuracy, external-START/aperture timing, clock-to-epoch mapping, or a complete
G03/G04/G05/G06 gate.

## Rejected external-START timestamp attempt

On 12 October 2026 the front-terminal four-wire nominal 100 ohm resistor and
physical output-off state were reconfirmed. Read-only identity/TSP/output checks
passed at `169.254.113.151`, and the exact v13 volatile runtime was installed
at IDLE/OFF/0 A with an empty buffer. The first arm was correctly rejected
because START read high; force-safe plus recovery returned IDLE/OFF/0 A.

A second attempt reached WAITING_START/OFF/READY, but the generator transitioned
before the operator had a scope or logic analyzer ready. The run completed one
reading and reported `start_overrun=true`. At 1.000013 mA it measured
102.512836 mV; the raw source and measurement statuses were 200 and 8. The four
run-tagged instrument event markers were captured, but there was no independent
physical START/BUSY trace. The attempt is therefore explicitly rejected for
timestamp validation. Its checksummed diagnostic record is
[`k2460-2026-10-12-timestamp-proof-rejected.json`](evidence/k2460-2026-10-12-timestamp-proof-rejected.json).
Only after that record was verified was the single retained reading explicitly
discarded; IDLE/OFF/0 A and an empty buffer were confirmed. G03/G06 remain open.

## Available-sequence assessment

`docs/available-sequences.md` maps twelve proposed excitation sequences plus
PITT-style voltage steps onto the current intent, compiler, simulator, default
Flyer, and target-hardware boundaries. It identifies direct examples, bounded
uniform-grid prototypes, missing generator provenance, and the cases that would
benefit from finite nonuniform segment composition. It also records phased
plans for a separate example gallery and later operational commissioning.

This is documentation and implementation planning only. No program model,
runtime, target adapter, hardware capability, or acceptance gate changed.
Strict MkDocs and `git diff --check` passed after adding the page and navigation.

## Timestamp-proof fixture prepared offline

Repeat `2026110205` used the same one-reading +1 mA / 0.2 V / 1 NPLC proof,
with the unchanged physical setup freshly confirmed. After the missed capture
on run `2026110204`, the scope was confirmed waiting in Single on CH2 BUSY rising
before instrument arm, with a 100 ms window and 10–20 ms pretrigger. The user
explicitly authorized discard of only the archived run `2026110204` reading,
the repeat, event archival and recovery. Archive checksum and fresh record
match passed before discard. The runner observed LOW, armed at
WAITING_START/READY high/BUSY low/output OFF, and completed at block 17 with
START/timer-overrun false. The record is 1.0000133188 mA source readback,
102.50782967 mV, statuses 200/8. Nine events (seven information, two mode-change
warnings 1811, no errors) were archived immediately; four unique monotonic
markers give a 26.58 microsecond BUSY bracket, 41.01964 ms measurement block and
buffered timestamp 0.11454 ms before POST_MEASURE. Fresh-session and
retained-IDLE reads matched; final state was IDLE/OFF/0 A with one new retained
record and zero unread events. Default-buffer counts/extents/capacities stayed
unchanged. The user subsequently reported START-to-BUSY rise **5.009(1) ms**
and fall **50.311(1) ms**, giving BUSY-high duration 45.302 ms. With the
parenthetical interpreted as 0.001 ms uncertainty, the marker-bracketing model
gives a conditional START-to-buffer-timestamp interval of
**48.35098–48.37956 ms**, approximately 48.365 ms. Confidence/coverage and full
scope/channel/threshold error budget are unknown; actual sample rate/settings
were not supplied. No trace is exported to the repository, and actual save on
the scope is not independently confirmed. The rise delay differs by 3.733 ms
from run `2026110203`, consistent with the proof model's 10 ms START polling
loop in blocks 3–6; that code-based interpretation does not establish a cause
or latency distribution. No individual aperture, calibrated timing accuracy or
hardware gate is established. No hardware commands were sent for the scope
analysis. Evidence: [raw repeat](evidence/k2460-2026-11-02-timestamp-attempt-05.json),
[checksum-linked review](evidence/k2460-2026-11-02-timestamp-attempt-05-review.json),
and [scope analysis](evidence/k2460-2026-11-02-timestamp-attempt-05-scope-analysis.json).

On 2 November 2026, repeat `2026110204` followed fresh user confirmation of
the unchanged nominal 100 ohm front-terminal four-wire resistor, physical
output OFF and START/scope setup. Query-only preflight confirmed v14
IDLE/OFF/0 A, one retained record and no unread events; the idle START-overrun
flag had become true after later continuous-generator edges. The user explicitly
authorized disposition of only the archived run `2026110203` reading, one
+1 mA / 0.2 V / 1 NPLC repeat, event archival and recovery. Archive checksum
and fresh retained-record match passed before that single-record discard.
The guarded procedure waited for a fresh HIGH-to-LOW transition, armed at
WAITING_START/READY high/BUSY low/output OFF, and completed at block 17 with
START/timer-overrun false. The new record is 1.0000148322 mA source readback,
102.4916172 mV, source/measurement statuses 200/8. Nine events (seven information,
two mode-change warnings 1811, no errors) were saved as consumed. Four unique
monotonic markers give a 23.88 microsecond BUSY bracket, 41.01732 ms measurement
block and timestamp 0.11626 ms before POST_MEASURE. Independent-session and
retained-IDLE reads matched. Final observation was IDLE/OFF/0 A, one new retained
record and zero unread events. All default-buffer counts/extents/capacities
remained unchanged, including 12,867 records in `defbuffer1`; contents were not
compared. The user reported missing the scope trigger when selecting Single, so this
run has no scope timing evidence. The scope is an older Agilent Infinium
(exact model unspecified), with waveform retention reported possible only on
the scope. This is preserved in the
[scope status](evidence/k2460-2026-11-02-timestamp-attempt-04-scope-status.json).
No individual aperture or hardware gate is established. Evidence:
[preflight](evidence/k2460-2026-11-02-timestamp-attempt-04-preflight.json),
[raw repeat](evidence/k2460-2026-11-02-timestamp-attempt-04.json), and
[checksum-linked review](evidence/k2460-2026-11-02-timestamp-attempt-04-review.json).

On 2 November 2026, clean-edge attempt `2026110203` repeated the one-reading
proof with explicit authorization to discard only the archived diagnostic
reading and archive the new run's events. Archive checksums and fresh-session
record equality were verified before discard. The user confirmed a 33120A
continuous 0.01 Hz, 50% square wave through 1 kohm to START pin 3 / ground pin 9,
75 mV LOW, 4.5 V HIGH, a clean edge within input limits, and START/BUSY capture.
The guarded procedure waited for LOW, then reached WAITING_START/READY high/
BUSY low/output OFF. The next rising edge completed one reading at block 17
with START-overrun and timer-overrun both false. Output returned OFF locally.
The record contains 1.0000137845 mA source readback, 102.48708725 mV and
source/measurement statuses 200/8. Nine events (seven information, two expected
mode-change warnings 1811, no errors) were archived immediately. All four unique
tagged markers are monotonic. A fresh session matched the raw record and absolute
timestamp; recovery reached IDLE/OFF/programmed 0 A and retained-IDLE retrieval
matched the same new reading. Default-buffer extent/capacity stayed unchanged
at 12,867 records in `defbuffer1` and zero in `defbuffer2`. Final unread event
counts were zero. This record was later explicitly dispositioned for run
`2026110204`, after archive checksum verification and fresh record matching. Evidence is
[`k2460-2026-11-02-timestamp-attempt-03.json`](evidence/k2460-2026-11-02-timestamp-attempt-03.json),
with a checksum-linked
[instrument review](evidence/k2460-2026-11-02-timestamp-attempt-03-review.json).

The user reported START-to-BUSY rise as **1.276(1) ms**, interpreted as
1.276 ms with 0.001 ms uncertainty; confidence/coverage and a full scope/
threshold/channel error budget were not supplied. Falling timing was lost when
the scope buffer cleared; no exported trace is available. It is not an input
to the current timestamp calculation. The BUSY marker bracket is 22.28
microseconds; measurement-block span is 41.03528 ms. The buffered timestamp is
40.91820 ms after PRE_MEASURE and 0.11708 ms before POST_MEASURE. Combining
the marker-bracketing model with the reported scope timing gives a conditional
START-to-buffer-timestamp interval of **44.62756–44.65184 ms**, approximately
44.64 ms. This is a calculated interval under stated assumptions, not a
validated total timing-accuracy claim. Scope observations and calculation are
preserved in
[`k2460-2026-11-02-timestamp-attempt-03-scope-analysis.json`](evidence/k2460-2026-11-02-timestamp-attempt-03-scope-analysis.json).
The nominal 20 ms integration setting does not establish the source-readback
and DUT aperture positions or a shared aperture. This is a clean single-run
START observation with retained-data and timestamp-marker evidence; complete
START-race, timestamp-reference/aperture and hardware gates remain open. Later
edges from the still-running square wave may change the historical overrun
flag, so the clean flag claim is scoped to the recorded terminal/recovery
observations.

On 2 November 2026, authorized timestamp attempt `2026110201` prepared with
four-wire remote sense but was rejected before trigger initiation because
START was HIGH. Query-only inspection confirmed output OFF and an empty proof
buffer. Force-safe/recovery reached IDLE/OFF/0 A. After the user drove START low,
a query confirmed LOW and the user authorized archival of the rejection's
warning 1811 and error -286. These diagnostic events are preserved in
[`k2460-2026-11-02-start-low-diagnostics.json`](evidence/k2460-2026-11-02-start-low-diagnostics.json).

Attempt `2026110202` reached WAITING_START/READY high/BUSY low/output OFF and
completed one reading at terminal block 17 with output OFF. It reported
`start_overrun=true`, so no clean timing or timestamp-reference claim is made.
The raw record contains 1.0000156472 mA source readback, 102.48732567 mV and
source/measurement statuses 200/8. Absolute timestamp and configured 1 NPLC /
50 Hz are archived in
[`k2460-2026-11-02-timestamp-attempt-02.json`](evidence/k2460-2026-11-02-timestamp-attempt-02.json).
A fresh session matched the record and timestamp; recovery reached IDLE/OFF/
programmed 0 A and retained-IDLE retrieval matched the same record. The verified
audit is
[`k2460-2026-11-02-timestamp-attempt-02-audit-verified.json`](evidence/k2460-2026-11-02-timestamp-attempt-02-audit-verified.json).
The earlier audit file preserves a host assertion failure caused by comparing
a tuple against a JSON list; it stopped before recovery. Both persisted records
match exactly. Default-buffer count, index extent and capacity remained
unchanged at 12,867 records in `defbuffer1` and zero in `defbuffer2`. After
explicit user authorization, all seven information events and one warning were
archived in sequence in
[`k2460-2026-11-02-timestamp-attempt-02-events.json`](evidence/k2460-2026-11-02-timestamp-attempt-02-events.json).
All four unique timestamp markers were present and monotonic. Final unread
event counts were zero; the proof reading and default-buffer extents matched
before/after retrieval. The warning was the digital-mode change warning 1811;
information event 2753 reported multiple blocks waiting on the same event and
a model not optimized for speed.

The user reported approximately 1.8 ms START-to-BUSY-rise, 47.1 ms
START-to-BUSY-fall, and 560 microseconds START-edge fuzziness. The edge was one
intended DC-voltage-setting transition on the signal generator, held high
through and after completion. No raw scope trace was supplied. BUSY-high time
is therefore approximately 45.3 ms. Instrument markers bracket BUSY assertion
over 28.58 microseconds and the measurement block over 41.02474 ms. The buffered
timestamp is 40.91342 ms after PRE_MEASURE and only 0.11132 ms before
POST_MEASURE. This is evidence that the timestamp lies near the end of the
operation; it does not prove either electrical aperture's start or end.

Using the full 560 microsecond fuzzy-edge width as a provisional plus/minus
uncertainty gives a conditional START-to-buffer-timestamp interval of
44.58650–45.73508 ms. That uncertainty is an explicit analysis assumption,
without a characterized total scope/threshold/channel error budget. Configured
1 NPLC at 50 Hz corresponds to a nominal 20 ms integration setting, not the
total paired-record duration. The official
[2460 reference manual](https://download.tek.com/manual/2460-901-01C_Sept_2019_Ref.pdf),
sections 14-183–184, places source readback immediately before the DUT
measurement, so the current and voltage must not be assumed simultaneous or
given an identical proved aperture. Section 14-218 defines digital input
overrun as an ignored event while the detector is already detected. A generator
DC-setting transient/threshold recrossing is a plausible explanation for the
flag, but its cause has not been established. The review and artifact checksums
are in
[`k2460-2026-11-02-timestamp-attempt-02-review.json`](evidence/k2460-2026-11-02-timestamp-attempt-02-review.json).
This remains reviewed diagnostic evidence. Its proof reading was initially
retained and matched independently, then explicitly discarded after archive
verification under the user's authorization for attempt `2026110203`. The
subsequent clean repeat is recorded above; justified aperture mapping remains
open and no acceptance gate is promoted.

On 2 November 2026, query-only resumption found the commissioned identity/TSP
mode, output OFF at 0 A and an empty trigger model, but no loaded runtime.
The user confirmed physical four-wire resistor wiring and explicitly authorized
packaged volatile v14 installation only. Installation and a separate fresh
query-only session verified the expected ABI/build/digest-named script, runtime
IDLE, output OFF at 0 A, an empty trigger model and no allocated runtime buffer.
The pre-existing `defbuffer1` count (12,867), index extent and capacity (100,000)
were unchanged; `defbuffer2` remained empty. Record contents were not retrieved
or compared. Event counts remained two information, zero warnings and zero
errors, without event consumption. No preparation, arm or acquisition occurred.
The instrument's two-wire setting was preserved; the timestamp proof must select
remote sense for the confirmed physical four-wire wiring. Installation evidence
is [`k2460-2026-11-02-runtime-v14-install.json`](evidence/k2460-2026-11-02-runtime-v14-install.json).
This is installation/state evidence only and promotes no timing or hardware
acceptance gate.

Runtime v14 retains the v13 timestamp fixture, and
`notebooks/keithley_2460_timestamp_proof.ipynb` provides a guarded
one-reading proof that brackets BUSY assertion and digitization with uniquely
tagged instrument event-log timestamps. It retrieves the buffered absolute
timestamp, NPLC and detected line frequency only after a terminal/output-off
check, and combines them with a user-entered scope measurement from physical
START to BUSY. Informational-event retrieval is a separately enabled consuming
step and all retrieved entries are included in an exclusive-create JSON record.

Focused unit tests cover command serialization, parsing, run-ID selection,
clock arithmetic and bounded START-time intervals. This is offline fixture
validation only. It does not establish the target timestamp convention,
START-to-aperture latency or G03/G06 until actual trace/evidence review.

## Living user quickstart

`docs/user-guide.md` documents the commissioned configuration, immutable
per-run intent, planned measurement profiles, Ophyd Flyer lifecycle, paired I/V
records and retained-data behavior. The companion simulator-only notebook
`notebooks/ophyd_electrochemistry_quickstart.ipynb` provides selectable
unipolar-current, voltage-pulse, and charge-balanced alternating-current
programs. Each executes through a real RunEngine, produces 20 paired records,
finishes COMPLETE with output false and creates an interactive two-panel Plotly
figure with the command trace on the matching source axis.

Contract tests execute every code cell with all three selections and check the
terminal safe state, paired electrical fields, separate source/measurement
status words, zero commanded charge for the balanced program, and visualization
output. Voltage-pulse support here is planning/simulation evidence only; the M6
default allowlist and target runtime remain unchanged.

## First M6 classic-Ophyd Flyer slice

`Keithley2460Device` now implements the finite Flyer lifecycle over a typed
backend returning the fixed M5 schema, metadata and record chunks. It uses real Ophyd Status
objects, background completion monitoring, explicit external-START armed
semantics, distinct abort/acquisition outcomes, bounded stop, recovery,
terminal offset-chunk collection, snapshot readback, configuration metadata and
explicit retained-data disposition. The allowlist accepts `GalvanostaticHold`
and `CurrentPulseSequence`.

Contract cases run holds and two-pulse trains through a real Bluesky RunEngine
for both start modes and validate Event Model documents plus all 18 fixed data fields.
They also cover partial records after abort, failed completion with successful
shutdown, terminal snapshot readback, no-START timeout with an empty collectable
buffer, recovery, and rejection of unsupported CV before output. Ruff, strict
source mypy and strict MkDocs pass.
The RunEngine cases use two-record chunks for a four-record acquisition and
verify the exact `(0, 2)` then `(2, 2)` retrieval sequence. Chunk size is the
only allowlisted runtime configuration field and is bounded to 4,096 records.
The pulse cases prove that one external START releases the complete locally timed
train. A separate case proves two trigger-per-pulse `count=1` acquisitions with
distinct acquisition IDs, explicit retained-data disposition, START deassertion
and re-arming. Multi-edge operation within one arm is not implemented or claimed.

The independent simulator now interprets START as a physical HIGH/LOW level and
applies the configured `rising`, `falling` or `either` edge. Contract cases cover
both either-edge directions, directional inactive-level enforcement before arm,
an inert static level, ignored return transitions after leaving WAITING_START,
and immediate-mode recovery independent of the unused START level. The selected
backend edge is published as read-only Ophyd Device configuration. These are
software semantics, not new digital-I/O hardware evidence.

This slice uses only the independent simulator backend. No adapter from the raw
2460 buffer is provided because its first-reading-relative timestamp does not
yet establish actual START or aperture timing. No hardware gate is promoted.

## M3 bounded transport and initial read-only commissioning

Implemented an optional, lazy-loaded PyVISA backend with explicit resource,
backend, LF/CRLF framing, finite open/I/O timeouts, bounded chunks and hard
command/response byte limits. Whole write/read transactions share one lock. Any
timeout, partial write, malformed or oversized reply invalidates the session.
Mutating writes are never retried and uncertain delivery raises a dedicated
ambiguity error. Arbitrary runtime upload remains disabled; M4 permits only the
exact packaged source/ABI/digest while in TSP mode with output confirmed OFF.

Thirty-two mocked transport tests cover concurrent callers, connection failure,
timeout classification, missing termination, non-ASCII/empty/oversized replies,
partial mutation writes, exact identity/language parsing, wrong model/mode,
session invalidation, evidence fields and the disabled runtime path. The complete
suite now has 212 passing tests on macOS/Python 3.12.13. Ruff lint/format, strict
mypy, locked dependency validation and strict MkDocs also pass.

The diagnostic was then run on 5 October 2026 using PyVISA 1.16.2 and pyvisa-py
0.8.1 against both explicit resources. Raw socket and VXI-11 returned identical
identity `KEITHLEY INSTRUMENTS,MODEL 2460,04686198,1.7.16a` and `SCPI` language.
Only manual-reviewed query-only `*IDN?`, `*LANG?`, and `:OUTPut:STATe?` commands
were sent. Both sessions reported source output `0` (OFF). Exact reports are retained in
`docs/evidence/k2460-2026-10-05-connectivity.json`.

This verifies initial reachability, framing, identity, and instrument-reported
output state only. The DUT was the user's 3 V coin cell rather than a characterized
dummy load; no source, measurement, transfer-abort/starvation, shutdown, timing,
electrical safety, isolation, or OFF-mode loading behavior was tested. All hardware
gates remain NOT RUN.

## Preliminary 100 ohm resistor smoke test

After the coin cell was removed, a user-supplied nominal 100 ohm resistor was
connected to the front terminals with four-wire sense. The user confirmed the
physical output indicator was off. The reviewed SCPI sequence configured current
source and voltage measurement without reset, used source readback and remote
sense, and imposed the instrument's minimum 0.2 V current-source voltage limit.

At 100 µA, source readback was 99.99917 µA and measured voltage was 10.27817 mV
(102.7826 ohm). At 1 mA, source readback was 0.9999969 mA and measured voltage
was 102.5312 mV (102.5315 ohm). Neither point tripped the limit. Output OFF was
confirmed after each point; cleanup programmed 0 A, and a separate query-only
session confirmed output `0` and programmed current `0`.

The guarded notebook and machine-readable record are
`notebooks/keithley_2460_resistor_smoke.ipynb` and
`docs/evidence/k2460-2026-10-05-resistor-smoke.json`. Because the resistor was
not calibrated, these values are plausibility evidence rather than accuracy
acceptance. No abort, disconnect, transfer fairness, timing, OFF-mode impedance,
or failure path was tested, so every hardware gate remains NOT RUN.

A follow-up bipolar sweep took five readings at each of six setpoints from
−1 mA to +1 mA. All 30 readings had the expected polarity and no limit trip.
Linear regression of four-wire voltage against measured current gave
102.502310 ohm slope, −33.020 µV intercept, R-squared 0.999999999914, and a
1.252 µV worst absolute residual. Output OFF was confirmed between points and
again from an independent session. The full record is
`docs/evidence/k2460-2026-10-05-resistor-bipolar-sweep.json`, with reproduction
in `notebooks/keithley_2460_resistor_bipolar_sweep.ipynb`; the same
uncalibrated-load and untested-failure-path limitations apply.

The buffer/cleanup procedure in `notebooks/keithley_2460_buffer_cleanup.ipynb`
was first exercised offline, then run against the resistor. It recovered all 20
five-field records and observed 0.999998004 mA mean source readback,
102.489834 mV mean voltage, and an 82.5695 ms mean timestamp interval. The
deliberate host exception, outer cleanup, and independent new session all
confirmed output OFF and 0 A. A unique temporary buffer was deleted and the two
pre-existing, user-reviewed LAN errors remained unchanged. Full data are in
`docs/evidence/k2460-2026-10-05-buffer-cleanup.json`. Process death, Ethernet
loss, and instrument-local watchdog behavior remain untested.

## M4 finite-hold runtime and target installation

Implemented the M4 proof runtime with ABI `oe-k2460-m4-hold-v1`. The
PyVISA loader rejects arbitrary source, verifies the exact packaged digest,
requires TSP and output OFF, serializes the complete `loadscript` transaction,
does not save to nonvolatile memory, verifies ABI/build afterward, and never
retries an ambiguous partial upload.

The packaged TSP constructs finite TriggerFlow paths for immediate current hold
and external START. Both normal and start-timeout terminal paths contain explicit
source-OFF and flag-deassertion blocks. Programmatic abort separately aborts the
model, disables its timer, switches output off, deasserts flags, and programs
0 A. Hard runtime bounds are 10 mA, 2 V, and 60 s; the host further applies the
explicit directional-current, voltage-envelope, and power limits. External
ABORT, measurement-derived cutoffs, acquisition buffers, general program
lowering, and ophyd integration are intentionally absent.

Offline unit tests cover artifact identity/digest, exact source framing,
arbitrary-source rejection, SCPI/output-ON rejection, existing digest reuse,
parameter/safety bounds, strict status parsing, command construction, and
abort/force-safe/recovery confirmation. The source structure is checked for
local TriggerFlow timing and explicit ON/OFF blocks without a blocking host/TSP
`delay()` call.

The target was changed to TSP from the front panel and rebooted. The exact
blank-free wire artifact compiled, installed in volatile memory, initialized,
and returned typed `idle` status with an empty trigger model and output OFF.
Reusing the matching script did not rerun its top level. No hold was prepared or
armed and no sourcing occurred. Evidence is retained in
`docs/evidence/k2460-2026-10-05-m4-runtime-install.json`.

Live diagnosis found firmware-specific failure modes for blank `loadscript`
messages, a 70-character full-digest script name, and overwriting globals from a
prior diagnostic script. The loader now uses the exact blank-free 301-line wire
form, a 30-character name containing a 96-bit digest prefix, paced writes, a
compile barrier, and a clean-global precondition. The host still verifies the
full SHA-256. External START's clear/initiate/READY boundary remains a specific
G01 race to measure; local process-kill, network-loss, timeout, abort latency,
and electrical OFF behavior remain G02/G07 evidence. No hardware gate is
promoted by installation alone.

Responsive obsolete runtimes were subsequently replaced without reboot by
requiring an idle/empty trigger model, output OFF and 0 A, issuing force-safe,
deleting the owned volatile script, setting its known globals to `nil`, and
verifying absence plus OFF/0 A before installing the new digest. Reboot remains
the recovery for a script that is stuck running and cannot accept cleanup.

The first target prepare exposed unsupported `math.huge` before arming or
sourcing; force-safe left output OFF and 0 A. With the dialect fix, a +1 mA,
0.2 V-limit, 250 ms immediate hold progressed through PREPARED, RUNNING local
delay block 3, COMPLETE terminal block 6, and recovery to IDLE/OFF/0 A. Warning
1808 from reading READY/BUSY output pins was removed by tracking commanded
levels in software. A second identical hold added no warnings or errors, and an
independent session confirmed the final safe state. See
`docs/evidence/k2460-2026-10-05-m4-immediate-hold.json`. No electrical sample or
independent timing trace was collected, so G01/G02/G07 remain NOT RUN.

On 2026-10-09, the same runtime and resistor setup exercised programmatic abort
during a 5 s maximum +1 mA hold. The host observed RUNNING/output-on at delay
block 3, then confirmed ABORTED/output-off, READY/BUSY deasserted, `smu.OFF`, and
0 A. Request-to-confirmation wall time was 0.196 s. Repeated abort remained
ABORTED without an additional warning; recovery returned IDLE/OFF/0 A. The
active abort added the expected “Trigger model path 1 has been aborted” warning
and no error. The retained record is
`docs/evidence/k2460-2026-10-09-m4-programmatic-abort.json`. This is a state-path
and command-idempotence result, not an independent electrical cutoff-latency
measurement, so G02/G07 remain NOT RUN.

The first trace revealed that software-tracked BUSY remained low while the
TriggerFlow model was RUNNING. Build `m4-finite-current-hold-v2` refreshes
logical READY/BUSY from runtime state without reading output-configured pins.
After safe in-place volatile replacement, the target reported RUNNING/BUSY 1 and
ABORTED/IDLE with both flags low, without warning 1808 or a new error.

The external no-START path then exposed two pre-arm firmware differences:
trigger-mode input state reads return `nil` and post errors, while a valid
digital-input read returns symbolic `digio.STATE_HIGH`, not numeric 1. Both
attempts failed before initiation and were independently left OFF/0 A. Build
`m4-finite-current-hold-v4` samples and symbolically compares START only in
digital-input mode, then restores trigger-input mode and reports START unknown.
With the DB-9 connector unconnected and falling-edge START, WAITING_START showed
READY 1/BUSY 0/output-off; 34 polls stayed off. The two-second local timeout was
observed after 2.047 s at block 15 with flags low, no overrun, and no new warning
or error; recovery returned IDLE/OFF/0 A. Evidence is retained in
`docs/evidence/k2460-2026-10-09-m4-start-timeout.json`. Polling does not prove
absence of a brief pulse or independently validate timing, so G01/G02 remain
NOT RUN.

Programmatic abort was then issued while the same unconnected-input model was
WAITING_START at block 5, before timeout and without a START edge. READY was 1,
BUSY/output were low, and abort confirmation after 0.199 s host wall time
reported ABORTED, flags low, `smu.OFF`, and 0 A. Repeated abort added no warning;
recovery returned IDLE/OFF/0 A. Only the expected active-abort warning was added
and errors remained at six. Evidence is retained in
`docs/evidence/k2460-2026-10-09-m4-waiting-start-abort.json`; electrical latency
and READY/START boundary timing remain untested.

A retained SCPI query-only preflight reached serial 04686198 at
2026-10-05T14:25:58Z and reconfirmed firmware 1.7.16a, SCPI mode, and output OFF.
No mutating command was sent. The result is retained in
`docs/evidence/k2460-2026-10-05-m4-preflight.json`; live work stopped before the
front-panel command-set change. Later TSP preflight and installation used
`TCPIP0::169.254.113.151::5025::SOCKET`.

The complete offline suite now has 230 passing tests on macOS/Python 3.12.13.
Ruff lint/format, strict source/script mypy, lock consistency, strict MkDocs,
notebook JSON/offline artifact execution, and package metadata checks are the
required final validation set for this increment.

## M5 measurement-record foundation

The first M5 slice was implemented offline on 9 October 2026. The public
`measurement-v2` models retain explicit electrical and timing origins, raw and
actual-START-relative timestamps, aperture/availability boundaries, source
setpoint, separate source/measurement status words and schedule association.
Version 2 removes the ambiguous combined status word. Validation rejects nonfinite
values, invalid apertures, ambiguous mapping, source-function changes, timestamp
reference mismatches, record gaps and declared-capacity overruns.

Frozen buffers bind records to acquisition, request and compiled-program IDs.
Full-buffer and per-chunk SHA-256 values cover canonical JSON records. Chunk
retrieval is addressed by immutable offset rather than an advancing cursor; a
simulated lost reply can therefore be retried without skipping samples. The M2
adapter marks both electrical values and timing as synthetic and preserves an
empty no-START terminal outcome without inventing a START timestamp.

The narrow target increment adds an immediate-only, maximum-eight-reading TSP
path backed by a 16-record fill-once buffer. Three +1 mA records on the nominal
100 ohm front-terminal four-wire resistor contained source readback, measured
voltage, buffer-relative time and both status words. The same offset read was
identical on retry. Source status confirmed readback, four-wire sense and output
on during each aperture; measurement status identified front terminals and the
first-reading-in-group marker, with no questionable bit. Mean V/I was 102.5112
ohm. Warning/error counts were unchanged. Recovery and an independent session
confirmed IDLE/OFF/0 A while the records remained retained; after archiving,
explicit discard left the buffer empty and the source safe. The exact record is
[retained](evidence/k2460-2026-10-09-m5-buffer-proof.json).

Validated locally with Python 3.12.13: 270 tests pass. Ruff lint/format, strict
mypy, notebook JSON/code compilation, and strict MkDocs also pass. The target
proof does not map first-reading-relative timestamps to actual START, establish
capacity/no-wrap behavior, calibrate the resistor, export the shared archive, or
complete hardware gates G03/G04/G06.

The follow-up raw-archive slice validates contiguous chunk assembly, versioned
plain-data encoding, independent record and payload checksums, allowlisted
round-trip parsing, UTC/timestamp-origin metadata, tamper rejection and
exclusive-create/no-overwrite output. On target, the maximum eight-reading proof
was retrieved as 3+3+2 chunks and reloaded from the checked archive before an
independent retention audit and explicit discard. The instrument ended
IDLE/OFF/0 A with an empty buffer. This still does not exercise the full
16-record capacity or wrap behavior.

The separate output-off capacity probe allocated an empty 5,110,784-record
standard buffer with `buffer.make(0, ...)`, then deleted it and reclaimed its
memory. Existing buffer capacities/content, warning/error counts, runtime state,
output and source level were unchanged. This demonstrates available allocation
on the commissioned firmware/configuration; it does not demonstrate filling,
wrap prevention, sustained acquisition or large-data transfer.

Runtime build v6 replaces the earlier fixed limits with exact-sized fill-once
allocation, separate configured host allocation/transfer ceilings, and hard TSP
backstops of 5,000,000 records per acquisition and 4,096 records per transfer.
The exact v5 artifact was replaced only after proving its buffer empty and the
source IDLE/OFF/0 A. The target then filled an exact 17-record buffer, returned
10+7-record chunks, and reproduced the first 10-record chunk on retry. The raw
archive passed both checksums and round-trip decoding. A new session confirmed
all 17 records retained at IDLE/OFF/0 A before explicit discard left the source
OFF/0 A and the 17-record buffer empty. Warning/error counts stayed at 5/6.
This validates the old-boundary crossing, not the configured 250,000-record
ceiling, sustained acquisition, or large transfers.

Validated locally with Python 3.12.13: 274 tests pass. Ruff lint/format, strict
source mypy, notebook JSON/code compilation, and strict MkDocs also pass.

## Manual external-START runtime proof

Runtime v10 adds native either-edge START, keeps that input in trigger mode while
arming, and exits the successful TriggerFlow path explicitly through block 0.
On the commissioned 2460, removal of a held 1 kohm line-3-to-pin-9 pull-down after
READY produced WAITING_START/OFF, RUNNING/ON/BUSY, source OFF, and COMPLETE at
block 12. Recovery confirmed IDLE/OFF/0 A and warning/error counts remained 2/2.
The exact trace is retained in
[the v10 evidence](evidence/k2460-2026-10-09-m4-external-start-v10.json).

Validated locally with Python 3.12.13: 280 tests pass. Ruff lint/format and
strict source mypy pass. The manual trace does not independently measure
electrical timing or promote G01; race-boundary and repeated/fault cases remain.

## External-START buffered acquisition

Runtime v11 extended the M5 buffer path with the proven either-edge START wait,
READY/BUSY signaling, local timeout and explicit successful exit. Its first
five-reading target attempt exposed a small-count allocation defect inherited
from v6: `buffer.make(5, ...)` did not satisfy the runtime's exact-capacity
assumption because this instrument applies a 16-record minimum. The attempt
retained no readings and ended with output off; two error events were retained.

Runtime v12 allocates at least 16 physical slots while keeping the requested
digitize count and host/TSP ceilings unchanged. The corrected run reported
WAITING_START/OFF/READY until the user removed a held 1 kohm line-3-to-pin-9
resistor. It then reported RUNNING/ON/BUSY at digitize block 10 and
COMPLETE/OFF at block 13. Five records filled extent 1–5 of capacity 16. Their
source statuses were 200, measurement statuses were 264 then 8, and mean V/I
was 102.5105 ohm. Retry retrieval was identical and the v2 archive round-tripped
with record SHA-256
`a79b44b102dd7a90917c64d734f302fe1db411d9bea4add45f8035dd7760ded2`
and payload SHA-256
`ac539ebd333a6356bd01ce1ea4d3833161603456b1ccc85876dc68fefdfb3fb0`.
The successful run added no warnings or errors. Recovery and a new session
confirmed IDLE/OFF/0 A with the records retained; explicit post-archive discard
left the buffer empty. Evidence is retained in
[the run record](evidence/k2460-2026-10-09-m5-external-start-v12-evidence.json)
and [raw archive](evidence/k2460-2026-10-09-m5-external-start-v12-archive.json).
This does not establish START-to-aperture latency, electrical timing, no-wrap at
scale, or any complete hardware gate.

Validated locally with Python 3.12.13: 286 tests pass. Ruff lint/format, strict
source mypy, notebook JSON/code compilation, archive round-trip parsing, and
strict MkDocs all pass.

The follow-up external-start scale run filled an exact 256-record buffer and
retrieved it in two contiguous 128-record chunks. The second chunk retried
identically. Sample indices were 0–255, timestamps increased strictly across
10.4123 s, mean interval was 40.8326 ms, and mean V/I was 102.5069 ohm. The
v2 archive passed record and payload checksum round-trip. Recovery and a fresh
session confirmed all 256 records retained at IDLE/OFF/0 A before explicit
post-archive discard; warning/error counts remained 2/4.

`start_overrun` became true during digitization. The model still completed once
with exactly 256 records, but the flag shows at least one additional detected
edge after the accepted START. Manual-contact bounce is plausible but unproven;
the result therefore does not count as clean single-edge or repeated-input
acceptance. Evidence is retained in
[the scale-run record](evidence/k2460-2026-10-09-m5-external-start-256-v12-evidence.json)
and [raw archive](evidence/k2460-2026-10-09-m5-external-start-256-v12-archive.json).

The proposed controlled source, an Agilent 33120A, was next checked in plain
digital-input mode without arming or sourcing. Its user-verified 1 Hz,
0.01–4.56 V square wave drove line 3 through 1 kohm. The 33120A was actually in
50 ohm display mode, so the scope measurement, not its displayed amplitude,
establishes those levels. The Keithley recognized 16
alternating HIGH/LOW transitions over eight seconds, with a 0.49994 s mean
host-observed half-period and no steady-state warning or error. The SMU remained
IDLE/OFF/0 A. The retained
[generator-input evidence](evidence/k2460-2026-10-09-generator-digital-input.json)
does not yet establish trigger-mode capture or edge timing.

With the generator held LOW, rising-only pre-arm validation passed and the
runtime reached WAITING_START/OFF/READY. A front-panel DC increase started and
completed one five-record acquisition, but `start_overrun` became true during
digitization and one warning was added. Data retry, checked archive, recovery,
independent retention audit and explicit discard all completed at IDLE/OFF/0 A.
It was later reported that the generator was in 50 ohm display mode and displayed
4.5 V for approximately five seconds before the setting was halved. This implies
a nominal unloaded target near 9 V; the actual line-3 voltage was not captured
and may have exceeded the Keithley's +5.25 V permitted input. The generator was
physically disconnected. This run is invalid as trigger-acceptance evidence and
line 3 required a functional retest before further trigger work. See the
[run record](evidence/k2460-2026-10-09-m5-rising-generator-v12-evidence.json)
and [archive](evidence/k2460-2026-10-09-m5-rising-generator-v12-archive.json).

The follow-up functional retest used a scope-verified 0.10 Hz, approximately
0.01–4.60 V square wave through 1 kohm. With source output off, line 3 recognized
LOW, HIGH and LOW; the runtime armed during the latter LOW interval. The next
rising edge completed exactly one five-record run with no START overrun through
COMPLETE and recovery. Mean V/I was 102.5025 ohm, repeated and independent
buffer transfers matched SHA-256, no error was added, and the source finished
IDLE/OFF/0 A. A later physical disconnect produced a post-run floating-HIGH edge
and set the historical overrun flag, distinguishable from the captured clean
terminal result. This is narrow functional and single-run evidence, not a full
G01 result. See the
[clean-run record](evidence/k2460-2026-10-09-m5-rising-square-clean-v12-evidence.json)
and [archive](evidence/k2460-2026-10-09-m5-rising-square-clean-v12-archive.json).

## RunEngine failed-Status teardown fix

Reproduced the user's `Future exception was never retrieved` message on local
Python 3.12.14 with locked Bluesky 1.14.6, after all six existing RunEngine tests
reported success. Earlier test counts below did not detect this teardown logging
defect. The expected failed acquisition and retained partial data were correct;
the duplicate Future diagnostic was an upstream RunEngine issue.

Updated the dependency range to `bluesky>=1.15.1,<1.16` and lock to 1.15.1,
which includes [upstream fix #1972](https://github.com/bluesky/bluesky/pull/1972).
The new subprocess regression checks the unchanged abort/retention test through
process exit under asyncio debugging. It requires both expected failure semantics
and absence of unretrieved Future/Task messages; no runtime monkey patch or log
filter is used.

Confirmed the new regression fails with Bluesky 1.14.6 in an isolated environment
and passes with 1.15.1. Validated locally with Python 3.12.14: 180 tests pass,
including the new regression, Ruff lint/format, strict source/release-helper mypy,
locked dependency check, strict MkDocs, uv build and strict Twine metadata checks.
The project retains its 3.11–3.13 CI target; the user's macOS/Python 3.14.5 setup
was not reproduced locally. All hardware gates remain NOT RUN.

The fix `.tgz` is an overlay relative to M2; package version and contract revision
remain the unreleased `0.0.0` and `0.2` respectively.

## M2 implementation validation

This increment follows M1 and adds an independent event-clock runtime, ideal
synthetic circuit, typed transaction-locked fake transport and frozen retained
records. Both start modes, source/measurement clocks, input races, idempotent
abort/recovery and host reconciliation are implemented in simulation only.

Validated locally with uv 0.12.19 / Python 3.12.14:

- 179 tests pass: 67 new M2 simulator cases plus the prior 112 M1/contract/release
  cases. Only this local Python/platform combination was executed.
- Independent expected source traces and aperture integrals, including PRBS
  equal adjacent bits, multisine through arbitrary playback, pulse baselines,
  CV cycles/endpoints, full final dwells and exact/cross-edge apertures.
- READY/stale/held/coincident input races, local START timeout, abort during wait,
  playback/aperture/overhead, deadline/sensor/buffer/cutoff faults and compliance
  provenance. Complete/partial records survive shutdown and explicit recovery.
- Coherent immutable snapshots, bounded frozen chunks, digest-checked full
  diagnostic export, explicit retained-data disposition, ambiguous mutations,
  lost replies/chunks, reconnect without START replay and bounded polling.
- Ruff lint/format, strict source/release-helper mypy, locked dependency check,
  strict MkDocs, offline simulator example, uv wheel/sdist build and strict Twine
  metadata checks. The package still uses the unreleased `0.0.0` sentinel.

Simulation does not interpret TSP or implement the operational ophyd Device,
PyVISA framing, real clocks, analog settling, paired V/I measurements or physical
shutdown latency. Reviewed packaged TSP programs remain planned; user-written TSP
execution is outside scope. All hardware gates remain NOT RUN.

The M2 `.tgz` contains only changed/new repository-relative files after M1.
The M1 validation entry below records the prior increment's coverage.

## M1 implementation validation

This increment is relative to the uv/release and waveform-contract overlays.
M1 now has structurally validated immutable SI models/configuration, first-class
PRBS/arbitrary intent, pure coherent multisine generation, canonical request
round trips and explicit-profile bounded planning compilation.

Validated locally with uv 0.12.19 / Python 3.12.14:

- 112 tests: 100 M1 unit cases plus the prior twelve RunEngine/release cases.
  PRBS checks include full-vector fixtures independently calculated from polynomial
  bit recurrences for every order 2–10, cyclic state uniqueness/balance/correlation
  and fixed seed/phase vectors. Multisine recovery uses an independent DFT.
- Timing/feasibility tests cover CV endpoint/cycle joins, finite pulse/baseline
  dwell, independent aperture mapping, source/list/record/block/byte budgets,
  cutoff cadence, unsupported modes/abort, conservative cell/power limits and hashes.
- Strict source/release-helper typing, Ruff lint/format, locked dependency check,
  strict MkDocs and local Markdown target/anchor checks.
- Offline PRBS example, isolated uv wheel/sdist build, strict Twine metadata and
  packaged resource/unit-test/example inspection.

`hardware_ready=False` is returned for every compiled plan, including declared
bench profiles. Output is finite planning IR, not TSP/TriggerFlow. Source schedules
and cutoff/abort declarations are not measured physical evidence. Firmware-specific
lowering, transport, independent simulator, Device and data acquisition were not
implemented at M1; all bench gates remained NOT RUN. Other CI platforms/Python versions
are configured, not claimed as locally tested. No remote mutation was performed.

The update archive is `.tgz`, containing only changed/new repository-relative
files after the previous overlay, without Git history or generated/environment files.

## Waveform contract update — revision 0.2

This documentation-only increment is relative to the previously delivered
uv/semantic-release overlay. It adds planned first-class PRBS, finite arbitrary
waveforms and a pure multisine generator sharing arbitrary playback, with ADR
0002, provenance, M1/M2 acceptance and hardware gate G08.

Reviewed Tektronix's function-generation brief and official 10 ms pulse example.
Configuration-list waveform generation supports the architecture; PRBS/multisine
lowering is a design inference. The simple pulse example contains no measurement.
Neither source establishes our waveform timing, paired measurement, storage,
phase fidelity or abort latency. All hardware gates, including G08, are NOT RUN.

Validation: strict locked-uv MkDocs build, local Markdown targets/anchors and
contract/gate references, whitespace and changed-files archive contents.
No Python/API/lockfile/workflow changes or new waveform execution tests are
included. The prior twelve-test result below is from the uv update, not evidence
of newly implemented waveform support. No remote mutations were performed.

## uv and release-workflow update

Baseline: GitHub `main` commit `f8e5e555228d8700fa08f1f23782237fd0bb0688`.
Compared the current McSAS3/MoDaCor pyprojects and reusable release/CI workflows.
Validated with uv 0.12.19 and Python 3.12.14:

- `uv lock --check` and locked environment creation/sync.
- Ruff lint/formatting and strict mypy checks for source and release helper.
- Twelve tests: the six original RunEngine contract checks plus six isolated
  Git/metadata release-gating tests. These never push or publish.
- Strict MkDocs build; isolated uv wheel/sdist build and strict Twine checks.
- Actionlint 1.7.12, invoked with `uvx --from actionlint-py==1.7.12.25 actionlint`,
  validates all six workflows and local reusable-workflow references.
- Disposable-checkout release preparation: actual Python Semantic Release 10.7.0
  stamps `0.1.0`, inserts its changelog section and permits a fresh matching lock.
  A local `v0.1.0` tag plus a subsequent `fix:` commit correctly previews `0.1.1`.
  Later commits with an already-tagged version do not qualify for republishing.

No remote mutation, Actions run, release creation, or publishing was performed.
The matrix targets Python 3.11–3.13 on Linux and Python 3.12 on macOS/Windows;
those additional environments are configured, not claimed as locally tested.
PyPI setup and the optional release-PR token remain repository-owner configuration.
The supplied overlay has only changed/new files at repository-relative paths.

## Source review

The official datasheet, official 2460 pulse example, Tektronix's maintained
command API, Bluesky hardware protocols, ophyd architecture, QueueServer
introduction, and PyVISA resource documentation were reviewed. The full 2460
reference PDF was unavailable to the tools used; exact runtime/manual checks
remain a hardware implementation prerequisite. See [assumptions](assumptions.md).

## Local validation environment

Python 3.12; Bluesky 1.14.6; ophyd 1.11.2; event-model 1.24.0.
The package remains a contract/interface template with no operational hardware
driver. CI targets Python 3.11–3.13; only Python 3.12 was run locally.

## Checks

- Lint and formatting check for source, examples, and tests.
- Strict type check for the 13 source modules.
- Six contract checks through a real RunEngine/test-only witness: both start
  modes, partial-event/event-page document validation, acquisition failure
  versus successful abort, UNKNOWN output on failed shutdown, and normal/aborted
  follower-plan finalization with retained readings.
- Strict documentation build and wheel/source-distribution build.
- Distribution resource inspection for typed package marker and TSP placeholder.

These checks validate API consumption and template consistency. The witness
encodes intended behavior; it is not independent proof of instrument timing,
electrical shutdown, firmware behavior, or safety. No hardware tests were run.

The initial local Git commit and `contract-v0.1` tag version the baseline. No
remote repository, GitHub Actions execution, deployment, or package publication
was performed. All listed checks passed locally; six tests passed. The deliberately
failed acquisition test also produced an upstream RunEngine `FailedStatus`
future diagnostic while its expected exception, finalizer, data, and stop-document
assertions passed. Do not confuse that intentional failure path with hardware
acceptance.
