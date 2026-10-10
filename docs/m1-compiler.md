# M1: validated intent and pure planning compiler

M1 implements immutable structurally validated models, deterministic PRBS and
multisine generation, canonical request round trips, and a pure bounded planning
compiler. [M2](m2-simulator.md) adds independent simulated execution over this IR.
The [M3 transport](m3-transport.md) adds bounded communications but no operational
device or TSP code generation. All hardware gates remain NOT RUN.

## Try the offline example

From the repository root:

```bash
uv sync --locked
uv run --locked python -m examples.compile_program
uv run --locked pytest tests/unit
```

The [complete example](https://github.com/BAMresearch/ophyd-electrochemistry/blob/main/examples/compile_program.py)
uses an explicit **synthetic** capability/configuration profile. It compiles a
127-bit PRBS with 10 ms requested bit dwell, ten repeats and 20 ms electrical
measurement cadence: 12.7 s, 1,270 logical source points and 635 electrical
records under that synthetic profile. Output includes `hardware_ready: false`.
These numbers are planning arithmetic, not a 2460 speed specification. The
module does not open its `SIM::NO_CONNECTION` resource.

The public intent classes are `PotentiostaticHold`, `GalvanostaticHold`,
`CyclicVoltammetry`, `CurrentPulseSequence`, `VoltagePulseSequence`,
`ArbitraryWaveform` and `PRBSWaveform`.
`generate_multisine(MultisineSpec(...))` returns `ArbitraryWaveform` retaining its
specification. Constructors reject nonfinite/wrongly typed SI values, booleans
used as counts, inconsistent cutoffs and malformed waveform intent. Physical
limits and feasibility are checked at compilation, not proved by construction.

## Compile and export a plan

`Keithley2460Compiler(capabilities).compile(request, config)` is the concrete
pure API in `ophyd_electrochemistry.keithley.k2460`. Required profiles are
`Keithley2460Capabilities` with one or more `SourceCapabilities`. They declare
range/compliance, transitions, timer/action/settling/aperture characteristics,
fidelity criteria, finite storage/layout/payload budgets and supported features.
There is no inferred profile or instrument query. A future backend must bind a
profile to actual firmware, ranges, measurement schema/settings and bench evidence.
`max_command_slope_per_s` bounds adjacent setpoint change divided by the preceding
dwell. It is a discrete command policy, not a guarantee of analog rise time or
overshoot; those require load/range characterization at G08.
`evidence_kind="bench"` is a caller declaration, not independent verification;
the returned planning IR still has `hardware_ready=False`.

`Keithley2460Config` retains explicit cell safety, terminals/sense, I/O and timing
policies. `TimingPolicy` now requires `required_cutoff_latency_s` as well as the
existing timeouts/abort bound. Source-period, measurement-period, total-duration,
tone-frequency and scan-rate error tolerances default to **zero**, meaning no
intentional change is accepted. Any relaxation must be explicit. OFF mode names
are allowed by the profile, never interpreted as instrument constants in M1.

The compiler returns an immutable `CompiledProgram`:

| Field | Meaning |
|---|---|
| `source` | Finite steps and integer tick dwells, half-open boundaries and repeat count |
| `measurement` | First integration start, period, aperture, overhead and count in ticks |
| `sample_mapping(i)` | Planned aperture and first/last logical points, repeat/point position and settled single-dwell flag |
| `requested_duration_s`, `achieved_duration_s` | Intent versus quantized planning duration; not observed physical timing |
| `achieved_source_period_s` | Uniform waveform/CV dwell; None for holds/nonuniform pulse dwell pairs |
| `mean_level`, `ac_rms_level`, `ac_crest_factor` | Dwell-weighted source statistics; constant excitation has zero AC crest factor by convention |
| `commanded_charge_c` | Mean commanded current times plan duration, or None for voltage sourcing |
| `effective_voltage_cutoffs_v` | Current-mode cutoffs narrowed within the mandatory cell safety envelope |
| `budget` | Unique/logical points, measurement/source/combined buffer records, abstract block estimate and serialized bytes |
| `canonical_request_json`, `request_sha256` | Type-tagged versioned request encoding and digest |
| `canonical_program_json`, `program_sha256` | Full plan/config/profile/generator encoding and digest |
| `waveform_sha256`, `generator_provenance_json` | Source schedule digest and retained PRBS convention or multisine tones/envelope |

The IR schema is `ophyd-electrochemistry/finite-ir-v1`; compiler version is
`m1-compiler-v1`. It prescribes terminal `output_off` and faulting cutoffs, but
does not execute them. Export `canonical_program_json` with future acquisition
data; no file is written implicitly. Trigger-block budgets use an explicitly
declared abstract list-loop layout (`fixed + per_point * unique_points`), not
an emitted/verified TriggerFlow model. Actual code generation/layout and native
firmware counter/list constraints remain M3–M5 work.

## Timing and endpoints

All plan times use integer ticks. Nearest-tick, half-up quantization must satisfy
the applicable explicit tolerances, including accumulated total duration. Profile
action/settling/aperture/overhead times must be exactly representable. Measurements
begin after initial action plus settling, then follow their independent period.
Only complete apertures plus overhead fitting before terminal shutdown are counted.
Apertures are `[start, end)`; one ending exactly on a source edge belongs to the
preceding dwell. M1 computes planned associations, not actual clock synchronization.

If the profile forbids integration across source updates/settling windows, such
a schedule is rejected. Otherwise `sample_mapping` explicitly marks those
readings as not wholly settled within one dwell. Overlap support must correspond
to a validated joint source/measurement runtime; it is not implied by separate
timer names.

For current sourcing, unspecified lower/upper voltage cutoffs inherit the
mandatory SafetyConfig bounds; explicit cutoffs may narrow them. Cutoff policy
is `fault`. When `local_cutoffs_use_measurements=True`, the bound includes sample
period, aperture, overhead and declared cutoff response latency. A slower sample
cadence cannot inherit a fast cutoff claim. False requires a separately evidenced
local monitoring/protection mechanism. Compliance and protocol cutoffs are distinct.
The initial action/settling delay also enters the worst-case cutoff bound when
it exceeds one sample period.

Current pulses begin with the pulse level, followed by baseline for
`period - width`, repeated `count` times. Zero baseline remains sourced, not OFF.
Voltage pulses use the same two-dwell schedule with voltage setpoints and current
compliance. A current pulse and an opposite-sign baseline can form a bipolar
sequence; equal dwell durations and magnitudes give zero net commanded charge.
Neither pulse model inserts an implicit zero-level rest.
Arbitrary/PRBS points each receive a full dwell, including the final point;
repeats add no endpoint or gap. Equal PRBS bits retain individual logical indices.

CV uses its electrical sample period as the discrete source-step period. Each
nonzero leg uses `ceil(abs(delta_voltage) / (scan_rate * achieved_step_period))`
equal voltage intervals; achieved leg rates must satisfy the scan-rate tolerance.
Vertices are exact, shared joins appear once, and zero-length legs are skipped.
Repeated cycles share their return/start point. The final return-to-start point
has **one additional full dwell** to permit a terminal endpoint measurement;
duration/storage include it. This final hold is reported, not hidden. Endpoint
cadence must fit source action, settling, full aperture and overhead; otherwise
CV is rejected rather than losing or integrating across its terminal endpoint.
points carry the index of the arriving leg; full-cycle shared joins are not
duplicated. CV expansion is budgeted before constructing its arrays.

## PRBS convention and reproducibility

Generator `fibonacci-xor-right-lsb-v1` supports orders 2–10. Output the register
LSB before each update; XOR the listed zero-based bits, shift right, and inject
the feedback at bit `order - 1`. Nonzero seed is the initial integer register.
Each repeat restarts the same sequence. Bits map 0 to low and 1 to high.

| Order | Feedback bit indices | Polynomial |
|---|---|---|
| 2 | 0, 1 | x² + x + 1 |
| 3 | 0, 1 | x³ + x + 1 |
| 4 | 0, 1 | x⁴ + x + 1 |
| 5 | 0, 2 | x⁵ + x² + 1 |
| 6 | 0, 1 | x⁶ + x + 1 |
| 7 | 0, 1 | x⁷ + x + 1 |
| 8 | 0, 2, 3, 4 | x⁸ + x⁴ + x³ + x² + 1 |
| 9 | 0, 4 | x⁹ + x⁴ + 1 |
| 10 | 0, 3 | x¹⁰ + x³ + 1 |

Tap selection uses reciprocal primitive polynomials associated with the table
in [AMD/Xilinx XAPP052](https://docs.amd.com/v/u/en-US/xapp052), with this project's
explicit XOR/LSB convention rather than the note's XNOR/register numbering.
No vendor implementation is included. Tests contain full-vector digests from
an independent polynomial bit recurrence for each supported order and verify
all cyclic register windows, bit balance and off-peak autocorrelation. Order 3,
seed 1 gives `1001011`, fixing shift/output/phase interpretation.

`prbs_bits` and `generate_multisine` have explicit expansion budgets (host default
100,000 points, multisine 64 tones); these are host allocation guards, not hardware
capacities. Compilation applies its stricter profile budgets before PRBS/CV
expansion. User-provided arbitrary tuples are already allocated by the caller.

## Multisine and canonical encoding

Multisine frequencies are checked against integer bins within the explicit
`coherence_tolerance_bins` (default 1e-9, maximum 1e-6). Requested frequencies
are retained and used to generate samples; they are not snapped. Phases normalize
to `[0, 2*pi)`. There is no duplicate final period endpoint, clipping or scaling.
Compilation checks the conservative combined envelope, actual samples/transitions,
profile points-per-highest-tone criterion and achieved-frequency tolerance.
Coherence residue/ordinary floating arithmetic are distinguished from permitted
intentional timer quantization.

Saved sample values and their digest are authoritative. Attached multisine
provenance is checked against regeneration within eight ULPs of the full
absolute bias/amplitude envelope; a contradictory spec/list is rejected.
Cross-platform sine results are not assumed bit-identical. The compiled payload
retains the originally saved samples and requested spec.

`canonical_request_json`, `request_sha256` and `request_from_json` are public
package APIs. Encoding uses sorted compact UTF-8 JSON, explicit model/enum tags,
normalized SI floats/negative zero and schema `ophyd-electrochemistry/request-v1`.
Decoding allows only known models, requires exact fields, reruns construction
validation, rejects duplicate keys/unknown schemas and limits input to 16 MiB.
It never imports a type named by an input file. The compiled hash also covers
physical configuration, capability evidence identifier and generator provenance;
changing those may change the plan hash while preserving the intent hash.
