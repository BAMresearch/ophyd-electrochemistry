# Operator guide: running the experiment from Excel and the command line

Step by step, from an Excel run sheet to HDF5 files, with the QueueServer
owning the Keithley 2460. **Status (3 Nov 2026):** the whole flow runs on the
simulator and against a real RE Manager; the 2460 hardware path still has to
pass bench validation (B1, B2) before it is used on cells.

All commands run in the repository folder with its environment active:

```sh
cd ~/Code/ophyd-electrochemistry
source .venv/bin/activate
```

## 1. Install (once per laptop)

```sh
uv sync --locked --extra experiment --extra visa --extra queueserver
brew install redis
```

## 2. Write the run sheet (Excel)

```sh
oe-template runs.xlsx
```

This writes example rows. Hover over a column header, or open the README sheet,
for what each column means. One row is one run:

- `measurement_id`: a unique name. It names the data file. Put `#` in front
  to skip a row.
- `protocol`: `bipolar_pulse`, `cycling`, `soc_position` (CC to a voltage,
  then rest) or `rest`.
- **Sign:** a positive current raises the cell voltage. For hard carbon against
  Na, **sodiation is a negative current** (`start_with = discharge`).
- `start_mode`: `immediate`, or `external` to wait for the beamline START
  pulse.

Examples (the values are placeholders until the cell data are known; see
`docs/beamtime-concept.md`):

| measurement_id | protocol | settings |
|---|---|---|
| hc01-sodiate-overnight | soc_position | current_a −2.25e-5, target_v 0.2, max_step_s 50400, rest_s 1800, interval_s 10 |
| hc01-sodiate-5mV | soc_position | current_a −4.5e-4, target_v 0.005, max_step_s 7200, rest_s 600, interval_s 1 |
| hc01-pulse | bipolar_pulse | current_a 4.5e-4, pulse_s 1, rest_s 4, repeats 200, v_low 0.005, v_high 0.2, start_with charge, start_mode external |
| ref01-cycle | cycling | current_a 2.25e-5, v_low 0.005, v_high 0.2, cycles 1, start_with discharge, cv_i_abs_min_a 4.5e-6, cv_after discharge, rest_s 1800, max_step_s 54000, interval_s 1 |

Do not edit the sheet after its runs are queued: a queued run refuses to start
if the file has changed.

## 3. Write the session file (limits)

The session file fixes the instrument and the safety limits for every run
in the session:

```sh
cp qserver/session-k2460.example.toml ~/k2460-session.toml
```

Edit it:
- `data_dir` is where the HDF5 files go (local disk).
- `[limits]` sets the absolute voltage window, maximum current, maximum
  duration and maximum records.

Every row is checked against these limits, and the voltage window stops any run
that leaves it. Use one session file per window, for example 0–0.3 V for the
measurements and 0–2.7 V for the overnight sodiation from about 2.5 V.

## 4. Validate (no instrument needed)

```sh
oe-validate runs.xlsx --session ~/k2460-session.toml
```

You get one line per run: duration, records, trigger blocks and sampling.
Fix every `FAIL` line before going on. Name rows to check only those.

## 5. At the instrument (each day)

1. The 2460 may have been off overnight; that is fine. Its runtime is
   installed again automatically, with the output OFF.
2. **Cell wiring:** front terminals, 4-wire. **Hard carbon on HI (force and
   sense), Na on LO.** Check the polarity: reversed leads make every step run
   the wrong way.
3. **Digital I/O:**

   | Line | Signal | Direction |
   |---|---|---|
   | 1 | READY | out |
   | 2 | BUSY | out |
   | 4 | step MARKER | out |
   | 6 | START | in, from the beamline |

4. Check the connection. This only reads from the instrument:

   ```sh
   oe-diagnose --resource 'TCPIP0::169.254.113.151::5025::SOCKET' --backend '@py' --require-language TSP
   ```

   Expect serial 04686198, TSP, output OFF.

Only one program may talk to the 2460: close any notebook that uses it.

## 6. Start the QueueServer (three terminals)

Terminal 1, redis:

```sh
redis-server
```

Terminal 2, RE Manager. Leave it running; it prints each run's progress:

```sh
OE_SESSION=~/k2460-session.toml start-re-manager --startup-dir qserver/startup \
  --existing-plans-devices ~/k2460-data/existing_plans_and_devices.yaml \
  --user-group-permissions qserver/startup/user_group_permissions.yaml
```

Terminal 3, control (all further commands):

```sh
qserver environment open
qserver status            # wait for: worker_environment_exists True, manager_state idle
```

## 7. Queue and run

```sh
oe-enqueue runs.xlsx hc01-sodiate-5mV hc01-pulse --session ~/k2460-session.toml
```

For each run it prints the summary. **Check the cell, wiring, voltage window,
current limit and output-off mode HIGH-Z**, then type the run's
measurement_id to confirm it. If you mistype one, nothing is queued.

```sh
qserver queue get         # what is queued
qserver queue start       # runs the queue in order
```

An `external` run waits with READY (line 1) high until the START edge on line
6. The session's `start_timeout_s` (if set) aborts the run when no START
arrives.

## 8. Watch, stop, recover

- **Progress:** `qserver status`, `qserver history get`, and terminal 2.
- **Data:** each run writes `<data_dir>/<measurement_id>_<UTC time>.h5`.
  - To look at a file while it is written, use a notebook: `from
    ophyd_electrochemistry.sequences.callbacks import plot_run_file`, then
    `plot_run_file(path).show()`. Rerun it to refresh.
  - Each file ends with an outcome: `completed`, `stopped_cutoff` (a cutoff
    with STOP ended it; normal), `aborted` or `error`.
- **Finish the current run, then stop:** `qserver queue stop`.
- **Stop the current run now:**

  ```sh
  qserver re pause immediate   # the 2460 aborts the run and turns the output OFF
  qserver re abort             # closes the run; the file keeps outcome "aborted"
  qserver queue get            # the aborted run is back at the front of the queue
  qserver queue item remove front   # remove it, unless you want to repeat it
  qserver queue add plan front '{"name": "recover_sequencer"}'
  qserver queue start          # recover, then the rest of the queue
  ```

- **After an error:** queue `recover_sequencer` at the front, as above,
  before anything else.
- **Never retry a run whose state is unclear.** Check that the output is OFF
  (front panel, or `oe-diagnose` once the QueueServer is stopped).

## 9. Change session, end of day

```sh
qserver queue stop
qserver environment close
qserver manager stop
```

Then stop redis with Ctrl-C in terminal 1. To use another session file (for
example the overnight window), restart terminal 2 with a different `OE_SESSION`.
Check that nothing is left running: no Python process from this folder should
use CPU after `manager stop`.

If the laptop or the RE Manager crashed during a run, the 2460 finishes the run
on its own and turns the output OFF. The next run then refuses to start until
the readings left on the instrument are saved. With the QueueServer stopped,
run:

```sh
python -c "from ophyd_electrochemistry.keithley.k2460.sequence_backend import k2460_sequence_backend as k
b = k('TCPIP0::169.254.113.151::5025::SOCKET', terminals='front', expected_serial_number='04686198')
b.connect(); print(b.archive_retained('$HOME/k2460-data/retained.npz')); b.close()"
```

## Quick reference

| Step | Command |
|---|---|
| New run sheet | `oe-template runs.xlsx` |
| Check rows | `oe-validate runs.xlsx [IDs] --session S.toml` |
| Check the 2460 | `oe-diagnose --resource '…' --backend '@py' --require-language TSP` |
| Start | `redis-server`; `OE_SESSION=S.toml start-re-manager --startup-dir qserver/startup …`; `qserver environment open` |
| Queue (with confirmation) | `oe-enqueue runs.xlsx ID … --session S.toml` |
| Run / stop after current | `qserver queue start` / `qserver queue stop` |
| Abort now | `qserver re pause immediate`, `qserver re abort`, then remove, recover, start |
| Status | `qserver status`, `qserver queue get`, `qserver history get` |
| Shut down | `qserver environment close`, `qserver manager stop`, Ctrl-C redis |

Alternative without the QueueServer: one notebook per run. `oe-new-run
runs.xlsx ID <folder> --v-min … --v-max … --i-max … --max-duration-s …
--max-records …` creates it; then set `BACKEND = "k2460"` in it.
