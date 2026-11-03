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

## Conventions

Hard carbon (HC) against Na metal, two electrodes. **HC on HI (force and
sense), Na on LO**, so the measured voltage is the cell voltage (5 mV–2.5 V).
In the sequence model a positive current raises the voltage: **sodiation
(discharge) is a negative current, desodiation (charge) a positive one**. With
the leads swapped every step would run the wrong way, so the wiring check before
each run must include polarity.

1C = 300–600 µA implies a capacity of about 0.3–0.6 mAh; 15–30 µA is then about
C/20.

## The protocols in sequencer terms

| ID | Protocol | Steps (one sequence each) | Window |
|---|---|---|---|
| P0 | Overnight sodiation (7–8 Dec) | CC −I → 0.2 V (NEXT), rest | 0–2.7 V |
| P1 | Sodiation without pulse | CC −I → 5 mV (NEXT), rest | 0–0.3 V |
| P2 | Desodiation with pulses | {+I pulse t_p, rest t_r} × n, the pulse STOPs the run at 0.1–0.2 V | 0–0.3 V |
| P3 | Combined pulse | CC −I → 0.2 V, rest, {−I t_p, rest t_r, +I t_p, rest t_r} × n | 0–0.3 V |
| P4 | Reference cycling | {CC −I → 5 mV, CV 5 mV → \|I\| < I_min, rest, CC +I → 0.2 V, rest} × n | 0–0.3 V |

P2 is our reading of "desodiate with pulse until 0.1–0.2 V" (see question 3).
In every case the absolute window stops the run; 0 V is the proposed hard
lower limit (no plating below 0 V vs Na is sought before the planned plating
test).

## Feasibility on the 2460 sequencer

Compiled with placeholder values (1C = 450 µA, C/20 = 22.5 µA, t_p = 1 s,
t_r = 4 s, 6 ms fast sampling, 1–10 s otherwise):

| ID | Blocks (≤ 255) | Records (upper bound) | Duration (upper bound) | Ranges |
|---|---|---|---|---|
| P0 | 28 | 5,220 | 14.5 h | 100 µA source, 7 V measure, 1 NPLC |
| P1 (1C) | 28 | 7,800 | 2.2 h | 1 mA, 2 V |
| P2 (≤ 2000 pulses) | 55 | 416,119 | 2.8 h | 1 mA, 2 V, 0.1/1 NPLC |
| P3 (n = 200) | 86 | 87,400 | 1.7 h | 1 mA, 2 V, 0.1/1 NPLC |
| P4 (n = 1, ≤ 15 h per CC) | 57 | 12,600 | 35 h | 100 µA, 2 V |

All of them fit the sequencer: block budget, buffer (1,000,000 records), current
and voltage ranges, and timing. The 0–0.3 V window measures on the 2 V range,
where the reading times were measured; P0 measures on the 7 V range (B1 test
R3). A 5 mV cutoff is well above the 2 V range's resolution and offset.

## Assessment

1. **Time and instruments.** There is one 2460, so one cell at a time.
   - P4 at C/20 needs on the order of 20–35 h per cycle (two half-cycles in the
     plateau, plus the CV hold). Two cycles exceed the two beamline days.
   - P0 for each cell takes a night.
   - Pulsed and non-pulsed runs on two cells, plus overnight sodiation and
     reference cycling, do not fit on one instrument in two days. Either the
     reference and overnight steps run on another cycler, or we need a second
     sourcemeter, or the plan shrinks.
2. **Pulse order at 5 mV.** After P1 the cell sits at 5 mV. A pulse train that
   starts with −I (as in P3) would cross the 5 mV cutoff on the first pulse and
   stop the run. Starting from 5 mV, pulses must start with +I, or the window
   must allow lower voltages, which is the plating risk to be avoided at first.
3. **"Desodiate with pulse".** Symmetric ±I pulses carry no net charge, so on
   their own they do not desodiate. Options:
   - unipolar +I pulses with rests (GITT-like, P2 above), ending at 0.1–0.2 V;
   - a CC desodiation with pulses superimposed;
   - asymmetric pulses.

   Each needs a defined end. The cutoff is tested on the measured voltage under
   load (during a +I pulse the voltage reads higher than the relaxed voltage),
   so a 0.2 V cutoff during a pulse is reached earlier than a 0.2 V rest voltage.
4. **Plating margin.**
   - In a two-electrode cell the Na counter electrode's overpotential makes the
     measured voltage lower than the HC potential during sodiation. A 5 mV
     cutoff on the measured voltage therefore errs on the safe side. This is
     reasoning, not a measurement.
   - The sequencer tests cutoffs only at samples. In current mode the 2460's
     compliance (|V| ≤ 0.4 V here) does not stop the voltage from going below
     0 V between samples, so sampling near 5 mV should be ≤ 1 s.
   - The CV hold at 5 mV (P4) itself sits close to plating conditions.
5. **What the run sheet can express today.**
   - The bipolar pulse protocol always starts with +I.
   - The cycling protocol puts the CV hold after the charge, not after the
     sodiation.
   - There is no "CC approach, then pulses" protocol (P3), and no unipolar pulse
     train (P2).

   All four are small extensions of existing builders (a pulse-polarity
   option, a CV-after-discharge option, an optional approach step, unipolar
   pulses), with no new instrument features. They are proposed here, not yet
   implemented.
6. **Pulse-length series.** Since pulsed vs non-pulsed matters more than t_p,
   the template's 16-run t_p series can give way to pairs (pulsed, non-pulsed)
   at one or two t_p values.
7. **Overnight sodiation (P0).** It needs a current (C/20 at 15–30 µA takes
   roughly a night from 2.5 V), a time limit, and an end state: rest after the
   cutoff (the voltage relaxes above 0.2 V), or a CV hold at 0.2 V. Its window
   (to 2.7 V) differs from the measurement window, so it is a separate session
   or session file.
8. **Dates.** 7–8 December 2026 is a Monday–Tuesday, in the second week of
   December. The MVP plan says the first week. Confirm the beamtime days.

## Questions for the material scientists

1. Beamtime days, and whether the 7–8 Dec overnight sodiation happens on our
   2460 (one cell at a time) or on your cycler.
2. How many cells: pulse cell(s), reference cell(s), and which ones must be
   measured at the beamline.
3. "Desodiate with pulse until 0.1–0.2 V": unipolar pulses with rests,
   CC with superimposed pulses, or something else? Does the cutoff apply to the
   voltage under load or after the rest?
4. t_p, t_r and n (or the total time per pulse run); the C-rate for the
   non-pulsed sodiation to 5 mV.
5. P4: I_min and the maximum CV time; number of cycles that matter at the
   beamline.
6. P0: current, time limit, and the end state at 0.2 V (rest or CV hold).
7. The cells' capacity (mAh), to fix 1C and C/20 exactly.
8. What the beamline records (technique, frame rate), so we can choose sampling
   rates and the step-marker use.

## Next steps (after answers)

- Implement the builder and run-sheet extensions from point 5 that the answers
  confirm.
- Write the beamtime run sheet. Run `oe-validate` with the beamtime session
  file, then rehearse it on the RC dummy cell (B2) with the same windows.
- Add a dummy-cell check of the 5 mV cutoff and HIGH-Z hold at low voltage to
  B2.
