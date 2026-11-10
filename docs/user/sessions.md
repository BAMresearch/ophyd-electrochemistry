# Sessions: set-up and limits

A session file describes one experimental environment: which instrument, where
the data go, and the operational limits every run must stay within. The
QueueServer loads one session for as long as it runs; every queued row is
validated against it.

## Loading a session is the confirmation

You confirm the set-up and limits **once, for the session**, not for each run.
Starting the RE Manager with a session file and opening its environment is that
confirmation. Before you do, check that the file matches what is connected:

- the cell (name it in the file's header comment), the wiring (front or rear
  terminals, four-wire) and the polarity;
- the voltage window and the current limit;
- the data folder.

Within the session, any row that passes validation runs without further
questions. For a different cell, wiring or window, use a different session file
and restart the RE Manager with it.

## The file

Start from the example and review every value:

```sh
cp qserver/session-k2460.example.toml ~/sessions/hc01-sod.toml
```

```toml
# hc01, HC on HI (force + sense), Na on LO, front terminals, four-wire.
# Sodiation pulses, window -3..3 V.
backend = "k2460"
data_dir = "~/k2460-data/hc01"
resource = "TCPIP0::169.254.113.151::5025::SOCKET"
serial = "04686198"            # refuses any other instrument
terminals = "front"
poll_period_s = 1.0            # how often the laptop reads new data
# start_timeout_s = 600.0      # external START: abort if none arrives in time

[limits]
v_min_abs = -3.0               # absolute voltage window, V
v_max_abs = 3.0
i_max_abs = 0.03               # A; also the current limit of a CV hold
max_duration_s = 172800.0      # per run
max_records = 1000000          # per run (the 2460 buffer holds 1,000,000)
```

| Setting | Meaning |
|---|---|
| `backend` | `k2460`, or `simulator` for the synthetic cell |
| `data_dir` | Where run files go. Use a local disk: live reading needs it |
| `resource`, `serial` | The instrument's address and serial number |
| `terminals` | `front` or `rear` |
| `start_timeout_s` | Optional. An external-start run aborts if no START comes in time |
| `[limits]` | The window, maximum current, and per-run duration and record bounds |

## What the limits do

- **Before a run:** every row is checked against them (`oe-validate`,
  `oe-enqueue`, and again when the run starts). A row that asks for more current,
  a threshold outside the window or a longer run is refused.
- **During a run:** the 2460 compares every reading with the voltage window and
  stops the run with the output OFF on the first reading outside it,
  independently of the row's own thresholds. Its voltage compliance is set
  0.1 V outside the window, so the cutoff acts before compliance does.
- Between readings the instrument does not watch the voltage; only the
  compliance limit applies there.
- The cell's rest voltage must be inside the window before a run.

## One session per window

Use a separate session for each voltage window and current range, for example:

- 0–3 V for the first sodiation from about 2.5 V;
- −3 to 3 V and 30 mA for sodiation pulses whose loaded voltage goes below
  0 V;
- 0–0.5 V and 100 µA for reference cycling with a CV hold (the hold's current
  limit is `i_max_abs`).

A narrow window protects better: reversed leads or a wrong row stop at the first
reading outside it.

## Output-off mode

The 2460 switches on with output-off mode NORMAL, which pulls a connected cell
towards 0 V. Opening the environment sets **HIGH-Z** for both source functions
and reads it back; every run sets and checks it again. Connect a cell only after
the environment is open (see [At the beamtime](beamtime.md)).

## Simulator sessions

`qserver/session-simulator.toml` has `backend = "simulator"` and `sim_speed`
(10 = ten times real time). Use it to try sheets and procedures; its data are
synthetic.
