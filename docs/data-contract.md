# Measurement and provenance contract

All protocol values are SI (V, A, s, V/s). CV cycle means start -> first vertex
-> second vertex -> start; endpoint samples must not be duplicated at joins.
Actual discrete voltage steps and achieved scan timing belong in configuration.
Pulses have explicit baseline, width, period, and finite count. Hold acquisition
starts its elapsed clock on actual execution, not preparation or external arming.

## Buffered record

Data keys are prefixed with the ophyd device name. The example device `ec` yields:

| Key | Type/unit | Meaning |
|---|---|---|
| `ec_sample_index` | integer | Zero-based monotonic acquisition index |
| `ec_time_relative` | number, s | Instrument time since actual START |
| `ec_voltage` | number, V | Measured voltage or explicitly identified source readback |
| `ec_current` | number, A | Measured current or explicitly identified source readback |
| `ec_source_function` | string | `voltage` or `current` |
| `ec_source_setpoint` | number, V or A | Commanded level; split descriptors if unit changes |
| `ec_status_bits` | integer | Raw instrument flags plus documented interpretation |
| `ec_cycle_index`, `ec_segment_index` | integer | Zero-based program position |

Source readback and measurement may be obtained sequentially or through buffer
options; no simultaneous dual-channel V/I guarantee is assumed. Declare each
field's origin, integration aperture, and any timestamp offset in metadata.
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
START. The descriptor must declare the sample timestamp's physical reference
and aperture. A single point index is valid as a dwell association only when
its integration aperture lies within that dwell; the reading is still integrated.
Crossing an edge
requires overlap information or an explicit ambiguous/invalid mapping flag;
unknown phase must not be represented as a valid index. Fields needed to expose
this mapping are finalized with their fixed descriptor schema at M5.

M1 exposes planning metadata, generator provenance and scheduled sample mappings
in `CompiledProgram`; see the [implemented schema](m1-compiler.md). Planned tick
associations do not constitute observed transition timestamps or the M5 electrical
record schema. `canonical_program_json` is the exportable plan; no acquisition
or waveform artifact is written implicitly by compilation.

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
is not a paired-record budget. Version one transfers terminal buffers in bounded
chunks and records counts/checksums. Acquisition outcome and data export outcome
are distinct. Collection is not a durable-storage acknowledgement: retain
instrument data until explicitly archived or discarded. A document transport
failure has no guaranteed exactly-once semantics; use sample_index/acquisition
ID for reconciliation and diagnostic export for replay.
