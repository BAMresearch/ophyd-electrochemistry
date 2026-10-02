# M2 independent simulator tests

M2 now implements `ophyd_electrochemistry.simulation`: a virtual-clock fake
runtime and typed fake transport with failure injection and synthetic buffered
records. Run `uv run --locked pytest tests/simulator`. The contract witness in
`tests/contract` remains separate. Neither suite is hardware acceptance or a TSP
interpreter. See the [M2 guide](../../docs/m2-simulator.md).

Contract revision 0.2 additionally requires PRBS/arbitrary/multisine through the
shared finite playback model. Cover point/bit/repeat boundaries, equal adjacent
PRBS bits, full final dwell, independent source/measurement schedules, aperture
overlap, timing/cutoff faults, resource exhaustion and abort during playback.
Expected traces use literal PRBS vectors, analytical multisine samples and
original request levels with independent tick integration. Tests forbid reuse of
production point/sample mapping helpers. Logical indices and partial data survive.
See [waveforms](../../docs/waveforms.md) and [roadmap](../../docs/roadmap.md).
