# Available excitation sequences

Updated: 2026-10-10

This document records which finite electrochemistry sequences can be expressed
with the current package, which can be useful offline examples, and what work is
needed before they become operational instrument programs. Example parameters
are synthetic intent, not cell-safe defaults or Keithley performance claims.

## Meaning of available

Availability has several distinct levels. A sequence must not be described as
generally supported without naming the applicable level.

| Level | Meaning |
|---|---|
| Intent | An immutable public model can represent the requested scientific intent. |
| Planning | The pure compiler validates an explicit configuration/capability profile and emits finite planning IR with `hardware_ready=False`. |
| Simulation | The independent runtime executes the compiled schedule and emits synthetic paired I/V records. |
| Default Flyer | The default simulator-backed `Keithley2460Device` allowlist accepts the program. |
| Target hardware | A packaged runtime, target adapter, timing/data mapping, safety behavior, and scoped bench evidence exist for the selected source mode and settings. |

The public intent models currently include `PotentiostaticHold`,
`GalvanostaticHold`, `CyclicVoltammetry`, `CurrentPulseSequence`,
`VoltagePulseSequence`, `ArbitraryWaveform`, and `PRBSWaveform`.
`MultisineSpec` is a retained generator specification;
`generate_multisine()` resolves it into an `ArbitraryWaveform` rather than a
separate execution protocol.

The default Flyer accepts only `GalvanostaticHold` and
`CurrentPulseSequence`. A simulator caller may explicitly widen that allowlist,
as the quickstart does for voltage pulses, but doing so does not create target
support. The real 2460 adapter remains blocked on the START-to-aperture timestamp
projection. Existing target evidence covers scoped runtime, hold, buffer, and
state paths—not a generally commissioned sequence library.

## Sequence availability

| # | Sequence | Current status | Present representation and limitation |
|---|---|---|---|
| 1 | Single pulse followed by relaxation | Ready with a phase limitation | `CurrentPulseSequence(count=1)` gives a pulse followed by one baseline/recovery dwell. It cannot add an independent pre-pulse baseline; use a bounded uniform-grid waveform, a separate acquisition, or future segment composition when that phase matters. |
| 2 | Periodic rectangular pulse train | Ready | Direct `CurrentPulseSequence`. One START releases the complete finite train; source timing is local rather than host-paced. |
| 3 | Pulse-width and duty-cycle sweep | Ready as multiple acquisitions | Generate several immutable `CurrentPulseSequence` requests. Each setting needs its own experiment/acquisition identity and retained-data disposition; width and duty cannot change between blocks inside one request. |
| 4 | Pulse-amplitude staircase | Bounded prototype | A current `ArbitraryWaveform` can contain successive amplitude/baseline blocks on one uniform grid. Separate pulse acquisitions are another option but introduce disposition/re-arm gaps. Long or unequal blocks motivate segment composition. |
| 5 | Burst–rest | Bounded prototype | A finite burst plus extended baseline can be flattened onto an `ArbitraryWaveform` grid while point/record budgets remain reasonable. Nested bursts and long rests are better represented by segments. |
| 6 | Bipolar pulse pairs | Partly ready | A two-level `+I/-I` alternation is a `CurrentPulseSequence`. A `+I, 0, -I, 0` pair needs `ArbitraryWaveform` or segments. Nominal zero commanded charge does not prove zero measured charge or zero state drift. |
| 7 | Intermittent current interruption (ICI) | Ready for a regular fixed pattern | Use the working current as the first dwell and zero current as the short baseline dwell. The model then repeats working-current → interruption. Starting with an interruption, preconditioning, evolving blocks, or richer relaxation semantics requires explicit composition. |
| 8 | GITT-style pulse–relaxation | Ready for a regular fixed pattern | Use a current pulse followed by a long zero-current baseline. A single measurement cadence applies to pulse and relaxation. Varying states, amplitudes, durations, sampling cadence, or termination rules need orchestration or a richer segment model. Diffusion analysis remains downstream. |
| 9 | HPPC-style charge/discharge pairs | Bounded prototype; segments preferred | A uniform-grid `ArbitraryWaveform` can encode discharge, rest, charge, and rest for small examples. Unequal multi-stage holds and state-of-charge staging make segment composition preferable. The name must not imply standards compliance. |
| 10 | PRBS | Ready for planning/simulation examples | First-class `PRBSWaveform`, deterministic expansion, canonical encoding, compilation, and independent simulation exist. It is not in the default Flyer allowlist or target runtime. |
| 11 | Single sine and coherent multisine | Ready for planning/simulation examples | A one-tone or multi-tone `MultisineSpec` is resolved by `generate_multisine()`. Playback is sampled zero-order hold. This is excitation planning, not commissioned waveform fidelity or calibrated impedance spectroscopy. |
| 12 | Chirp | Raw representation possible; named example should wait | Externally generated chirp samples fit `ArbitraryWaveform`, but no named/versioned `ChirpSpec` or retained chirp provenance exists. Add a pure reproducible generator before promoting a public example. |
| — | PITT-style voltage steps | Fixed-duration simulation example possible | `VoltagePulseSequence` or a voltage `ArbitraryWaveform` can express finite steps. Current-threshold termination, named PITT semantics, default Flyer support, and target voltage-source commissioning are absent. |

The existing quickstart demonstrates a unipolar current train, a fixed-duration
voltage pulse train, and equal positive/negative current dwells. The last of
these has zero net **commanded** charge because the magnitudes and dwell times
match. It has no zero-current rest between polarities.

## Uniform-grid waveform boundary

`ArbitraryWaveform` is useful for small resolved examples, but it is not a
general segment language:

- it has one fixed source function and one uniform `point_period_s`;
- every point is zero-order hold and the final point receives a full dwell;
- repeats reproduce the complete list without adding an endpoint or gap;
- measurement `sample_period_s` remains independent of the source-point period;
- there is no nonuniform dwell list, interpolation, streaming/refill, mixed
  source mode, or source-OFF segment; and
- a zero setpoint is active sourcing, not physical output OFF or a proven
  open-circuit relaxation state.

Flattening a long hold onto a fine grid can exhaust source-point, trigger-block,
measurement-record, or buffer budgets. An example must fail rather than silently
clip, normalize, resample, slow, truncate, or stream a schedule. Preserve every
resolved level, repeat boundary, requested/achieved time, generator identity,
commanded charge, and record-to-source mapping.

## Possible implementation plans

### Plan A — example library using current APIs

This is the lowest-risk work and can proceed without hardware:

1. Add a pure `examples/sequence_library.py` containing named builders for a
   single pulse/relaxation, rectangular train, width/duty sweep, fixed ICI,
   fixed GITT-style sequence, and two-level bipolar sequence.
2. Reuse `PRBSWaveform`, `MultisineSpec`, and `generate_multisine()` directly;
   do not recreate their generators.
3. Add small, explicitly bounded uniform-grid builders for staircase,
   burst–rest, four-stage bipolar, and illustrative HPPC-style shapes. Label
   these as resolved prototypes rather than new protocol classes.
4. Add a separate simulator-only sequence-gallery notebook instead of expanding
   the main lifecycle quickstart indefinitely. It should compile, summarize,
   and plot every resolved source schedule without opening VISA.
5. Test exact boundaries, total duration, repeat behavior, signed commanded
   charge, canonical round trips, resource rejection, and final output-OFF state.

A sweep is a collection of finite acquisitions, not an acquisition that mutates
its program while running. Give each request a unique identity and retain/archive
or explicitly discard its data before the next preparation.

### Plan B — finite current-segment composition

Add an offline model when long or nonuniform multi-stage schedules become a
priority. A possible initial design is a fixed-current source function with a
finite immutable tuple of labeled `(level_a, duration_s)` segments, an optional
finite repeat count, one global electrical sample period, voltage compliance,
and optional voltage cutoffs.

The design must decide and test:

- whether the first revision keeps one global measurement cadence or supports
  per-segment cadence;
- how segment, cycle, and repeat indices enter measurement mapping;
- how requested versus achieved segment times and total commanded charge are
  reported;
- how transition, power, cutoff, storage, and trigger-block budgets are bounded;
- that `sourced_zero` is explicit and distinct from `output_off` or any future
  validated open-circuit mode; and
- how canonical schema/versioning and generator/preset provenance are retained.

Avoid nested unbounded programs or implicit host loops. Add pure compiler and
independent simulator tests before allowing the model into any Flyer allowlist.
This model would efficiently cover preconditioning, amplitude staircases,
burst–rest, full bipolar pairs, HPPC-style shapes, and richer ICI/GITT programs.

### Plan C — versioned waveform generators

Before adding a named chirp, define a finite `ChirpSpec` with start/stop
frequency, amplitude, bias, phase convention, duration/sample grid, sweep law,
and generator version. Resolve it deterministically to `ArbitraryWaveform` and
retain enough specification/provenance to reproduce the saved levels.

The same provenance decision applies before bounded staircase or burst helpers
are promoted from examples to public preset APIs. Raw resolved levels and their
digest remain authoritative; generated waveforms must never be silently
renormalized or clipped.

### Plan D — operational commissioning

Operational sequence work stays behind the existing hardware milestones:

1. Complete the START-to-measurement-aperture proof and target-record projection,
   then connect the target backend to the Flyer.
2. Commission shared arbitrary-current playback with fixed measurement profiles,
   timing/storage limits, abort/cutoff behavior, and resistor-load fidelity
   evidence. PRBS and multisine should reuse this path.
3. Promote only the program types supported by that backend into its explicit
   allowlist; a simulator override is not target evidence.
4. Commission voltage sourcing separately before operational voltage waveforms
   or PITT-style presets.
5. Establish detector-exposure correlation independently. A requested short
   source dwell is not proof that an exposure resolves it.

Current amplitudes, compliance, cutoff values, sign convention, initial state of
charge, and temperature are experiment inputs. Until sign convention is explicit
in acquisition metadata, examples should say positive/negative source current
rather than assuming which polarity means charge or discharge.
