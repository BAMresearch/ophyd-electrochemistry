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
through skips. No hardware test code is present in this baseline.
