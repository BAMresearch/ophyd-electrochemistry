# Contract baseline

This documentation specifies the intended implementation of
`ophyd-electrochemistry`, starting with a Keithley 2460 backend. Contract
revision: **0.2**, updated **2026-10-02** from the 0.1 baseline.

The current package is an interface template. No instrument driver, TSP runtime,
or full simulator is implemented. Documentation review supports the architecture
but does not replace target-firmware and bench validation.

Begin with the [contract](implementation-contract.md) and
[evidence table](assumptions.md). The [roadmap](roadmap.md) defines how
implementation can proceed without assuming unverified hardware behavior.

Revision 0.2 adds first-class PRBS and generic finite arbitrary waveforms, with
multisine generated through the same playback mechanism. Read the
[waveform specification](waveforms.md) and [decision record](adr/0002-finite-waveforms.md)
for semantics, provenance and acceptance requirements. These are planned M1/M2
deliverables; the current Python API has not yet gained waveform models.
