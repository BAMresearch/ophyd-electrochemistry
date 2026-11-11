# Bench plan B3: a healthy coin cell (tier 2)

A healthy hard-carbon/Na coin cell, delivered desodiated at about 2.5 V. First
P0 overnight with the 2460 running on its own: C/5 sodiation to 5 mV, C/5
desodiation to 2.5 V, C/5 sodiation to 5 mV again (10 Nov); then the
beamtime protocol in small (P2d, P2s), which is also the draft beamtime sheet.
Protocol background: `docs/beamtime-concept.md`. Sheets: `bench/b3_sheets.py`.

## Before: write and check the sheets (no instrument)

```sh
uv run --locked --extra experiment python bench/b3_sheets.py --capacity-mah 0.712 \
    --sample-id 'AlCe Test Cell 1'
```

This writes `~/k2460-data/b3/b3-p0.xlsx` and `b3-pulse.xlsx` and validates every
row against its session; expect only `ok` lines. `--capacity-mah` is the
cell's active mass times 400 mAh/g (AlCe Test Cell 1: 1.78 mg, 0.712 mAh, so
C/5 = 142 µA). The cell is named in the header of `bench/b3/session-b3-p0.toml`
(fill in `session-b3-pulse.toml` for the pulse day).

**Confirmation (tier 2):** check the session header against the cell and the
wiring, and the limits below. Starting the RE Manager with that session is the
confirmation for every run in it.

| Session | Window | Current | For |
|---|---|---|---|
| `bench/b3/session-b3-p0.toml` | 0 to 3 V, 40 h | 200 µA | `hc-ocv`, `hc-p0` |
| `bench/b3/session-b3-pulse.toml` | −3 to 3 V | 30 mA | `p-ocv`, `p2d-desod`, `p2s-sod-1min`, `p2s-sod-5min` |

## Night 1: P0, C/5 sodiation, desodiation, sodiation

| Row | What | Bound | Expect |
|---|---|---|---|
| `hc-ocv` | 5 min rest; first second at 6 ms | 5 min | OCV about 2.5 V and steady; no dip at output ON (1 µA-range start). A negative OCV (reversed leads) stops at the first reading |
| `hc-p0` | One `cycling` row, 2 cycles starting with desodiation, ±C/5 (142 µA), 1 s sampling: [+I to 2.5 V, 30 min rest] [−I to 5 mV, CV 5 mV to \|I\| < C/50 (14 µA, ≤ 1 h), 30 min rest] × 2 | 36 h (CC steps ≤ 8 h) | The first +I half ends at the first reading if the cell is at or above 2.5 V, so its rest is the OCV; then sodiation about 5–6 h (first cycle includes the irreversible loss), desodiation about 5 h, sodiation about 5 h. About 17–19 h in all; ends sodiated at rest, output OFF; outcome `completed` |

Procedure:

1. Power on the 2460; `oe-diagnose` (serial 04686198, TSP, output OFF).
2. Start the RE Manager detached, in its own process group, so it survives
   the editor or terminal closing and can be killed as a whole (step 6):

   ```sh
   qserver/start-k2460-re-manager.sh --detach bench/b3/session-b3-p0.toml
   qserver environment open      # sets HIGH-Z; then connect the cell
   ```

   It logs to `~/k2460-data/re-manager.log`.

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
   voltage, kill the RE Manager's whole process group (manager, API server,
   worker) without cleanup, as in B2 Q8:

   ```sh
   qserver/kill-re-manager.sh          # must end with "nothing left"
   ```

   If it prints STILL RUNNING, do not disconnect; look first. (A plain
   `pkill -f bin/start-re-manager` misses the spawned worker on macOS: Codex,
   brief 12. The script was tested on the simulator session, 10 Nov.)

   No abort reaches the 2460, which keeps running with the output ON (front
   panel). The run file stays `incomplete` with the readings so far. Then
   unplug the Ethernet. Do not let a running RE Manager sleep with the laptop:
   when it wakes, its read fails and its error handling aborts the run if the
   2460 is still sourcing.

The 2460 runs every step itself (each CC step ends at its voltage or after
8 h), ends with the 30 min rest after the second sodiation and switches the
output OFF itself; the readings stay in its buffer (shown on K3, 5 Nov).

**Morning**, laptop back on the cable, RE Manager still stopped:

```sh
oe-diagnose --resource 'TCPIP0::169.254.113.151::5025::SOCKET' --backend '@py' --require-language TSP
oe-archive-retained --session bench/b3/session-b3-p0.toml
```

`oe-diagnose` reads the output state only: ON means `hc-p0` is still running
(wait; expect about 17–19 h, at most 36 h). `oe-archive-retained` refuses while the
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

Desodiation end (question 1), rate (2), and how the relaxed end treats the rest
history (9 Nov rehearsal). P0 (question 3) is decided: C/5 with the CV hold.
