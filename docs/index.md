# ophyd-electrochemistry

Bluesky and QueueServer control of a Keithley 2460 SourceMeter for battery
experiments: galvanostatic and potentiostatic cycling, and current pulses with
rests, with all timing and voltage cutoffs running on the instrument. Built
for operando beamtime measurements on hard-carbon/Na coin cells.

## Choose a route

- **First time:** the [Quickstart](user/quickstart.md) runs a complete example
  on the simulator in about 15 minutes, without an instrument.
- **Running experiments:** the [User guide](user/index.md): run sheets,
  sessions, the procedure at the beamtime, and the data.
- **Developing or reviewing:** the [Technical overview](technical/index.md):
  how the package is built, which component does what, the sequencer runtime,
  the simulator and the tests.
- **Project status:** [Current state](current-state.md) (what works, what is
  open), the [MVP sprint plan](mvp-plan.md), the
  [beamtime measurement concept](beamtime-concept.md) and the
  [validation record](validation.md) of bench results.

The [archive](archive/index.md) holds the earlier contract-revision-0.2 design
documents (milestones M1–M6), which the beamtime path does not use.
