# Bench plan B1 — the sequencer through the production backend (tier 1)

Purpose: run the packaged sequencer runtime on the 2460 through
`Keithley2460SequenceBackend`, the code path the beamtime notebook uses, and
answer the open B1 items in `docs/technical/sequencer.md` §13 (then `docs/sequencer-design.md`). Runner:
`bench/b1_sequencer.py`. One evidence file per load:
`docs/evidence/k2460-<date>-b1-<load>.json`, written after every test.

Tier 1 (passive loads only). Confirming the checklist below once authorizes the
whole matrix for that load: the runner installs the runtime, prepares, arms,
runs, aborts, archives and acknowledges proof records without asking per run.
It stops at the first unexpected error and reads back output OFF first.

## 0. Before connecting anything: rewire the digital I/O

Decided 3 Nov; S1/S2 used other lines.

| Line | Direction | Signal | Change since S1/S2 |
|---|---|---|---|
| 1 | output | READY (high while armed, waiting for START) | scope probe for READY (was markers) |
| 2 | output | BUSY (START to output OFF) | unchanged |
| 4 | output | step MARKER | scope marker probe moves here from line 1 |
| 6 | input | START | generator + 1 kΩ series resistor move here from line 3 |
| 3, 5 | — | unused | |

DB-9 pins (2460 Reference Manual): lines 1–4 on pins 1–4, line 5 on pin 6,
**line 6 on pin 8**, ground pin 9, +5 V supply on pin 7 (never connect a
signal there), Vext pin 5. The lines are open-drain with pull-ups, so an idle
input reads about 5 V.

## 1. Resistor matrix (`--load resistor`)

Confirm once:

- [ ] K2460 serial `04686198` at `169.254.113.151:5025`, TSP, nobody else connected
- [ ] 100 Ω / 1 W resistor, **front** terminals, 4-wire; **no cell**
- [ ] Output OFF on the front panel; limits ±0.8 V (±2.5 V in R3), 5 mA
- [ ] Scope: CH1 MARKER (line 4), CH2 BUSY (line 2), CH3 READY (line 1),
      optionally CH4 across the resistor
- [ ] Generator (for R8): high-Z output, square 0 → 4.5 V (4.5 Vpp, 2.3 V
      offset), 0.01 Hz, into line 6 through 1 kΩ
- [ ] The instrument was power-cycled today or not: either is fine (the runner
      installs the runtime; power-off clears it)

```sh
uv run --locked --extra visa --extra experiment python bench/b1_sequencer.py \
  --load resistor --i-confirm-resistor
```

About 10 minutes (R9 alone is 5). `--only R2,R8` reruns single tests; add
`--tag rerun1` when an evidence file of the day exists. HDF5 files and archives
go to `~/k2460-data/b1/`.

| Test | What | Decides / checks |
|---|---|---|
| R1 | Upload three programs without arming | Upload logs no error or warning events (else every prepare fails); clock mapping; `oe_seq_status` parses |
| R2 | S1 layout (13 steps, 6.25 ms) | Completed, all 208 records, R = 100 Ω ± 1 %, step durations. **Scope:** 13 marker pulses on line 4, BUSY on line 2 |
| R3 | 3 s rest at 6 ms on the 2 V and 7 V measure ranges | Reading time on 7 V (the timing table is from 2 V); a difference > 0.05 ms changes `SequencerProfile.reading_s` |
| R4 | Cutoff NEXT on the first sample of a step inside a 3× repeat block | Counter left part-way is reset; replay agrees with the logged END |
| R5 | Cutoff STOP | `stopped_cutoff`, stop path, output OFF |
| R6 | Reading outside the absolute window with NEXT | Absolute window always stops (`abs_window`) |
| R7 | Rest → CV 0.3 V → CV 0.2 V → CC −4.9 mA → rest | In-model I↔V switches with OUTPUT ON; CV readback 0.3 V / 3 mA; taper fires on step 2 only; event-log warnings recorded |
| R8 | S1 layout with external START | `WAITING_START` seen (block 2 while waiting). **Scope:** READY high until the START edge, then BUSY |
| R9 | 5 min of 2 s bipolar pulses at 6 ms via Flyer + RunEngine + HDF5 | All ~49,800 records in the file; run length vs nominal; live lag; host CPU |
| R10 | Abort 5 s into a 60 s run | ABORTED with OFF, records drained, abort event, recover to IDLE |
| R11 | Host process killed 0 s into a 20 s run | Model ends OFF by itself; next prepare refuses; `archive_retained`; prepare after |
| R12 | `rest_end_v` −0.05 V: −1 mA 0.5 s pulses (−0.10 V under load), 3 s rests (0 V) × 3 | Completes: a voltage beyond the threshold under load does not stop the run |
| R13 | `rest_end_v` +0.005 V, same pulses | `stopped_cutoff` on `v_min` in the first 1 s rest check, no records after it, OFF |
| R14 | As R12 with 2 s pulses: exactly 63 trigger blocks | The firmware accepts 63 (64 failed with event 1130); completes |
| D1 | Output-ON dip, 1 mA range (0.5 mA, 50 ms pulse): 2 s rest, 2 s rest, pulse, 2 s rest at 6 ms; runs a, b, a, b | (a) present start vs (b) first rest on 1 µA (`start_on_lowest_range`): first-sample dip at output ON and at the range change; output stays ON through the range change |
| D2 | As D1 on the 10 mA range (4.9 mA pulse) | as D1 |
| D3 | As D1 on the 100 mA range (11 mA, 1.13 V pulse; limits ±1.5 V, 15 mA) | as D1; **outside the ±0.8 V / 5 mA resistor limits: confirm separately** |
| DC1 | **Coin cell, tier 2** (`--load cell --i-confirm-cell`, window −3…3 V, 30 mA, HIGH-Z): D1 with one 6 ms −0.5 mA sample | The dip needs a load that holds a voltage before output ON (the resistor sits at 0 V); same comparison of (a) and (b) |
| DC2 | As DC1 on the 10 mA range (one 6 ms −1.1 mA sample) | as DC1 |
| DC3 | As DC1 on the 100 mA range (one 6 ms −10.1 mA sample, about 0.02 µAh) | as DC1 |

## 2. RC dummy cell (`--load rc`), after R1–R11

Needs from you: the dummy's topology and values (series R, C, any parallel
leakage R). Choose `--rc-current` so that charging from `--rc-v-low` to
`--rc-v-high` takes 10–60 s: t ≈ C·ΔV / I (e.g. 0.1 F, 0.8 V, 2 mA → 40 s).
Precharge is not needed: the first CC step charges from wherever it is.

Confirm once:

- [ ] RC dummy on the front terminals, 4-wire; values noted in the evidence tag
- [ ] Window `--rc-v-low` … `--rc-v-high` (limits ±0.3 V beyond), current
      `--rc-current` (limit 2× that, at least 5 mA); output OFF

```sh
uv run --locked --extra visa --extra experiment python bench/b1_sequencer.py \
  --load rc --i-confirm-rc --rc-current 0.002 --rc-v-low 0.2 --rc-v-high 1.0 --tag rc-0p1F
```

| Test | What | Decides / checks |
|---|---|---|
| C1 | CC to v_high (NEXT), CV taper at I/5 (NEXT), 5 s rest, CC to v_low (STOP); 50 ms sampling, no fast window (63-block limit) | All three cutoff kinds on a real voltage ramp; stopped_cutoff, OFF |
| C2 | 2 s rest right after C1 | HIGH-Z off state: first reading ≈ C1's last (within 50 mV, minus any leakage through a parallel R) |

## If something goes wrong

Ctrl-C is safe: the runner tries `oe_seq_off()` and records whether OFF was
confirmed; the trigger model is finite and ends with the output OFF on its own.
If R1 stops because the 2460 holds unread records of an earlier session, rerun
with `--archive-retained` (saves them to `~/k2460-data/b1/` first). Do not rerun
a test after any other unexpected stop; report the printed message and
the evidence file. To inspect without changing anything:

```sh
uv run --locked --extra visa oe-diagnose \
  --resource 'TCPIP0::169.254.113.151::5025::SOCKET' --backend '@py' --require-language TSP
```

Leave the evidence files uncommitted; Claude reviews them and records the
results in `docs/validation.md`.
