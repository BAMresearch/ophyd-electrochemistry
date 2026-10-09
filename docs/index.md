# Contract and implementation coverage

This documentation specifies the intended implementation of
`ophyd-electrochemistry`, starting with a Keithley 2460 backend. Contract
revision: **0.2**, updated **2026-10-02** from the 0.1 baseline.

M1 pure validation, waveform generation, canonical serialization and bounded
planning compilation are implemented, together with M2's independent virtual-clock
runtime and typed fake transport. M3 adds bounded PyVISA transactions and a
read-only identity/language/output-state diagnostic. A narrow executable M4 TSP
runtime has target compile/load/idle, immediate-hold, active-hold abort,
START-wait abort, and no-START timeout state-path evidence but no acquisition or
operational sourcing driver. M5 adds a shared immutable record schema and a
narrow exact-sized target-buffer proof with deterministic chunk retry;
operational target acquisition remains open.
Documentation, mock tests and identity queries do not replace target-firmware
and bench validation.

Begin with the [contract](implementation-contract.md) and
[evidence table](assumptions.md). The [roadmap](roadmap.md) defines how
implementation can proceed without assuming unverified hardware behavior.

Revision 0.2 adds first-class PRBS and generic finite arbitrary waveforms, with
multisine generated through the same playback mechanism. Read the
[waveform specification](waveforms.md) and [decision record](adr/0002-finite-waveforms.md)
for semantics, provenance and acceptance requirements. The current Python API
includes waveform intent and pure generators; the [M1 guide](m1-compiler.md)
describes the implemented compiler. The [M2 guide](m2-simulator.md) describes
simulated execution, faults and retained evidence. The [M3 guide](m3-transport.md)
documents the implemented transport, safe diagnostic and incomplete G05 evidence.
The [M5 data guide](m5-data.md) defines electrical origins, aperture mapping,
clock metadata and retained-buffer integrity without claiming hardware data.
