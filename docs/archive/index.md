# Archive: contract revision 0.2 (M1–M6)

These pages document the project's first design (October 2026): a general
contract for instrument-owned electrochemistry acquisition with waveform intent
(holds, pulses, CV, PRBS, multisine), a planning compiler, a virtual-clock
simulator, the `Keithley2460Device` Flyer and the M4/v17 TSP runtimes. The
beamtime work since 3 November replaced this path with the step-sequence model
and the sequencer runtime described in the [technical overview](../technical/index.md).

The code they describe is still in the package and its tests still pass, but it
is not used for experiments. Statements about status, next steps or "the
current implementation" in these pages date from before the sprint. Do not run
`tsp/runtime.tsp` (M4) with a cell connected: it uses output-off mode NORMAL.

| Page | Topic |
|---|---|
| [Implementation contract](implementation-contract.md) | Normative behaviour of revision 0.2 |
| [Architecture](architecture.md) | The 0.2 layering and the two-QueueServer pattern |
| [Waveforms](waveforms.md), [ADR 0002](adr/0002-finite-waveforms.md) | PRBS, arbitrary waveforms, multisine |
| [M1 compiler](m1-compiler.md) … [M6 device](m6-device.md) | Milestone guides |
| [M6 user guide](m6-user-guide.md) | `Keithley2460Device` configuration and quickstart notebook |
| [Available sequences](available-sequences.md) | Coverage of the 0.2 intent models |
| [Triggering](triggering.md), [Safety](safety.md), [Data contract](data-contract.md) | 0.2 lifecycle, shutdown and record contracts |
| [Assumptions](assumptions.md), [Hardware acceptance](hardware-acceptance.md), [Roadmap](roadmap.md) | Evidence gates and plan of the 0.2 design |
| [Timed-hold commissioning](timed-hold-commissioning.md), [Aperture calibration](timestamp-aperture-calibration.md) | October bench procedures (v17 runtime, superseded) |
| [ADR 0001](adr/0001-contract-baseline.md) | Contract baseline |
