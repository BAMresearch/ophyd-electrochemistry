# At the beamtime

The procedure at the instrument, in order. Prepare the run sheet and session
files beforehand and validate them ([Run sheets](run-sheets.md),
[Sessions](sessions.md)).

<span style="color:red">**Open the environment before connecting a cell.** After power-on the 2460's output-off mode is NORMAL, which pulls a connected cell towards 0 V. Opening the environment sets HIGH-Z. For the same reason, disconnect the cell before switching the 2460 off.</span>

## 1. Instrument and wiring

1. Switch the 2460 on. It may have been off overnight: the packaged program is
   installed again automatically, with the output OFF.
2. Ethernet from the laptop to the 2460. Only one program may talk to the 2460:
   close any notebook that uses it.
3. Digital I/O (the DB-9 connector on the rear):

   | Line | Signal | Direction | Meaning |
   |---|---|---|---|
   | 1 | READY | out | High while an external-start run waits for START |
   | 2 | BUSY | out | High from the start of a run until the output is OFF |
   | 4 | MARKER | out | Short pulse at the start of every step |
   | 6 | START | in | Rising edge starts an external-start run |

4. Check the connection; this only reads from the instrument:

   ```sh
   oe-diagnose --resource 'TCPIP0::169.254.113.151::5025::SOCKET' --backend '@py' --require-language TSP
   ```

   Expect serial 04686198, TSP, output OFF.

## 2. Start the QueueServer

Terminal 1, from the repository:

```sh
qserver/start-k2460-re-manager.sh ~/sessions/hc01-sod.toml
```

It starts redis if needed, prints the session's limits and runs the RE Manager
in this terminal. Leave it open: it shows each run's progress. To keep it
running when the terminal or editor closes, start it detached instead:
`nohup qserver/start-k2460-re-manager.sh … > ~/k2460-data/re-manager.log 2>&1 &`.

Terminal 2, for everything else:

```sh
qserver environment open
qserver status            # wait for: worker_environment_exists True, manager_state idle
```

Opening the environment connects to the 2460, sets HIGH-Z for both source
functions, reads it back and disconnects. If it fails, terminal 1 shows why:
**do not connect a cell**.

## 3. Connect the cell

Front terminals, four-wire. **Hard carbon on HI (force and sense), Na on LO.**
Check the polarity: reversed leads make every step run the wrong way. A narrow
session window catches this at the first reading.

## 4. Queue and run

```sh
oe-enqueue runs.xlsx hc01-sodiate hc01-desod-pulse --session ~/sessions/hc01-sod.toml
qserver queue get         # what is queued
qserver queue start       # runs the queue in order
```

`oe-enqueue` validates the named rows against the session, checks that each
fits the 2460's trigger model, prints a summary of each and adds them in order;
if any row fails, nothing is queued. The
`--session` file must be the one the RE Manager runs with. You can add rows
while the queue runs.

A row that ends on a voltage threshold (outcome `stopped_cutoff`) has finished
normally, and the queue continues with the next row.

### External start

An `external` row arms the 2460, raises READY (line 1) and waits with the
output OFF for a rising edge on START (line 6). The run then starts, and its
time zero is logged within about 0.1 ms of that edge. If the session sets
`start_timeout_s`, the run aborts when no START arrives in time.

## 5. Watch

- `qserver status`, `qserver history get`, and terminal 1.
- The viewer notebook for the data ([Data and the viewer](data.md)). It only
  reads files, so it can stay open and be restarted at any time without
  affecting a run.

## 6. Stop a run, recover

- **Finish the current run, then stop the queue:** `qserver queue stop`.
- **Stop the current run now:**

  ```sh
  qserver re pause immediate        # the 2460 aborts the run and turns the output OFF
  qserver re abort                  # closes the run; the file keeps outcome "aborted"
  qserver queue item remove front   # the aborted run is back at the front: remove it
  qserver queue add plan front '{"name": "recover_sequencer"}'
  qserver queue start               # recover, then the rest of the queue
  ```

- **After a failed run:** the QueueServer puts the failed item back at the
  front and stops. Remove it, queue `recover_sequencer` at the front as above,
  then start.
- **After a laptop or RE Manager crash during a run:** the 2460 finishes the
  run on its own and turns the output OFF. The next run then fails with "unread
  records" until the readings left on the instrument are saved:

  ```sh
  qserver queue item remove front
  qserver queue add plan front '{"name": "archive_retained"}'
  qserver queue start               # saves retained-<UTC time>.npz in data_dir
  ```

  Without a running QueueServer, `oe-archive-retained --session S.toml` does the
  same. The run's own file stays `incomplete`; the archive holds every reading
  the 2460 kept.
- **Never repeat a run whose state is unclear.** First check that the output is
  OFF (front panel, or `oe-diagnose` once the QueueServer is stopped).

## 7. Change session, end of day

```sh
qserver queue stop
qserver environment close
qserver manager stop
```

For another cell, wiring or window, start the RE Manager again with that
session file (step 2). At the end of the day, disconnect the cell before
switching the 2460 off. `redis-cli shutdown` stops redis.

## Fallback: one notebook per run

If the QueueServer is unavailable, `oe-new-run` makes a notebook for one row
with the limits you pass (see the [Quickstart](quickstart.md#without-the-queueserver)).
Set `BACKEND = "k2460"` and the instrument settings in its settings cell, which
serves as that run's session. Do not run it while a QueueServer environment is
open.
