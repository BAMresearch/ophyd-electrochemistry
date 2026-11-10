# Measurement and provenance contract

All protocol values are SI (V, A, s, V/s). CV cycle means start -> first vertex
-> second vertex -> start; endpoint samples must not be duplicated at joins.
Actual discrete voltage steps and achieved scan timing belong in configuration.
Pulses have explicit baseline, width, period, and finite count. Hold acquisition
starts its elapsed clock on actual execution, not preparation or external arming.

## Buffered record

The hardware-neutral Python representation is now fixed as
`ophyd-electrochemistry/measurement-v4`; see the [M5 data guide](m5-data.md).
It separates run-constant `MeasurementSchema`, per-sample `MeasurementRecord`,
terminal `RetainedBufferMetadata`, and offset-addressed `RecordChunk`. The first
M6 Bluesky projection is implemented for a terminal validated `RetainedBuffer`.
`project_current_hold_buffer` provides a pure frozen-2460-record projection;
the live target acquisition backend remains open.

Data keys are prefixed with the ophyd device name. The example device `ec` yields:

| Key | Type/unit | Meaning |
|---|---|---|
| `ec_sample_index` | integer | Zero-based monotonic acquisition index |
| `ec_time_relative` | number, s | Time since the declared `relative_time_origin`: actual START or first stored reading |
| `ec_instrument_timestamp` | number, s | Unmodified instrument clock value |
| `ec_voltage_aperture_start`, `ec_voltage_aperture_end` | number, s | Voltage integration window relative to actual START, or `-1` when unknown |
| `ec_current_aperture_start`, `ec_current_aperture_end` | number, s | Current integration window relative to actual START, or `-1` when unknown |
| `ec_voltage_timing_origin`, `ec_current_timing_origin` | string | Each aperture's provenance, or `unknown` |
| `ec_voltage_timing_uncertainty`, `ec_current_timing_uncertainty` | number, s | Each window boundary's conservative uncertainty, or `-1` when unestablished |
| `ec_available` | number, s | Earliest established complete-record availability, or `-1` when unknown |
| `ec_voltage` | number, V | Measured voltage or explicitly identified source readback |
| `ec_current` | number, A | Measured current or explicitly identified source readback |
| `ec_source_function` | string | `voltage` or `current` |
| `ec_source_setpoint` | number, V or A | Commanded level; split descriptors if unit changes |
| `ec_source_status` | integer | Untouched source-status word with a schema-level definition |
| `ec_measurement_status` | integer | Untouched measurement-status word with a schema-level definition |
| `ec_cycle_index`, `ec_segment_index` | integer | Zero-based program position |
| `ec_first_logical_point`, `ec_last_logical_point` | integer | Source-point aperture extent, or `-1` when mapping is unknown |
| `ec_repeat_index`, `ec_point_index` | integer | Source repeat/point at aperture start, or `-1` when unknown |
| `ec_mapping_quality` | string | Settled/unsettled single dwell, crossing, or unknown |

Source readback and measurement may be obtained sequentially or through buffer
options; no simultaneous dual-channel V/I guarantee is assumed. Declare each
field's origin and integration aperture independently. A known aperture carries
its own timing origin and optional uncertainty; an unproved aperture is absent,
not an assumed NPLC interval. The fixed stream uses `-1` only as a declared
unknown sentinel; it is not a negative physical time. Unknown aperture timing
requires unknown program mapping for the complete pair.

Configuration declares `relative_time_origin`, `instrument_timestamp_origin`,
`actual_start_known`, `timestamp_reference` and `timestamp_field`. The relative
zero point, raw instrument clock origin and physical timestamp reference are
independent. `RelativeTimeOrigin.ACTUAL_START` requires an instrument START
anchor for populated buffers. `FIRST_STORED_READING` requires an unknown START
anchor and zero relative time for the first stored reading. In that mode both
field windows, availability and program mapping must remain unknown. First-
reading zero is not physical START and supplies no START-alignment accuracy.
`instrument_timestamp_resolution_s` may be `None` for first-reading-relative
records when physical clock resolution is unestablished; printed timestamp
precision is not resolution or accuracy. Actual-START mode requires a known
positive resolution.
Flyer configuration also reports `voltage_origin`, `current_origin`,
`timing_origin` and `instrument_timestamp_resolution` (seconds, `-1` when
unknown). Provenance and physical clock resolution are distinct claims.

A timestamp at aperture start/midpoint/end selects one known field window.
`buffer_stored` and `unresolved` references select no field and establish no aperture. The raw
instrument timestamp and actual-START anchor remain separate. A known complete-
record availability must follow both known windows and any stored timestamp.
A stored timestamp must itself follow both known field windows. These checks
apply on a known actual-START time axis; changing the relative zero cannot
establish an aperture or a physical START observation.
The run configuration records the measurement-schema ID plus independent source
and measurement status-word definitions so the raw integer columns remain
interpretable without backend-private knowledge.
If usable paired V/I is unsupported, reject that acquisition schema rather than
inventing a second measurement. Limits/compliance flags must be retained.

## Event schema and clocks

`describe_collect()` initially returns `{'electrochemistry': {key: DataKey}}`;
scalar keys have shape `[]`, dtype, units where relevant, and a source URI.
`collect()` emits `{'time': epoch_s, 'data': {...}, 'timestamps': {...}}`.
Bluesky supplies event IDs, descriptors, and sequence numbers.

Store raw instrument timestamps and their interpretation. Map to epoch using a
measured calibration/anchor with uncertainty; record host round-trip brackets,
instrument clock offset/drift checks, timestamp resolution, and START anchor.
NTP on the hosts does not synchronize the instrument. If timestamps cannot be
mapped reliably, expose that limitation and use common hardware markers.
SAXS/WAXS correlation uses detector exposure start/end, not upload/callback time.

`primary` cached snapshots reuse their acquisition sample timestamp and add
snapshot age, validity, state, and output-state information. Never timestamp an
old sample as a fresh physical measurement. Stream schemas remain fixed during
an acquisition; optional fields require a descriptor/version change.

## Configuration and run metadata

Record protocol schema version and canonical JSON/hash; request/start mode;
experiment ID; device/serial/firmware; terminals and sense mode; physical limits;
I/O allocation, polarity and edge; timing/NPLC/ranges/filter/autozero; actual
compiled steps/period; buffer capacity/record count; Python/library and runtime
ABI/version/digest; contract revision; clock mapping and uncertainty; outcome,
termination cause, and complete/partial status. Outcomes require terminal records
or stop metadata because initial configuration cannot foresee an abort.

Operational preparation binds the request/program digests, expected measurement
records and effective buffer capacity in `PreparedAcquisition`. Before collection,
terminal `RetainedBufferMetadata` must match those four prepared values plus the
acquisition ID. This check detects stale or mis-associated buffers; a mismatch
fails collection and never implies permission to discard retained data.

## Waveform provenance and sample mapping

For PRBS/arbitrary/multisine programs in contract revision 0.2,
retain both generator intent and the exact expanded/compiled schedule. A request
hash identifies intent; a distinct compiled-program hash covers actual source
levels, dwells, repeats, source/measurement schedule, compiler version and relevant
capability/configuration inputs. Requested and compiled values must not overwrite
each other. Preserve actual generated floating-point values in a documented
canonical encoding so a digest can be checked without regenerating trigonometry.

| Waveform | Required additional provenance |
|---|---|
| All | Source function/units, unique list and total logical point count, repeats, requested/achieved source period and measurement schedule, duration, timing tolerances, quantization, range/compliance/cutoff policy, schedule encoding/digest and resource budget |
| PRBS | Generator/version, order, polynomial/taps, register/shift/output convention, seed, bit-to-level mapping, expanded sequence/digest, bit period, exact finite mean and commanded charge for current sourcing |
| Multisine | Generator/version, bias, tone bins/frequencies, peak amplitudes, explicit phases, point count, requested/achieved frequencies, actual samples/digest, mean/AC RMS/AC crest factor and analytical safety envelope |
| Terminal outcome | Executed point/bit/repeat extent, source/measurement deadline faults, cutoff/compliance events, last confirmed output transition and timing, incomplete waveform and retained-record extent |

Waveform acquisitions must provide a reproducible mapping from each electrical
record to logical waveform point/PRBS bit and repeat, relative to actual execution
START, or explicitly declare unknown mapping. The descriptor must declare the
sample timestamp's physical reference and aperture. A single point index is valid
as a dwell association only when both field integration windows lie within that
dwell; each reading is still integrated. Crossing an edge requires overlap
information or an explicit ambiguous/invalid mapping flag;
unknown phase must not be represented as a valid index. Fields needed to expose
this mapping are now fixed in the M5 Python record contract. The M6 descriptor
will keep the same meanings and encode absent indices as `-1`; it must not
silently treat a crossing or unknown association as a valid single point.

M1 exposes planning metadata, generator provenance and scheduled sample mappings
in `CompiledProgram`; see the [implemented schema](m1-compiler.md). Planned tick
associations do not constitute observed transition timestamps or the M5 electrical
record schema. `canonical_program_json` is the exportable plan; no acquisition
or waveform artifact is written implicitly by compilation.

M2 adds explicitly synthetic aperture-average records, local virtual ticks,
source traces and a checksummed simulation diagnostic export. These are not
observed hardware records or calibrated epoch timestamps. The [M2 guide](m2-simulator.md)
defines their schema, fault/retention semantics and simulated field origin;
the target-backed M5 acquisition remains pending. M2 now also adapts frozen
synthetic data into the shared M5 record schema without changing its origin.

A slower measurement schedule may span multiple source points. Requested
setpoints remain distinct from measurements and source readback; record source
schedule uncertainty and any observed timing evidence rather than treating the
compiled timetable as a measured transition trace. Electrical data alone does
not prove per-point detector correlation or waveform fidelity.

The waveform/schedule must be exportable with the acquisition. Small schedules
may be configuration metadata; large arrays need a checksummed associated
artifact, referenced by immutable identifier/path with acquisition/experiment
IDs. A digest alone is insufficient. Archive the schedule with the measurements,
including diagnostic export on failure; do not place an unbounded array in every
event. This does not introduce streaming external-asset support into version one.

Detector correlation retains exposure start/end, phase and clock uncertainty
through the facility's separate run/experiment linkage. Detector frame rate
does not set source-update rate; integrated SAXS/WAXS frames must not be labelled
as resolved individual PRBS bits without validated timing/exposure evidence.

## Storage lifecycle

No buffer wrap/overwrite is permitted. Estimate required storage before arm;
reject programs that exceed proven capacity. Generic advertised reading capacity
is not a paired-record budget. Version one transfers terminal buffers in bounded,
offset-addressed chunks and records counts/checksums. Repeating the same
acquisition ID, offset and bound must return the same frozen records. Acquisition
outcome and data export outcome
are distinct. Collection is not a durable-storage acknowledgement: retain
instrument data until explicitly archived or discarded. A document transport
failure still has no guaranteed exactly-once document semantics; use
sample_index/acquisition ID for reconciliation and diagnostic export for replay.

## Timed target candidate provenance

The timed-resistor candidate adds cached `ec_target_*` configuration: hardware
readiness (0), timing status (`uncommissioned`), instrument identity, runtime
build/full SHA-256, measurement profile and canonical request/plan/config/transport JSON and observed line frequency.
Program identity covers the plan, configuration and exact runtime artifact.
The target export wrapper `ophyd-electrochemistry/k2460-timed-hold-export-v1`
contains these inputs and the complete retained buffer under a SHA-256 checksum,
written with exclusive creation. It is separate from raw proof archives v1/v2/v3;
no automatic import/adoption of an unknown retained hardware run is implemented.
Physical cadence, output duration and protection latency require the
[timed-hold commissioning checks](timed-hold-commissioning.md).
