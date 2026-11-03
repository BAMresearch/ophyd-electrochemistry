# M6 classic-Ophyd device

The first M6 slice implements `Keithley2460Device`, a classic `ophyd.Device`
and Flyer over a typed acquisition backend. It is operational with the
independent simulator transport and runs through a real Bluesky `RunEngine`.
It is not yet an operational hardware driver.

Import it from `ophyd_electrochemistry.keithley.k2460`:

```python
from ophyd_electrochemistry.keithley.k2460 import Keithley2460Device

device = Keithley2460Device(simulator_transport, name="ec")
```

The constructor does not connect or mutate a backend. `stage()` connects,
reconciles state and requires confirmed output OFF. The default capability
allowlist accepts `GalvanostaticHold` and `CurrentPulseSequence`; other public
protocol models fail with `UnsupportedCapabilityError` before the backend is
prepared.

`VoltagePulseSequence` is available to pure planning and simulation. A caller
may explicitly opt a simulator-backed device into it through
`supported_program_types`, as the offline quickstart does. Do not apply that
override to a target backend without the separate voltage-source runtime and
hardware evidence.

## Pulse start semantics

`CurrentPulseSequence(count=N)` is one finite acquisition. With external start,
one START edge releases the complete instrument-timed sequence of N alternating
pulse and baseline dwells. Host or detector timing is not used between pulses.

Shot-by-shot external synchronization uses N separate acquisitions with
`count=1`: prepare and arm, accept one START edge, complete and collect, then
archive or explicitly discard the retained acquisition, deassert START and
re-arm for the next edge. This keeps the one-START/one-acquisition lifecycle and
gives every shot a distinct acquisition ID. Its cadence includes host disposition
and re-arm latency, so it is not a substitute for a high-rate hardware-triggered
sequence. A single arm that consumes N external edges is not implemented; it
would require a separate request mode, runtime behavior and repeated-input
hardware evidence.

The plan templates in `examples/follower.py` make both lifecycles executable.
`pulse_train_acquisition()` runs one pulse-sequence acquisition.
`repeated_single_pulse_acquisitions()` opens a separate follower run for every
externally triggered `count=1` shot and requires a unique raw-archive destination
for each. A shot is discarded only after its raw export succeeds. Execution or
export failure stops the sequence and leaves that acquisition retained.

## Implemented lifecycle

- `prepare()` creates a unique acquisition ID, delegates bounded backend
  validation, requires PREPARED with output OFF, and stores a validated
  `PreparedAcquisition` identity/storage/timing result for this request. A
  returned request digest must match the submitted request.
- `kickoff()` returns an Ophyd `Status`. Immediate mode requires RUNNING or an
  already-complete short program. External mode succeeds at WAITING_START with
  output OFF; it does not wait for START.
- `complete()` returns the same acquisition Status on repeated calls and uses a
  background monitor. Normal completion requires COMPLETE and confirmed output
  OFF. Its host watchdog is recalculated after every successful prepare from
  the backend-reviewed local completion bound and communication margin, with
  constructor `completion_timeout_s` retained only as a minimum. Abort, local
  START timeout and faults fail acquisition completion.
- `abort()` has a distinct successful shutdown Status while failing the
  acquisition Status. `recover()` requires ABORTED or ERROR and confirms IDLE/OFF.
- `stop()` is bounded and synchronous for Bluesky cleanup. `unstage()` confirms
  output OFF before closing the backend. Pause aborts; resume is rejected.
- `describe_collect()` and `collect()` emit the fixed M5 electrochemistry stream.
  Collection is terminal-only and requests retryable offset-addressed chunks;
  it neither materializes the full buffer in the Device nor discards retained
  data. Before reading chunks, acquisition ID, request/program digests, expected
  records and capacity must match the successful prepare result.
- Terminal `trigger()` latches the most recent retained record for `read()`.
  A live running snapshot remains open work.
- Diagnostic export and reason-bearing exact-acquisition discard remain explicit
operations. Collection does not imply durable archival or authorize discard.
Rejected re-preparation leaves an earlier retained acquisition's identity and
collection path intact. Explicit discard clears its prepared/effective watchdog
metadata before another request is accepted.

`collection_chunk_records` is the only runtime-configurable field in this slice.
It defaults to 128 and is bounded to 1–4,096 records. Configuration changes are
rejected while an acquisition is active. Physical limits, terminals and timing
remain constructor/backend configuration rather than mutable Ophyd settings.
The backend-selected `start_edge` (`rising`, `falling` or `either`) is reported
as read-only Device configuration after staging. It belongs to the commissioned
I/O profile and cannot be changed through `configure()` while running a plan.
`read_configuration()` reports `completion_timeout` as the configured minimum,
plus `prepared_completion_bound`, `prepared_communication_margin`, and the
resulting `completion_timeout_effective`. It also reports the prepared request
and program SHA-256 digests plus expected and capacity record counts. Numeric
prepared/effective values are zero and digests are `unavailable` until a prepare
succeeds; they are replaced, not accumulated, for each new acquisition.

Retained-schema configuration reports `relative_time_origin`,
`instrument_timestamp_origin`, `actual_start_known`, `timestamp_reference` and
`timestamp_field`, plus `voltage_origin`, `current_origin`, `timing_origin` and
`instrument_timestamp_resolution`. Resolution is in seconds and uses `-1` when
unknown; printed raw precision does not establish physical resolution. Before
retained schema is available, origins/references are
`unknown` and `actual_start_known` is `0`; it is `1` only with an established
instrument START anchor. These are configuration fields; the event stream
remains fixed at 25 fields.

When a `ClockMapping` exists, event-envelope time is mapped from the instrument
timestamp. Otherwise the required Bluesky event envelope uses host emission
time while the unmodified instrument and relative/aperture times remain explicit
data fields. Such events must not be treated as epoch-correlated measurements.

## Deliberate target boundary

The packaged 2460 runtime returns current readback, measured voltage, separate
source and measurement status words
and a `relativetimestamps` value whose first record is zero. That establishes a
first-reading-relative buffer clock, not actual START or the integration aperture.
Shared schema v4 can preserve this with `FIRST_STORED_READING`, unknown actual
START, unknown field windows/availability and unknown program mapping. Its
timestamp reference must remain honestly declared; neither a zero timestamp nor
clock resolution establishes physical START or 10 ms accuracy.
The shared schema also permits unknown physical clock resolution in first-reading
mode; raw printed timestamp precision supplies no such evidence.

`project_current_hold_buffer` converts a complete frozen extent of validated raw
chunks into shared records, including a partial acquisition's retained prefix.
It preserves raw buffer time, measured voltage, current readback and separate
statuses while declaring unknown START, windows, availability, mapping and
physical resolution. It is a pure function, not a connection owner or live
`M4RuntimeController` backend; it does not change the runtime or commission
physical timestamp meaning.

`Keithley2460Backend` now supplies an explicitly opted-in resistor commissioning
candidate for `GalvanostaticHold`. It uses the exact separately installed v15
artifact, one serialized connection owner, nominal local duration/cadence timers,
local measured-voltage cutoff/deadline checks and bounded terminal chunk reads.
It rejects pulses, voltage sourcing, unsupported firmware and unknown retained
records. Connect performs inspection only; installation is a separate guarded
operation. `hardware_ready=False` and `commissioning_resistor=True` keep its
uncommissioned scope explicit. The independent simulator remains the operational
offline backend.

The new path preserves both requested duration and sample spacing; it does not
infer execution duration from v14 average reading rates. Measurement requests
are nominally at 0, P, ..., duration-P, followed by output OFF at the duration
timer. The schedule requires an exact integer duration/P, P >= 0.1 s, duration
<= 60 s and <= 600 records. A half-slot deadline checks for lateness before and
after each sequential measurement; such checks cannot preempt that measurement.
Timer consumption, physical cadence/duration and cutoff/abort latency remain
bench gates. The returned completion bound is a candidate operational watchdog
input, not an established physical protection bound. See the
[commissioning procedure](timed-hold-commissioning.md).

Target configuration reports cached `target_hardware_ready`, `target_timing_status`,
instrument identity, runtime build/digest, profile and canonical request/plan/config
JSON. No configuration read queries hardware. The candidate defaults to the new
2 V / 1 NPLC measurement profile for the planned higher-signal resistor case.
That profile is unavailable in the unchanged v14 count-proof API.

A known terminal model fault with output still ON triggers one shutdown attempt
during completion cleanup. Uncertain delivery latches ERROR/UNKNOWN and requires
explicit stopped/OFF inspection and recovery; the mutation is not retried. Failed
outcomes remain failed after recovery. Overrun flags are assessed at the first
terminal observation; later idle START edges do not retroactively relabel a
previously observed clean completion. Transfers validate unchanged retained
extent and produce deterministic frozen shared chunks. Exclusive-create exports
include full request/config/plan/identity/artifact/record provenance with a
checksum; disposition requires the exact acquisition ID and a reason.
Physical START/aperture calibration remains necessary for timing claims, rather
than a prerequisite for retaining honestly uncalibrated I/V data.

## Validation scope

Recovery preserves collection and snapshot access to the previous retained
acquisition from confirmed output-OFF IDLE, even if no header was fetched before
recovery. Explicit disposition still requires its exact acquisition ID and a
reason. Recovery does not change the failed completion outcome.

Contract tests run holds and two-pulse trains through Bluesky for immediate and
external starts, validate Event Model documents, verify the complete 25-field
stream, preserve partial records after abort, exercise no-START timeout and
recovery, and reject unsupported CV before output. These are software lifecycle
results over synthetic data; no hardware gate is promoted.
