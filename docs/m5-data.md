# M5 measurement records and retained buffers

M5 has a hardware-neutral record contract plus a deliberately narrow target
proof. The packaged Keithley runtime allocates a bounded fill-once buffer
for an explicitly bounded number of immediate or externally started 1 NPLC readings and returns raw
source readback, measured voltage, buffer-relative timestamp, source status and
measurement status. This is commissioning code, not the operational acquisition
backend.

## Public model

Import the immutable models from `ophyd_electrochemistry`:

- `MeasurementSchema` holds run-constant source function, voltage/current field
  origins, timing origin, timestamp reference and resolution, actual-START clock
  anchor, status-bit definition, optional clock-to-epoch mapping, and the explicit
  synthetic flag.
- `MeasurementRecord` holds one complete paired V/I record, raw instrument and
  actual-START-relative timestamps, aperture start/end/availability, source
  setpoint, status bits and source-schedule association.
- `RetainedBuffer` binds records to acquisition/request/program identities and
  finite capacity/expected-count/outcome metadata.
- `RetainedBufferMetadata` reports the retained extent and a checksum over all
  records. `RecordChunk` reports one bounded offset-addressed slice and its own
  checksum.

The schema identifier is `ophyd-electrochemistry/measurement-v1`. All electrical
and time values use SI units. Nonfinite values, invalid apertures, inconsistent
mapping indices, record gaps, capacity overruns, source-function changes and
timestamp-reference mismatches are rejected.

`FieldOrigin` distinguishes measured, source-readback and synthetic electrical
values. `TimingOrigin` independently distinguishes instrument-reported,
configuration-derived and synthetic timing. A record set may only claim
`synthetic=True` when both electrical values and timing are explicitly synthetic.
This prevents simulator output from being mistaken for target measurements.

## Aperture and waveform mapping

Every record contains a half-open integration aperture `[start, end)` and the
time at which the complete value became available. `TimestampReference` says
whether `time_relative_s` denotes aperture start, midpoint or end. The raw
instrument timestamp, actual-START instrument timestamp and clock resolution are
retained separately.

`MappingQuality` has four values:

| Value | Meaning |
|---|---|
| `settled_single_dwell` | Complete aperture lies in one source dwell after settling |
| `unsettled_single_dwell` | Complete aperture lies in one dwell but is not wholly settled |
| `crosses_transition` | Aperture spans more than one logical source point |
| `unknown` | No valid program association is available |

Known mappings require first/last logical point plus repeat, point, cycle and
segment indices. Unknown mappings require all six indices to be absent. The
future fixed Bluesky event projection will encode an absent index as `-1`; the
typed Python record keeps `None` so it cannot be mistaken for a valid index.
The source setpoint is the commanded value at aperture start and is never a
substitute for voltage or current.

## Deterministic readback

`RetainedBuffer.chunk(offset=..., max_records=...)` is a pure, bounded read from
a frozen terminal acquisition. Retrying the same acquisition ID and offset
returns the same records and checksum. Sample indices must be contiguous from
zero and agree with the chunk offset. `next_offset` is the next request boundary;
`final` becomes true when it equals the retained count. An offset equal to the
retained count returns an empty final chunk, while an offset beyond it fails.

This is deliberately different from an implicit destructive cursor. A transport
reply may still be lost, but an offset-addressed retry does not skip data. The
instrument buffer remains retained until the separate explicit archive/discard
lifecycle authorizes reuse.

Checksums use the project's sorted compact canonical JSON encoding and SHA-256.
They establish transfer consistency, not durable archival or physical validity.

## Simulator adapter and remaining boundary

The M2 runtime can expose a terminal `RetainedBuffer` and
`read_record_chunk(offset=..., max_records=...)`. It converts integer ticks to SI
seconds and preserves its aperture mapping, while marking all electrical and
timing origins synthetic. A no-START timeout produces an empty terminal buffer
with no fabricated START timestamp. The legacy cursor-based `collect_chunk()`
remains for compatibility and still demonstrates why lost cursor replies are
not exactly-once.

## Narrow target proof

Runtime build `m4-finite-current-hold-v12` provides
`CurrentHoldAcquisitionProof`, `RuntimeBufferInfo`, `BufferedReading`, and
`RuntimeRecordChunk`. Hard limits remain ±10 mA and 2 V, while configuration
limits may be lower. The acquisition count has an explicit host ceiling
(250,000 records by default) and a 5,000,000-record TSP backstop. The runtime
allocates the requested capacity (or the instrument's 16-record minimum), selects
fill-once mode, configures
voltage measurement at 1 NPLC, and records immediate versus external start mode
in the request. External mode keeps output OFF while READY is asserted; a START
edge locally asserts BUSY, clears the buffer, turns output on, makes exactly the
requested number of measurements, turns output off, and exits the successful
path without entering timeout cleanup. An unanswered wait takes a separate local
source-OFF timeout path and leaves the buffer empty. Immediate mode retains its
existing behavior. Retrieval
requires a terminal state and confirmed output off, and is separately bounded
to at most 4,096 records per transfer (128 by default). A retained buffer blocks
the next prepare until an explicit reason-bearing discard.

`RuntimeBufferArchive` v2 assembles a complete sequence of independently validated
chunks without hiding gaps or overlaps. Its versioned plain-data payload fixes
the runtime identity, request, instrument identity, capacity, timestamp origin
and raw records. The record array retains its own SHA-256, while the enclosing
JSON adds a checksum over the complete canonical payload. Version 2 records the
request start mode; the allowlisted parser still reads existing v1 immediate-mode
archives without changing their schema. It checks both checksums.
`write_runtime_buffer_archive()` uses exclusive creation and refuses
to overwrite an existing path; successful export does not itself authorize
instrument-buffer discard.

On 9 October 2026, the target 2460 acquired three records at +1 mA with a 0.2 V
limit from the front-terminal four-wire nominal 100 ohm resistor. Buffer extent
was 1–3 of capacity 16. All source statuses were 200, carrying the source
readback, four-wire-sense and output-on bits; all measurement statuses carried
the front-terminal bit. Mean measured V/I was 102.5112 ohm. Repeating the same
offset read returned the same records and SHA-256. Warning and error counts did
not change, recovery reached IDLE/OFF/0 A, and a new VISA session independently
confirmed that state while the three records remained retained. See the
[complete evidence](evidence/k2460-2026-10-09-m5-buffer-proof.json) and guarded
`notebooks/keithley_2460_m5_buffer.ipynb`.

This does not yet instantiate `MeasurementRecord`: the 2460
`relativetimestamps` field is zero at the first reading and therefore is not an
observed actual-START reference. The resistor is uncalibrated. Measurement
status 264 on the first record is the documented front-terminal bit plus the
first-reading-in-group bit; subsequent records report only the front-terminal
bit. No record has the questionable-measurement bit. These observations are not
generalized beyond this evidence.

A follow-up exercised the proof runtime's deliberately imposed maximum of eight
readings. Retrieval in
3+3+2-record chunks covered offsets 0–7; retrying the middle chunk returned the
same content and digest. The complete archive reloaded equal to its in-memory
form with payload SHA-256
`0e6d134e8bdca3c64463f0b935f2424ffd7807c172997c621f528b6694cdf841`.
Mean V/I was 102.5092 ohm, warning/error counts were unchanged, and a new session
verified all eight records retained at IDLE/OFF/0 A before explicit discard left
the buffer empty. See the
[run evidence](evidence/k2460-2026-10-09-m5-max8-evidence.json) and
[machine-verifiable raw archive](evidence/k2460-2026-10-09-m5-max8-archive.json).

Eight is not a 2460 capacity limit. With output off and all existing buffers left
intact, a temporary standard-style `buffer.make(0, ...)` allocation reported
5,110,784 currently available records on this firmware/configuration. The two
default buffers retained capacities of 100,000 each, the 16-record proof buffer
was unchanged, and the temporary buffer was deleted and garbage-collected. The
warning/error counts did not change and the source remained IDLE/OFF/0 A. This
is allocation evidence only: the buffer was not filled and no large transfer or
wrap behavior was tested. See the
[capacity-probe evidence](evidence/k2460-2026-10-09-buffer-capacity-probe.json).

Runtime build v6 then removed the artificial v5 limits. Safe replacement first
verified that the v5 buffer was empty and the source was IDLE/OFF/0 A. A 17-count
request allocated exactly 17 standard fill-once records, filled extent 1–17 and
was retrieved as 10+7 records; retrying the first 10-record transfer returned the
same digest. The checked archive reloaded exactly with payload SHA-256
`4368f2779dee394e4cf6097ac2fea9659c616046ab52b24809e5e6431bee8f77`.
Mean V/I was 102.5087 ohm. Warning/error counts remained 5/6, and an independent
session confirmed all 17 records retained at IDLE/OFF/0 A before explicit discard
left the exact-sized buffer empty. See the
[run evidence](evidence/k2460-2026-10-09-m5-v6-exact-buffer-evidence.json) and
[raw archive](evidence/k2460-2026-10-09-m5-v6-exact-buffer-archive.json). This
validates a small exact-size allocation and a transfer larger than the old
eight-record ceiling, not a 250,000-record acquisition or multi-megabyte reply.

Runtime v11 added external START to this buffer path, but its first five-record
target attempt exposed an older small-count defect: it requested five physical
buffer slots even though this 2460 applies a 16-record minimum. Preparation
stopped with no buffer, and the inherited finite-hold path completed output-off
without retaining readings. Runtime v12 allocates `max(requested count, 16)`,
still digitizes exactly the requested count, and rejects a physical capacity
smaller than the request.

The corrected target run waited with READY high and output off. Removing the
held 1 kohm START-to-ground resistor generated the accepted rising edge; the
runtime reported RUNNING/output-on/BUSY at digitize block 10, then COMPLETE at
block 13 with output and both flags low. Five records filled extent 1–5 of the
16-record buffer. Retry retrieval was identical, the version-2 archive preserved
`start_mode=external_trigger`, and warning/error counts did not change during
the v12 run. Recovery and a new session confirmed IDLE/OFF/0 A while all records
remained retained; explicit post-archive discard then emptied the buffer. Mean
V/I was 102.5105 ohm on the uncalibrated resistor. See the
[run evidence](evidence/k2460-2026-10-09-m5-external-start-v12-evidence.json)
and [checked raw archive](evidence/k2460-2026-10-09-m5-external-start-v12-archive.json).
This proves the narrow state/data path, not edge-to-aperture latency or electrical
timing.

The following remain open before M5 is complete:

- map target timestamps/apertures to actual START without inventing precision;
- extend fill-once/no-wrap and transfer evidence well beyond the 17-record proof;
- map this raw archive into the shared `MeasurementRecord` archive only after
  the START/aperture relationship is established;
- calibrate or explicitly decline instrument-clock-to-epoch mapping;
- complete G03, G04 and G06 hardware evidence.

The operational ophyd `describe_collect()`/`collect()` projection remains M6 and
will consume this fixed record meaning rather than inventing a second schema.
