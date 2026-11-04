# Beamtime measurement concept (preliminary) and assessment

Received 3 Nov 2026 from the material scientists; to be refined. This page
records the concept, translates it into sequencer steps, checks it against the
2460 sequencer, and lists what we need to know before writing the beamtime run
sheet. Values marked *placeholder* are ours, not theirs.

## The concept as received

> First of all, it is important to us to avoid the risk of plating during
> pulsing. Therefore, we would suggest starting with the sodiation without pulse
> until the cut-off voltage of 5 mV (deep in the plateau area).
> Then we can desodiate with pulse until the cut-off voltage of 0.1-0.2 V to stay
> in the plateau area. Once this is done we could sodiate again, this time
> risking the plating with pulsing or just taking a 2nd cell and doing pulse only.
>
> Combined pulse:
> CC discharge (sodiation)→ 0.2 V, rest, {−I for t_p, rest t_r, +I for t_p, rest t_r} × n
>
> For the reference cycling cells this would mean the following protocol:
>
> Reference cycling: {CC discharge (sodiation)→ 5 mV, [CV hold → |I| < I_min], rest, CC
> charge → 0.2 V, rest} × n
>
> Generally, for us it is more important to compare pulsed and non-pulsed
> de-/sodiation than different pulse t_p between each other.
>
> The total voltage range of the measurements would be from 5 mV-0.2 V.
> For the reference cycling, the applied current would be in the range of 15-30 µA.
> For the pulses we would suggest a higher current rate (1C) which would result in
> an applied current of 300-600 µA.
>
> The cells that we bring will be cycled beforehand and in desodiated state
> (~2.5V) for the transport. We should then sodiate them over night (7.-8.12.) to
> the desired voltage of ca. 0.2 V (shortly before the plateau region starts) for
> the measurements to start.

## Refinement, 4 Nov 2026 (discussion with Jonas)

Decided:
- **Unipolar pulses, starting fully sodiated.** Pulsed sodiation risks Na-metal
  plating. The direction does not change from pulse to pulse, so sodiation and
  desodiation under pulses can be compared.
- **Terms:** *sodiation* and *desodiation* instead of discharge and charge,
  which are ambiguous in half cells. Run sheets accept both.
- **Cells:** a d = 12 mm electrode with about 1.5 mg of active material:
  - 400 mAh/g (BAM core-shell), so about **0.6 mAh**;
  - or 350 mAh/g (commercial Kuranode), so about 0.53 mAh.
- **Currents:** CC at **30 µA (C/20)**; pulses at **600 µA (1C)**.
- **Pulses:** 10 s to 1 min. Jonas suggested 1 min pulses with 5 min rests,
  which makes a 1C half-cycle (1 h of pulse time) last 6 h. The scientists
  expect no slow structural dynamics after the first seconds of a pulse or
  rest, so the short timescales matter most.
- **Purpose:** a proof of principle, to see whether there is anything
  interesting at all. Longer or shorter pulses and other rests can follow in a
  later proposal.
- **SAXS:** 10–100 ms per frame, depending on the flux.

The pulse current came up as both "1C" and "40C". 40C would be 24 mA, which is
40 times the stated 600 µA; see question 1 below.

The sodiation (P0) will probably run separately, a day in advance (user, 4 Nov).

## Conventions

Hard carbon (HC) against Na metal, two electrodes. **HC on HI (force and
sense), Na on LO**, so the measured voltage is the cell voltage (5 mV–2.5 V).
In the sequence model a positive current raises the voltage: **sodiation is a
negative current, desodiation a positive one**. With the leads swapped every
step would run the wrong way, so the wiring check before each run must include
polarity.

With 0.6 mAh: 1C = 600 µA, C/20 = 30 µA, and one 1C pulse of 10 s passes
1/360 of the capacity.

## The protocols in sequencer terms

| ID | Protocol | Run-sheet protocol | Steps | Window |
|---|---|---|---|---|
| P0 | Sodiation to fully sodiated, the day before | `soc_position` | CC −I → 5 mV (NEXT), rest | 0–3 V |
| P2d | Desodiation pulses | `pulse_train`, +I | rest, {+I t_p, rest t_r} × n; a pulse reaching 0.2 V STOPs the run | 0–0.5 V |
| P2s | Sodiation pulses (plating risk) | `pulse_train`, −I | rest, {−I t_p, rest t_r} × n; a pulse reaching 5 mV STOPs the run | 0–0.5 V |
| P1 | Non-pulsed comparison | `soc_position` | CC ±I → 0.2 V or 5 mV, rest | 0–0.5 V |
| P4 | Reference cycling | `cycling` | {CC +I → 0.2 V, rest, CC −I → 5 mV, CV 5 mV → \|I\| < I_min, rest} × n | 0–0.5 V, own session (point 4) |

The bipolar pulse (former P3) and the separate sodiation to 5 mV (former P1)
are no longer planned. `bipolar_pulse` stays available. In every case the
absolute window stops the run, and 0 V is the hard lower limit.

## Feasibility on the 2460 sequencer

Compiled with 1C = 600 µA, C/20 = 30 µA, 6 ms fast sampling for 0.5 s per
step, then 0.1 s (pulse trains) or 1 s (CC):

| Case | Blocks (≤ 255) | Records (upper bound) | Duration (upper bound) |
|---|---|---|---|
| P0, C/20 to 5 mV (≤ 24 h) | 28 | 88,200 | 24.5 h |
| P0, C/10 to 5 mV (≤ 14 h) | 28 | 52,200 | 14.5 h |
| P2d, 60 s / 300 s × 60 (full half-cycle) | 55 | 226,159 | 6.0 h |
| P2d, 10 s / 50 s × 360 (full half-cycle) | 55 | 273,559 | 6.0 h |
| P2d, 10 s / 300 s × 60 (1/6 of the capacity) | 55 | 196,159 | 5.2 h |
| P2s, 10 s / 50 s × 360 | 55 | 273,559 | 6.0 h |
| P1, CC 1C desodiation to 0.2 V | 28 | 72,000 | 2.0 h |
| P4, one C/20 cycle (≤ 20 h per CC step) | 57 | 151,200 | 42 h |

All of them fit: block budget, buffer (1,000,000 records), ranges, and timing.
The 0–0.5 V window measures on the 2 V range; P0 measures on the 7 V range
(B1 test R3).

The duration of a pulse run is the pulse time it needs, times
(1 + t_r / t_p). For a full half-cycle at 1C:
- 6 h with t_r = 5 t_p;
- 31 h with 10 s pulses and 5 min rests.

## Assessment

1. **Time and instruments.** There is one 2460, so one cell at a time.
   - A full pulsed half-cycle takes about 6 h.
   - P0 at C/20 takes about 20 h for the whole capacity, so it does not fit
     a night: either C/10 (about 10 h) or an earlier start (question 2).
   - P4 at C/20 takes about 40 h per cycle and does not fit the beamtime. It
     belongs on another cycler, or before the beamtime.
   - Two days fit, for example: P2d and P1 on one cell, or P2d and P2s on one
     cell, plus the overnight P0 for the next cell.
2. **Where a pulse run ends.** A pulse train STOPs when a pulse reaches the
   threshold, measured **under load**:
   - a desodiation pulse reads higher than the relaxed voltage, so 0.2 V is
     reached before the relaxed voltage gets there;
   - a sodiation pulse reads lower, so 5 mV under load is a conservative
     plating guard.

   Alternatively, the run can end after a fixed number of pulses (a fraction of
   the capacity) without reaching a threshold.
3. **Plating margin.**
   - In a two-electrode cell the Na counter electrode's overpotential makes the
     measured voltage lower than the HC potential during sodiation. A 5 mV
     cutoff on the measured voltage therefore errs on the safe side. This is
     reasoning, not a measurement.
   - The sequencer tests cutoffs only at samples. The voltage limit does not
     stop the voltage from going below 0 V between samples. Pulse trains sample
     every 0.1 s after the first 0.5 s, and CC runs near 5 mV should sample at
     1 s or faster.
4. **CV hold current.**
   - The CV hold at 5 mV (P4) itself sits close to plating conditions.
   - Its current limit is the session's maximum current.
   - It follows its CC step even when that step ended on its time limit rather
     than at 5 mV.

   Run P4 in a session whose maximum current is near the cycling current
   (100 µA), not the pulse current (found 4 Nov, B2 plan).
5. **What the run sheet can express.** Since 4 Nov, every protocol above is a
   sheet row:
   - `pulse_train` with a signed current;
   - `start_with` and `cv_after` accept `sodiation` and `desodiation`.

   A "CC approach, then pulses" in one run is no longer needed: it is a
   `soc_position` row followed by a `pulse_train` row.
6. **Sampling against SAXS.** 6 ms for the first 0.5 s of every step, then
   0.1 s, covers 10–100 ms frames.
   - Faster sampling after the first 0.5 s (for example 10 ms throughout a
     10 s pulse) costs records but fits: about 1,000 per pulse.
   - Step markers on line 4 mark every pulse start for alignment with the
     frames.
7. **Bench rehearsal.** B2 (`bench/plan-b2.md`) runs this protocol in small on
   a coin cell:
   - K3: overnight sodiation to 5 mV;
   - K4 and K5: desodiation trains of 10 s / 50 s and 60 s / 300 s;
   - K6: a sodiation train;
   - K8: a C/20 reference cycle over a weekend.
8. **Dates.** 7–8 December 2026 is a Monday–Tuesday, in the second week of
   December. The MVP plan says the first week. Confirm the beamtime days.

## Questions for the material scientists

Answered on 4 Nov: unipolar pulses (old question 3), the capacity (old 7),
pulse lengths in principle (old 4), and SAXS frame times (old 8).

1. **Pulse current:** 1C (600 µA) or 40C (24 mA)? 40C passes 1/90 of the
   capacity per second and would need the 100 mA range. The cutoffs would
   probably end such pulses at once; B2's K2 measures the cell resistance that
   decides this.
2. **Fully sodiated start:** sodiation at C/20 takes about 20 h. Is C/10
   overnight acceptable, or does it start earlier or run on your cycler? Is a
   CV hold at 5 mV wanted at the end?
3. **Pulse run end:** a fixed number of pulses (which fraction of the
   capacity), or until 0.2 V under load? The same question for sodiation
   pulses and 5 mV.
4. **Sodiation pulses:** wanted at the beamline despite the plating risk
   (P2s), or only desodiation pulses?
5. **Non-pulsed comparison:** at the pulse current (1C CC) or at C/20?
6. **t_p and t_r** for the beamtime: 10 s / 50 s, 1 min / 5 min, or both.
7. How many cells, and which material (core-shell or Kuranode) in which cell.
8. Beamtime days (point 8), and what else the beamline records.

## Next steps

- B2 on the coin cell (from 5 Nov) with this protocol in small.
- After the answers: write the beamtime run sheet and its sessions (pulse
  session 0–0.5 V / 1 mA; reference session 0–0.5 V / 100 µA; sodiation session
  0–3 V), validate with `oe-validate`, and rehearse on the simulator.
