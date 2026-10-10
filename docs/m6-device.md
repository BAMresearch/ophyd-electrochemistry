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
  validation and requires PREPARED with output OFF.
- `kickoff()` returns an Ophyd `Status`. Immediate mode requires RUNNING or an
  already-complete short program. External mode succeeds at WAITING_START with
  output OFF; it does not wait for START.
- `complete()` returns the same acquisition Status on repeated calls and uses a
  background monitor. Normal completion requires COMPLETE and confirmed output
  OFF. Abort, local START timeout and faults fail acquisition completion.
- `abort()` has a distinct successful shutdown Status while failing the
  acquisition Status. `recover()` requires ABORTED or ERROR and confirms IDLE/OFF.
- `stop()` is bounded and synchronous for Bluesky cleanup. `unstage()` confirms
  output OFF before closing the backend. Pause aborts; resume is rejected.
- `describe_collect()` and `collect()` emit the fixed M5 electrochemistry stream.
  Collection is terminal-only and requests retryable offset-addressed chunks;
  it neither materializes the full buffer in the Device nor discards retained data.
- Terminal `trigger()` latches the most recent retained record for `read()`.
  A live running snapshot remains open work.
- Diagnostic export and reason-bearing exact-acquisition discard remain explicit
operations. Collection does not imply durable archival or authorize discard.

`collection_chunk_records` is the only runtime-configurable field in this slice.
It defaults to 128 and is bounded to 1–4,096 records. Configuration changes are
rejected while an acquisition is active. Physical limits, terminals and timing
remain constructor/backend configuration rather than mutable Ophyd settings.
The backend-selected `start_edge` (`rising`, `falling` or `either`) is reported
as read-only Device configuration after staging. It belongs to the commissioned
I/O profile and cannot be changed through `configure()` while running a plan.

When a `ClockMapping` exists, event-envelope time is mapped from the instrument
timestamp. Otherwise the required Bluesky event envelope uses host emission
time while the unmodified instrument and relative/aperture times remain explicit
data fields. Such events must not be treated as epoch-correlated measurements.

## Deliberate target boundary

The packaged 2460 runtime returns current readback, measured voltage, separate
source and measurement status words
and a `relativetimestamps` value whose first record is zero. That establishes a
first-reading-relative buffer clock, not actual START or the integration aperture.
The public M5 schema requires actual-START-relative aperture meaning. Therefore
the M6 backend protocol consumes validated schema and retained metadata plus
bounded, offset-addressed `RecordChunk` values, and no adapter from
`M4RuntimeController` is supplied yet.

This boundary prevents a convenient but false timestamp projection. Hardware
connection requires either additional runtime fields or validated timing evidence
that supplies the missing START/aperture relationship. The Device-side bounded
chunk projection is ready; a target adapter must supply matching schema,
metadata and retryable `RecordChunk` transactions.

## Validation scope

Contract tests run holds and two-pulse trains through Bluesky for immediate and
external starts, validate Event Model documents, verify the complete 19-field
stream, preserve partial records after abort, exercise no-START timeout and
recovery, and reject unsupported CV before output. These are software lifecycle
results over synthetic data; no hardware gate is promoted.
