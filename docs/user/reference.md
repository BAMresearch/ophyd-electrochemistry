# Command reference

Run from the repository folder with its environment active
(`source .venv/bin/activate`). `S.toml` is the session file.

## Commands

| Step | Command |
|---|---|
| New run sheet | `oe-template runs.xlsx` |
| Check rows (no instrument) | `oe-validate runs.xlsx [ID …] --session S.toml` |
| Check the 2460 (read-only) | `oe-diagnose --resource 'TCPIP0::169.254.113.151::5025::SOCKET' --backend '@py' --require-language TSP` |
| Start redis + RE Manager | `qserver/start-k2460-re-manager.sh S.toml` |
| Open the environment (sets HIGH-Z) | `qserver environment open` |
| Queue rows | `oe-enqueue runs.xlsx ID … --session S.toml` |
| Run the queue / stop after the current run | `qserver queue start` / `qserver queue stop` |
| Abort now | `qserver re pause immediate`, `qserver re abort`, `qserver queue item remove front` |
| Recover after an abort or error | `qserver queue add plan front '{"name": "recover_sequencer"}'`, `qserver queue start` |
| Save readings after a crash | `qserver queue add plan front '{"name": "archive_retained"}'`, `qserver queue start`; or `oe-archive-retained --session S.toml` with the QueueServer stopped |
| Status | `qserver status`, `qserver queue get`, `qserver history get` |
| One notebook per run | `oe-new-run runs.xlsx ID FOLDER --v-min … --v-max … --i-max … --max-duration-s … --max-records …` |
| Shut down | `qserver environment close`, `qserver manager stop`, `redis-cli shutdown` |

Only three plans can be queued: `run_from_sheet` (added by `oe-enqueue`),
`recover_sequencer` and `archive_retained`.

## Run outcomes

| Outcome | Meaning | Next |
|---|---|---|
| `completed` | Ran to the end | – |
| `stopped_cutoff` | A row threshold (`v_low`, `v_high`, `rest_end_v`) ended it, output OFF | Normal; the queue continues |
| `aborted` | Stopped by you, or no external START in time | Remove the item, queue `recover_sequencer` |
| `error` | Instrument or software error; output OFF confirmed | Read terminal 1, remove the item, queue `recover_sequencer` |
| `incomplete` (file) | Still running, or the writer crashed | After a crash: `archive_retained` |

A reading outside the session's voltage window also stops the run
(`stopped_cutoff`; its `stop` event names the limit that was crossed).

## Common messages

| Message | Cause |
|---|---|
| `… changed since this run was enqueued; enqueue again` | The run sheet was edited after `oe-enqueue` |
| `unread records` | A crashed run's readings are still on the 2460: archive them |
| `exceeds … trigger blocks` | The row needs more than the 2460's 63 blocks (see [Run sheets](run-sheets.md#how-much-fits-in-one-run)) |
| `An RE Manager is already running` | Stop it first: `qserver manager stop` |
| HIGH-Z not confirmed at `environment open` | Do not connect a cell; check the instrument and terminal 1 |
