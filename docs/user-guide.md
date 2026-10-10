# User guide and quickstart

This is the user-facing entry point for configuring an experiment, running the
Ophyd lifecycle and interpreting the resulting paired current/voltage records.
The operational device currently runs against the independent simulator. The
real 2460 adapter remains blocked on the target START/aperture timestamp proof.

The companion `notebooks/ophyd_electrochemistry_quickstart.ipynb` is an offline
executable example. It creates a simulated current-pulse experiment, runs it
through a real Bluesky RunEngine, tabulates the emitted records and draws a
preliminary I/V time-series plot. It never opens a VISA connection.

## Configuration model

Configuration is divided into three layers. This prevents an experiment from
leaving the instrument partially configured through a sequence of unrelated
signal writes.

### Commissioned hardware configuration

The beamline or instrument owner creates a reviewed `Keithley2460Config` in the
startup environment:

```python
config = Keithley2460Config(
    visa_resource="TCPIP0::169.254.113.151::5025::SOCKET",
    source_terminal="front",
    sense="remote",  # four-wire
    io=DigitalIOConfig(
        ready=1,
        busy=2,
        start=3,
        start_edge="rising",
    ),
    safety=SafetyConfig(
        voltage_min_v=-0.2,
        voltage_max_v=0.2,
        charge_current_max_a=0.001,
        discharge_current_max_a=0.001,
        power_abs_max_w=0.001,
        source_off_mode="normal",
    ),
    timing=TimingPolicy(
        command_timeout_s=10,
        external_start_timeout_s=60,
        shutdown_timeout_s=2,
        poll_period_s=0.01,
        required_abort_latency_s=1,
        required_cutoff_latency_s=1,
    ),
)
```

This layer owns the resource address, terminals, two/four-wire sense, digital
I/O allocation and edge, safety envelope, output-off mode, timeouts and buffer
ceilings. Routine plans should report these settings but not mutate them. A
separate bench-evidence capability profile declares the source/timing/storage
behavior that has actually been commissioned for a particular firmware and
wiring setup.

### Per-run scientific intent

The user creates one immutable `AcquisitionRequest`. A pulse train is expressed
in SI units:

```python
request = AcquisitionRequest(
    experiment_id="coin-cell-pulse-0042",
    start_mode=StartMode.EXTERNAL_TRIGGER,
    program=CurrentPulseSequence(
        baseline_current_a=0.0,
        pulse_current_a=0.001,
        pulse_width_s=0.100,
        period_s=1.000,
        count=20,
        sample_period_s=0.020,
        voltage_limit_v=0.2,
    ),
)
```

The request holds source levels, durations, measurement period, compliance,
optional cutoffs and start mode. It is compiled and validated as a whole before
arming. Requests that exceed the commissioned electrical envelope, cannot fit
the measurement aperture, exceed finite memory, miss a timing tolerance or use
an uncommissioned feature fail before output is enabled.

### Measurement profiles

The commissioning runtime currently fixes 1 NPLC, filter off, relative
measurement off, one explicit autozero followed by autozero off, and fixed
ranges. The operational backend should replace those hidden constants with an
immutable `MeasurementSettings` model or reviewed named profiles such as
`pulse_fast`, `hold_precision` and `cv_standard`.

Timing-sensitive pulse profiles should normally use fixed source and
measurement ranges because autoranging can introduce variable latency. The
chosen NPLC, autozero/filter/range policy and profile identity must be emitted
as configuration metadata. This model is planned, not yet a public API.

### Ophyd runtime configuration

The current `configure()` surface deliberately permits only the nonphysical
retrieval chunk size:

```python
before, after = ec.configure({"collection_chunk_records": 512})
```

It is limited to 1–4096 records and cannot be changed during an active
acquisition. `read_configuration()` currently reports the backend, host polling
and completion timeouts, chunk size, start mode, commissioned START edge and
acquisition ID. The run descriptor also records the measurement-schema ID and,
once retained data are available, the definitions of both status words. The
target backend should additionally report model, serial,
firmware, terminals, sense, off mode, safety/capability profile and complete
measurement settings.

There is intentionally no raw SCPI/TSP escape hatch and no sequence such as
`current.put()`, `range.put()`, `output.put()`. The backend receives one
validated finite request.

## Run lifecycle

A low-level Bluesky plan follows the standard Flyer lifecycle:

```python
yield from bps.stage(ec)
yield from bps.prepare(ec, request, wait=True)
yield from bps.kickoff(ec, wait=True)
yield from bps.complete(ec, wait=True)
yield from bps.collect(ec, return_payload=False)
yield from bps.unstage(ec)
```

- `stage()` connects, reconciles state and requires confirmed output OFF.
- `prepare()` validates and loads the complete finite acquisition while keeping
  output OFF.
- `kickoff()` starts immediate mode or returns after external mode has armed
  READY with output OFF.
- `complete()` waits for the finite instrument-owned program and confirms output
  OFF.
- `collect()` retrieves retained records through retryable bounded chunks; it
  neither erases the instrument buffer nor implies durable archival.
- `unstage()` confirms output OFF before closing the backend.

Normal users should call a plan wrapper such as `follower_acquisition()` or
`pulse_train_acquisition()` from `examples/follower.py`. A single external
START releases a complete locally timed pulse train. Trigger-per-pulse operation
uses separately prepared `count=1` acquisitions, each with a unique acquisition
ID and archive destination.

## Resulting I/V data

Each sample is one paired record, not an independently timed voltage row and
current row. A real current-source resistor proof produced:

| sample | first-reading-relative time (s) | measured voltage (V) | source-current readback (A) | source status | measurement status |
|---:|---:|---:|---:|---:|---:|
| 0 | 0.00000000 | 0.102512836 | 0.0010000132 | 200 | 264 |
| 1 | 0.04083408 | 0.102512121 | 0.0010000130 | 200 | 8 |
| 2 | 0.08166742 | 0.102511644 | 0.0010000127 | 200 | 8 |
| 3 | 0.12250006 | 0.102511168 | 0.0010000130 | 200 | 8 |
| 4 | 0.16333348 | 0.102511406 | 0.0010000129 | 200 | 8 |

For current sourcing, voltage is measured and current is source readback. The
commanded current remains a separate source-setpoint field. The raw checked JSON
archive also records request, instrument and runtime identities, capacity,
checksums and the two untouched instrument status words.

The normalized Bluesky stream is named `electrochemistry`. Every event includes
sample index; actual-START-relative, instrument, aperture and availability
times; voltage and current; source function and setpoint; aperture-to-program
mapping quality; and pulse/repeat/cycle/segment indices. A missing program index
is represented as `-1` in the fixed Bluesky event schema and as `None` in the
typed Python record.

For a pulse experiment, records are scheduled measurements rather than one row
per pulse. Mapping metadata distinguishes a settled aperture inside one dwell,
an unsettled aperture, an aperture crossing a transition, and an unknown
association.

## Source and measurement status

The hardware-near archive and shared measurement schema preserve separate
status words:

- `ec_source_status` — untouched source status word;
- `ec_measurement_status` — untouched measurement status word.

The run-constant schema carries an independent definition for each word.
Decoded convenience flags may be added later, but these raw values remain
authoritative. Simulator source-status bit 0 denotes synthetic compliance during
at least part of the aperture; its measurement status is currently zero. Those
simulator meanings are not Keithley bit definitions.

## Preliminary visualization

The quickstart plots both measured voltage and current against
`ec_time_relative`, shades pulse dwells from the commanded schedule and prints a
compact record table. The plot is a first-look diagnostic, not an acceptance
test: it does not establish calibration, timing accuracy, settling, compliance
behavior or hardware synchronization.

When an instrument-to-epoch `ClockMapping` is available, Bluesky event-envelope
time uses it. Otherwise the event envelope uses host emission time solely to
satisfy Event Model; the unmodified instrument and relative/aperture times remain
explicit data fields and are the correct values for acquisition-timing analysis.

## Living-document review points

Review this guide and the quickstart notebook whenever any of the following
changes:

- the measurement or status schema;
- public protocol/request fields or measurement profiles;
- device lifecycle, retained-data or archive behavior;
- target backend configuration and reported metadata;
- a hardware acceptance gate changes status;
- a new commissioned protocol becomes available.

Milestone completion should include this review even when no edit is needed.
The guide must distinguish implemented simulator behavior, prepared target code
and reviewed hardware evidence.
