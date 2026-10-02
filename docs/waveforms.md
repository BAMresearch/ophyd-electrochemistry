# PRBS, arbitrary waveforms and multisine

Contract revision 0.2 defines waveform intent and future instrument execution.
M1 now implements `PRBSWaveform`, `ArbitraryWaveform`, `MultisineSpec`, pure
generation and planning compilation; the public program union includes PRBS
and arbitrary intent. No instrument playback is implemented. See the
[M1 API and runnable example](m1-compiler.md) for exact current behavior.

## Shared execution model

`PRBSWaveform` is a first-class electrochemical program. `ArbitraryWaveform` is
the common finite playback representation. A pure multisine generator produces
an `ArbitraryWaveform` with retained generator provenance. All three use the
same compiler, instrument-local runtime, Device/Flyer lifecycle and protections.
Generation may happen in Python before preparation; playback timing may not.

| Parameter / execution requirement | Meaning and validation |
|---|---|
| `source_function` | Exactly `current` or `voltage`, fixed during an acquisition |
| Source levels / bias / amplitudes | SI amperes for current sourcing or SI volts for voltage sourcing; source function determines units |
| `sample_period_s` | Positive requested electrical measurement period; independent of source-update/bit period |
| `voltage_limit_v` | Required complementary compliance for current sourcing |
| `current_limit_a` | Required complementary compliance for voltage sourcing |
| Cell cutoff policy | Explicit applicable voltage/current cutoffs and termination policy; inherit cell-specific safety configuration |
| `repeats` | Positive integer; excludes booleans, fractional values and unbounded playback |
| Timing tolerances | Explicit acceptance tolerances for source and measurement schedules, supplied by the request or validated configuration |

Exactly the applicable complementary compliance is required; contradictory
parameters are rejected. Levels may be positive, negative or zero within the
cell/instrument envelope. Zero current/voltage is a sourced setpoint, not physical
disconnection. Sourcing and sinking limits, power, range transitions, cutoff
evaluation latency and the repeat-boundary transition all require validation.
Voltage/current waveform variants are exposed by a backend only after the
corresponding capability is commissioned.

No waveform changes source function mid-run. Current-driven battery excitation
is the initial implementation priority; voltage-driven playback follows the
same model but has its own capability/evidence scope. There are no universal
battery defaults. These definitions apply independently of battery chemistry.

## ArbitraryWaveform

The initial scope is a uniform finite list, not a callable evaluated during
execution or a streaming waveform. Its distinguishing fields are:

| Planned parameter | Meaning |
|---|---|
| `levels` | Nonempty immutable sequence of finite SI setpoints |
| `point_period_s` | Positive requested dwell of each point |
| `repeats` | Number of complete list repetitions |

For `N` points, requested dwell `dt` and `R` repeats, requested duration is
`N * dt * R`. Point `j` starts at `j * dt` within each repeat and is held until
the next point. The last point receives a full dwell. A repeat starts again
at the first point without inserting an extra endpoint or gap. At the final
boundary the runtime shuts output OFF within its validated shutdown tolerance;
it does not leave the final level enabled. Terminal timing is retained.

The compiler emits a zero-order-hold schedule with bounded timing error, not
linear interpolation. Nonuniform dwell lists, on-the-fly extensions, user-supplied
TSP and source-OFF segments within a waveform are deferred. Other existing
protocols may retain their explicitly defined rest semantics.

All points must be available locally before arming. Looping/reusing an uploaded
list is allowed if it preserves the finite schedule and record indices; runtime
list refill over Ethernet is not. Storage estimates include every measurement,
all repeats and any source-transition records, not just the unique list length.
Application-specific list limits must not be treated as firmware capacities.

## PRBSWaveform

PRBS means a deterministic pseudorandom binary sequence, not independent random
pulses. In addition to the shared parameters it specifies:

| Planned parameter | Meaning |
|---|---|
| `low_level`, `high_level` | Distinct finite levels with `low_level < high_level`; bit 0 maps to low and bit 1 to high |
| `bit_period_s` | Positive dwell of every logical bit |
| `generator_id` | Versioned maximal-length LFSR convention, including polynomial/taps, register orientation, output bit and update order |
| `order` | Integer in the generator's explicitly supported order table |
| `seed` | Initial LFSR register integer in `1 .. 2**order - 1`; not a generic random-number-generator seed |
| `repeats` | Complete sequence repetitions starting at the same seed/phase |

The generator's supported polynomials and convention must be documented and
checked against independent known sequences. Arbitrary user tap sets and shorter
truncated sequences are deferred. Maximal period, valid binary levels and
absence of the all-zero state must be established for each supported order.
Large orders are rejected before allocating/expanding an infeasible sequence.

Sequence length is `L = 2**order - 1`; requested duration is
`L * bit_period_s * repeats`. Adjacent equal bits still consume separate logical
dwells. Coalescing them is permitted only if full dwell duration, logical bit
mapping, measurement schedule and abort latency are preserved. Timing and repeat
boundaries never depend on network writes or random generation at runtime.

For example, order 7 has 127 bits. A requested 10 ms bit period yields a
1.27 s sequence and 12.7 s for ten repeats. This arithmetic is not evidence
that the selected 2460 setup can execute or measure at 10 ms.

For current sourcing, record the exact sequence mean and commanded charge
`sum(levels) * achieved_bit_period_s * repeats`. A maximal-length binary sequence
does not have equal counts of zeros and ones; symmetric levels therefore need
not be charge-neutral. Choose levels/bias explicitly if a particular commanded
mean is required. Measured charge uses measured current and its actual sampling
aperture/timestamps; commanded charge is not a measurement.

## Multisine generator

`MultisineSpec` describes a pure deterministic construction. It is retained
alongside the generated `ArbitraryWaveform`; it is not a separate operational
program or an instrument-side sine generator. Its distinguishing fields are:

| Planned parameter | Meaning |
|---|---|
| `bias` | Finite DC source level in the selected SI unit |
| `frequencies_hz` | Nonempty tuple of distinct positive tone frequencies |
| `amplitudes` | Matching positive peak amplitudes, not RMS or peak-to-peak |
| `phases_rad` | Matching explicit finite phases, normalized canonically |
| `point_period_s` | Positive source-update period |
| `point_count` | Finite positive integer `N`, with no duplicated period endpoint |
| `repeats` | Complete generated period repetitions |

At requested point `k` for `k = 0 .. N-1` the generator evaluates
`bias + sum(A_i * sin(2*pi*f_i*k*dt + phase_i))`.
The initial coherent mode requires each `f_i = bin_i / (N * dt)` for a distinct
integer bin with `0 < bin_i < N/2`; zero and Nyquist tones are rejected.
Bin matching uses an explicit documented numerical tolerance, never implicit
frequency snapping. No phase search, optimization or randomization happens
during playback. A later phase-selection helper must save the resolved phases
and its algorithm/version/seed as provenance.

Check combined peaks and transition limits across the generated list and its
repeat boundary. Also validate the conservative analytical envelope
`bias +/- sum(amplitudes)`; this initial policy may reject some otherwise
bounded phase combinations, but must not silently relax protection. Apply
physical power/complementary compliance and cell cutoffs to the combined signal.
Clipping or normalization would change the excitation and is rejected.

Record tone bins, requested frequencies/phases, generator version, actual
generated samples/hash, requested and achieved period, achieved frequencies,
DC mean, AC RMS and AC crest factor (`max(abs(level - mean)) / AC_RMS`).
Any timer quantization must stay within declared source/frequency tolerances;
achieved coherent frequencies are `bin_i / (N * achieved_dt)`. Preserve the
original request and report compiled phase/time origin instead of overwriting it.
Numeric generation reproducibility is verified by preserved samples and digest,
not by assuming cross-platform floating-point sine evaluations are bit-identical.

Nyquist validation alone is insufficient: require a backend-specific proven
points-per-highest-tone/fidelity criterion, settling limits and electrical
measurement aperture/bandwidth. The sampled output has staircase/hold effects;
successful playback does not establish calibrated EIS, cell linearity or
stationarity. Spectral/impedance analysis belongs downstream of acquisition.

## Timing, data and SAXS/WAXS correlation

Keep source-update rate, electrical sample rate and detector frame rate separate.
A 10 frames/s detector has a 100 ms frame interval. A 10 ms source bit period
therefore gives ten bits per frame interval; actual exposure width and phase
decide what is integrated. A frame timestamp is not a per-bit observation.
Do not automatically slow the electrochemical program to match detector frames.

Preserve executed waveform phase/step indices, actual START anchor, measurement
aperture and detector exposure intervals with clock uncertainty. Repeated
phase-locked/stroboscopic acquisition requires a separately validated facility
synchronization scheme; this revision does not add a per-point output marker
or change the four-line handshake. Source schedule, measurement schedule and
detector timing are correlated through metadata, not assumed synchronized.

The compiler must establish which source step was active at each measurement.
If an integration aperture crosses steps, record the overlap or mark the mapping
ambiguous; never label the entire integrated reading as an instantaneous response
to one setpoint. Preserve missed source/measurement deadlines and compliance
flags. A timing violation must terminate or follow an explicitly validated
fault policy, not silently distort the excitation. See the [data contract](data-contract.md).

## Evidence and implementation order

Tektronix's [function-generation brief](https://www.tek.com/en/documents/technical-brief/equipping-source-measure-units-with-function-generation-using-tsp-technology)
describes voltage/current configuration-list waveforms and an arbitrary-waveform
app for the 2460. This supports the architectural choice. Deriving PRBS and
multisine playback from a prepared list is our design inference, not evidence
of native named PRBS/multisine commands. The app's limits and triggering behavior
are not our runtime contract; do not run it as a competing instrument controller.

The [official 10 ms pulse example](https://www.tek.com/en/support/faqs/model-2460-smu-tsp-simple-script-voltage-pulses)
does not include simultaneous electrical measurement or prove arbitrary-list
timing under our ranges/load. Gates G02/G03/G04/G08 remain NOT RUN.

M1 implements models, deterministic expansion, hashes and pure resource/timing
planning against explicit profiles. Actual firmware/TSP code generation remains
unimplemented. M2 adds independent simulated execution/fault injection. After the
minimal hold runtime proof, M7 commissions arbitrary current playback, PRBS,
multisine and voltage variants through the shared path. See [roadmap](roadmap.md),
[ADR 0002](adr/0002-finite-waveforms.md) and [hardware acceptance](hardware-acceptance.md).
