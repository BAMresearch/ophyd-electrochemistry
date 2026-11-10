# Coin-cell exploration series, 6 Nov 2026 (tier 2)

The agent explores the dynamic behaviour of cell `Richard-HC400-rev` on its own,
with sodiation and desodiation pulses. The cell may break; the operator accepts
that. This replaces the per-run confirmation: the operator confirms the cell,
wiring, limits and off mode once, by running `authorize`, and nothing changes
physically until the series ends. This series was the trial; since 6 Nov the
rule is in `AGENTS.md`.

The operator stays in attendance. `bench/explore.py` refuses anything outside
this page. All runs go through it; its row checks are unit-tested.

## Limits

| Item | Limit |
|---|---|
| Cell, wiring | `Richard-HC400-rev`, HC on HI (force and sense), Na on LO, front terminals |
| Voltage window | −3 to +3 V (2460 stops the run); desodiation pulses stop at 2.9 V |
| Current | ≤ 19.2 mA (40C of the assumed 0.48 mAh); raised from 4.8 mA at the operator's offer on 6 Nov, confirmed by a second `authorize` |
| Pulse length | 50 ms to 60 s |
| Rests | 1 s to 10 min |
| Charge | ≤ 60 µAh moved in one run; net within ±40 µAh over the series |
| Time | 2 h for the whole series, from `authorize` |
| Off mode | HIGH-Z |
| Protocols | `rest`, `pulse_train`, `bipolar_pulse`; immediate start; ≤ 63 trigger blocks |

## Start (operator)

K9 and any K10 rows run before this, with the −3 V session.

```sh
qserver environment close
qserver manager stop
qserver/start-k2460-re-manager.sh bench/b2/session-cell-explore.toml
qserver environment open
uv run --locked --extra experiment python bench/explore.py authorize
```

Data go to `~/k2460-data/b2/explore`; set `DATA_DIR` there in the viewer. To end
the series at any time, tell the agent, or use `qserver re pause immediate` and
guide §9: the output goes OFF.

## Stop rules

The agent stops, leaves the instrument as it is and reports on any of:

- a run that ends other than `completed` or `stopped_cutoff`, an instrument
  error, or an uncertain output state. No retry, reset or power cycle;
- a negative voltage at the end of a run;
- a pulse below 20 Ω (short) or a current more than 2 % off its setpoint
  (compliance or open);
- the reference resistance at the end of the 1 s pulse changed by more than a
  factor 2 from the first one in that direction. (The fast resistance is not
  used: K7 to K9 moved it by a factor 4 in normal operation.)
- anything else it cannot explain, including rest voltages that do not fit
  relaxation.

A stop at the 2.9 V or −3 V limit is an expected result, not a stop rule. The
agent does not repeat that row at the same or higher current in that direction.

## Method

- Reference pair between experiments, to separate parameter effects from drift:
  `ref-NNd` then `ref-NNs`, each a `pulse_train` of 3 × 1 s at ±480 µA with 20 s
  rests, compared per direction. (A `bipolar_pulse` with long rests exceeds the
  63 trigger blocks.)
- One parameter at a time: amplitude, pulse length, rest; desodiation first in
  each block (Jonas). Then refine where the behaviour changes.
- `series.jsonl` in the data folder logs each row, per-pulse results and any
  stop. The agent records why it chose each run, and reports with charts.

Results describe this damaged cell and test the method; they are not beamtime
conclusions.
