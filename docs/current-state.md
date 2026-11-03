# Current project state

Updated: 2026-11-02

This is the concise, mutable resumption checkpoint for ongoing development.
Repository-wide working rules live in the root-level `AGENTS.md`. The original
root-level `ophyd-electrochemistry-handover.md` is an archival account of the
5 October starting point and is not an active task list. The checkout,
contracts, tests, and evidence remain authoritative.

## Implementation checkpoint

- M1–M5 and the first simulator-backed M6 classic-ophyd Flyer slice are
  implemented.
- `Keithley2460Backend` now implements a serialized timed-hold Flyer candidate
  over a separate packaged v17 artifact, now installed. It requires explicit
  `commissioning_resistor=True`, exact firmware 1.7.16a and an already-installed
  digest-matched runtime; connecting never installs/resets/changes language.
  This is an **uncommissioned resistor candidate**, with `hardware_ready=False`.
  V17's first energized case collected only one of ten requested readings and
  stopped with `cadence_overrun`; this candidate is **not usable for timed holds
  yet**. A later one-reading / 1 s-spacing diagnostic completed normally; repeated
  sampling remains failed and physical duration is unverified. The latest run-05
  reading is archived and retained. Last independently checked
  hardware state is runtime IDLE/model EMPTY/output OFF/programmed 0 A.
- Latest accepted initial scope: **basic finite current holds with timing
  explicitly uncalibrated**, with **order-of-10-ms alignment as the desired
  target**. Stop identical timing repeats and prioritize the limited target
  adapter. Shared schema v4 now represents unknown actual START and field windows
  coherently. No 10 ms accuracy bound or hardware acceptance pass is claimed.
  The existing v14 measurement routine is count-bounded: it does not implement
  `GalvanostaticHold.duration_s` or `sample_period_s`, nor local cell cutoffs.
  The user explicitly selected **specified duration and sample spacing** over
  count-bounded holds. The new path preserves both settings as instrument-local
  timer parameters; it does not infer duration from measured average rates.
  It supports exact integral duration/spacing ratios, spacing >= 0.1 s, duration
  <= 60 s and <= 600 records, with local cutoff and half-slot deadline branches.
  Sequential measurement is not preemptible: cadence, event consumption, cutoff
  boundary semantics and worst-case shutdown/protection latency remain bench gates.
- The guarded timestamp-proof notebook and runtime v14 fixture are prepared,
  but the physical proof has not been completed.
- Runtime v14 and archive v3 implement immutable, query-verified K2460
  measurement profiles at 1, 0.1 and 0.01 NPLC with a fixed 0.2 V range,
  filter/autorange/relative off and one output-off autozero. All three have
  scoped resistor rate evidence; their operational aperture timing remains
  uncommissioned.
- The target runtime has completed immediate retained-buffer acquisition through
  10,000 records and external-start acquisition through 256 records, with
  deterministic chunk retry, archival, recovery, and independently matched
  fresh-session transfer.
- Shared measurement schema v4 exposes independent voltage/current integration
  windows and explicitly distinguishes actual-START zero from first-stored-reading
  zero. Unknown START requires unknown windows, availability and program mapping;
  physical clock resolution may also be unknown. The 25-field Bluesky stream
  retains separate untouched source/measurement statuses and explicit clock/value
  origins in configuration. Older shared schema identifiers are rejected; raw
  Keithley archives and runtime v14 are unchanged.
- Exported `project_current_hold_buffer()` validates raw chunk checksums and a
  complete contiguous retained extent (including an interrupted prefix), preserves
  raw timestamps, current readback, voltage and statuses, and supplies no START,
  aperture, epoch or accuracy claim. Its run08 regression reads archived evidence
  locally, not hardware. The timed backend uses this pure conversion.
- The simulator and quickstart use the v4 projection with identical explicitly
  synthetic field windows. The aperture-calibration design is in
  `docs/timestamp-aperture-calibration.md`; no new operational hardware fixture
  is commissioned or installed.
- `VoltagePulseSequence` is available for immutable intent, canonical request
  encoding, pure compilation, and simulated execution. It remains outside the
  hardware-facing M6 default allowlist and has no commissioned target runtime.
- The installed candidate v17 artifact is exact v14 wire source plus packaged
  `tsp/timed_hold.tsp`, SHA-256
  `68b25b2e332351cc134ccdf47d04799a365e2294776f9ab30ca78f6e5408b521`.
  It sets the measurement range before its compatible source limit, rejects
  profile-incompatible compliance, and clears the stopped trigger model during
  explicit initialization/recovery. Immediate mode omits external-only blocks,
  renumbers all branch/measurement references and terminal markers, and checks
  the firmware block list before arm. Base v14's digest is unchanged. Explicit
  replacement accepts only exact known v14/v15/v16 identities, runtime IDLE,
  stopped model, output OFF, programmed 0 A and an empty proof buffer.
- The user requested a stronger CH4 signal and freshly confirmed **100 ohm / 1 W**.
  The proposed first timed case is **+5 mA / 1 V compliance / 1 s / 0.1 s spacing**,
  10 records, 10 mA source range and new `NPLC_1_2V` profile. Nominal resistor
  voltage/power are **0.5 V / 2.5 mW**; worst-case request power is 5 mW.
  The 2 V / 1 NPLC profile is uncommissioned and rejected by the old v14 count
  proof. Resistor confirmation does not confirm other wiring/output conditions
  or authorize retained-data discard, installation or arming.
- Target configuration/export preserves identity, line frequency, exact artifact,
  request/plan/config/transport provenance. Terminal transfer is bounded and
  retains partial records. Transport uncertainty requires explicit stopped/OFF
  inspection/recovery; a known model fault with output ON gets one cleanup
  shutdown attempt, never an automatic retry. Recovery retains data and outcome.
  A previously observed clean completion is not relabelled by later idle START
  overrun flags. The Flyer can collect/dispose owned data after safe recovery.
- The project uses the BSD 3-Clause License.

## Hardware and evidence snapshot

Latest lab session: **2 November 2026**. The user freshly confirmed the
front-terminal four-wire nominal 100 ohm resistor, physical output OFF and
START/BUSY scope wiring. Identity/TSP queries at `169.254.113.151:5025` matched
serial `04686198`, firmware `1.7.16a`. Runtime v14 was initially absent and was
installed only after scoped authorization. Its packaged SHA-256 is
`c4c40686bf2016f5c55c820b5d0bd34f78915dd10e6a77823d0a9cad245c6e82`.
Installation and a separate query-only session verified IDLE/OFF/0 A and
preserved default-buffer extents. Initial query/install evidence files use the
prefix `docs/evidence/k2460-2026-11-02-`.

**Latest hardware milestone: v17 one-reading duration diagnostic completed; repeated cadence remains failed.**
Fresh confirmations and explicit scoped authorizations covered each exact runtime
replacement and one bounded +5 mA / 1 V / 1 s / 0.1 s resistor capture, resulting
record/event archival, recovery and preservation of default buffers. V15 failed
OFF preparation (5077, compliance set before range). V16 corrected that order,
then failed model initiation (2709 for external-only graph islands). Both
attempts collected zero records and were recovered/archived separately.

V17 is installed, build `m4-finite-current-hold-v17`, SHA-256
`68b25b2e332351cc134ccdf47d04799a365e2294776f9ab30ca78f6e5408b521`.
Installation and an independent session verified IDLE/EMPTY/OFF/0 A, empty proof
buffer, unchanged defaults and zero events. The first v17 preparation (run-03)
was blocked before arm by the host parser missing firmware whitespace padding.
It was independently recovered OFF/0 A with zero records and two archived 5076
warnings. The host-only parser fix passed a regression using the actual captured
block list; no further upload occurred. After conclusive no-arm/zero-record safe
inspection, the same authorized single capture continued as run-04.

**Run `timed-20261102-04`: failed cadence; partial record later explicitly dispositioned for run-05.** Preparation
verified +5 mA / 10 mA source range / 1 V compliance, profile 4 / 1 NPLC / 2 V,
duration 1 s / period 0.1 s / count 10, 29 contiguous actual firmware blocks and
zero unread errors. Arm reached a terminal error at block 28, `cadence_overrun`,
output OFF. One of ten requested readings was collected:
**4.983975552 mA / 512.47859001 mV**, source/measure statuses **200/8**, raw timestamp
0 relative to first stored reading. No actual START, aperture or timestamp
resolution is established. The source signal was present, but the user reports
**"scope did not trigger"**; there is no physical pulse-width/plateau measurement.
Five authorized events were archived: three informational (2753 not optimized
for speed/multiple blocks waiting on same event, 2731 initiated, 2732 idled), two
5076 range-change warnings, no instrument errors. This is an incomplete failed
hold, not successful finite-hold acceptance. The event timestamps span about
102.5431 ms from initiation to idling; that is not calibrated physical duration.

Explicit recovery retained failure and the record. Independent final queries
verified runtime IDLE/model EMPTY/output OFF/programmed 0 A, proof buffer count
**1/capacity 16**, default extents unchanged and zero unread events. Fresh raw
retrieval matched the projected I/V/time/status/index. Export payload SHA-256 and
file checksum were independently rechecked. Default contents were not retrieved.
Evidence:

- `docs/evidence/k2460-2026-11-02-timed-v17-install.json`
- `docs/evidence/k2460-2026-11-02-timed-v17-run-03.json` (OFF parser rejection)
- `docs/evidence/k2460-2026-11-02-timed-v17-run-04.json`
- `docs/evidence/k2460-2026-11-02-timed-v17-run-04-export.json`
- `docs/evidence/k2460-2026-11-02-timed-v17-run-04-review.json`

**Latest run `timed-20261102-05`: one reading retained, do not discard.** The
user had time for one final diagnostic and freshly confirmed unchanged 100 ohm /
1 W front-four-wire setup, physical OFF and scope setup. They explicitly authorized
fresh archive checksum/record matching, discard of only run-04's reading, one
immediate +5 mA / 1 V / 1 s hold at **1 s sample spacing (one reading)**, consuming
event archival and recovery. No runtime replacement occurred. Run-04's fresh raw
chunk matched exactly and export checksums passed before its authorized discard.

Run-05 completed normally with one of one readings, **4.9842773005 mA /
512.48002052 mV**, statuses 200/8. The normal duration path was exercised without
repeating the sampling loop. All five events were archived (three information,
two 5076 warnings, zero errors). Initiation-to-idle event span was **1.00257588 s**;
this is instrument event timing, not verified physical pulse duration. Recovery
and an independent connection verified **IDLE/EMPTY/OFF/0 A**, READY/BUSY low,
**one retained reading/capacity 16**, unchanged default extents and zero events.
Fresh raw/projected equality and export payload/file checksums passed. Evidence:
`docs/evidence/k2460-2026-11-02-timed-v17-run-05.json`, its `-export.json` and
`-review.json` siblings. No physical scope values were obtained: the user reported
trigger-setting uncertainty and asked whether generator START still initiated the
run. Clarified that these holds use **immediate software start**; scope acquisition
must trigger on CH4 voltage rise or CH2 BUSY. No additional test was run or
requested. The user was told output is OFF/0 A and they can switch off the
generator, scope and Keithley. Equipment power-down is **not confirmed**.
No new acquisition or disposition is authorized. Repeated cadence and physical
timing gates remain unpromoted. The focused 63 timed-runtime unit tests passed;
no package source or runtime bytes changed for this diagnostic.

**Open design issue:** the deadline/cadence event-consumption scheme did not
complete the requested schedule. A WAIT on one block may not clear observations
held by separate branch blocks; this is a hypothesis consistent with the first
cycle result, not established firmware behavior. Review Rev. C 8-29/8-37 and
14-251/14-267..269 before revising the model. Do not disable the failing guard
merely to report success or infer physical timing from an offline model.
The inert Lua recorder verified graph construction/reachability and OFF terminal
paths only; it does not emulate event storage/reset semantics. No hardware
acceptance gate has been promoted. No new acquisition, record discard or runtime
replacement is currently authorized. All old mutating runners are completed and
must not be rerun.

The preceding v14 observation, after clean-edge attempt **2026110208**, was:

- runtime IDLE, trigger model IDLE, output OFF, programmed source level 0 A;
- front terminals and four-wire remote sense selected;
- one new retained proof record in a 16-record runtime buffer;
- `defbuffer1`: 12,867 records, indices 1–12,867, capacity 100,000;
- `defbuffer2`: empty, capacity 100,000; and
- zero unread information/warning/error events after authorized archival.

Default-buffer record contents were not retrieved or compared; their count,
index extent and capacity were preserved throughout the session. Do not clear
these records. The run `2026110208` proof reading was later explicitly
checksum-verified, independently matched and dispositioned for the v15 installation
above. Its original archive remains intact.

**Latest corrected-scope repeat, run 2026110208:** the user explicitly confirmed
CH4 1 Mohm/DC/1x at 50 mV/div with 0–200 mV visible, unchanged resistor/direct
HI–LO cable/START wiring, physical output OFF and scope waiting Single on BUSY
rise at 20 ms/div with 10–20 ms pretrigger. Explicit permission covered only
archived/matched run07 proof-record discard, one +1 mA / 0.2 V / 1 NPLC repeat,
consuming event archival and recovery. Query-only preflight verified IDLE/OFF/0 A
and unchanged buffers; archive checksum and fresh old-record equality passed
before discard. LOW was observed and arm reached WAITING_START/READY high/BUSY
low/output OFF. Completion at block 17 had START/timer-overrun false. New reading
is **1.0000127368 mA / 102.48923302 mV**, raw statuses 200/8. All nine events
(seven information, two mode-change warnings 1811, no errors) were archived.
Four unique monotonic run-tagged markers give BUSY bracket 27.50 us, paired
measurement block 41.01070 ms and reading 0.11516 ms before POST_MEASURE.
Fresh-session reading/timestamp equality and retained-IDLE retrieval passed.
Final state is IDLE/OFF/0 A with one new record retained and default-buffer
counts/extents/capacities unchanged; contents were not compared. The user reports
substantial CH4 noise but approximate **0 → 100 → 0 mV**, qualitatively compatible
with the instrument reading; no quantitative amplitude uncertainty or accurate
settling measurement is established. START-to-BUSY rise/fall are **6.162(1) /
51.461(1) ms**, giving **45.299 ms BUSY high**. START-to-CH4 rise/fall are
**6.8(1) / 50.3(1) ms**, giving nominal 43.5 ms between crossings, rise 0.638 ms
after BUSY rise and fall 1.161 ms before BUSY fall. Parentheses are interpreted
as 1 us for BUSY and 0.1 ms for CH4, with unspecified coverage/correlation and
full error budget. CH4 transition durations are only order-of-2-ms estimates,
explicitly not accurately measurable. No fresh sample rate/record length or
exported trace was supplied. The conditional marker calculation puts the
buffered timestamp **49.49278–49.52228 ms after START**, not either field aperture.
No calibrated amplitude correction, electrical aperture or timing accuracy is established.
Evidence is `docs/evidence/k2460-2026-11-02-timestamp-attempt-08-preflight.json`,
`k2460-2026-11-02-timestamp-attempt-08.json` and checksum-linked
`k2460-2026-11-02-timestamp-attempt-08-review.json`, plus
`k2460-2026-11-02-timestamp-attempt-08-scope-analysis.json` in the same directory. No
gate was promoted by this scope capture. Run08 was later dispositioned only
under the separate explicit v15-installation authorization above.

**Earlier direct-cable repeat, run 2026110207:** the user freshly confirmed CH4's
unattenuated BNC centre to resistor HI/shield LO, actual 1 Mohm/DC/1x, unchanged
front-terminal four-wire nominal 100 ohm resistor and START setup, physical
output OFF and scope waiting in Single on BUSY rising at 20 ms/div with
10–20 ms pretrigger. Explicit permission covered only the archived run06 proof
record discard, one +1 mA / 0.2 V limit / 1 NPLC repeat, consuming event archival
and recovery. Read-only preflight verified IDLE/OFF/0 A and preserved extents.
Archive checksum and fresh run06 record matching passed before authorized
discard. LOW was observed, arm reached WAITING_START/READY high/BUSY low/output
OFF, and completion at block 17 had both overrun flags false. The new reading
is **1.0000149487 mA / 102.50234604 mV**, raw statuses 200/8. Nine events (seven
information, two mode-change warnings 1811, no errors) were archived. Fresh
reading/timestamp matching and recovery/retained-IDLE retrieval passed; final
IDLE/OFF/0 A, one new record retained, default-buffer extents/capacities unchanged.
The BUSY marker bracket is 27.54 us, measurement block 41.01130 ms and reading
timestamp 0.10578 ms before POST_MEASURE. The user subsequently reported CH4
overloaded due to a wrong setting and requested a repeat; no valid scope
baseline, plateau, delays or transitions are accepted for run07. Exact wrong
setting/type of overload is unspecified. A tentative later observation of
approximately 100 mV has no uncertainty or valid trace and does not resolve the
run06 discrepancy. The report is saved in
`docs/evidence/k2460-2026-11-02-timestamp-attempt-07-scope-status.json`.
No electrical aperture/timing accuracy claim
or gate promotion. Evidence is
`docs/evidence/k2460-2026-11-02-timestamp-attempt-07-preflight.json`,
`k2460-2026-11-02-timestamp-attempt-07.json`, and checksum-linked
`k2460-2026-11-02-timestamp-attempt-07-review.json` in the same directory.
The run07 reading was subsequently explicitly dispositioned, checksum-verified
and independently matched before discard for completed run08 above. No hardware
commands were sent for the capture-status report itself.

The generator is a user-confirmed **Agilent 33120A**, now a continuous 0.01 Hz,
50% square wave through 1 kohm to START pin 3, ground pin 9. START/BUSY
observations used high-impedance scope inputs. The user measured 75 mV LOW and
4.5 V HIGH, reported a clean edge, and confirmed the whole edge stayed within
-0.25 to +5.25 V. START is no longer static HIGH: its current phase must be
observed before any future arm. The generator was not controlled remotely.

Latest user update: the probes have been replaced with **Tektronix P2220**
switchable passive probes, currently set to **1x**, and CH3 has been moved to
the **Keithley side of the 1 kohm START resistor**. This is a reported wiring
change. P2220 official instructions specify fixed 1x
compensation for 1 Mohm inputs and nominal 6 MHz bandwidth. Proposed CH4 wiring,
with output physically confirmed OFF and only the reviewed resistor attached:
BNC to CH4, ground clip to the resistor's FORCE LO/SENSE LO end, tip to its
FORCE HI/SENSE HI end, leaving four-wire connections intact. Use 1 Mohm, DC,
1x channel scaling. This intentionally earth-references analog LO through the
scope. The user subsequently explicitly confirmed CH4 wired as instructed,
1 Mohm/DC/1x, physical output OFF, the same four-wire resistor, clean pin-side
START within -0.25..5.25 V with unchanged generator, and scope waiting in Single
on CH2 BUSY rising with 100 ms window/10–20 ms pretrigger. Its initial preflight could not
open `169.254.113.151:5025` (connection timeout), before identity or state queries.
The failed attempt is saved as
`docs/evidence/k2460-2026-11-02-timestamp-attempt-06-preflight.json`;
it contains no instrument observations. The subsequent read-only retry connected
successfully without reset or power cycle and verified IDLE/OFF/0 A, four-wire
sense, one retained reading, unchanged default-buffer extents and no unread
events (`timestamp-attempt-06-preflight-retry.json` in the same directory).

The user explicitly authorized discarding only the archived, independently
matched run 2026110205 proof reading, one +1 mA / 0.2 V limit / 1 NPLC acquisition
with CH4 analog observation, consuming event archival and recovery to IDLE/OFF/0 A.
**Run 2026110206 completed:** archive checksum and fresh old-record match passed
before that discard; LOW was observed, arm reached WAITING_START/READY high/BUSY
low/output OFF, and completion at block 17 had START/timer-overrun false. The
reading is 1.0000148322 mA / 102.57840157 mV, statuses 200/8. All nine events
(seven information, two mode-change warnings 1811, no errors) were archived;
four unique run-tagged markers are monotonic. A fresh connection matched the
reading/timestamp, and recovery/retained-IDLE retrieval verified IDLE/OFF/0 A.
Default-buffer counts/extents/capacities remained unchanged; contents were not
compared. The BUSY marker bracket is 26.82 microseconds, the paired measurement
block 41.02950 ms, and the reading timestamp 0.11530 ms before POST_MEASURE.
The user subsequently reported pin-side START-to-BUSY rise **8.740(1) ms** and
fall **54.055(1) ms**, giving **45.315 ms BUSY high**. All edges are reported
visible; actual settings are **10 MSa/s, 20.0 ms/div, 2 Mpts**, consistent with a
200 ms record rather than the earlier nominal 100 ms confirmation. Interpreting
parenthetical delay uncertainty as 1 us gives a conditional marker-based
START-to-buffered-timestamp interval **52.08702–52.11584 ms**. Coverage, threshold
definitions and full scope/channel error budget remain unspecified; 100 ns
sample spacing is not calibrated timing accuracy.

CH4 was reported noisy with plateau **29(1) mV**, **0–100% rise 0.90(1) ms** and
**100–0% fall 0.93(1) ms**. Preserve those endpoint definitions; these are neither
10–90% durations nor START-to-CH4 delays. The reported plateau differs from the
Keithley reading of 102.5784 mV. Absolute plateau versus pulse excursion and OFF
baseline are unknown; inspect those and actual CH4 input/coupling/attenuation,
probe switch and connection nodes before using CH4 for source timing/settling.
The user then tentatively identified 29 mV as the absolute ON plateau and
reported START-to-CH4 rise **9.6(1) ms** and fall **52.7 ms**. The rising
parenthetical is interpreted as 0.1 ms; falling uncertainty and midpoint
threshold confirmation were not supplied. Nominal differences give 43.1 ms
between CH4 crossings, CH4 rise 0.860 ms after BUSY rise and CH4 fall 1.355 ms
before BUSY fall. These remain provisional analog observations, with no combined
uncertainty or settled-output claim; amplitude/baseline/settings remain unresolved.

After that capture the user replaced CH4's probe with banana-plug wiring and
requested a repeat. Direct-cable BNC centre-to-HI/shield-to-LO assignment,
unattenuated cable, actual 1 Mohm/DC/1x input settings, physical output OFF and
scope readiness were subsequently explicitly confirmed. Scoped run06
disposition and new run/event-archival permission were received, and run07
completed as recorded above. See
`docs/evidence/k2460-2026-11-02-timestamp-attempt-06-scope-followup.json` for
the verbatim report, original-capture arithmetic and separate new setup.
No cause for the amplitude discrepancy is established. No exported
trace is available. No individual electrical aperture or timing accuracy is
established and no gate is promoted. Evidence:
`docs/evidence/k2460-2026-11-02-timestamp-attempt-06.json` and checksum-linked
`k2460-2026-11-02-timestamp-attempt-06-review.json`, plus user-report reduction in
`k2460-2026-11-02-timestamp-attempt-06-scope-analysis.json`. No hardware commands
were sent for this scope reduction. The run06 reading was later explicitly
dispositioned and independently matched before discard for run07 above.

The preceding scope report identified an **Agilent 54831B**, with **CH1 READY,
CH2 BUSY, CH3 generator output before the 1 kohm series resistor**, and CH4
unassigned. The available 1:10 voltage probe is marked **P060-S**. The user
reports DUT LO is connected only to FORCE LO/SENSE LO, with no chassis/earth
bond. A normal grounded analog probe would introduce that bond. Scope input
selection (1 Mohm/50 ohm, AC/DC) is documented; P060-S compensation/input
specifications and actual compatibility remain unverified. No analog connection,
new hardware query or energized run followed these reports. Original and
clarified reports are preserved as `k2460-2026-11-02-scope-equipment-report.json`
and `k2460-2026-11-02-scope-wiring-clarification.json` under `docs/evidence/`.

**Timing qualification for prior captures:** CH3 observed the generator side, not Keithley START
pin 3. Prior scope-analysis intervals remain conditional arithmetic results;
they do not establish a pin-referenced START interval without a measured or
bounded series-path propagation/threshold correction. Do not retrospectively
relabel earlier unexported captures as pin-side observations. The common
reference offset cancels from the reported BUSY-high duration, subject to the
original scope error budget. A future capture should observe START at pin 3
on the instrument side of the resistor, retaining the series resistor and pin-9
ground path. Run06 now has a user-reported pin-side capture; no raw trace has
been exported or independently inspected.

On resumption for the next test, query-only inspection found IDLE/OFF/0 A,
one retained reading, unchanged default-buffer extent and zero unread events.
Later generator edges had set START-overrun true while idle; this did not alter
the previous run's recorded clean terminal observations. The user freshly
confirmed the unchanged setup and explicitly authorized each subsequent repeat
and disposition of only its predecessor's archived proof reading. Checksums and
fresh record equality passed before each discard; default buffers were preserved.
Repeat `2026110204` completed cleanly and its instrument evidence is archived,
but the user missed the scope trigger when selecting Single. No scope timing is
claimed for that run. Its reading was explicitly dispositioned for the next run.
See `docs/validation.md` and the `timestamp-attempt-04*` evidence files.

Earlier repeat **2026110205** used the same +1 mA / 0.2 V limit / one 1 NPLC
proof. The user confirmed the scope was already waiting in Single on **CH2 BUSY
rising**, with a 100 ms window and 10–20 ms pretrigger, before instrument arm.
This avoids requiring scope operation between arm and START; the pretrigger
includes START. The scope is an older Agilent Infinium (exact model unspecified),
with waveform retention reported possible only on the scope. The guarded runner
observed LOW, reached WAITING_START/READY high/BUSY low/output OFF, and completed
at block 17 with START/timer-overrun false. Source readback was 1.0000133188 mA
and voltage 102.50782967 mV, statuses 200/8. Nine events were archived (seven
information, two mode-change warnings 1811, no errors), including four unique
monotonic run-tagged markers. A fresh connection matched the record and absolute
timestamp; recovery and retained-IDLE retrieval confirmed IDLE/OFF/0 A with the
new record retained. Default-buffer counts/extents/capacities remained unchanged.
The BUSY marker bracket is 26.58 microseconds, measurement-block span 41.01964 ms,
and buffered timestamp 0.11454 ms before POST_MEASURE. These do not prove either
individual electrical aperture. The user reported **START-to-BUSY rise
5.009(1) ms and START-to-BUSY fall 50.311(1) ms**. These reported labels must
now be read with the generator-side observation qualification above. Their difference gives
45.302 ms BUSY high. Interpreting each parenthetical as 0.001 ms uncertainty,
the marker-bracketing calculation gives a conditional buffered-timestamp offset
of **48.35098–48.37956 ms after START**, approximately 48.365 ms. Confidence/
coverage, scope/channel/threshold error budget, sample rate and actual settings
are unspecified; no exported waveform is in the repository and actual scope
save is not independently confirmed. The rise delay differs by 3.733 ms from
run `2026110203`. This is consistent with the proof model's 10 ms START polling
loop (blocks 3–6), a code-based interpretation rather than an established cause
or measured latency distribution. Evidence is
`docs/evidence/k2460-2026-11-02-timestamp-attempt-05.json`, checksum-linked
`k2460-2026-11-02-timestamp-attempt-05-review.json`, and
`k2460-2026-11-02-timestamp-attempt-05-scope-analysis.json` in the same directory.
No hardware acceptance gate is promoted. No hardware commands were sent for
this scope-analysis update.

Earlier attempt `2026110201` was rejected before initiation with START HIGH;
inspection and guarded force-safe/recovery reached IDLE/OFF/0 A. Its warning
1811 and error -286 were archived with explicit permission in
`docs/evidence/k2460-2026-11-02-start-low-diagnostics.json`.
Attempt `2026110202` completed one reading but reported START overrun after a
fuzzy signal-generator DC-setting transition. Its approximate scope observations
and provisional uncertainty remain diagnostic only. That old proof reading was
explicitly discarded only after archive checksum checks and independent
record matching, under the user's authorization for the clean repeat. All
prior evidence remains preserved; see `docs/validation.md` for details.

Clean-edge attempt `2026110203` waited for observed LOW, armed at
WAITING_START/READY high/BUSY low/output OFF, and completed one reading at
terminal block 17 with **START-overrun and timer-overrun false**. It measured
1.0000137845 mA source readback and 102.48708725 mV with source/measurement
statuses 200/8. All nine resulting events were archived with user permission:
seven information events and two mode-change warnings 1811, with no error.
Information event 2753 reported multiple blocks waiting on the same event and
a model not optimized for speed. Four unique run-tagged markers are monotonic.
A fresh session matched the raw record and absolute timestamp; recovery reached
IDLE/OFF/0 A and the same record remained readable from IDLE. This run's reading
was later explicitly dispositioned for repeat `2026110204`. The clean flags are
scoped to the recorded terminal/recovery observations; later square-wave
edges may change the historical detector flag without starting a new run.

The user reported rising START-to-BUSY delay **1.276(1) ms**, interpreted as
1.276 ms with 0.001 ms uncertainty. Its physical START-pin reference remains
unproved following the later generator-side observation report. Confidence/coverage and the full scope/
threshold/channel error budget are unspecified. BUSY falling timing was lost
when the scope buffer cleared; no exported trace is available. Falling timing
is not an input to the current timestamp analysis. Under the marker-bracketing
model and reported scope uncertainty, the conditional buffered-timestamp offset
is **44.62756–44.65184 ms after START**, approximately 44.64 ms. The BUSY marker
bracket spans 22.28 microseconds. The measurement block spans 41.03528 ms;
the reading timestamp is 40.91820 ms after PRE_MEASURE and 0.11708 ms before
POST_MEASURE. These calculated values are not a validated total timing-accuracy
claim or proof of either electrical aperture. The configured 1 NPLC at 50 Hz
is nominally 20 ms; source readback occurs before the DUT measurement, so the
paired I/V values must not be assigned a common proved aperture. No timestamp-
reference/aperture convention or complete hardware gate is promoted.

The earlier `2026110203` proof evidence is
`docs/evidence/k2460-2026-11-02-timestamp-attempt-03.json`, with a checksum-linked
instrument review in `k2460-2026-11-02-timestamp-attempt-03-review.json` and the
scope observations/conditional calculation in
`k2460-2026-11-02-timestamp-attempt-03-scope-analysis.json` in the same directory.

The October instrument was a Keithley 2460 at
`169.254.113.151`. The last described load was a nominal 100 ohm resistor on the
front terminals in four-wire mode. These are historical facts, not permission
or confirmation for a future hardware operation. Reconfirm the address, VISA
resource, load, terminal/sense wiring, physical output indicator, runtime state,
and relevant event/error log before live work.

Completed evidence includes read-only connectivity, resistor plausibility and
bipolar sweeps, bounded buffer retrieval/cleanup, packaged volatile TSP runtime
installation and replacement, immediate and external-start finite holds,
programmatic abort, and retained-buffer archival/recovery. Consult
`docs/validation.md`, `docs/hardware-acceptance.md`, and `docs/evidence/` for the
scope and limitations of each result.

The three v14 measurement profiles have each completed a 256-record immediate
resistor run at +1 mA. Observed paired-I/V rates were 24.497 Hz at 1 NPLC,
207.034 Hz at 0.1 NPLC and 821.183 Hz at 0.01 NPLC. Voltage population standard
deviations were 0.982, 275.822 and 302.250 microvolt respectively. These are
scoped observations for this firmware and bench, not guaranteed maxima or
accuracy claims; external-START/aperture timing remains unproven.

On 12 October the resistor/output-off setup was freshly reconfirmed. A v13
timestamp attempt started before an independent trace was available and ended
with `start_overrun=true`; it is retained only as rejected diagnostic evidence,
made no timing claim, and its one buffer record was explicitly discarded after
archival. A later v14 installation attempt failed while opening the VISA socket,
before any command reached the instrument.

Query-only connectivity was restored later on 12 October at
`169.254.113.151:5025`. Identity, serial number, firmware and TSP mode matched
the commissioned instrument. Read-only inspection found the exact v13 runtime
IDLE, the trigger model idle, output OFF, programmed level 0 A, an empty
16-record buffer, and zero unread warning/error events. The front panel had
shown `START input is asserted before arm` at 13:11:49, but the later inspection
found no active runtime or unread-event indication of that fault.

After explicit authorization, the guarded replacement path deleted only that
allowlisted empty v13 runtime and installed packaged v14 with SHA-256
`c4c40686bf2016f5c55c820b5d0bd34f78915dd10e6a77823d0a9cad245c6e82`.
The installation session and a fresh query-only session independently confirmed
v14 IDLE, trigger model idle, output OFF, programmed level 0 A, no retained
records, and zero unread warnings/errors. No acquisition was prepared or armed,
no output was enabled, and no reboot was needed. This live observation does not
replace fresh load/wiring confirmation before an energized run.

The nominal 100 ohm front-terminal four-wire resistor, physical output-OFF
indicator and a static-low 0.02 V START input were freshly confirmed for the
profile benchmark; immediate mode did not use START. Each v3 archive decoded
successfully, and a fresh session matched all 256 retained live records before
any authorized discard. All source statuses were 200; measurement status was
264 for the first record and 8 thereafter. Warning/error counts stayed zero and
every run finished output OFF at 0 A. The comparison and all archive checksums
are in
`docs/evidence/k2460-2026-10-12-measurement-profile-benchmark.json`.

Each profile's records were discarded only after separate user authorization.
A fresh session after the final discard verified v14 IDLE, trigger model idle,
output OFF, programmed level 0 A, an empty 256-record-capacity buffer, and zero
warnings/errors. That audit exposed a host lifecycle mismatch: the high-level
chunk reader rejected retrieval after recovery to IDLE even though retained
data remained readable through the runtime's own inactive-model/output-OFF
guard. The host now permits bounded reads from output-OFF IDLE while continuing
to reject active/prepared and output-on states. Focused offline tests pass; the
TSP artifact is unchanged.

Later on 12 October, an immediate +1 mA run at 0.01 NPLC filled an exact
10,000-record standard buffer without wrap. Acquisition took 12.181 s of host
wall time and the buffer-relative span was 12.17023136 s (821.595 observed
paired-I/V records/s). Bounded chunks of 4096, 4096 and 1808 records were
retried, archived, decoded and matched record-for-record from a fresh session.
Warning and error counts remained zero. After archive verification and explicit
user authorization, all 10,000 records were discarded. A fresh query-only
session confirmed v14 IDLE, trigger model idle, output OFF, programmed level
0 A, an empty 10,000-record-capacity buffer, and zero warnings/errors. The
archive, summary and discard record are
`docs/evidence/k2460-2026-10-12-capacity-10000-archive.json`,
`docs/evidence/k2460-2026-10-12-capacity-10000-evidence.json` and
`docs/evidence/k2460-2026-10-12-capacity-10000-discard.json`.

## User interface and quickstart

- `docs/user-guide.md` describes immutable per-run configuration, the Flyer
  lifecycle, paired I/V records, retained-data behavior, and preliminary result
  inspection.
- `docs/available-sequences.md` distinguishes current intent/planning/simulation
  coverage from default-Flyer and target support, and records phased plans for
  an example gallery, finite segment composition, waveform generators, and
  hardware commissioning.
- `notebooks/ophyd_electrochemistry_quickstart.ipynb` provides selectable
  unipolar-current, voltage-pulse, and charge-balanced alternating-current
  examples. It runs without importing the `examples` package or requiring an
  editable install. It locates the checkout, exposes `src/`, and defines its
  synthetic configuration and plan locally.
- The notebook uses Plotly for linked voltage/current plots, hover inspection,
  source-command display on the matching axis, and shaded pulse dwells.
- Configuration cells contain short field comments. `command_timeout_s` bounds
  one backend transaction, `external_start_timeout_s` bounds the local START
  wait, and `shutdown_timeout_s` bounds confirmation of abort/output OFF. None
  of these values represents acquisition duration.
- Simulator advancement and the host completion watchdog are derived from the
  compiled program duration. `prepare()` now returns validated local-completion
  and communication bounds plus request/program digests and record/capacity
  bounds. A reused Flyer replaces its effective watchdog for every successful
  request while retaining the constructor timeout only as a minimum. Explicit
  disposition clears stale bounds; rejected re-preparation preserves an earlier
  retained acquisition. The regression case with four 0.5 s periods and 0.2 s
  pulses produces 200 records at a 10 ms sample period and finishes with state
  COMPLETE and output off.
- Under the current synthetic profile, the minimum sample period is 3 ms
  (2 ms aperture plus 1 ms overhead). That is simulator behavior, not a
  hardware-rate claim.

The simulator supplies reviewed synthetic prepare bounds. The timed target
candidate supplies its own nominal operational watchdog inputs and compound VISA
observation margin. They are explicitly uncommissioned physical timing/protection
bounds; no synthetic planning assumptions are inherited. Users do not need to enlarge communication or shutdown timeouts
merely because a pulse program is longer.

## Working tree and validation

The checkout contains uncommitted November bench evidence/documentation,
shared measurement-v4 and the new timed-hold candidate: model/public exports, simulator projection,
Flyer stream/configuration, tests, example, quickstart/user/contracts, changelog
and aperture-calibration design. Preserve all earlier user-authored changes.
Always inspect current `git status`; no commits or pushes were made.

The latest full validation after adding the voltage and bipolar pulse examples
reported:

- 321 tests passed;
- Ruff lint and formatting passed;
- strict mypy passed;
- strict MkDocs passed;
- `git diff --check` passed; and
- the lockfile was resolved and checked after adding notebook dependencies.

Strict MkDocs, Ruff lint/format, strict mypy, and `git diff --check` were rerun
for the pulse-example update and passed.

The earlier completion-watchdog checkpoint reported 359 tests passed. Ruff
lint/formatting, strict source mypy, strict MkDocs, notebook JSON validation and
`git diff --check` passed. These are that previous checkpoint's offline checks;
they were not rerun for the 2 November query-only inspection. All three profiles
have scoped resistor-bench evidence. Runtime v14 was initially absent at the
2 November inspection and was then reinstalled with scoped authorization and
independent verification as recorded above. The November evidence JSON files
were validated and `git diff --check` passed after the installation update.
After the November proof attempt and retained-IDLE audit, all 11 November
evidence JSON files validated, record/timestamp equality checks passed, and
strict MkDocs and `git diff --check` passed. No source code was changed.
After event archival/review, all 13 November evidence JSON files validated;
the analysis was recomputed from raw events, all four unique markers were
verified, and the proof record matched before/after event retrieval. Strict
MkDocs and `git diff --check` passed for the review update.
After the clean repeat and scope analysis, all 17 November evidence JSON files
validated. The old-record archive checksums, fresh-session/retained-IDLE record
equality, four unique markers, nine archived events, default-buffer preservation
and conditional scope calculation were checked. Strict MkDocs and
`git diff --check` passed. No package source code was changed or offline suite
rerun for this bench-only evidence update.

The two subsequent repeats, missed-capture report and latest scope analysis
brought the November collection to 24 valid JSON evidence files; the later
user-reported scope equipment record and wiring clarification bring the
collection to 26. The clarification qualifies earlier START-pin timing claims;
original run/analysis files remain untouched. New review/status source checksums,
raw/fresh-session/retained-IDLE equality, four unique markers per run, event
counts and unchanged default-buffer extents were checked. Strict MkDocs and
`git diff --check` passed after these evidence/documentation updates. The M5
proof description was corrected to describe the containing measurement operation
rather than claim individual aperture placement. No package source or runtime
artifact changed; the offline test suite was not rerun.

The subsequent shared measurement-v3 implementation passed **379 tests**,
Ruff lint/format, strict source/release-helper mypy, strict MkDocs and
`git diff --check`. The offline simulator example ran successfully with 635
synthetic records, schema v3 and `hardware_ready=False`. Tests include separate
windows, unknown windows/availability, raw timestamp references, selected-field
validation, synthetic-origin guards, storage after integration, checksummed
retention, fixed 25-field Event Model documents and quickstart execution. The
packaged runtime digest was rechecked unchanged; no hardware commands were
sent during this software/procedure preparation. The calibration document is
a design specification, not an executable or commissioned bench fixture.

Latest software checks after the v17 host-padding fix: **527 tests passed**,
Ruff lint/format (94 files), strict mypy (29 source/helper files), strict MkDocs,
whitespace checks, Lua 5.0.2 syntax compilation and inert mode-graph construction
checks. V17's actual event-semantics bench result remains failed. No code change
after that bench run changes the installed TSP artifact.

Source/wheel builds and strict Twine checks passed. All 31 packaged source/
resource files matched the checkout; isolated wheel v14/v17 reconstruction and
source-distribution fixture byte equality passed. All **53 November evidence
JSON files** parsed. The software check record is
`docs/evidence/k2460-2026-11-02-timed-v17-software-validation.json`
(repository-relative path). These checks do not override the failed hardware
cadence result.

## Safe resumption

The user returned to the lab on 2026-11-02 and selected specified-duration finite
holds with explicit timing unknowns and an order-of-10-ms desired alignment.
The target backend and schema v4 are implemented, but the timed runtime failed
its first energized ten-reading case as detailed above. At about 15:27 UTC the
user said they had another hour in the lab; do not assume availability afterward.

Latest independently verified state: exact v17 installed, runtime IDLE/model
EMPTY/output OFF/programmed 0 A, **one retained run-05 reading/capacity 16**,
unchanged defaults (12,867/0) and zero unread events.
The final run-05 used immediate start and completed normally; no scope capture
is confirmed. The user was told they can power down all equipment; shutdown is
not confirmed. Treat all hardware conditions as historical on
resumption. Freshly confirm address/identity/TSP, resistor/terminal/sense/scope
wiring and physical OFF, then query-only inspect stopped/OFF/0 A, exact runtime,
retained records/default extents/events. Preserve all records. Any new preparation
would overwrite the retained run-05 reading, so requires explicit disposition after a fresh
archive match. No automatic mutation retry or implicit runtime replacement.

## Next work

1. Review and correct the instrument-local cadence/deadline design in software.
   V17 is a failed commissioning candidate, not a usable duration/spacing
   interface. Keep timing/protection/cadence gates unpromoted. The actual padded
   firmware block-list fixture is `tests/fixtures/k2460-v17-immediate-block-list.txt`;
   the inert graph recorder is `tests/fixtures/timed_hold_graph.lua`.
2. Validate revised semantics against the official manual, meaningful event-state
   tests and actual archived evidence. A static graph check cannot establish event
   consumption. Keep source/data/contracts/user guide/quickstart aligned.
3. Only after concrete review, arrange another scoped bench case with fresh setup
   confirmation, explicit disposition of archived/freshly matched run-05 data,
   exact runtime replacement authorization if bytes change, one finite capture,
   consuming event archival and recovery. Preserve all default-buffer records.
4. Keep `hardware_ready=False`, unknown START/apertures and other waveform/battery
   capabilities unavailable. No commits, pushes, releases or publication were made.

When this checkpoint changes, replace stale statements here rather than
appending a chronological development diary. Put durable behavior in contracts,
bench results in evidence/validation, and historical narrative in the handover.
