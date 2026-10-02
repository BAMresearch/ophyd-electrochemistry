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

## Storage lifecycle

No buffer wrap/overwrite is permitted. Estimate required storage before arm;
reject programs that exceed proven capacity. Generic advertised reading capacity
is not a paired-record budget. Version one transfers terminal buffers in bounded
chunks and records counts/checksums. Acquisition outcome and data export outcome
are distinct. Collection is not a durable-storage acknowledgement: retain
instrument data until explicitly archived or discarded. A document transport
failure has no guaranteed exactly-once semantics; use sample_index/acquisition
ID for reconciliation and diagnostic export for replay.
