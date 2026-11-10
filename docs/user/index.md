# User guide

This guide is for the people who run experiments with the Keithley 2460: write
the runs, start them, watch the data and recover when something goes wrong. It
assumes no knowledge of the code.

## What you work with

| Thing | What it is | Where |
|---|---|---|
| **Run sheet** | An Excel workbook, one row per run: protocol, currents, times, thresholds, sampling | You write it; `oe-template` makes an example |
| **Session file** | A small TOML file that fixes the instrument, the data folder and the safety limits for a set of runs. Loading it is your confirmation of the set-up | One per cell, wiring and voltage window |
| **QueueServer** | Runs the queued rows one after another; the only program that talks to the 2460 | `qserver/` in the repository |
| **Run file** | One HDF5 file per run with every reading, the events and the full settings | The session's `data_dir` |
| **Viewer notebook** | Lists, follows and compares run files; read-only | `notebooks/k2460_view.ipynb` |
| **Per-run notebook** | Runs one row without the QueueServer (fallback) | `oe-new-run` makes one |

## How a run works

```text
laptop                                   Keithley 2460                   cell
──────                                   ─────────────                   ────
run sheet row ─┐
session limits ┴► validate ► upload ───► trigger model runs on its own ─► HI / LO
                                         (timing, voltage cutoffs,
run file ◄──── read the buffer ◄──────── markers, output OFF at the end)
viewer   ◄──── (every second)
```

The laptop checks each row against the session limits, translates it into a
program for the 2460 and uploads it. From then on **the 2460 runs the sequence
by itself**: it times every step and sample, watches the voltage limits and
switches the output OFF at the end. The laptop only reads the stored readings
about once per second and writes them to the run file. If the laptop crashes, the
run still finishes safely on the instrument and the readings can be recovered.

## Safety in one paragraph

Each session file sets an absolute voltage window and a maximum current. Every
row is validated against them before anything reaches the instrument, and the
2460 itself stops the run with the output OFF if a reading leaves the window.
The output-off mode is **HIGH-Z**, so an idle or stopped output does not
discharge the cell. Load a session only for the cell and wiring actually
connected: loading it is the confirmation for every run in that session.
Individual runs are not confirmed again.

## Pages

1. [Quickstart](quickstart.md): install and run a complete example on the
   simulator, without an instrument.
2. [Run sheets](run-sheets.md): how to write rows for each protocol, and how
   many repeats fit in one run.
3. [Sessions](sessions.md): the session file, its limits and when you need a
   new one.
4. [At the beamtime](beamtime.md): the day's procedure at the instrument, from
   switching on to shutting down, including stopping and recovering.
5. [Data and the viewer](data.md): what a run file holds and how to look at it.
6. [Command reference](reference.md): every command on one page.
