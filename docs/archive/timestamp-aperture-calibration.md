# Independent electrical aperture calibration

Status: **design specification; no executable hardware fixture is commissioned**.
The user now prioritizes basic finite current holds with explicitly uncalibrated
timing and an approximate 10 ms alignment target. Fine independent I/V aperture
calibration is outside that initial scope; stop identical START/BUSY repeats.
A limited hardware interface still needs a coherent unknown-actual-START
contract revision and adapter implementation before its acceptance tests. This
scope decision does not establish 10 ms timing accuracy or promote any gate.

The existing packaged runtime and retained bench records are unchanged. The
earlier finite proof, `2026110205`, establishes a conditional START-to-buffered-
timestamp interval, not the current or voltage integration boundaries. This
procedure describes the evidence required before adding a new reviewed fixture.

## What the present evidence establishes

The user reported START-to-BUSY rise 5.009(1) ms and fall 50.311(1) ms for
run `2026110205`. The BUSY interval is 45.302 ms; the instrument measurement
operation spans 41.01964 ms. Under the existing marker-bracketing model and
reported uncertainty assumption, the buffered timestamp is 48.35098–48.37956 ms
after START. These are scoped observations, not a calibrated accuracy claim.
The trace is reported retainable only on an older Agilent Infinium; exact model,
sample rate, channel skew and exported waveform remain unavailable.

The [Keithley reference manual, Rev. C](https://download.tek.com/manual/2460-901-01C_Sept_2019_Ref.pdf)
establishes the following:

| Manual section | Established meaning |
|---|---|
| Source readback, 4-44–45 and 14-183–184 | Actual source readback is measured before the DUT measurement |
| `smu.measure.nplc`, 14-151–152 | Integration duration of the selected measurement function; 1 NPLC at 50 Hz is nominally 20 ms |
| Measure/Digitize, 8-27–28 and 14-262–264 | A finite block waits through measurement, processing and buffer storage |
| Trigger/status events, 8-29–30, 8-54 and 16-9–10 | Listed events do not expose separate electrical aperture-open/close signals |
| SRQ example, 16-22–23 | Before/after log events bracket the complete measurement operation |

The manual does not establish a 20 ms current-readback window merely from the
DUT NPLC setting, or identify a shared I/V aperture. BUSY and log markers around
the finite block do not supply those missing boundaries. Ordinary constant-
resistor START/BUSY repetitions cannot identify them.

## Scope equipment and wiring now reported

Latest update: the user replaced the probes with **Tektronix P2220**, set to
**1x**, and moved CH3 to the Keithley side of the START series resistor.
The user subsequently confirmed CH4 across the
resistor (tip HI/ground LO), 1 Mohm/DC/1x, same four-wire resistor, physical
output OFF and BUSY-triggered scope readiness. Authorized run 2026110206
completed and returned to IDLE/OFF/0 A. Its user-reported BUSY delays are
8.740(1)/54.055(1) ms; all edges were visible at 10 MSa/s, 20 ms/div and 2 Mpts.
The noisy CH4 plateau was reported as 29(1) mV versus the instrument's
102.5784 mV, with baseline/excursion interpretation unresolved. Check the held
trace's baseline and actual probe/channel settings and connection nodes before
using its analog timing. Reported 0–100% rise 0.90(1) ms and 100–0% fall
0.93(1) ms are not START-to-CH4 delays or 10–90% durations. No raw trace has been
exported; no source settling or individual measurement aperture is established.
The later report gives START-to-CH4 rise 9.6(1) ms and fall 52.7 ms, without
falling uncertainty or threshold confirmation. The user then replaced the CH4
probe with banana wiring and requested a repeat. For an unattenuated direct
cable, use BNC centre to resistor HI and shield to LO with 1 Mohm/DC/1x scope
settings. These connections/settings and physical output OFF/capture readiness
were subsequently confirmed. With explicit run06 disposition and bounded
run/event-archival authorization, run07 completed, archived/matched its new
1.0000149487 mA / 102.50234604 mV reading, and recovered to IDLE/OFF/0 A with
default buffers preserved. The new reading is retained. The user reported CH4
overload/wrong setting for run07 and requested another repeat; no valid analog
or delay evidence is accepted from that capture. Proposed corrected setup is
1 Mohm/DC/1x at 50 mV/div with 0–200 mV visible, Single on BUSY rising at
20 ms/div with pretrigger. These settings/readiness and explicit run07
disposition/event-archival permission were subsequently confirmed; run08
completed, archived/matched its new 1.0000127368 mA / 102.48923302 mV reading,
and recovered to IDLE/OFF/0 A with default buffers preserved. The latest user
report is very noisy but roughly 0/100/0 mV, START-to-BUSY 6.162(1)/51.461(1) ms
and START-to-CH4 6.8(1)/50.3(1) ms; transition durations are not accurately
measurable (order 2 ms only). This supports qualitative voltage consistency,
not calibrated amplitude, settling or field integration windows. Run08 remains retained
without new discard/run permission. The old 29 mV discrepancy remains unresolved and must not be
treated as fixed merely by changing the cable.
Earlier reports below remain
historical and continue to qualify earlier timing evidence.

The user identifies the scope as **Agilent 54831B** and the available 1:10
voltage probe as **P060-S**. Channels are CH1 READY, CH2 BUSY, CH3 generator
output **before** the 1 kohm START resistor, with CH4 unassigned. DUT LO is
connected only to Keithley FORCE LO/SENSE LO, with no chassis/earth bond reported.
The [original equipment report](../evidence/k2460-2026-11-02-scope-equipment-report.json)
and [wiring clarification](../evidence/k2460-2026-11-02-scope-wiring-clarification.json)
are preserved separately.

This changes the interpretation of the previously reported START delays:
the generator-side edge has no established transfer correction to Keithley
pin 3. Earlier marker calculations remain conditional; their stated scope
uncertainty does not cover an unmeasured series-path/threshold difference.
The reported BUSY-high interval subtracts two delays to the same reference,
so a common reference offset cancels, subject to the original scope error
budget. A future trace must observe pin 3 on the instrument side of the
resistor while retaining the 1 kohm series path and pin-9 ground.

The [54830-series quick-start guide, 3-12](https://www.keysight.com/us/en/assets/9018-04651/quick-start-guides/9018-04651.pdf)
documents 1 Mohm/50 ohm input selection and AC/DC coupling. A conventional
high-impedance 10:1 passive probe uses a 1 Mohm input with matching attenuation
selection; the actual P060-S specifications/compensation range and its behavior
on this scope remain unverified. Kikusui's official legacy inventory lists
P060-S as a probe, but accessible official specifications were not found.

A conventional passive probe has an earth-referenced ground lead
([manufacturer primer](https://www.tek.com/en/documents/whitepaper/abcs-probes-primer)).
Keithley FORCE LO/SENSE LO are not internally bonded to chassis (reference
manual 4-1 and 5-12). An analog ground clip would therefore introduce an earth
reference to the presently floating DUT LO. An intentional grounded-LO,
resistor-only arrangement is a candidate for review; no analog wiring is
commissioned by this document. CH4 can potentially capture resistor voltage
once that arrangement and probe are verified, but a constant electrical input
still does not identify either integration window.

## Next preparation with the present equipment

For the newly reported P2220 at 1x, the
[official instructions](https://download.tek.com/manual/071146400.pdf) specify
fixed compensation for 1 Mohm scope inputs; nominal bandwidth is 6 MHz. With
physical Keithley output OFF and only the reviewed nominal 100 ohm resistor
attached, connect the probe BNC to CH4, its ground clip to the resistor end
shared by FORCE LO/SENSE LO, and its tip to the end shared by FORCE HI/SENSE HI.
Leave all four Keithley wires connected. This intentionally bonds analog LO to
earth through the scope and its other channel grounds; never put a ground clip
on HI. Keep the ground lead short. Set CH4 to 1 Mohm, DC coupling and 1x scaling;
20 mV/div is a starting display setting for the expected approximately 100 mV
signal under the previously used +1 mA test. Connection, settings and physical
output-OFF confirmation were subsequently received for completed run06. This preparation authorizes no new
acquisition or retained-reading discard. A later switch to 10x requires matching
channel scaling and compensation verification.

The following preparation notes describe the earlier P060-S proposal:

Preserve the existing scope capture before changing settings. With Keithley
output confirmed OFF, move the CH3 observation point to START pin 3 after the
series resistor, retaining the resistor and pin-9 ground path. This is a
measurement-node correction, not authorization to arm or discard a reading.

For a conventional high-impedance 10:1 probe, the proposed CH4 preparation is
1 Mohm input, DC coupling and matching 10:1 channel scaling. Verify the actual
probe's attenuation and compensation against a known low-voltage square-wave
source before using its edge shape as evidence; P060-S compatibility has not
been established from its model label. Keep it off the DUT during that check.
If compensation cannot be established, do not use its edge timing to support
an accuracy claim.

A subsequent resistor-only analog timing capture could use the existing bounded
v14 one-reading proof, with CH4 observing resistor voltage after intentional
LO grounding is reviewed and freshly confirmed. It would independently observe
source output/settling alongside pin-referenced START and BUSY, but would not
identify integration windows from a constant electrical input. Such a run
requires separate retained-reading disposition and scoped energized authorization.
A controlled-stimulus aperture fixture remains a different, uncommissioned step.

## Required independent observation

For the exact finite-first-reading profile under test, introduce a bounded
change in an electrical quantity during the measurement operation. Capture the
actual electrical waveform and a common execution marker independently of the
buffer timestamp. Sweep the change through the operation and compare the
instrument's reported averages with that captured waveform.

Both current and voltage must vary observably to identify both windows. A
voltage disturbance across a regulated constant-current load can help identify
the voltage window, but cannot identify current integration from a constant
current. Any current inferred from resistor voltage requires a characterized
resistor and a justified dynamic relationship, with uncertainty. Two coincident
scope traces do not establish two simultaneous instrument integrations.

A proposed electrical arrangement must establish safe limits, load/source
interaction and grounding before a fixture is packaged. No diagram or upload
command is prescribed until those prerequisites are resolved. In particular,
adding a grounded analog probe while the scope is already grounded to digital
I/O changes the electrical arrangement. The generator's present TTL-level MAIN
output is a START source, not an approved DUT stimulus.

## Bounded scan and analysis

After the electrical arrangement and packaged fixture are reviewed:

1. Fix the instrument identity/firmware, terminal/sense wiring, source and
   measurement ranges, NPLC, source readback, filters, autozero, source delay and
   complete trigger path. Calibration of later background readings must not be
   transferred automatically to the first finite reading.
2. Specify a finite list of stimulus phases, bounded amplitudes and durations,
   repetition count, capacity, compliance/cutoff policy, local START/execution
   deadlines and safe shutdown. Acquisition and stimulus timing must remain
   local; LAN writes must not schedule individual transitions.
3. Preserve existing evidence and obtain explicit retained-record disposition.
   Confirm output OFF, the reviewed physical arrangement and scope capture,
   then obtain authorization for that exact bounded energized test.
4. For each shot, preserve raw instrument fields/status/timestamps and actual
   scope waveform with run identity and checksums. Use measured stimulus timing,
   rather than the programmed delay or host command time.
5. Cover both stable plateaus and the entire transition response separately for
   current and voltage. Repeat transition positions and include rising and
   falling changes. Missing plateau coverage makes a window unidentifiable.
6. Normalize each field using its own measured plateaus. An ideal rising step
   at time `e`, averaged over a rectangular window `[a,b)`, has normalized
   response `clip((b-e)/(b-a), 0, 1)`. Fit each field separately. Where settling
   matters, integrate the captured waveform against a candidate weighting
   function rather than assume an ideal step.
7. Assess residuals and sensitivity, repeatability, scope timebase and channel
   skew, stimulus/settling uncertainty, measurement noise, and fit uncertainty.
   Report an effective weighting window if the data does not support a
   rectangular aperture. Do not force either width to the DUT NPLC duration.
8. Relate each identified field window to START, the raw buffered timestamp and
   complete-record availability. Preserve an unresolved reference or unknown
   window when evidence is insufficient; do not subtract a guessed aperture
   from the buffer timestamp.

Reject timing acceptance for lost trace/record association, compliance, overrun,
clipping, incomplete scan coverage, unstable stimulus or changed settings. A
partial acquisition remains retained with its unsuccessful outcome. No complete
G01/G03/G04/G06 gate follows from this design or an offline fit.

## Candidate fixtures requiring further review

| Candidate | Use | Unresolved prerequisite |
|---|---|---|
| External bounded electrical stimulus/phase scan | Potentially preserves the exact finite reading path | Approved electrical coupling, independently timed changing I/V, isolation/grounding and stimulus control |
| Instrument-local source steps with background measurement | Potential exploratory identification of separate windows on the same resistor | Source changes during an in-flight reading and equivalence to the existing first finite reading are unproved |

The second candidate uses documented background-measurement, delay and source
configuration-recall building blocks (14-257–264), but those building blocks
alone do not prove the combined behavior. It is not an operational fixture.

The [33120A user guide, SYNC Signal, p. 68](https://www.keysight.com/us/en/assets/9018-04436/user-manuals/9018-04436.pdf)
describes waveform-dependent SYNC behavior. Do not treat SYNC as a freely
adjustable second stimulus channel or connect it to START without independently
verifying its complete voltage waveform and timing for the selected mode.

Before choosing the fixture, verify P060-S compatibility/compensation, sample
rate, channel skew and retention/export method, intentional analog/digital ground
relationships, resistor dynamic behavior and available stimulus hardware. Scope
and probe model labels and current grounding/node arrangement are now reported;
these do not supply a stimulus or aperture calibration. These are the remaining concrete bench inputs.

## Data representation and execution boundary

Shared schema `measurement-v3` carries independent `MeasurementAperture` values
for current and voltage, with origin and optional boundary uncertainty. Unknown
windows and availability are `None` in Python, projected as declared `-1`
sentinels in the fixed Bluesky stream. `buffer_stored` and `unresolved` timestamp
references identify no electrical field. Synthetic simultaneous windows remain
explicitly synthetic.

This representation preserves uncertainty; it does not commission a target
adapter or waive the actual-START/clock mapping and hardware acceptance gates.
Only a reviewed packaged artifact may implement the selected fixture. Installation,
retained-record discard, arming, output enable and consuming event retrieval
require their applicable guarded preconditions and scoped user authorization.
