# Assumption verification — 2026-10-02

**Supported** means primary documentation establishes a capability, not that the
target unit was tested. **Design** is our chosen contract. **Open** requires
firmware/bench evidence. No instrument was connected during this task.

| ID | Assumption | Assessment and implementation consequence |
|---|---|---|
| A01 | Rear orange block can carry handshake | **Corrected.** Rear DUT terminals and female DB-9 digital I/O are separate [S1]. |
| A02 | Two outputs and two inputs fit | **Supported.** Six user-defined lines [S1]; verify physical pin mapping. |
| A03 | READY/BUSY levels can be instrument-controlled | **Supported.** Digital-I/O action block is documented [S2]; arm ordering remains G01. |
| A04 | Local finite timing/pulses are possible | **Supported.** Official 2460 pulse example uses local TriggerFlow [S3]. Arbitrary requested timing still requires G04. |
| A05 | Digital ABORT guarantees immediate preemption | **Open.** Event branching and model abort are documented [S2]; they do not establish asynchronous output shutdown. Gate G02. |
| A06 | Model abort automatically switches output off | **Not established.** Treat abort and output OFF as distinct requirements; test G02. |
| A07 | Buffer gives simultaneous measured V and I | **Not established.** Advertised capacity includes selected measurements/time [S1]; determine readback/pairing and record budget at G03. |
| A08 | Buffer capacity is a fixed 250k full records | **Corrected.** Datasheet advertises >250k readings [S1]; actual allocated paired storage is a separate budget. |
| A09 | Flyer/prepare API fits | **Supported.** Bluesky defines prepare, kickoff/complete, and collect descriptors/events [S4, S5]. Local witness validates the sequence. |
| A10 | External kickoff may return while waiting | **Design extension.** Define kickoff success as armed readiness, not first sourced sample. Test with pinned Bluesky. |
| A11 | Two QueueServers share one Bluesky run | **Corrected.** Independent workers run Bluesky [S6]; design uses separate runs and a shared experiment ID. |
| A12 | Ethernet/PyVISA/TSP is a suitable boundary | **Supported architecture** [S1, S7]; exact VISA backend, framing, socket port, abort starvation, and control ownership require G05. |
| A13 | Instrument and detector clocks align | **Open.** Add measured mapping/uncertainty; no automatic synchronization claim. G06. |
| A14 | 3.3 V/industrial I/O directly connects | **Corrected.** HIGH minimum is 3.7 V [S1]; electrical commissioning required. |
| A15 | Disconnect/pause always turns output off | **Corrected.** Host cleanup is conditional; local bounded termination and tested physical shutdown are required. G07. |
| A16 | Arbitrary current/voltage lists are a suitable waveform boundary | **Supported architecture.** Tektronix's AFG brief describes configuration-list waveform generation for the 2460 [S10]. Our finite timed playback/compiler is a design; firmware capacity, measured playback timing and abort path remain G02/G04/G08. App limits are not backend limits. |
| A17 | PRBS is a native named 2460 function | **Not established; not required.** First-class public PRBS with pure deterministic expansion to locally executed levels is our design inference from list generation [S10]. No native PRBS command or rate is assumed. |
| A18 | Multisine can share arbitrary-waveform playback | **Design inference** from local list generation [S10]. Pure coherent generation and provenance are specified; usable tone bandwidth, amplitude/phase fidelity and measurement aperture require G03/G04/G08. No calibrated EIS claim. |
| A19 | A 10 ms pulse proves 10 ms waveform updates with measurements | **Not established.** The official 10 ms example switches output on/off without measurement [S3]. List recall, load/range settling, measurement and abort behavior need independent G02/G04/G08 evidence. |
| A20 | A 10 frames/s detector resolves individual 10 ms bits | **Not established.** Ten bits fit one 100 ms frame interval; exposure integration, phase and clock uncertainty determine resolution. Source/electrical/detector schedules stay distinct; G06 applies. |

## Evidence limits

Reference manual landing page [S8] identifies Rev C and firmware 1.7.0+.
The full 45 MB manual could not be retrieved by the web reader (size limit),
and direct download returned HTTP 403. Therefore this review does not claim
full-manual command validation. Commands were cross-checked in Tektronix's
maintained `tm_devices` command documentation, whose upstream provenance is [S9].
Verify exact constants, parameters, block limits, ports, and interlock behavior
against the target firmware/manual before any runtime is implemented or loaded.

The initial local API check used Bluesky 1.14.6 and ophyd 1.11.2. The later
[RunEngine teardown fix](development.md#runengine-abort-test-teardown-message)
updates the runtime dependency/lock to Bluesky 1.15.1 and revalidates the contract
tests. No claim is made that the conference's complete leader/follower workflow
was reproduced.

## Primary sources

- **S1:** [Tektronix 2460 datasheet](https://www.tek.com/en/datasheet/2460-source-measure-unit), connectivity, TriggerFlow, General Characteristics/Digital I/O.
- **S2:** [Tektronix tm_devices trigger commands](https://tm-devices.readthedocs.io/stable/reference/tm_devices/commands/gen_auxckp_smu/trigger/), model abort, digital-I/O, branch-on-event, wait/source-output.
- **S3:** [Tektronix 2460 pulse example](https://www.tek.com/en/support/faqs/model-2460-smu-tsp-simple-script-voltage-pulses).
- **S4:** [Bluesky 1.14.6 hardware protocols](https://blueskyproject.io/bluesky/v1.14.6/hardware.html).
- **S5:** [ophyd architecture](https://blueskyproject.io/ophyd/architecture.html), asynchronous acquisition/status.
- **S6:** [QueueServer introduction](https://blueskyproject.io/bluesky-queueserver/introduction_for_users.html), manager/worker ownership.
- **S7:** [PyVISA resources](https://pyvisa.readthedocs.io/en/latest/introduction/resources.html).
- **S8:** [2460 reference manual landing page](https://www.tek.com/en/keithley-source-measure-units/keithley-smu-2400-graphical-series-sourcemeter-manual-8).
- **S9:** [Tektronix tm_devices upstream](https://github.com/tektronix/tm_devices), maintained command documentation.
- **S10:** [Tektronix function-generation technical brief](https://www.tek.com/en/documents/technical-brief/equipping-source-measure-units-with-function-generation-using-tsp-technology), current/voltage configuration-list waveforms and arbitrary-waveform app for the 2460. Reviewed 2026-10-02; used as architecture evidence, not timing, capacity or safe-abort acceptance.

Keep these sources linked, not copied into the repository. Source review date
is distinct from hardware commissioning date.
