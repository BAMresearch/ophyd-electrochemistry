# Bench plan B2 — QueueServer and notebook on the 2460: resistor, then a coin cell

For Thu 5 Nov 2026, continuing on the next bench day and over the weekend.
There is no RC dummy cell yet. B1's C1/C2 are deferred, and a hard-carbon / Na
coin cell takes the place of B2's "sacrificial cell". Operator steps follow
`docs/operator-guide.md`. This tests the guide as well as the code.

Prepared offline:
- `bench/b2_sheets.py` writes and validates the run sheets.
- `bench/b2/*.toml` are the session files.
- `bench/b2_monitor.py` watches CPU, memory and the viewer's refresh cost, and
  writes the evidence.
- The cell sheets were rehearsed on the simulator through a real RE Manager
  (`docs/evidence/k2460-2026-11-04-b2-sim*.json`). The simulator's cell is
  generic, not hard carbon, so only the flow, the load and the record counts
  carry over, not the voltages.

Evidence: one file per load, `docs/evidence/k2460-<date>-b2-{resistor,cell}.json`,
left uncommitted for Claude to review.

## Order of the day

| # | What | Load | Tier |
|---|---|---|---|
| 0 | Rewire the digital I/O (`plan-b1.md` §0): READY 1, BUSY 2, MARKER 4, START 6 | — | — |
| 1 | B1 resistor matrix R1–R11 (`plan-b1.md` §1), about 10 min | 100 Ω | 1 |
| 2 | Q1–Q10 below: QueueServer and notebook on hardware | 100 Ω | 1 |
| 3 | K0–K2 below: first contact with the coin cell | cell | 2 |
| 4 | K3 overnight: full sodiation to 5 mV ("a day in advance", as at the beamtime), monitored | cell | 2 |
| later | K4–K6 on the next bench day (the beamtime day in small); K8 over the weekend | cell | 2 |

Gate for step 2: R1, R2, R5, R7, R10 and R11 pass. Gate for step 3: Q2–Q8 pass.
If R3 changes `SequencerProfile.reading_s` for the 7 V range, Claude updates the
profile, and the sheets are written and validated again before the cell runs
(the wide cell session measures on the 7 V range).

## Preparation (morning)

The sheets are written once, then never edited. A queued row refuses to start
if its sheet changed.

```sh
uv run --locked --extra experiment python bench/b2_sheets.py \
  --capacity-mah <cell capacity> --sample-id <cell ID>
```

This writes one sheet per session (`b2-resistor.xlsx`, `b2-cell-wide.xlsx`,
`b2-cell.xlsx`, `b2-cell-cycle.xlsx` in `~/k2460-data/b2/`) and prints one
`ok` line per row. Currents scale with the capacity: 1C = capacity / 1 h
(default 0.6 mAh → 600 µA; C/20 = 30 µA). For a Kuranode cell use about 0.53.

Note the coin cell's material, its history and its last known state (shipped
desodiated at ~2.5 V?) in the evidence tag or the sample ID.

Terminals:
- **1:** redis.
- **2:** RE Manager. `OE_SESSION` changes per section.
- **3:** `qserver` and `oe-*` commands. Run them from `~/k2460-data/b2`.
- **4:** monitor (`watch`), started with each section:

```sh
uv run --locked --extra experiment python bench/b2_monitor.py watch \
  ~/k2460-data/b2/<resistor|cell> --csv ~/k2460-data/b2/<resistor|cell>/monitor.csv
```

**Viewer:** `notebooks/k2460_view.ipynb` copied to `~/k2460-data/b2/viewer.ipynb`,
with `DATA_DIR` set to the section's data folder.

## 2. Resistor through QueueServer and notebook (`--load resistor`, tier 1)

Confirm once:

- [ ] 100 Ω / 1 W on the **front** terminals, 4-wire; **no cell**
- [ ] Output OFF; limits from `bench/b2/session-resistor.toml`: ±0.8 V, 5 mA,
      2 h, data in `~/k2460-data/b2/resistor`
- [ ] Generator for Q6 as in B1 R8 (0 → 4.5 V square, 0.01 Hz, line 6 via 1 kΩ);
      scope as in B1

This authorizes Q1–Q10 on the resistor. Each row is still confirmed by typing
its ID in `oe-enqueue`.

| Test | Row / action | Pass when |
|---|---|---|
| Q1 | Guide §5–6 with `OE_SESSION=bench/b2/session-resistor.toml`: `oe-diagnose` first, then `oe-validate b2-resistor.xlsx`, then `environment open` | Worker connects: serial, runtime installed, output OFF; manager idle |
| Q2 | `b2r-rest` | `completed`, about 100 records, V ≈ 0, \|I\| < 1 µA |
| Q3 | `b2r-pulse` (20 × ±450 µA, 1 s, 6 ms fast window); viewer Follow open | `completed`; V = ±45 mV (R = 100 Ω ± 1 %); 6 ms spacing; Follow redraws live and ends on its own |
| Q4 | `b2r-soc` (CC −450 µA to −30 mV) | Cutoff event at the first sample, then the rest; `completed` |
| Q5 | `b2r-cycle` (CC → CV → CC with in-model I↔V switches) | Every cutoff and the CV taper fire at once; `completed` within a minute. If it is still running after 2 min: abort (§9) and note it |
| Q6 | `b2r-pulse-ext` (external START) | READY high (scope, line 1) until the generator's edge, then BUSY; `completed` |
| Q7 | `b2r-abort` (20 min); about 1 min in, follow guide §9 to the letter | File `aborted`; output OFF; after `recover_sequencer` the queue continues (enqueue `b2r-rest` behind it) |
| Q8 | `b2r-crash` (2 min); about 30 s in, press **Ctrl-\\** in terminal 2 (kills the whole RE Manager group) | No `start-re-manager` process left (`pgrep -fl start-re-manager`). The 2460 ends the run on its own with output OFF (BUSY low, ≤ 2 min; before that a prepare refuses with "idle model and output OFF"). Then restart, and `b2r-rest` **fails** with unread records. Guide §10 `archive_retained` saves them. `b2r-rest` then completes |
| Q9 | `b2r-soak` (1 h of pulses at 6 ms, about 150k records); viewer Follow open; monitor running | `completed` with all records; worker RSS flat (growth < 50 MB); CPU stays below one core; viewer refresh time recorded |
| Q10 | With the QueueServer stopped: `oe-new-run ~/k2460-data/b2/b2-resistor.xlsx b2r-pulse ~/k2460-data/b2/nb --v-min -0.8 --v-max 0.8 --i-max 0.005 --max-duration-s 7200 --max-records 1000000`, set `BACKEND = "k2460"`, run all | `completed`; live plot; HDF5 beside the saved notebook |

Q8 can leave the file `incomplete`, because its writer was killed. That is
expected. `archive_retained` holds the records the 2460 kept.

## 3. Coin cell (`--load cell`, tier 2)

**Every cell run is confirmed by you,** by typing its measurement ID in
`oe-enqueue` after checking:
- the cell ID;
- the wiring: **HC on HI (force and sense), Na on LO**;
- the voltage window;
- the current limit;
- the output-off mode: **HIGH-Z**.

`oe-diagnose` shows HIGH-Z only after the worker has prepared once. The runtime
sets HIGH-Z for both functions and reads it back on every prepare. The output
stays OFF until the run arms.

**Window rule (K-rule).** Before any cell run, the cell's last measured rest
voltage must be above 0 V and at least 0.05 V below the session window's upper
edge:
- `session-cell-wide.toml`: 0–3.0 V;
- `session-cell.toml` and `session-cell-cycle.toml`: 0–0.5 V.

Why: the voltage compliance is the window's upper edge plus 0.1 V. A current
source facing a cell above its compliance would draw current from the cell to
pull it down, until the first sample (within milliseconds) stops the run on the
window. *Not verified on the 2460*, so we avoid that situation. First contact
therefore uses the wide session.

**Protocol (agreed 4 Nov with the material scientists):**
- the cell holds about 0.6 mAh (1.5 mg × 400 mAh/g): 1C = 600 µA, C/20 = 30 µA;
- start **fully sodiated**;
- **unipolar** pulses, with no change of direction between pulses;
- 1C pulses of 10 s to 1 min;
- CC at C/20.

`b2_sheets.py` defaults to 0.6 mAh. Pass `--capacity-mah` if this cell differs.
Negative current = sodiation. Sheets accept `sodiation` and `desodiation`
wherever `discharge` and `charge` are accepted.

Confirm once for the section (plus per run, as above):

- [ ] RE Manager restarted with `OE_SESSION=bench/b2/session-cell-wide.toml`
      (data in `~/k2460-data/b2/cell`)
- [ ] Resistor removed; output OFF; cell in its holder, 4-wire, polarity checked

| Test | Row | Session | Pass when / decides |
|---|---|---|---|
| K0 | Handheld DMM on the cell, 2460 output OFF (optional, sources nothing) | — | OCV and its sign before anything is sourced |
| K1 | `k1-ocv`: 2 min rest at 0 A | wide | OCV between 0.05 and 2.9 V, drift below 1 mV/min. Reversed leads read negative: the 0 V window stops at the first sample |
| K2 | `k2-pulse-check`: 5 sodiation pulses at 1C, 1 s each with 4 s rests (no plating risk near 2.5 V) | wide | Voltage falls during the pulses: apparent R > 0 (the polarity is as wired) and below 1 kΩ (the cell is alive). Also: the first rest reading ≈ K1's last reading (HIGH-Z off state, B1's C2) |
| K3 | `k3-sodiate-5mV`: CC −C/10 to 5 mV (whole capacity in about 10 h; at most 14 h), 30 min rest, 1 s sampling; Follow and monitor overnight | wide | `completed`; the cutoff event at 5 mV, no reading below 0 V; rest voltage recorded; monitor flat overnight |

K3 uses C/10, not C/20: at C/20 a full sodiation takes about 20 h and does not
fit a night. The same applies to the day-before sodiation at the beamtime (see
`docs/beamtime-concept.md`).

Branches after K1:
- **OCV ≥ 0.25 V:** K2, then K3 tonight.
- **OCV < 0.25 V (partly sodiated):** K2, then K3 (it ends sooner).
- **OCV < 0.05 V, OCV > 2.9 V, a drift over 1 mV/min, or K2's R > 1 kΩ:** stop
  the cell section and report.

### Next bench day: the beamtime day in small (`session-cell.toml`, 0–0.5 V)

Restart the RE Manager with `OE_SESSION=bench/b2/session-cell.toml` once K3
has ended and its rest voltage satisfies the K-rule. Each pulse train passes
1/6 of the capacity (10 min of 1C) in about 1 h. Sampling is 6 ms for the first
0.5 s of every step, then 0.1 s, which matches 10–100 ms SAXS frames.

| Test | Row | Pass when / shows |
|---|---|---|
| K4 | `k4-desod-pulse-10s`: 60 desodiation pulses at 1C, 10 s each with 50 s rests; STOP at 0.2 V | `completed`; the voltage response and relaxation per pulse |
| K5 | `k5-desod-pulse-60s`: 10 desodiation pulses at 1C, 60 s each with 300 s rests (Jonas's 1 min / 5 min); STOP at 0.2 V | `completed`; the same 1/6 of the capacity with longer pulses |
| K6 | `k6-sod-pulse-10s`: 60 sodiation pulses at 1C, 10 s / 50 s, back toward the fully sodiated state; STOP at 5 mV under load | **Plating risk: decide on the day.** `completed` or `stopped_cutoff`; compare with K4 at the same SOC |
| K8 | `k8-reference-cycle` (weekend), from `b2-cell-cycle.xlsx` with `OE_SESSION=bench/b2/session-cell-cycle.toml` (0–0.5 V, **100 µA**): CC desodiation C/20 to 0.2 V, rest, CC sodiation C/20 to 5 mV, CV at 5 mV to C/100 (≤ 1 h), rest. At most 20 h per CC step, 42 h in total | `completed`; non-pulsed reference; ends fully sodiated for the next pulse day; monitor flat |

K4–K6 stand in for the beamtime protocol, which is still being refined. The
STOP thresholds are tested on the voltage **under load**. A desodiation pulse
reads higher than the relaxed voltage, and a sodiation pulse lower, so both
trains stop on the safe side.

**Why K8 has its own session.** A CV hold's current limit is the session's
`i_max_abs`. A CC step that ends on its time limit instead of its voltage
cutoff (for example, because the capacity is larger than assumed) still goes on
to the CV hold. At 5 mV that hold would sodiate at up to the limit:
- 1 mA (about 2C) in `session-cell.toml`;
- 100 µA in `session-cell-cycle.toml`.

The 100 µA session also measures C/20 on the 100 µA range.

## End of each section

Stop the monitor (Ctrl-C in terminal 4), then:

```sh
uv run --locked --extra experiment python bench/b2_monitor.py evidence --load resistor \
  ~/k2460-data/b2/resistor ~/k2460-data/b2/nb --csv ~/k2460-data/b2/resistor/monitor.csv
uv run --locked --extra experiment python bench/b2_monitor.py evidence --load cell \
  ~/k2460-data/b2/cell --csv ~/k2460-data/b2/cell/monitor.csv
```

The evidence lists, per run file:
- the outcome, records and duration;
- the sample spacing per interval;
- the first and last rest voltage;
- the voltage and current ranges;
- the apparent resistance (positive = polarity as wired);
- the non-step events.

It also lists the CPU and memory of every RE Manager process, and the viewer's
refresh time.

## If something goes wrong

- Guide §9 (stop, recover) and §10 (archive after a crash) apply unchanged.
- On a cell, after any unexpected stop:
  - do not rerun anything until the output is confirmed OFF (front panel, or
    `oe-diagnose` with the QueueServer stopped);
  - re-establish the cell's rest voltage with `k1-ocv` in the wide session.
- Never retry a row whose state is unclear. Report the message and the evidence
  file.
