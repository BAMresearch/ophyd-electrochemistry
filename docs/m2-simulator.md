# M2 independent simulator

M2 implements a deterministic fake runtime and typed fake host transport over
M1's finite planning IR. It never opens VISA, interprets TSP, uploads a script or
energizes hardware. Use it to test lifecycle, schedules and retained evidence;
all hardware gates remain **NOT RUN**. The first M6 device slice now wraps this
typed backend and exercises it through a real RunEngine; that does not make
simulated electrical behavior hardware evidence.

## QuickStart

```bash
uv run --locked python -m examples.simulate_program
uv run --locked pytest tests/simulator
```

The example uses the explicitly synthetic profile from `examples.compile_program`:
PRBS order 7, seed 1, +/-10 mA, 10 ms bit dwell, ten repeats and 20 ms electrical
sampling. It waits 15 virtual ticks before START, then executes 1,270 logical
source points over 12,700 ticks and retains 635 synthetic records. The profile
defines a 1 ms tick; none of its values are commissioned instrument specifications
or generally safe battery limits. The example writes no data file implicitly.

## API and ownership

Import `SimulatedRuntime`, `FakeTransport`, `FakeCell` and `FaultInjection` from
`ophyd_electrochemistry.simulation`. Construction requires explicit capabilities,
configuration and an ideal synthetic cell. Profiles must have
`evidence_kind="simulation"`. No new dependencies are needed.

| API | Implemented behavior |
|---|---|
| `runtime.prepare(request, acquisition_id=...)` | Pure M1 compilation before mutation; output OFF, flags down, PREPARED; unique session acquisition ID |
| `runtime.kickoff()` | Immediate execution or installed external wait with READY; asserted inputs reject arming |
| `runtime.set_inputs(start=..., abort=...)` | Physical START HIGH/LOW level plus logical ABORT assertion; applies configured rising/falling/either START detection, ignores stale/out-of-wait edges, and gives enabled asserted ABORT precedence in a coincident batch |
| `runtime.advance_ticks(n)` | Advances independent integer clock and executes all due local events, without sleeps or host polling |
| `runtime.completion()` | Nonblocking: pending `None`, successful `TerminalOutcome`, or failure exception |
| `runtime.abort(reason=...)` | Confirmed OFF and unsuccessful acquisition; repeated abort preserves outcome; lost acknowledgement raises `ShutdownUnconfirmed` |
| `runtime.recover()` | Explicit stopped/OFF/inactive-input check, then IDLE; never clears evidence or re-arms |
| `runtime.latch_snapshot()` / `read_snapshot()` | Immutable coherent buffered sample, original aperture-start tick and age at latch; no extra measurement |
| `runtime.collect_chunk(max_records=...)` | Bounded chunks from a frozen terminal buffer; only previously unemitted records |
| `runtime.retained_buffer()` | Adapt frozen synthetic records into the shared M5 schema with explicit synthetic origin |
| `runtime.read_record_chunk(offset=..., max_records=...)` | Deterministic retryable M5 slice; does not advance the legacy collection cursor |
| `runtime.export_retained_data(path)` | Complete diagnostic JSON, schedule, traces, raw synthetic records, outcome and checksum; does not discard |
| `runtime.discard_retained_data(acquisition_id=..., reason=...)` | Explicit exact-ID disposition permits next preparation; keeps evidence readable until then |

These are synchronous **simulation methods**, not ophyd Status/Flyer methods.
The existing RunEngine witness remains a separate API compatibility test. M6
will adapt the established runtime/transport behavior to actual Status objects,
descriptors, cleanup hooks and partial events.

`FakeTransport` provides the host methods above except local clock/input control.
`connect()` establishes a link but leaves host output UNKNOWN; `inspect()`
reconciles state. Transactions use one runtime lock. Disconnecting does not halt
the local program or its finite START timeout. `poll_until_terminal(max_ticks=...,
poll_ticks=...)` is a bounded test convenience. Playback is invariant under the
poll interval because source actions are scheduled on the separate virtual clock.

Do not wrap these methods with a raw `write()`/`query()` string API: this simulator
does not emulate PyVISA framing or firmware commands. M3 characterizes that layer.
Prepared, reviewed, versioned TSP programs remain in scope for M4/M7. User-written
TSP upload/execution is outside the current scope.

## Independent scheduler and measurement meaning

The runtime consumes levels/dwells from the compiled IR but never calls the
compiler's `point_at_tick()` or `sample_mapping()` methods, nor a waveform
generator. Source actions advance by the preceding dwell. Repeats use the same
finite list, including equal adjacent PRBS bits; zero levels remain sourced.
No extra endpoint is inserted; every final dwell finishes before output OFF.
Only the next source and measurement events are queued, avoiding a tick-by-tick
loop or allocation of the whole repeated event queue.

The synthetic cell is an ideal voltage source in series with an explicit resistor.
It calculates V/I from the source level and applies ideal complementary
compliance. Source changes are instantaneous; declared settling windows only
mark mapping validity. There is no electrochemical kinetics, analog rise/settling,
noise, heating, leakage, physical OFF-mode loading or independent paired V/I
measurement model.

Each `SimulatedRecord` integrates that ideal response across its entire half-open
aperture `[start, end)`, and becomes available after measurement overhead. It
retains first/last logical points, repeat/point/cycle/segment indices, commanded
setpoint at aperture start, and a flag for a wholly settled single dwell. An
aperture ending exactly on a source edge excludes the new point; an aperture
crossing an edge reports both points and the integrated response. Source setpoint
is not substituted for the synthetic voltage/current values.

At equal ticks, injected sensor faults precede measurement closure; aperture
closure and publication precede normal termination and source updates; new
apertures open last. A separate input batch executes at the current tick in caller
order. ABORT within one coincident input batch wins over START. These explicit
rules make tests reproducible; firmware must prove its own ordering.

Simulator status bit `1` means ideal compliance was active during at least part of
the aperture. It is **not** a raw Keithley status bit. Every record has synthetic
origin. Tick timestamps have no epoch calibration, offset/drift uncertainty or
detector synchronization claim. The M5 adapter preserves that limitation and
does not relabel these values as observed hardware measurements.

Current-source cutoffs compare synthetic voltage against the compiled lower/upper
limits, including equality. With measurement-based monitoring, the retained
published aperture average triggers termination. With independent monitoring,
the ideal voltage is checked at each source transition; it is constant between
transitions in this circuit. Shutdown acknowledgement is instantaneous in the
normal simulation. Declared abort/cutoff latencies are validated by M1 but are
not modeled as calibrated shutdown delays, proof of preemption, or physical
protection. There is no emergency-stop claim.

## Faults, host uncertainty and retention

`FaultInjection` accepts zero-based source-deadline point and measurement-deadline
sample indices, a sensor-failure tick relative to actual START, a downward buffer
capacity override, and lost shutdown confirmation. Source and complete electrical
records share the declared buffer budget; exhaustion aborts without wrapping or
overwriting. Failed acquisitions retain published complete records. An open
aperture or a closed aperture whose overhead/publication is unfinished is dropped
rather than represented as a complete measurement.

Confirmed local faults enter ABORTED. Lost OFF acknowledgement enters ERROR with
output `None` (UNKNOWN); failed completion and repeat abort cannot claim success.
`restore_shutdown_confirmation()` is a test hook; a subsequent explicit recovery
can confirm OFF, but the original failed outcome and uncertainty remain historical
evidence. New preparation requires explicit disposition even after recovery or
collection, including a terminal acquisition with zero records.

`link.fail_next_transaction()` loses a reply **after** the requested operation
has executed. Reconnect/inspect must reconcile it; no command is automatically
retried or START replayed. A lost chunk reply may already advance the collection
cursor. Collection is therefore not exactly-once delivery; diagnostic export
always retains all raw records for reconciliation by acquisition/sample ID.

The JSON export schema is `ophyd-electrochemistry/simulation-v1`, using M1's
finite canonical JSON encoding. Its envelope holds `sha256` and `payload`; the
digest covers sorted, compact UTF-8 canonical payload JSON. It includes the full
compiled schedule, request/plan hashes, virtual clock, cell/fault settings,
source trace, records and terminal outcome. It is a simulation diagnostic schema,
not a replacement for the future M5 acquisition archive.

## Verification boundaries

Independent expected traces cover holds, CV endpoints/cycles, pulse/baseline
dwells, literal PRBS vectors and analytical multisine samples. Aperture tests
integrate original request levels independently and check exact-edge/cross-edge
associations. Lifecycle tests cover READY races, stale/held/coincident inputs,
abort during waiting/playback/aperture/overhead, repeated cleanup, local START
timeout, partial-buffer retention and snapshot consistency. Fake-link tests cover
ambiguous mutations, disconnect, reconciliation, chunk loss and bounded polling.

This completes the scoped M2 simulator. It does not implement TSP ABI/digest
installation, hardware I/O polarity, observed source timing, Bluesky Device
integration or beamline runs. M3 now provides separately tested real framing,
timeouts and read-only identity commissioning; see the [M3 guide](m3-transport.md)
and [hardware acceptance](hardware-acceptance.md). Operational runtime work remains M4.
