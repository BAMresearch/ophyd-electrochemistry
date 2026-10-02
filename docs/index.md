# Contract baseline

This documentation specifies the intended implementation of
`ophyd-electrochemistry`, starting with a Keithley 2460 backend. Contract
revision: **0.1**, established **2026-10-02**.

The current package is an interface template. No instrument driver, TSP runtime,
or full simulator is implemented. Documentation review supports the architecture
but does not replace target-firmware and bench validation.

Begin with the [contract](implementation-contract.md) and
[evidence table](assumptions.md). The [roadmap](roadmap.md) defines how
implementation can proceed without assuming unverified hardware behavior.
