# Bench plan B3: a healthy coin cell (tier 2)

A healthy hard-carbon/Na coin cell, delivered desodiated at about 2.5 V. First
P0, the overnight sodiation to 5 mV with the 2460 running on its own; then the
beamtime protocol in small (P2d, P2s), which is also the draft beamtime sheet.
Protocol background: `docs/beamtime-concept.md`. Sheets: `bench/b3_sheets.py`.

## Before: write and check the sheets (no instrument)

```sh
uv run --locked python bench/b3_sheets.py --capacity-mah 0.6 --sample-id <CELL-ID>
```

This writes `~/k2460-data/b3/b3-p0.xlsx` and `b3-pulse.xlsx` and validates every
row against its session; expect only `ok` lines. Set `--capacity-mah` from the
cell's material (0.6 mAh BAM core-shell, 0.53 mAh Kuranode). Fill in the cell ID
in the header of `bench/b3/session-b3-p0.toml` (and `session-b3-pulse.toml`
for the pulse day).

**Confirmation (tier 2):** check the session header against the cell and the
wiring, and the limits below. Starting the RE Manager with that session is the
confirmation for every run in it.

| Session | Window | Current | For |
|---|---|---|---|
| `bench/b3/session-b3-p0.toml` | 0 to 3 V | 100 µA | `hc-ocv`, `hc-p0` (or `hc-p0-cv`) |
| `bench/b3/session-b3-pulse.toml` | −3 to 3 V | 30 mA | `p-ocv`, `p2d-desod`, `p2s-sod-1min`, `p2s-sod-5min` |

## Night 1: P0, overnight sodiation

| Row | What | Bound | Expect |
|---|---|---|---|
| `hc-ocv` | 5 min rest; first second at 6 ms | 5 min | OCV about 2.5 V and steady; no dip at output ON (1 µA-range start). A negative OCV (reversed leads) stops at the first reading |
| `hc-p0` | CC −C/10 (−60 µA for 0.6 mAh) to 5 mV, then 30 min rest, 1 s sampling | 14.5 h | 5 mV after about 10 h for the full capacity; a `cutoff` event ends the CC step, the rest follows; outcome `completed` |
| `hc-p0-cv` | Instead of `hc-p0`: CC to 5 mV, CV hold at 5 mV until \|I\| < C/50 (at most 2 h), rest | 16.5 h | Only if Jonas wants the CV hold (open question 3) |

Procedure:

1. Power on the 2460; `oe-diagnose` (serial 04686198, TSP, output OFF).
2. Start the RE Manager detached, so it survives the editor or terminal
   closing:

   ```sh
   nohup qserver/start-k2460-re-manager.sh bench/b3/session-b3-p0.toml \
     > ~/k2460-data/b3/re-manager.log 2>&1 &
   qserver environment open      # sets HIGH-Z; then connect the cell
   ```

3. Connect the cell: front terminals, four-wire, **HC on HI, Na on LO**.
4. Queue and start:

   ```sh
   oe-enqueue ~/k2460-data/b3/b3-p0.xlsx hc-ocv hc-p0 --session bench/b3/session-b3-p0.toml
   qserver queue start
   ```

5. Check `hc-ocv` in the viewer; if the OCV is not about 2.5 V, stop the queue
   (`qserver queue stop`) and look before `hc-p0` starts. Queue nothing after
   `hc-p0`.
6. **Detach before taking the laptop away** (the 2460 runs `hc-p0` alone).
   Once `hc-p0` is running and the viewer shows the current and a falling
   voltage, kill the RE Manager and its worker without cleanup, as in B2 Q8:

   ```sh
   pkill -KILL -f bin/start-re-manager
   pgrep -fl start-re-manager          # must print nothing
   ```

   No abort reaches the 2460, which keeps running with the output ON (front
   panel). The run file stays `incomplete` with the readings so far. Then
   unplug the Ethernet. Do not let a running RE Manager sleep with the laptop:
   when it wakes, its read fails and its error handling aborts the run if the
   2460 is still sourcing.

The 2460 ends the CC step at 5 mV (or after 14 h), rests 30 min and switches
the output OFF itself; the readings stay in its buffer (shown on K3, 5 Nov).

**Morning**, laptop back on the cable, RE Manager still stopped:

```sh
oe-diagnose --resource 'TCPIP0::169.254.113.151::5025::SOCKET' --backend '@py' --require-language TSP
oe-archive-retained --session bench/b3/session-b3-p0.toml
```

`oe-diagnose` reads the output state only: ON means `hc-p0` is still running
(wait, at most 14.5 h after its start). `oe-archive-retained` refuses while the
run is active; once it has ended it writes `~/k2460-data/b3/retained-<UTC>.npz`
with every reading of `hc-p0` (raw fields: time, voltage, source current,
status) and releases the buffer. Then start the RE Manager with
`session-b3-pulse.toml` for day 2; if `qserver queue get` shows `hc-p0` back at
the front of the queue, remove it (`qserver queue item remove front`), never
start it again. Leave the cell connected (output OFF, HIGH-Z); disconnect it
before anything could power-cycle the 2460.

## Day 2: pulses (draft beamtime protocol)

In `session-b3-pulse.toml`, after P0. Each row ends on its relaxed limit
(`rest_end_v`, checked in the last second of each rest); the repeats are only a
bound (1.5× the capacity).

| Row | What | Relaxed end |
|---|---|---|
| `p-ocv` | 5 min rest | – (must read near 5 mV after P0, positive) |
| `p2d-desod` | +10C 1 s pulses, 60 s rests | above 0.2 V (`--desod-end-v`; open question 1) |
| `p2s-sod-1min` | −10C 1 s pulses, 60 s rests | below 0.05 V (`--sod-early-v`) |
| `p2s-sod-5min` | −10C 1 s pulses, 5 min rests | below 5 mV (decided 6 Nov) |

The rate (`--rate-c`, 5–40C) waits for open question 2; regenerate the sheets
with the agreed values. `--external` makes the pulse rows wait for the START
input (dress rehearsal with the beamline trigger). The sodiation rows start from
where `p2d-desod` ends (desodiated to the relaxed 0.2 V) and take the cell back
to 5 mV, so the cell ends the day sodiated.

## Open for Jonas

Desodiation end (question 1), rate (2), C/10 and the CV hold for P0 (3), and how
the relaxed end treats the rest history (9 Nov rehearsal).
