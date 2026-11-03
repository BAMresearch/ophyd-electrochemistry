# Bench plan B1 — the sequencer through the production backend (tier 1)

Purpose: run the packaged sequencer runtime on the 2460 through
`Keithley2460SequenceBackend`, the code path the beamtime notebook uses, and
answer the open B1 items in `docs/sequencer-design.md` §13. Runner:
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

Check the DB-9 pin for each line, and the ground pin, against the Reference
Manual's digital I/O port table before connecting (not verified in this repo).

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
| C1 | CC to v_high (NEXT), CV taper at I/5 (NEXT), 5 s rest, CC to v_low (STOP) | All three cutoff kinds on a real voltage ramp; stopped_cutoff, OFF |
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
