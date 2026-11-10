# Quickstart

This runs the whole chain (run sheet, QueueServer, run file, viewer) on the
built-in simulator, which models a synthetic cell. No instrument is needed and
nothing here can reach a 2460. Allow about 15 minutes.

## 1. Install (once per laptop)

You need [uv](https://docs.astral.sh/uv/getting-started/installation/), git
and redis (`brew install redis` on a Mac). uv downloads Python 3.14 itself.

```sh
git clone -b mvp-sprint https://github.com/BAMresearch/ophyd-electrochemistry.git
cd ophyd-electrochemistry
uv sync --locked --extra experiment --extra visa --extra queueserver
source .venv/bin/activate
```

The extras add Excel, HDF5 and plotting (`experiment`), the instrument
connection (`visa`) and the RE Manager (`queueserver`). All further commands
in this guide run in the repository folder with this environment active.

Clone the repository rather than installing the package alone: the QueueServer
startup files, the example session files and the notebooks live in it. To check
the installation, run `pytest` (a few minutes; the QueueServer tests need redis).

## 2. Make a run sheet

```sh
oe-template runs.xlsx
```

Open `runs.xlsx`. Each row is one run; hover over a column header, or open the
README sheet, for what each column means. The examples include
`example-rest` (a short rest that only measures the voltage) and
`example-pulse-train`.

## 3. Validate it against a session

```sh
oe-validate runs.xlsx --session qserver/session-simulator.toml
```

You get one line per row with its duration, number of records and trigger
blocks. `qserver/session-simulator.toml` is a session for the simulator; it runs
10 times faster than real time.

## 4. Start the QueueServer

In terminal 1:

```sh
qserver/start-k2460-re-manager.sh qserver/session-simulator.toml
```

This starts redis if needed, prints the session limits and then runs the RE
Manager in this terminal. Leave it running: it prints each run's progress.

In terminal 2:

```sh
qserver environment open
qserver status        # wait for: worker_environment_exists True, manager_state idle
oe-enqueue runs.xlsx example-rest example-pulse-train --session qserver/session-simulator.toml
qserver queue start
```

`oe-enqueue` checks the rows again, prints a summary of each and adds them to
the queue. `qserver status` and terminal 1 show the progress.

## 5. Look at the data

The run files land in the session's `data_dir`, here `~/k2460-data/qserver-sim`.
Copy the viewer next to them and open it in VS Code (kernel: this
repository's `.venv`) or JupyterLab:

```sh
cp notebooks/k2460_view.ipynb ~/k2460-data/qserver-sim/viewer.ipynb
```

Set `DATA_DIR = Path.home() / "k2460-data" / "qserver-sim"` in its first
cell and run all cells: an overview table of the runs, a live view of the
newest run, and an overlay of two runs. See [Data and the viewer](data.md).

## 6. Stop

```sh
qserver environment close
qserver manager stop
```

## Without the QueueServer

For a single run, a notebook is enough:

```sh
oe-new-run runs.xlsx example-pulse-train ~/k2460-data/nb-sim \
  --v-min 0 --v-max 3 --i-max 0.01 --max-duration-s 200000 --max-records 1000000
```

This validates the sheet against the limits you give and writes
`~/k2460-data/nb-sim/example-pulse-train.ipynb`. Open it and run all cells:
it shows the row, a simulated preview, a live plot and the saved data. It uses
the simulator until you set `BACKEND = "k2460"` in its settings cell.

## Next

Write your own rows ([Run sheets](run-sheets.md)), make a session file for your
cell ([Sessions](sessions.md)), then follow [At the beamtime](beamtime.md) at
the instrument.
