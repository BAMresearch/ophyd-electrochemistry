# Operator guide: running the experiment from Excel and the command line

Step by step, from an Excel run sheet to HDF5 files, with the QueueServer
owning the Keithley 2460. **Status (4 Nov 2026):** the whole flow runs on the
simulator and against a real RE Manager; the 2460 hardware path still has to
pass bench validation (B1, B2) before it is used on cells.

All commands run in the repository folder with its environment active:

```sh
cd ~/Code/ophyd-electrochemistry
source .venv/bin/activate
```

## 1. Install (once per laptop)

Needs [uv](https://docs.astral.sh/uv/getting-started/installation/) and git.
uv downloads Python 3.14 itself if it is missing. The branch must be on GitHub;
as of 3 Nov `mvp-sprint` is still local, so push it first.

**A. Clone the repository (recommended).** The QueueServer startup files
(`qserver/`), the example session files and the bench scripts live in the
repository, not in the installed package. The tests need a clone too.

```sh
git clone -b mvp-sprint https://github.com/BAMresearch/ophyd-electrochemistry.git
cd ophyd-electrochemistry
uv sync --locked --extra experiment --extra visa --extra queueserver
brew install redis
```

`uv sync` creates `.venv` with Python 3.14 and the exact package versions in
`uv.lock`. It also installs the development tools: pytest, the Lua runtime
checker (lupa), ruff and mypy. To check the installation, run
`uv run --locked pytest`. Everything should pass; the QueueServer tests need
redis.

**B. Package only.** This gives the notebook and the `oe-*`, `qserver` and
`start-re-manager` commands, but not the `qserver/` startup files, the tests or
lupa:

```sh
uv venv --python 3.14 ~/oe-env
source ~/oe-env/bin/activate
uv pip install "ophyd-electrochemistry[experiment,visa,queueserver] @ git+https://github.com/BAMresearch/ophyd-electrochemistry@mvp-sprint"
```

- Keep the `git+` prefix and the extras in brackets:
  - `visa` adds PyVISA, needed to talk to the 2460;
  - `experiment` adds Excel, HDF5, plots and notebooks;
  - `queueserver` adds the RE Manager.
- `uv pip install` takes the newest compatible dependencies, not the tested
  versions in `uv.lock`.

## 2. Write the run sheet (Excel)

```sh
oe-template runs.xlsx
```

This writes example rows. Hover over a column header, or open the README sheet,
for what each column means. One row is one run:

- `measurement_id`: a unique name. It names the data file. Put `#` in front
  to skip a row.
- `protocol`: `pulse_train` (pulses in one direction, with rests),
  `bipolar_pulse`, `cycling`, `soc_position` (CC to a voltage, then rest) or
  `rest`.
- **Sign:** a positive current raises the cell voltage. For hard carbon against
  Na, **sodiation is a negative current**. `pulse_train` and `soc_position`
  take a signed `current_a`. In `start_with` and `cv_after`, write `sodiation`
  or `desodiation`; `discharge` and `charge` mean the same.
- `start_mode`: `immediate`, or `external` to wait for the beamline START
  pulse.

Examples for a 0.6 mAh cell (1C = 600 µA), following
`docs/beamtime-concept.md`. The values are not final:

| measurement_id | protocol | settings |
|---|---|---|
| hc01-sodiate | soc_position | current_a −6e-5, target_v 0.005, max_step_s 50400, rest_s 1800, interval_s 1 |
| hc01-desod-pulse | pulse_train | current_a 6e-4, pulse_s 10, rest_s 50, repeats 360, v_high 0.2, start_mode external |
| hc01-sod-pulse | pulse_train | current_a −6e-4, pulse_s 10, rest_s 50, repeats 360, v_low 0.005 |
| ref01-cycle | cycling | current_a 3e-5, v_low 0.005, v_high 0.2, cycles 1, start_with desodiation, cv_i_abs_min_a 6e-6, cv_after sodiation, rest_s 1800, max_step_s 72000, interval_s 1 |

A pulse train stops the run when a pulse reaches `v_high` or `v_low`. The
2460 checks this on the voltage under load. Run reference cycling with a CV
hold in a session whose `i_max_abs` is near the cycling current (step 3).

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
that leaves it. Use one session file per window and current, for example:
- 0–3 V for the sodiation from about 2.5 V;
- 0–0.5 V / 1 mA for pulses;
- 0–0.5 V / 100 µA for reference cycling. A CV hold's current limit is the
  session's `i_max_abs`.

The cell's rest voltage must be inside the window before a run: the voltage
limit is the window's upper edge plus 0.1 V.

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
oe-enqueue runs.xlsx hc01-desod-pulse hc01-sod-pulse --session ~/k2460-session.toml
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

## 8. Watch the data (viewer notebook)

The viewer notebook only reads the run files. It never talks to the 2460 or the
QueueServer, so it can stay open during runs, and stopping it does not affect a
measurement. Run it on the laptop that writes the data: the live view needs the
files on local disk.

1. Once per session, copy the viewer next to the data, so your settings do not
   change the repository copy:

   ```sh
   cp notebooks/k2460_view.ipynb ~/k2460-data/viewer.ipynb
   ```

2. Open it in either of two ways:
   - **VS Code:** open the copy and select the kernel
     `ophyd-electrochemistry/.venv`.
   - **JupyterLab:** from the repository folder, run

     ```sh
     uv run --locked --with jupyterlab jupyter lab ~/k2460-data/viewer.ipynb
     ```

3. In the first cell, set:
   - `DATA_DIR` to the session's `data_dir`;
   - optionally `FOLLOW`, `COMPARE` and `X_AXIS` (see the table below).

   Then run the cells from the top. The four sections:

   | Section | What it shows | Settings |
   |---|---|---|
   | **Overview** | One row per run file, newest first: measurement ID, sample, protocol, start time (UTC), duration, records, outcome, and counts of cutoffs, stops, aborts and errors. Rerun the cell to refresh. | `DATA_DIR` |
   | **Follow a run** | Voltage and current against time for the run being written, redrawn in place every 2 s until the run ends. Stop it early with the notebook's stop button (■); the measurement continues. | `FOLLOW` = a file name, or `None` for the newest run |
   | **One run at full view** | The followed run sample by sample (thinned by extrema above `FULL_VIEW_POINTS` per trace), with cutoff, stop and abort markers and the events table. | `X_AXIS` = `"time"` or `"charge"` |
   | **Compare runs** | Voltage of several runs overlaid, each starting at zero, for example pulsed against non-pulsed sodiation. | `COMPARE` = list of file names (default: the two newest finished runs); `X_AXIS` |

Reading the plots:
- **Charge** (`X_AXIS = "charge"`) is the integral of the measured current, in
  mAh, from the start of each run. Sodiation is a negative current, so its
  charge decreases.
- **Thinning:** long runs are thinned for display by keeping each section's
  highest and lowest point, so short pulses stay visible. Zoom in to see
  detail. The file always holds every sample.
- **Outcome:** each file ends with one of `completed`, `stopped_cutoff` (a
  cutoff with STOP ended it; normal), `aborted` or `error`.
  - `incomplete` means the run is still being written, or its writer crashed.
    Follow cannot tell the two apart, so stop it by hand if the run is not
    advancing.
  - `unreadable` files are listed in the overview with the reason.

## 9. Stop, recover

- **Progress:** `qserver status`, `qserver history get`, and terminal 2.
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

## 10. Change session, end of day

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
| Watch data | `~/k2460-data/viewer.ipynb` (copy of `notebooks/k2460_view.ipynb`): Overview, Follow, Compare |
| Shut down | `qserver environment close`, `qserver manager stop`, Ctrl-C redis |

Alternative without the QueueServer: one notebook per run. `oe-new-run
runs.xlsx ID <folder> --v-min … --v-max … --i-max … --max-duration-s …
--max-records …` creates it; then set `BACKEND = "k2460"` in it.
