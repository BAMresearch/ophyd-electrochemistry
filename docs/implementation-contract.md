# Implementation contract — revision 0.2

MUST is required behavior; SHOULD permits a documented alternative. This file
defines intended behavior. Current implementation coverage is in
[validation](validation.md), not implied by normative language.

## C01 — Ownership and boundaries

One follower process MUST exclusively own the instrument. The leader MUST NOT
open a competing control session. Protocol models MUST contain electrochemical
intent only; `AcquisitionRequest` carries start mode and the shared experiment ID.
The device MUST NOT know QueueServer URLs or leader identities. Python MUST NOT
time waveform steps through network writes and sleeps.

## C02 — Validation before output

`prepare(request)` MUST validate finite numbers, duration, sampling feasibility,
voltage/current/power limits, line uniqueness/range, requested capabilities, and
buffer/TriggerFlow/configuration-list budgets. It MUST reject unsupported
features explicitly. M1 model construction validates structure, finite SI values
and intrinsic consistency. It does **not** establish cell safety, physical
feasibility or commissioned capabilities; the pure compiler adds explicit
profile/configuration checks. Operational `prepare` must still validate its
actual hardware/runtime configuration before arming.

An invalid request MUST NOT energize output. A valid preparation MUST apply
instrument compliance and the selected terminals/sense configuration, allocate
run-specific storage, install/check the runtime, verify output OFF, and reach
PREPARED. READY and BUSY remain deasserted. Preparation MUST NOT accept START or
begin the trigger model; this resolves the earlier contradictory descriptions.

## C03 — Kickoff and completion

`kickoff()` MUST accept only PREPARED. IMMEDIATE starts the finite program; its
status succeeds after execution is confirmed (or normal completion is observed
for a program shorter than the first poll). EXTERNAL_TRIGGER starts acquisition
control and arms the START wait with output OFF; its status succeeds when the
wait is confirmed armed and READY is asserted. It MUST NOT wait for START.
This deliberate extension of the usual flyer kickoff meaning MUST appear in
API docs, examples, and configuration metadata.

`complete()` MUST return promptly with the status for the entire prepared
acquisition. It MAY be called immediately after kickoff. It MUST NOT request
early termination; it waits for normal completion, abort, or fault. Repeated
calls MUST refer to the same acquisition outcome. It succeeds only after normal
termination and confirmed safe output/flags, and fails on abort/fault/timeout.

## C04 — Abort and recovery

Programmatic `abort(reason=...)` MUST always be available. Optional external
ABORT MUST request the same logical terminal behavior locally on the instrument.
Abort MUST inhibit further sourcing, stop execution, switch output OFF in the
validated off mode, clear READY/BUSY after shutdown, preserve readings, and latch
ABORTED. ABORTING is the intermediate state. External abort capability MUST be
disabled/rejected until its mechanism and latency are proven for the firmware.

Abort status MUST succeed only after shutdown confirmation. The acquisition's
completion status MUST fail with an abort exception, even if abort status succeeds.
A transport failure MUST produce ERROR with output state UNKNOWN, not a false
claim that the DUT is safe. Repeated abort MUST be idempotent. `recover()` MUST
verify safe output, inactive inputs, and stopped runtime before entering IDLE.
It MUST NOT source, clear retained data, resume, or re-arm.

## C05 — Bluesky cleanup

`stop(success=False)` MUST synchronously perform bounded abort/shutdown and raise
if confirmation fails; it returns None as the Bluesky Stoppable hook. When
`success=True`, an interrupted active program is still ABORTED, not a successful
electrochemical acquisition. On a confirmed terminal state, `stop()` MUST be
idempotent and preserve the original outcome. `unstage()` MUST confirm output OFF, release
resources, and preserve terminal outcome and uncollected data. Cleanup MUST NOT
automatically restore a previously enabled output. Pausing MUST follow an
explicit policy: the initial operational device will abort and require a new
acquisition, with no replay of the old program. Hard process termination cannot
guarantee cleanup.

## C06 — Read and collect

While running, `trigger()` MUST latch a coherent latest buffered snapshot;
`read()` MUST return that snapshot without reconfiguring measurement or adding
a competing instrument measurement. Snapshot sample time/age/validity MUST be
visible; output OFF before first START is not a valid zero-valued measurement.
Snapshot triggering MUST fail clearly if no measurement exists. Standalone
single-point acquisition is deferred until it has its own validated mode.

`describe_collect()` MUST define the `electrochemistry` stream.
`collect()` MUST yield partial events with `time`, `data`, and `timestamps`,
without `uid`, `descriptor`, or `seq_num`. It MUST work after normal completion
and after a confirmed abort/fault when storage is readable. Version one collects
only a frozen terminal buffer. Repeated collection yields only previously
unemitted records; buffer retrieval MUST NOT clear data. An explicit diagnostic
export permits recovery if document emission fails. New preparation MUST reject
uncollected previous data unless explicitly archived/discarded by the operator.

## C07 — Configuration and transport

Use `prepare(AcquisitionRequest(...))`; do not overload ophyd
`configure(mapping)` with a protocol object. `configure(mapping)` returns old/new
configuration readings, validates a documented allowlist, and MUST reject active
acquisition changes. Constructor configuration is preferred for physical limits.
`read_configuration()` and `describe_configuration()` MUST expose reproducibility
fields in [data contract](data-contract.md).

Transport MUST serialize whole transactions, bound query/transfer duration,
prioritize abort between chunks, check command errors, and verify instrument
identity and TSP mode without silently resetting the device. Reconnection MUST
inspect/reconcile state with output treated as UNKNOWN and never replay START.

## C08 — Runtime and capability claims

The versioned packaged TSP runtime MUST own timing and I/O transitions. Host
status polling MUST NOT implement waveform timing or external-abort shutdown.
Runtime ABI and digest checks MUST precede arming. Code that blocks the interpreter
from servicing control/abort is unacceptable without a tested independent path.
No continuous/pulse/measurement speed, paired-V/I simultaneity, emergency-stop
classification, or clock synchronization guarantee may be claimed without evidence.

Only packaged, reviewed, version-controlled TSP programs and their compiled
parameters are in scope. User-written TSP upload/execution is outside the current
scope. M2's typed simulator has no raw-script API and does not implement TSP.

## C09 — Generic finite arbitrary waveforms

The public intent layer MUST support a finite `ArbitraryWaveform` containing
explicit current or voltage levels, a uniform requested source-update period,
and a positive finite repeat count. One source function MUST remain fixed for
the acquisition. Each point is a zero-order hold; the final point has a full
dwell, and repetition MUST NOT add or duplicate an endpoint. Infinite playback,
host-paced streaming, implicit interpolation, and source-function changes are
outside this revision. The detailed planned schema is in [waveforms](waveforms.md).

Source updates and electrical measurements MUST have separate timing
specifications. The compiler MUST validate their joint schedule, declared timing
tolerances, all points/transitions, complementary compliance, cell cutoffs, and
finite runtime/storage budgets before output. It MUST reject an unachievable
request rather than silently clip, rescale, resample, drop points, or slow it down.
Playback MUST use a fully prepared instrument-local program and inherit C03–C08,
including both start modes, abort responsiveness, output OFF on termination,
and retained partial records. A zero setpoint MUST NOT be interpreted as OFF.

Requested, compiled and observed timing MUST remain distinct. The compiled
waveform, its digest, generator provenance and record-to-waveform mapping MUST
be reproducible/exportable under the [data contract](data-contract.md).
Model availability MUST NOT imply a backend capability has passed acceptance.

## C10 — PRBS as a first-class waveform

The public intent layer MUST include a first-class `PRBSWaveform`, rather than
requiring users to construct an unlabelled arbitrary list. Its finite two-level
sequence MUST specify source function, low/high levels, bit period, generator
identifier/version, supported order, nonzero initial register seed and repeats.
The initial generator family MUST use validated maximal-length binary LFSR
sequences of length `2**order - 1`, with fixed documented tap, shift, output-bit
and update conventions. Unsupported orders/generators/seeds MUST be rejected.

The pure generator/compiler MUST preserve the expanded sequence and hash.
Every bit consumes one dwell, even when adjacent bits have the same level;
any execution optimization MUST preserve bit timing and logical indices.
Repeats restart the same sequence. Finite-sequence mean and commanded integrated
charge MUST be reported for current excitation; balanced-looking levels MUST
NOT be assumed to produce zero net charge. No random reseeding, clipping or
automatic balancing is permitted. See [PRBS semantics](waveforms.md#prbswaveform).

## C11 — Multisine through the arbitrary-waveform mechanism

Multisine MUST be a pure deterministic waveform generator that returns an
`ArbitraryWaveform` plus a retained `MultisineSpec`; it MUST NOT introduce a
second transport/runtime execution path. The specification MUST include source
function, DC bias, explicit tone frequencies, peak amplitudes and phases, sample
period, finite sample count and repeats. Random phases, if offered later, MUST
be resolved and preserved before compilation.

The initial generator MUST use coherent tones on integer bins of the finite
waveform period, exclude DC/Nyquist tones, and reject duplicate/out-of-band tones.
It MUST validate the summed waveform and between-sample safety envelope, not
just individual amplitudes. No clipping or silent normalization is allowed.
Requested tones/phases and achieved frequencies, quantization, mean, peak/RMS
excitation and crest factor MUST be recorded. Sampled playback is a staircase
approximation; continuous sinusoidal fidelity or calibrated impedance MUST NOT
be claimed from successful compilation. See [multisine semantics](waveforms.md#multisine-generator).
