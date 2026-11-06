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

**Later on 4 Nov (user):**
- the beamline should look for **sub-second effects**, so **short (about 1 s)
  pulses at high rate**. Long pulses at modest rates are lab-cycler work;
- rests are **zero-current steps** (our `rest`: a 0 A current step with the
  output on), shortened from 5 min to **1 min**;
- a pulse run **ends when the voltage limit is reached**, not after a set
  number of pulses;
- **both directions**: desodiation and sodiation;
- the bench coin cell is not new and its state of charge is unknown.

The "40C" from the discussion fits this: at 40C, 1 s pulses with 1 min rests
sweep a half-cycle in about 1.5 h.

## Conventions

Hard carbon (HC) against Na metal, two electrodes. **HC on HI (force and
sense), Na on LO**, so the measured voltage is the cell voltage (5 mV–2.5 V).
In the sequence model a positive current raises the voltage: **sodiation is a
negative current, desodiation a positive one**. With the leads swapped every
step would run the wrong way, so the wiring check before each run must include
polarity.

With 0.6 mAh: 1C = 600 µA, 10C = 6 mA, 40C = 24 mA. One 1 s pulse at nC passes
n/3600 of the capacity, so a half-cycle takes 3600/n pulses.

## The protocols in sequencer terms

| ID | Protocol | Run-sheet protocol | Steps | Window |
|---|---|---|---|---|
| P0 | Sodiation to fully sodiated, the day before | `soc_position` | CC −I → 5 mV (NEXT), rest | 0–3 V |
| P2d | Desodiation pulses | `pulse_train`, +I | rest, {+I 1 s, rest 60 s} × (bound), ending at the upper voltage limit | see "Ending a high-rate pulse run" |
| P2s | Sodiation pulses (plating risk) | `pulse_train`, −I | rest, {−I 1 s, rest 60 s} × (bound), ending at the lower voltage limit | same |
| P1 | Non-pulsed comparison | `soc_position` | CC ±I → 0.2 V or 5 mV, rest | 0–0.5 V |
| P4 | Reference cycling (lab cycler, or before the beamtime) | `cycling` | {CC +I → 0.2 V, rest, CC −I → 5 mV, CV 5 mV → \|I\| < I_min, rest} × n | 0–0.5 V, 100 µA session |

The bipolar pulse and the long pulses (10 s–1 min at 1C) are no longer planned
for the beamtime. Both stay available. In every case the absolute window stops
the run.

## Feasibility on the 2460 sequencer

1 s pulses sampled at 6 ms throughout and during the first second of each rest,
then 0.25 s. The repeat bound is 1.5× the capacity (the voltage limit normally
ends the run sooner):

| Case | Blocks (≤ 255) | Records (bound) | Duration (bound) | Nominal half-cycle |
|---|---|---|---|---|
| P2d/P2s at 40C (24 mA) | 49 | 77,353 | 2.3 h | 1.5 h |
| P2d/P2s at 20C | 49 | 154,303 | 4.6 h | 3.1 h |
| P2d/P2s at 10C | 49 | 308,203 | 9.2 h | 6.1 h |
| P2d/P2s at 5C | 49 | 616,003 | 18.3 h | 12.2 h |
| P0, C/10 to 5 mV (≤ 14 h) | 28 | 52,200 | 14.5 h | about 10 h |
| P4, one C/20 cycle | 57 | 151,200 | 42 h | about 40 h |

- The 2460 sources 24 mA on its 100 mA range. 30 mA session limits cover 40C.
- 6 ms is the fastest sampling in the current timing profile. The bench
  measured 1.24 ms per reading at 0.01 NPLC (E3, 3 Nov). Sampling faster than
  6 ms during a pulse would need a profile change and a bench check; this is
  proposed, not done.

## Assessment

1. **Time and instruments.** There is one 2460, so one cell at a time.
   - A pulsed half-cycle (1 s / 60 s) takes 3600/n pulses: about 1.5 h at
     40C, 3 h at 20C, 6 h at 10C.
   - P0 at C/20 takes about 20 h for the whole capacity, so it does not fit
     a night: either C/10 (about 10 h) or an earlier start (question 2).
   - P4 at C/20 takes about 40 h per cycle and does not fit the beamtime. It
     belongs on another cycler, or before the beamtime.
   - At 20–40C, both directions on one cell fit a few hours, so two days can
     hold several cells or rates, each needing its own P0 the night before.
2. **Where a pulse run ends.** See the next section. At high rate this
   decides whether a pulse run works at all.
3. **Plating margin.**
   - In a two-electrode cell the Na counter electrode's overpotential makes the
     measured voltage lower than the HC potential during sodiation. A 5 mV
     cutoff on the measured voltage therefore errs on the safe side. This is
     reasoning, not a measurement.
   - The sequencer tests cutoffs only at samples. The voltage limit does not
     stop the voltage from going below 0 V between samples. The 1 s pulses
     sample every 6 ms, and CC runs near 5 mV should sample at 1 s or faster.
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
6. **Sampling against SAXS.** 6 ms throughout each 1 s pulse and the first
   second of each rest, then 0.25 s, covers 10–100 ms frames.
   - Step markers on line 4 mark every pulse and rest start for alignment
     with the frames.
   - Sampling faster than 6 ms is proposed (Feasibility).
7. **Bench rehearsal.** B2 (`bench/plan-b2.md`) runs this protocol in small on
   a coin cell:
   - K2: a 1–40C rate check, away from the plating region;
   - K3: overnight sodiation to 5 mV;
   - K4 and K5: 1 s pulse trains at the chosen rate, desodiation then
     sodiation;
   - K6: a C/20 reference cycle over a weekend.
8. **Dates.** 7–8 December 2026 is a Monday–Tuesday, in the second week of
   December. The MVP plan says the first week. Confirm the beamtime days.

## Ending a high-rate pulse run

`pulse_train` tests its voltage limits **during each pulse, under load**. At
1C the IR drop is small, so that is close to the relaxed voltage. At high rate
it is not: 10C = 6 mA through a 50 Ω cell gives 0.3 V, and 40C gives 1.2 V.

The consequences:
- **Desodiation from fully sodiated:** the first pulse reads above 0.2 V and
  ends the run. It also exceeds a 0–0.5 V window. This carries no risk, but
  produces no data.
- **Sodiation:** the first pulse reads below 0 V and the session window stops
  the run.
- **The measured voltage is not the hard-carbon potential.** In a half-cell at
  high rate, the Na counter electrode's overpotential is a large part of the
  drop. Li et al. (2017) found that it drives half-cells to the lower cutoff
  prematurely at high rates. So a reading below 0 V under load does not by
  itself mean plating on the hard carbon. A two-electrode cell cannot tell
  how much of the drop is which.

Options:

| | End test | Under-load excursions bounded by | Notes |
|---|---|---|---|
| A | During the pulse (now) | the same limits | Works only if the IR drop ≪ the voltage span; fine at 1C, not at 10–40C |
| B | Relaxed voltage: only in the last second of each 60 s rest | the session window, which must then allow the under-load excursion | Recommended. A small builder change: the rest splits into 59 s plus 1 s with the cutoff. Desodiation needs a wider upper window (no plating question). Sodiation needs a lower window below 0 V *under load*, which is the scientists' plating call |
| C | During the pulse, limits shifted by I·R | the shifted limits | Fragile: R changes with SOC and rate |

**Decided 6 Nov (sodiation):** option B, with a 5 min relaxation. A sodiation
pulse run stops when the voltage at the end of a 5 min rest is at or below
5 mV. Under load the voltage may go down to −3 V (Jonas): bulk plating that
reintegrates during the rest is of interest. These runs come after the
desodiation pulses on the same cell, because desodiation after plating cannot
be trusted to look normal. Not built yet: `pulse_train` still tests its limits
during the pulse.

The 5 min rests may shorten to 1 min if tests show the limit can be inferred
from the recorded data; otherwise long rests only near the end of a run, if
that is easy to build. A cutoff cannot leave a repeat block, so "near the end"
means two sheet rows: 1 min rests until an earlier relaxed threshold, then
5 min rests to 5 mV. First data, on the high-resistance coin cell after K3: the
voltage rose 75 mV between 1 and 5 min of rest, and a log-time fit to 5–60 s
predicted 38 mV too low at 5 min.

Bench B2's K2 rate check measures the IR drop at 1–40C on the coin cell,
staying away from the plating region. Those numbers decide the rate, the windows
and the option.

## Literature on short, high-rate pulses (search of 4 Nov)

There seems to be no published study of 1 s high-rate pulses on hard carbon,
with or without SAXS. The closest work:

- **Operando SAXS on hard carbon.** Pore filling lowers the SAXS intensity,
  because the electron-density contrast between pore walls and pore volume
  drops. This is assigned to the low-voltage plateau, roughly below 0.1–0.15 V.
  - [Stevens and Dahn's intercalation–pore filling model, and an operando
    SAXS/WAXS/Raman three-stage model (adsorption, accumulation, pore
    filling)](https://pubs.rsc.org/en/content/articlehtml/2025/ee/d4ee06029f);
  - [operando SANS separating intercalation and pore
    filling](https://pubs.rsc.org/en/content/articlehtml/2024/ta/d3ta04739c);
  - [nanostructure-resolved sodiation](https://arxiv.org/pdf/2502.12139).

  These studies were slow, at constant current. None reports pore-filling
  kinetics in time. **A sub-second SAXS response to a pulse would be new**, and
  the plateau is where SAXS is most sensitive.
- **Intermittent current interruption (ICI).** The closest established method:
  1 s current interruptions during constant current, with the voltage sampled
  during the pause. The jump in the first ms gives the ohmic resistance, the
  √t slope to 1 s gives diffusion, and the 1 s value includes charge transfer.
  [arXiv 2104.09260](https://arxiv.org/abs/2104.09260);
  [Chalmers, aging use](https://research.chalmers.se/en/publication/527061);
  [Chalmers, modelling](https://research.chalmers.se/en/publication/531576).
  This is the mirror image of our 1 s pulses after rests, and the same analysis
  (IR at about 2 ms, √t over 1 s) applies to our 6 ms data.
- **Half-cell limits at high rate.** [Li et al., Chem. Commun. 2017, "Hard
  carbon anodes of sodium-ion batteries: undervalued rate
  capability"](https://www.doi.org/10.1039/C7CC00301C) used three-electrode
  cells at 1–10C. The Na counter electrode's overpotential, not the hard
  carbon, limits half-cells at high rate. This matters for how we read the
  voltage under load and for the plating limit.
- **Fast charging of hard carbon.** [Li, Vasileiadis et al., Nature Energy
  2024, "Origin of fast charging in hard carbon
  anodes"](https://www.nature.com/articles/s41560-023-01414-5.pdf): plateau
  storage just above the Na plating potential, without plating, at high areal
  capacity and fast charging. Note the author correction.
- **Pulsed charging.** These studies are about cell aging, at lower frequencies
  and full-cell level, not structure:
  - [Schwedland et al., Batteries & Supercaps 2026, pulsed charging of
    NFPP/hard-carbon pouch cells](https://chemistry-europe.onlinelibrary.wiley.com/doi/10.1002/batt.70446):
    the IR drop scales with the pulse amplitude, and pulses reach the cutoff
    sooner;
  - [Nature 2025, "Extending battery lifetime by pulsed
    charging"](https://www.nature.com/articles/s44406-025-00013-5): Li plating
    depends on the pulse duration;
  - [a review of pulsed current](https://vbn.aau.dk/ws/files/335266165/energies_13_02458_v2.pdf).
- **Structure under fast electrochemistry.** These show that high rates create
  non-equilibrium structural states that relax slowly:
  - LiFePO4 by time-resolved operando XRD up to 10C, including Liu et al.,
    Science 2014 ([record](https://bibbase.org/network/publication/liu-strobridge-borkiewicz-wiaderek-chapman-chupas-grey-capturingmetastablestructuresduringhighratecyclingoflifeponanoparticleelectrodes-2014));
  - [operando XRD combined with impedance at ultra-high
    rates](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4569719/).

  They are the closest precedent for looking at structure within seconds of a
  current step.

These come from abstracts and excerpts, not full texts; check the details
before citing. The ICI papers and Li et al. 2017 are the most directly useful
for designing and interpreting the pulses.

## Questions for the material scientists

Answered on 4 Nov: unipolar pulses, the capacity, SAXS frame times, rests of
1 min at zero current, the run end at the voltage limit, both directions, and
short high-rate pulses for the beamtime.

1. **End condition at high rate:** sodiation is decided (see "Ending a
   high-rate pulse run"). Do desodiation runs end the same way, at the relaxed
   upper limit after the rest?
2. **Rate:** the highest rate worth trying (40C?), after the B2 rate check.
3. **Fully sodiated start:** C/10 overnight acceptable, or start earlier or use
   your cycler? A CV hold at 5 mV at the end?
4. **Non-pulsed comparison:** at the pulse rate (CC at nC) or at C/20?
5. How many cells, and which material in which cell.
6. Beamtime days, and what else the beamline records.

## Next steps

- B2 on the coin cell (from 5 Nov): rate check, then 1 s high-rate pulse trains
  both ways.
- After the end-condition decision: implement option B if chosen (a small
  builder change and one run-sheet column).
- After the answers: write the beamtime run sheet and its sessions (pulse
  session 0–0.5 V / 1 mA; reference session 0–0.5 V / 100 µA; sodiation session
  0–3 V), validate with `oe-validate`, and rehearse on the simulator.
