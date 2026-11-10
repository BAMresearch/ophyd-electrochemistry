# QueueServer session for the K2460 sequencer

The RE Manager worker owns the 2460 for the whole session: do not run a
notebook against the instrument at the same time. Every queued run is one
run-sheet row, validated against the session limits when it is enqueued and
again when it runs; the plan refuses a sheet that changed in between. The
session file fixes the set-up and limits; loading it is the confirmation for
every run in the session. The operator procedure is in `docs/user/beamtime.md`.

```sh
uv sync --locked --extra experiment --extra visa --extra queueserver
redis-server &                                   # once per machine
cp qserver/session-k2460.example.toml ~/k2460-session.toml   # review every value
OE_SESSION=~/k2460-session.toml uv run --locked start-re-manager \
  --startup-dir qserver/startup \
  --existing-plans-devices ~/k2460-data/existing_plans_and_devices.yaml \
  --user-group-permissions qserver/startup/user_group_permissions.yaml
```

`qserver/start-k2460-re-manager.sh ~/k2460-session.toml` does the redis and
RE Manager steps in one command (after `uv sync` and the session copy).

In another terminal:

```sh
uv run --locked oe-validate runs.xlsx --session ~/k2460-session.toml   # every row; no I/O
uv run --locked qserver environment open        # sets HIGH-Z; connect cells after this
uv run --locked oe-enqueue runs.xlsx R5-001 R5-002 \
  --session ~/k2460-session.toml                 # validates, prints a summary per run
uv run --locked qserver queue start
uv run --locked qserver status
```

`qserver re abort` stops the current run: the Flyer aborts, drains and the
file keeps outcome `aborted`. Queue `recover_sequencer` before the next run if
the worker reports an error, and `archive_retained` after a host crash (it
saves the 2460's unread records in `data_dir`). Run files go to the session's
`data_dir` (SWMR: open them read-only from a notebook while they are written).
Only `run_from_sheet`, `recover_sequencer` and `archive_retained` are permitted
(`startup/user_group_permissions.yaml`); no namespace function may be executed.

`qserver/session-simulator.toml` runs the same flow on the synthetic cell;
`tests/contract/test_queueserver_manager.py` does that against a real RE
Manager and redis.
