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
