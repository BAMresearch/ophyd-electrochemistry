# Contract and implementation coverage

This documentation specifies the intended implementation of
`ophyd-electrochemistry`, starting with a Keithley 2460 backend. Contract
revision: **0.2**, updated **2026-10-02** from the 0.1 baseline.

M1 pure validation, waveform generation, canonical serialization and bounded
planning compilation are implemented. No instrument driver, TSP runtime or
independent simulator is implemented. Documentation/pure tests do not replace
target-firmware and bench validation.

Begin with the [contract](implementation-contract.md) and
[evidence table](assumptions.md). The [roadmap](roadmap.md) defines how
implementation can proceed without assuming unverified hardware behavior.

Revision 0.2 adds first-class PRBS and generic finite arbitrary waveforms, with
multisine generated through the same playback mechanism. Read the
[waveform specification](waveforms.md) and [decision record](adr/0002-finite-waveforms.md)
for semantics, provenance and acceptance requirements. The current Python API
includes waveform intent and pure generators; the [M1 guide](m1-compiler.md)
describes the implemented compiler. M2 independent simulation is next.
