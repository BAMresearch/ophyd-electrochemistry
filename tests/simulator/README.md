# Simulator milestone

M2 will implement an independent fake transport/instrument with failure injection
and simulated buffered records. The contract witness in `tests/contract` is not
a physical or TSP simulator. Do not count its results as hardware acceptance.

Contract revision 0.2 additionally requires PRBS/arbitrary/multisine through the
shared finite playback model. Cover point/bit/repeat boundaries, equal adjacent
PRBS bits, full final dwell, independent source/measurement schedules, aperture
overlap, timing/cutoff faults, resource exhaustion and abort during playback.
Expected traces must come from an independent reference schedule rather than
the production compiler under test. Preserve logical indices and partial data.
See [waveforms](../../docs/waveforms.md) and [roadmap](../../docs/roadmap.md).
