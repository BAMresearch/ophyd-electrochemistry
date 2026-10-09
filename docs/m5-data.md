# M5 measurement records and retained buffers

M5 now has its first hardware-neutral implementation slice. It defines the
meaning, validation and integrity boundaries for finite electrical records before
the Keithley runtime is taught to acquire them. Nothing in this module connects
to an instrument or enables output; target buffer acquisition remains open.

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

The following remain open before M5 is complete:

- implement instrument-local paired V/I acquisition in the packaged TSP runtime;
- prove the 2460 buffer fields, status bits, timestamp reference and capacity on
  the nominal resistor setup;
- implement bounded offset-addressed target retrieval and full archive export;
- calibrate or explicitly decline instrument-clock-to-epoch mapping;
- complete G03, G04 and G06 hardware evidence.

The operational ophyd `describe_collect()`/`collect()` projection remains M6 and
will consume this fixed record meaning rather than inventing a second schema.
