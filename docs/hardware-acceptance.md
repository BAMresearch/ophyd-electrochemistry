# Hardware acceptance record template

All gates are **NOT RUN**. Execute first with a characterized dummy load/source,
never infer a gate pass from docs or a simulator. Keep raw scope traces and
instrument buffers with each record. Firmware changes invalidate relevant gates.

| Gate | Test | Required evidence |
|---|---|---|
| G01 | Immediate/external START, READY race, held/stale/repeated START | Output off before START; post-READY edge never lost; stale edge never starts next run; READY/BUSY timing traces |
| G02 | Programmatic and external abort in wait, measure, delay, rest, sourcing, waveform/list/repeat boundaries, export | Verified OFF mode, retained data, coincident START priority, no re-enable; worst observed and bounded latency against a declared requirement |
| G03 | V/I field origins, timestamp alignment, configured storage capacity | Known load/source comparisons, measure/readback configuration, raw buffer mapping, paired-record budget, no wrap |
| G04 | Requested periods, CV segments, pulses, waveform source updates and independent measurement settings | Actual timing distributions/jitter, aperture/settling and deadline faults under ranges, NPLC/autozero/filter settings; reject impossible periods |
| G05 | VISA resource, error/termination, partial transfer, abort during retrieval | Identity/firmware, TSP mode, bounded transaction behavior, abort starvation bound, no competing controllers |
| G06 | Clock anchor/drift and detector exposure correlation | Bracketed anchors, offset/resolution/uncertainty, hardware marker/exposure comparison across a long run |
| G07 | Missing START, disconnect, worker kill, prepare fault, pause, recovery | Instrument-local finite timeout/output shutdown; UNKNOWN reporting on lost confirmation; no auto-rearm/data loss |
| G08 | PRBS/arbitrary/multisine fidelity, phase and protection | Captured actual current/voltage on a characterized load/source; point/bit/repeat continuity, requested versus actual timing, source/measurement aperture mapping, list/storage limits, compliance/cutoff response, multisine spectrum and crest factor; scope per source mode/range/load/firmware |

## Waveform evidence scope

G08 requires representative worst-case adjacent transitions, long equal-bit
runs, repeat-boundary jumps and final dwell/shutdown. Record requested and
compiled waveform digests with actual scope/acquisition traces. Compare multisine
tone amplitudes/phases and unwanted spectral content against declared tolerances;
identify hold/settling and integration effects. Repeat electrical measurements
under the intended measurement settings, not solely source-only playback.

No general 10 ms or shorter capability is enabled by the pulse example, app
documentation or simulator. Characterize the requested periods on the target
setup. Voltage and current modes, source-only operation and measured playback
have separate evidence scopes. External abort still requires G02; G08 cannot
replace it. Per-point/stroboscopic detector correlation additionally requires
G06 and a validated facility timing scheme, without assuming spare I/O markers.

## Per-gate form

- Gate and contract clauses:
- Date / operator / reviewer:
- Instrument model, serial, firmware, reference manual:
- Python, dependencies, Git commit, runtime ABI/digest:
- Wiring/pinout, load/source, ranges, protection/off-mode configuration:
- Declared limits (including latency and timing tolerance):
- Reproduction procedure and repetitions:
- Observations, failures, raw evidence paths/checksums:
- Result: NOT RUN / FAIL / PASS (with scope of applicability):
- Capability enabled / residual limitation:

An opt-in hardware fixture must require explicit resource/configuration and
confirm the dummy-load setup. Future `pytest -m hardware` must fail if requested
hardware or required evidence is absent; it must not silently report acceptance
through skips. The guarded resistor-smoke notebook is a commissioning aid, not
that acceptance fixture.

## Initial communications evidence — 2026-10-05

This is partial commissioning evidence, not a G05 result. From the user's macOS
workstation, the link-local target answered ICMP and accepted HTTP, VXI-11 and raw
socket connections. The bounded diagnostic queried the same Model 2460 over both
explicit PyVISA resources using pyvisa-py:

- `TCPIP0::169.254.77.125::5025::SOCKET`;
- `TCPIP0::169.254.77.125::inst0::INSTR`.

Both returned serial `04686198`, firmware `1.7.16a`, command language `SCPI`, and
source-output state `0` (OFF).
The exact JSON is retained in [the connectivity evidence](evidence/k2460-2026-10-05-connectivity.json),
and the procedure is described in the [M3 guide](m3-transport.md) and repository
notebook `notebooks/keithley_2460_connectivity.ipynb`.

Only manual-reviewed `*IDN?`, `*LANG?`, and `:OUTPut:STATe?` queries were sent in
that initial test. The connected DUT was the user's 3 V coin cell, not a
characterized dummy load. Electrical OFF-mode behavior, partial transfers,
retrieval/abort fairness, controller exclusion and shutdown behavior were not
tested. G05 and every other gate therefore remain **NOT RUN**.

## Preliminary resistor smoke test — 2026-10-05

After the coin cell was removed, the user connected a nominal 100 ohm resistor
to the front terminals using four-wire sense and confirmed the OUTPUT indicator
was off. A bounded SCPI procedure then configured current source, voltage
measurement, remote sense, a 0.2 V voltage limit, fixed ranges, source readback,
one reading at 1 NPLC, and disabled filtering/relative offset. It did not send
`*RST` or recall a saved setup.

| Requested current | Source readback | Measured voltage | V/I result | Limit trip |
|---:|---:|---:|---:|---:|
| 100 µA | 99.99917 µA | 10.27817 mV | 102.7826 ohm | No |
| 1 mA | 0.9999969 mA | 102.5312 mV | 102.5315 ohm | No |

The output was switched off after each point. Cleanup programmed 0 A and
confirmed output OFF; a new, query-only session independently returned output
state `0`, front terminals, current-source mode, and programmed current `0`.
The exact result is retained in [the resistor smoke evidence](evidence/k2460-2026-10-05-resistor-smoke.json),
and the guarded reproduction procedure is in
`notebooks/keithley_2460_resistor_smoke.ipynb`.

The resistor was not calibrated, so this demonstrates plausible sourcing,
four-wire voltage acquisition, source readback, compliance status, and bounded
shutdown only. It is not an accuracy calibration and does not exercise abort,
disconnect, OFF-mode impedance, timing, buffer transfer, or competing-controller
behavior. No hardware acceptance gate is promoted from **NOT RUN**.

### Bipolar linearity and repeatability follow-up

The same physical setup was subsequently measured at −1 mA, −500 µA, −100 µA,
+100 µA, +500 µA, and +1 mA, with five readings per point. A fixed 1 mA source
range and the same 0.2 V limit were used; output OFF was confirmed between every
point. All 30 source-readback/voltage pairs had the expected polarity and no
limit trip.

A least-squares fit of sensed voltage against measured source-current readback
gave a slope of 102.502310 ohm, an intercept of −33.020 µV,
R-squared of 0.999999999914, and a maximum absolute residual of 1.252 µV. An
independent session again confirmed output `0` and programmed current `0`.
Full per-reading data are retained in
[the bipolar sweep evidence](evidence/k2460-2026-10-05-resistor-bipolar-sweep.json).
The guarded reproduction is
`notebooks/keithley_2460_resistor_bipolar_sweep.ipynb`.
These results strengthen polarity, pairing, linearity, and repeatability
evidence, but the uncalibrated resistor still prevents an accuracy claim and no
hardware gate is promoted.

## Buffer and exception-cleanup test — 2026-10-05

The guarded `notebooks/keithley_2460_buffer_cleanup.ipynb` ran with +1 mA, a
0.2 V limit, front-terminal four-wire voltage sense, and 1 NPLC. It created a
unique 32-reading fill-once buffer, acquired 20 readings, switched output off,
verified buffer count/start/end, and retrieved source readback, voltage, relative
time, source status, and measurement status.

All 20 records were recovered. Mean source readback was 0.999998004 mA, mean
voltage was 102.489834 mV, and mean V/I was 102.490038 ohm. Relative timestamps
spanned 1.568821 s; the mean interval was 82.5695 ms (12.1110 Hz), with 2.943 µs
sample standard deviation. All measurement-status values identified the front
terminals and none set the questionable-measurement bit.

The instrument already contained two errors, which the user identified as the
09:30 LAN events “invalid IP address” and “lan configuration was reset.” They
were preserved. The error count remained two after configuration, acquisition,
retrieval, deliberate host-side exception, cleanup, and temporary-buffer
deletion. A new session independently confirmed output OFF, front terminals,
programmed current 0 A, and the unchanged error count.

The complete record is [the buffer/cleanup evidence](evidence/k2460-2026-10-05-buffer-cleanup.json).
This is useful G03/G05 and ordinary exception-cleanup evidence, but it does not
test process death, Ethernet loss, retrieval interruption, abort starvation, or
instrument-local watchdog behavior. No hardware gate is promoted to PASS.

### Packaged-runtime retained-buffer proof — 2026-10-09

Runtime build `m4-finite-current-hold-v5` acquired three 1 NPLC readings at
+1 mA with a 0.2 V limit into its 16-record standard fill-once buffer. Each
record contained source-current readback, measured voltage, relative timestamp,
source status and measurement status. The extent was 1–3, and retrying offset
zero returned identical records and checksum. Source status 200 documented
readback, four-wire sense and output on during each reading. Measurement status
was 264 for the first reading (front terminal plus first-in-group) and 8 for the
others; none carried the questionable bit. Mean V/I was 102.5112 ohm.

Warnings and errors did not increase. Recovery and a new session independently
confirmed IDLE, output `smu.OFF`, programmed current 0 A, and retained extent
1–3. After the evidence was archived, explicit discard emptied the buffer and
again confirmed IDLE/OFF/0 A. See the
[complete M5 proof](evidence/k2460-2026-10-09-m5-buffer-proof.json).

The load is uncalibrated and buffer timestamps are relative to the first reading,
not an observed actual START. Capacity/no-wrap limits, calibrated accuracy,
clock mapping, interruption behavior and larger transfers remain untested. This
is useful partial G03/G04 evidence; neither gate is promoted.

The follow-up maximum-count proof acquired eight records and retrieved them as
3+3+2 contiguous offset chunks. A retry of the middle chunk was identical. The
new raw-target archive verified both its record checksum and whole-payload
checksum after reload, and exclusive-create output prevents silent replacement
of an earlier archive. Mean V/I was 102.5092 ohm; warning/error counts did not
change. An independent session confirmed all eight records retained with
IDLE/OFF/0 A before reason-bearing discard emptied the buffer. The
[run evidence](evidence/k2460-2026-10-09-m5-max8-evidence.json) links the
[verifiable archive](evidence/k2460-2026-10-09-m5-max8-archive.json). This reaches
the deliberately configured eight-reading runtime limit, not the 16-record
buffer capacity, and does not test wrap behavior.

An output-off capacity probe subsequently requested the largest currently
available standard-style user buffer. Firmware 1.7.16a allocated 5,110,784
records while the two 100,000-record default buffers and 16-record proof buffer
remained allocated. The empty temporary buffer was immediately deleted and
garbage-collected; warning/error counts and all pre-existing buffers were
unchanged, and the source remained IDLE/OFF/0 A. The
[capacity record](evidence/k2460-2026-10-09-buffer-capacity-probe.json) proves
allocation under this configuration, not filling, no-wrap behavior, sustained
acquisition, or multi-megabyte transfer.

Runtime build v6 subsequently allocated an exact 17-record standard fill-once
buffer and acquired extent 1–17. Retrieval used 10+7-record chunks, crossing the
former eight-record transfer limit, and the first 10-record chunk retried with
identical content and checksum. The canonical archive reloaded successfully;
an independent session then confirmed all records retained at IDLE/OFF/0 A
before explicit discard left an empty 17-record buffer. Mean V/I was 102.5087
ohm and warning/error counts stayed at 5/6. See the
[v6 evidence](evidence/k2460-2026-10-09-m5-v6-exact-buffer-evidence.json) and
[verifiable archive](evidence/k2460-2026-10-09-m5-v6-exact-buffer-archive.json).
This adds exact-allocation, small fill-once and bounded-transfer evidence; it
does not promote G03/G04 or validate the configured 250,000-record host ceiling.

## M4 runtime installation, immediate hold, and programmatic abort — PASS within narrow scope

The exact-artifact loader, packaged finite-current-hold TSP runtime, typed host
adapter, and guarded commissioning notebook are implemented. After the retained
[SCPI preflight](evidence/k2460-2026-10-05-m4-preflight.json), the user selected
TSP at the front panel and rebooted. Serial 04686198 then compiled, loaded, and
initialized ABI `oe-k2460-m4-hold-v1` from volatile memory. The typed state was
`idle`, the trigger model was empty, and output was `smu.OFF`. Reuse verified the
same ABI/build without rerunning the script top level. No hold was prepared or
armed, and no source output was enabled. The complete record is
[M4 runtime-install evidence](evidence/k2460-2026-10-05-m4-runtime-install.json).

Commissioning exposed three firmware-1.7.16a constraints now enforced by the
loader: omit blank messages within `loadscript`, keep the instrument-side script
name to a 24-hex-character digest prefix, and require clean runtime globals for a
first install. The full SHA-256 remains host-verified and an existing matching
script is not rerun. Stuck scripts required reboot, but responsive idle/aborted
versions were replaced in place using force-safe, deletion of the owned volatile
script, explicit removal of its globals, and OFF/0 A verification.

The user reconfirmed the nominal 100 ohm resistor, front terminals, four-wire
sense, and physical OUTPUT-off state. The first prepare attempt failed before
arming because firmware TSP lacks `math.huge`; force-safe and an independent
session confirmed output OFF and 0 A. After removing that unsupported check, one
+1 mA, 0.2 V-limit, 250 ms immediate hold reached PREPARED with output off,
RUNNING at the local delay block with BUSY/output on, COMPLETE at terminal block
6 with BUSY/output off, then recovered to IDLE and 0 A.

Status reads initially posted warning 1808 because READY/BUSY output pins were
read back. The runtime now reports its software-tracked commanded levels. A
second identical hold completed with warning and error counts unchanged; two
informational events were added. Final independent queries confirmed IDLE,
`smu.OFF`, and 0 A. The full record is
[M4 immediate-hold evidence](evidence/k2460-2026-10-05-m4-immediate-hold.json).

This proves the narrow local immediate TriggerFlow path, not electrical current,
voltage, or duration accuracy: this M4 runtime does not acquire readings and no
independent timing trace was taken.

On 2026-10-09, a query-only reconnect reconfirmed serial 04686198, firmware
1.7.16a, TSP mode, and `smu.OFF`. After the user reconfirmed the same resistor,
front-terminal four-wire wiring, and physical OUTPUT-off indicator, a 5 s
maximum +1 mA hold reached RUNNING at delay block 3. A host-requested abort then
reported ABORTED/output-off, deasserted READY/BUSY, and independently read
`smu.OFF` and 0 A. Request-to-confirmation wall time was 0.196 s. A repeated
abort remained safely ABORTED without an additional warning, and recovery
returned IDLE/OFF/0 A. The active abort added the expected instrument warning
“Trigger model path 1 has been aborted”; the error count remained zero. See
[M4 programmatic-abort evidence](evidence/k2460-2026-10-09-m4-programmatic-abort.json).

The 0.196 s observation includes host/VISA/query latency and is not an
independent electrical measurement of output cutoff. It therefore establishes
the programmatic-abort state path and idempotent repeated command, not G02/G07
abort-latency or electrical-OFF acceptance.

The first abort trace also exposed a status-only defect: after avoiding warning
1808, software tracking did not follow READY/BUSY changes made inside
TriggerFlow. Runtime build `m4-finite-current-hold-v2` derives the tracked levels
from the lifecycle state without reading output-configured pins. It was replaced
in place and the repeated bench run reported RUNNING/BUSY 1 followed by
ABORTED/IDLE with READY/BUSY 0 and no new error.

With the rear digital-I/O connector confirmed completely unconnected, the
no-START timeout path was then exercised. Commissioning first established two
firmware constraints without arming: trigger-mode inputs cannot be read through
`digio.line[N].state` or `digio.readport()`, and digital-input state is returned
as symbolic `digio.STATE_HIGH` rather than numeric 1. Those rejected attempts
left output OFF/0 A and added six retained errors in total. Build
`m4-finite-current-hold-v4` samples START in digital-input mode using symbolic
comparison immediately before switching to trigger-input mode; while armed it
reports START as unavailable rather than reading it illegally.

Using falling-edge START so the floating-high unconnected input was inactive,
the successful run reported WAITING_START with READY 1, BUSY 0, and output off.
All 34 host polls observed output off. The local two-second timeout reached
START_TIMEOUT at terminal block 15 after 2.047 s host-observed wall time, with
both flags low and no detector/timer overrun. Warnings and errors were unchanged.
The programmed 1 mA level remained configured while output was off; recovery
explicitly restored IDLE/OFF/0 A. See
[M4 start-timeout evidence](evidence/k2460-2026-10-09-m4-start-timeout.json).

Polling does not exclude a brief electrical pulse, and host time is not an
independent timing trace. The result proves the no-edge state path, not the
external START boundary or G01/G02 timing.

The same unconnected-input setup then exercised programmatic abort while the
runtime was still WAITING_START at polling block 5. READY was asserted, BUSY and
output were low, and no START was applied. Abort was confirmed after 0.199 s host
wall time with the trigger model ABORTED, both flags low, `smu.OFF`, and 0 A.
Repeated abort added no warning, recovery returned IDLE/OFF/0 A, and the retained
error count stayed at six. The active abort added only the expected
trigger-model-aborted warning. See
[M4 START-wait abort evidence](evidence/k2460-2026-10-09-m4-waiting-start-abort.json).

This establishes safe command/state behavior while waiting, not electrical
abort latency or actual external-edge timing.

External START and READY/BUSY require verified DB-9 wiring and timing capture,
which are not available yet. External ABORT remains disabled. Process death,
Ethernet loss, electrical abort latency, and actual START-edge behavior still
need separate evidence. G01, G02, and G07 therefore remain **NOT RUN**.
