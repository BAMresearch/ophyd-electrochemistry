# B3 exploration series: characterise AlCe Test Cell 1 (tier 2)

The agent characterises the cell's key parameters on its own for 2 h, with
pulsed and constant-current (interrupted) runs. The operator checks the window
below, starts the session and runs `authorize`; nothing changes physically until
the series ends. All runs go through `bench/explore.py --series b3`, which
refuses anything outside this page (unit-tested in `tests/unit/test_bench_b2.py`).

## What we know (P0, 10–11 Nov)

Hard carbon 1.78 mg against Na. At ±C/5 (142 µA) between 5 mV and 2.5 V:
first sodiation 418 µAh, desodiation 259 µAh, second sodiation 261 µAh. So
about **0.26 mAh reversible** (145 mAh/g) at this rate; this page's C-rates use
1C = 260 µA. Apparent 1 s resistance 1.3–3.6 kΩ; the CV holds at 5 mV stopped
at 1 h with the current still at 3–4 × C/50; the voltage relaxed from 2.5 V to
1.07 V in 30 min. The operator attributes the resistance to an interlayer on the
anode. The cell is now sodiated, at rest since 11 Nov.

## Limits (for the operator to check)

| Item | Limit | Why |
|---|---|---|
| Cell, wiring | AlCe Test Cell 1, front terminals, HC on HI (force and sense), Na on LO, output-off HIGH-Z | |
| Absolute window | −3 to +3 V (session; 2460 stops the run) | As Jonas, 6 Nov. With kΩ in series, most of an under-load excursion is IR across the interlayer, not electrode potential |
| Row cutoffs | each current row stops at +2.9 V (desodiation) and −2.9 V (sodiation) | A clean `stopped_cutoff` before the absolute window |
| Current, pulses < 1 s | ≤ 10.4 mA (40C) | Operator's limit; escalated only while the last row's resistance predicts staying inside ±2.9 V (at 2 kΩ that is about 1.3 mA, 5C) |
| Current, pulses ≥ 1 s | ≤ 1.3 mA (5C) | Agent's choice: long pulses move charge and heat the interlayer |
| Pulse length | 50 ms to 120 s | 120 s for the interrupted CC runs |
| Rests | 1 s to 10 min | |
| Charge | ≤ 80 µAh per run; net over the series between −15 µAh (further sodiation) and +150 µAh (desodiation) | The cell starts full: little room to sodiate further (plating), and it is never desodiated beyond about 60 % |
| Time | 2 h from `authorize` | |
| Protocols | `rest`, `pulse_train`, `bipolar_pulse`; immediate start; ≤ 63 trigger blocks | |

## Start (operator)

```sh
qserver/start-k2460-re-manager.sh --detach bench/b3/session-b3-explore.toml
qserver environment open          # sets HIGH-Z
qserver queue item remove front   # hc-p0, left from 10 Nov; never start it
qserver queue get                 # must be empty
uv run --locked --extra experiment python bench/explore.py --series b3 authorize
```

Data: `~/k2460-data/b3/explore`; set `DATA_DIR` there in the viewer. To end the
series at any time, tell the agent, or `qserver re pause immediate` (output OFF).

## Stop rules

As `plan-b2-explore.md`: a run that ends other than `completed` or
`stopped_cutoff`, an instrument error or uncertain output state (no retry, reset
or power cycle); a negative voltage at the end of a run; a pulse below 20 Ω or
a current more than 2 % off its setpoint; anything the agent cannot explain.
The reference rule now compares a reference only with an earlier one in the
same direction that started within 50 mV (6 Nov compared across voltages). A
row ending on its ±2.9 V cutoff is a result: no repeat at the same or higher
current in that direction.

## Method and order (desodiation first in every block)

| Block | Rows | Parameters | About |
|---|---|---|---|
| A, sodiated | `ocv-01`: 5 min rest, 6 ms for the first second | OCV after 2 days, drift | 6 min |
| | `ref-01d`, `ref-01s`: 3 × 1 s at ±1C, 20 s rests | Reference for drift | 4 min |
| | `step-01d`: one 5 s pulse at +1C, 6 ms for 1 s, then 50 ms; 60 s rest | R(t) from 6 ms to 5 s: series R at the first sample, interlayer R and its time constant, √t (diffusion) slope; recovery in the rest | 3 min |
| | `amp-NNd-xC`: 3 × 0.5 s at +2, 5, 10, 20, 40C, 10 s rests | Overpotential against current: linear (ohmic) or log (interface, Butler–Volmer/Tafel) | 12 min |
| B, desodiate | `ici-d`: 15 × 60 s at +1C with 10 s interruptions (6 ms for 1 s) | ICI: R and diffusion slope every 4.3 µAh over 65 µAh (25 %); quasi-OCV from the rest ends | 18 min |
| C, partly desodiated | `ref-02d/s`, `step-02d/s`, `amp-NNd/s-xC` in both directions | Same parameters at a second state of charge; sodiation pulses only here, with headroom before plating | 30 min |
| D, back | `ici-s`: 15 × 60 s at −1C | ICI on sodiation; hysteresis against `ici-d`; returns the cell near its start | 18 min |
| E | `ref-03d/s`, `ocv-02` | Drift over the series | 10 min |

Amplitude rows stop escalating at the first row that ends on a cutoff or whose
predicted voltage leaves ±2.9 V. If the 1 s resistance falls under pulsing, as
on 6 Nov, the references show it and the agent waits out the recovery before
comparing states. Rows are adjusted to the results; the report says why.

Analysis, per pulse: R(t) = (V(t) − V_pre)/I at 6 ms, 0.1 s, 0.5 s and 1 s;
the 6 ms value is the series (ohmic) part, the rise to 1 s the interlayer and
charge transfer, and V against √t the diffusion part (ICI, Lacey 2017; Chien et
al. 2023, equivalent to GITT, Weppner and Huggins 1977, at short times). Report:
a PDF in `~/k2460-data/b3/explore`, plus the evidence file.

## Caveats

- Two electrodes: every number includes the Na counter electrode, whose
  interphase in carbonate electrolytes has a high and variable impedance
  (Iermakova et al. 2015). The interlayer cannot be assigned to the HC side
  from these data alone; a reference electrode would be needed (Ledwoch et al.).
- Charge transfer on hard carbon changes strongly above about 0.1 V (Aniskevich
  et al.), so the two states (A, C) are expected to differ.
- The 2460 cuts off on samples: a 6 ms first sample bounds the overshoot.

## References

- W. Weppner, R. A. Huggins, J. Electrochem. Soc. 124 (1977) 1569 (GITT).
- M. J. Lacey, ChemElectroChem 4 (2017) 1997 (intermittent current interruption).
- Y.-C. Chien et al., Nat. Commun. 14 (2023) 2289, [arXiv:2104.09260](https://arxiv.org/abs/2104.09260) (ICI against GITT).
- O. Iermakova et al., J. Electrochem. Soc. 162 (2015) A7060 (Na metal interphase impedance).
- D. Ledwoch et al., [Na-ion transport parameters in hard carbon electrodes](https://research.birmingham.ac.uk/en/publications/determining-the-electrochemical-transport-parameters-of-sodium-io) (3-electrode EIS/GITT).
- Y. Aniskevich et al., [EIS, GITT and Raman on hard carbon](https://sisu.ut.ee/wp-content/uploads/sites/638/aniskevich_yauhen.pdf).
- 6 Nov series on Richard-HC400-rev (`plan-b2-explore.md`): overpotential about linear in log current; resistance falls under pulsing and recovers over 10–20 min.
