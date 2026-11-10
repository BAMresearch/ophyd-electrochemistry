# Timed current-hold commissioning plan

Status: **v17 installed; repeated cadence failed (1/10 records); one-reading duration diagnostic completed normally**.
The selected interface must honor a specified hold duration and sample spacing.
The historical v14 count-bounded acquisition must not approximate these settings
using average measured reading rates. Fine electrical-aperture calibration is a
separate task; it is not a prerequisite for explicitly uncalibrated resistor I/V
acquisition.

This plan separates offline code review from guarded bench acceptance. It grants
no upload, retained-record discard, arm, START or output authorization. The
oscilloscope may remain off during software development. Dynamic acceptance
requires a separately authorized bench session and suitable physical timing
observations.

## Candidate scope and code gate

The v17 artifact combines the unchanged packaged v14 base with a
packaged, reviewed timed-hold extension. Its complete SHA-256 is
`68b25b2e332351cc134ccdf47d04799a365e2294776f9ab30ca78f6e5408b521`.
This exact v17 is installed; its first energized commissioning case is incomplete.
V15's first preparation failed safely before arm (5077): compliance was set
before its compatible measurement range. V16 corrects that order and clears the
stopped model during explicit initialization/recovery. V17 is installed but not commissioned. V16 later passed preparation but failed initiate with missing-block
events matching external-only islands in the immediate graph. V17 builds
contiguous mode-specific graphs and inspects firmware block numbering before
arm. Its first energized run retained one reading at about 0.512 V, then stopped
with `cadence_overrun`; the user scope did not trigger. Recovery retained that
record at IDLE/EMPTY/OFF/0 A. That run-04 record was later checksum-verified,
freshly matched and explicitly dispositioned for the one-reading run-05.
Run-05 (+5 mA / 1 V / 1 s / 1 s spacing) completed normally with one reading at
512.48002052 mV, now archived and retained at independently verified
IDLE/EMPTY/OFF/0 A. Do not overwrite this latest record without explicit
disposition. No scope capture is confirmed, so physical pulse duration remains
unverified. This isolated the normal duration path; it did not fix or validate
repeated sampling. See the run-05 evidence/review. The latest test used immediate
software start; scope triggering should use CH4 voltage rise or CH2 BUSY, rather
than the generator START waveform.
The event-consumption scheme requires review; inert Lua graph checks do not
establish firmware behavior. The specific range/limit rule is in the official Keithley 2460
Reference Manual Rev. C, 4-73 and 14-194; fixed-range compliance must be at least
10.6% of that measurement range (0.212 V for the new 2 V profile).
Use the actual packaged digest, not a stale copied identifier, before uploading.

The connection-owning backend candidate requires `commissioning_resistor=True`
and retains `hardware_ready=False`. Its constructor selects the source range
and measurement profile; it must inspect an already installed exact candidate,
never install, reset or change command language while connecting. The opt-in is
scope acknowledgement, not confirmation of wiring, safety or bench acceptance.

The initial review envelope is a known resistor only: absolute current at most
10 mA, profile-dependent compliance at most 2 V, requested sample spacing at least 0.1 s, duration
at most 60 s and an integral duration/spacing ratio no greater than 600 records.
These are candidate ceilings, not commissioned limits. Actual configuration may
be stricter. The candidate supports the three existing 0.2 V profile selectors and the new
`NPLC_1_2V` profile. None of their timed execution paths is commissioned.
`NPLC_1_2V` uses
1 NPLC and a fixed 2 V measurement range, only on the timed candidate; it is
**uncommissioned**. The three historical v14 profiles retain their fixed 0.2 V
range and scoped resistor evidence; that evidence does not commission this new
range. Batteries, unknown DUTs, pulses,
external ABORT and voltage-source operation remain outside this plan.

The user freshly confirmed a **100 ohm, 1 W resistor** for the higher-signal
first case. This confirms resistance and rating only, not physical output OFF,
scope settings, complete wiring or permission to upload, arm or source. The
rating does not authorize operation at 1 W.

Before any installation or energized test, review and test:

- Finite request validation, exact sample count, schedule/deadline handling,
  rejected unsupported requests and confirmed output OFF after preparation.
- Instrument-local duration, cadence, START timeout and voltage-cutoff handling;
  no network operation per sample and no host sleep used to time output.
- Exclusive connection ownership, bounded transactions, abort priority between
  transfers, uncertainty handling and a reviewed completion/shutdown bound.
- Terminal outcome preservation, explicit recovery, immutable retained prefixes,
  retryable chunks and prepared/retained identity matching through the Flyer.
- Exact packaged-artifact allowlisting and digest checks, with replacement
  rejected unless the old runtime is stopped, output OFF, source level 0 A and
  its proof buffer empty following explicit disposition.

Sequential measurement is not preemptible merely because abort, cutoff or
deadline logic exists. Review where each condition can be serviced and what
happens if a measurement outlasts its budget. The exact-boundary behavior of
`LIMIT_OUTSIDE` must be verified before claiming inclusive voltage cutoffs. Average resistor reading rates do
not establish worst-case service latency. An explicit candidate flag cannot
substitute for measured latency evidence or confer production capability.

## Guarded replacement and session preconditions

1. Freshly confirm the instrument address/identity, firmware, command language,
   reviewed front-terminal four-wire resistor, current limits, physical output
   OFF, source-off mode and applicable manual shutdown path. Reconfirm START
   wiring/levels and inactive input before any external arm. Historical setup
   observations are not current preconditions.
   For the proposed higher-signal case, separately confirm CH4 across the reviewed
   resistor nodes with the accepted LO earth reference, input 1 Mohm/DC/1x and a
   suitable display such as 200 mV/div with approximately 0–1 V visible. Confirm
   output physically OFF before any connection/settings change and capture
   readiness before the separately authorized acquisition.
2. Use a query-only inspection to establish runtime/trigger-model stopped state,
   output OFF, programmed 0 A, runtime identity/digest, all retained extents and
   relevant events. Do not reset, change language, upload or arm to diagnose a
   connection problem.
3. Preserve any retained data and obtain explicit disposition before replacement.
   The former run **2026110208** record was archived, independently matched and
   explicitly discarded for the completed v15 installation. The failed first
   timed preparation collected no records; its proof buffer was count 0/capacity
   16 after the early failed preparations. The latest energized v17 run retains
   **one** run-05 record/capacity 16 with a checksummed export and independent raw
   equality; preserve it until explicit disposition. Inspect fresh values and stop
   on any unexpected data.
4. Preserve all default-buffer records. The historical `defbuffer1` extent was
   **12,867 records, indices 1–12,867, capacity 100,000**; `defbuffer2` was empty
   with capacity 100,000. Inspect fresh values and reconcile differences before
   proceeding. Never clear or overwrite them. Extent equality alone does not
   prove content equality.
5. Obtain scoped permission for replacement of only the exact reviewed packaged
   runtime identity/digest. The replacement procedure must reject unknown
   installed scripts, active state, output uncertainty, nonzero source level or
   retained proof data. Verify the installed candidate digest and IDLE/OFF/0 A
   in a separate query-only check; installation is not acquisition permission.

Never automatically retry a mutation whose delivery/execution is uncertain.
Reconnect by inspection, require explicit stopped/output-OFF confirmation and
preserve records. If shutdown cannot be confirmed, report ERROR/UNKNOWN and
require explicit recovery; do not report success from a disconnected worker.

## Bench acceptance matrix

Before energizing, fix the exact request, profile, thresholds, expected outcomes,
timing definitions, measurement method/uncertainty and pass/fail limits for each
case. Each execution must fall within explicit scoped authorization; one authorization
may cover a fully defined group of runs and their retained-data disposition. Begin with **+5 mA, 1 V compliance,
1 s requested duration and 0.1 s sample spacing: 10 expected paired records**.
This is a proposed first case, not permission or an already commissioned example.
Use the uncommissioned `NPLC_1_2V` candidate profile and a 10 mA source range.
For the nominal 100 ohm resistor, Ohm's law predicts approximately **0.5 V** on
CH4 and `I²R = (0.005 A)² × 100 ohm = 2.5 mW` dissipation. These are nominal
predictions, not measured amplitude or accuracy evidence.

The future case-specific `SafetyConfig` must encompass the selected limits:
voltage bounds of -1 to +1 V, charge/discharge current limits of 5 mA and a power
limit of 5 mW cover the request's `|I| × compliance = 5 mW` envelope. Review all
settings together before preparation; the higher candidate ceiling does not
silently expand this scoped case or its authorization.

The following constructs the candidate and request only. Use an independently
reviewed `config` with the limits above and the current confirmed resource;
construction performs no connection, installation or acquisition.

```python
from ophyd_electrochemistry import AcquisitionRequest, GalvanostaticHold, StartMode
from ophyd_electrochemistry.keithley.k2460 import (
    K2460MeasurementProfile,
    Keithley2460Backend,
    Keithley2460Device,
    PyVisaKeithley2460Transport,
    VisaTransportConfig,
)

transport = PyVisaKeithley2460Transport(
    VisaTransportConfig(
        resource_name=config.visa_resource,
        backend="@py",
        required_language="TSP",
        io_timeout_ms=1000,
    )
)
backend = Keithley2460Backend(
    transport,
    config,
    source_range_a=0.01,
    measurement_profile=K2460MeasurementProfile.NPLC_1_2V,
    expected_serial_number="04686198",  # Reconfirm the actual instrument.
    commissioning_resistor=True,
)
ec = Keithley2460Device(
    backend,
    name="ec",
    supported_program_types=(GalvanostaticHold,),
    collection_chunk_records=config.buffer.transfer_chunk_records,
    shutdown_timeout_s=config.timing.shutdown_timeout_s,
)
request = AcquisitionRequest(
    experiment_id="timed-resistor-commissioning",
    start_mode=StartMode.IMMEDIATE,
    program=GalvanostaticHold(
        current_a=0.005,
        voltage_limit_v=1,
        duration_s=1,
        sample_period_s=0.1,
    ),
)
```

After scoped installation and the independent query-only verification, the
authorized acquisition uses the normal stage/prepare/kickoff/complete/collect
lifecycle. Connecting to a different or absent artifact fails inspection and
does not install it. Terminal data remains retained after unstage; export and
explicit disposition are separate operations.

| Case | Required observations and pass conditions |
|---|---|
| Normal immediate hold | Preparation leaves output OFF. Kickoff confirms running or an already terminal short run. Obtain exactly 10 records, correct identities/setpoint/status origins and normal terminal outcome; confirm output OFF and READY/BUSY inactive. The programmed source setpoint can remain at the requested current while OFF; explicit guarded recovery returns it to 0 A without clearing records. Archive and independently match a fresh retrieval. |
| Normal external hold | Establish a valid inactive START before arm. Kickoff succeeds at armed WAITING_START with output OFF/READY high/BUSY low, before the START edge. One reviewed edge releases one complete hold; verify count, finite termination and the same final safe state/data integrity as immediate mode. |
| Missing START | Keep START inactive through the configured local wait. Output stays OFF, no valid measurement is fabricated, timeout fails acquisition completion, flags return inactive and the empty terminal extent remains inspectable. No host polling loop substitutes for the local timeout. |
| Programmatic abort | Abort during an active hold. Measure request-to-output-OFF latency, including an unfavorable phase within measurement. Shutdown status succeeds only after confirmation; acquisition completion remains failed/aborted. Preserve every complete retained reading and the aborted outcome. Repeated abort must not erase them. |
| Local voltage cutoff | With the same reviewed resistor, preselect a safe measured-voltage cutoff that the approved current will cross, inside compliance; do not change wiring while energized. Demonstrate local termination with host polling unable to provide the cutoff. Measure threshold-crossing-to-output-OFF latency, verify the designated cutoff outcome and retained prefix. Compliance alone does not pass this case. |
| Deadline/cadence failure | Establish the reviewed response to a missed measurement/output-off deadline. Use offline fault injection first; any hardware stress case needs its own safe scope. No silent cadence slowdown, dropped samples or successful completion after a deadline fault. Preserve the fault and complete prefix. |
| Recovery and retained data | After abort, timeout and cutoff, verify stopped/OFF/0 A and inactive inputs before recovery to IDLE. Recovery does not source, re-arm, resume or clear data. Verify identical records/statuses/checksums before and after recovery and a fresh connection. Only explicit reason-bearing disposition permits reuse. |

## Coarse timing and protection acceptance

Measure physical output duration and sample cadence separately from buffered
timestamp formatting. For external mode also observe physical START at the
Keithley input and the relevant output/flag transitions. Define onset/end
thresholds and instrument/scope resolution, uncertainty and coverage before
reducing results. Buffer intervals describe the raw stored-reading clock; they
do not by themselves locate either electrical aperture or physical START.

The desired coarse alignment target is of order **10 ms**, not a demonstrated
accuracy or a default tolerance. Review the measured duration/cadence errors,
START relationship, shutdown/cutoff latency and measurement uncertainty against
explicit limits before accepting the selected profile and minimum spacing.
An output-off deadline or local cutoff cannot be accepted from average rate
evidence. Measure latency at unfavorable phases and retain the justified service
bound, including sequential-measurement blocking. Failure leaves the relevant
capability unavailable; do not broaden the envelope or relabel a deadline miss
as normal completion.

The backend records observed line frequency alongside the selected NPLC profile;
NPLC/line frequency is not used to fabricate either field's aperture. Cached
`target_transport_config_json` retains the VISA resource and I/O limits, and the
resource must match the reviewed device configuration. The host observation
margin includes compound bounded I/O; it is separate from local timing accuracy.
Status inspection only updates logical observations; finite timers are disabled
through explicit initialization/preparation/abort/recovery rather than a query
side effect.

Use shared schema v4 with first-stored-reading origin, unresolved timestamp
reference and unknown actual START, I/V windows, availability, program mapping
and physical clock resolution until separate evidence establishes each claim.
Do not convert a coarse scope observation into calibrated electrical-aperture
timestamps. Preserve the independent raw archives and both untouched statuses.

Record each case's exact request/artifact digests, identity/settings, fresh
preconditions, authorization scope, raw data, event log, terminal observations,
retention/retrieval checks and timing method/results in new evidence files.
Promote only the case/profile/envelope actually demonstrated. Passing offline
tests, uploading the candidate or acquiring plausible I/V does not pass this
bench matrix or authorize use on a battery.
