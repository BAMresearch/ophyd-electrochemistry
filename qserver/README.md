# QueueServer session for the K2460 sequencer

The RE Manager worker owns the 2460 for the whole session: do not run a
notebook against the instrument at the same time. Every queued run is one
run-sheet row, validated against the session limits when it is enqueued and
again when it runs; the plan refuses a sheet that changed in between. For a
hardware session, the enqueue command asks you to confirm each run by typing
its measurement ID after reviewing its summary.

```sh
uv sync --locked --extra experiment --extra visa --extra queueserver
redis-server &                                   # once per machine
cp qserver/session-k2460.example.toml ~/k2460-session.toml   # review every value
OE_SESSION=~/k2460-session.toml uv run --locked start-re-manager \
  --startup-dir qserver/startup \
  --existing-plans-devices ~/k2460-data/existing_plans_and_devices.yaml \
  --user-group-permissions qserver/startup/user_group_permissions.yaml
```

In another terminal:

```sh
uv run --locked qserver environment open        # connects nothing yet
uv run --locked ophyd-electrochemistry-enqueue runs.xlsx R5-001 R5-002 \
  --session ~/k2460-session.toml                 # summary + typed confirmation per run
uv run --locked qserver queue start
uv run --locked qserver status
```

`qserver re abort` stops the current run: the Flyer aborts, drains and the
file keeps outcome `aborted`. Queue `recover_sequencer` before the next run if
the worker reports an error. Run files go to the session's `data_dir` (SWMR:
open them read-only from a notebook while they are written). Only
`run_from_sheet` and `recover_sequencer` are permitted
(`startup/user_group_permissions.yaml`); no namespace function may be executed.

`qserver/session-simulator.toml` runs the same flow on the synthetic cell;
`tests/contract/test_queueserver_manager.py` does that against a real RE
Manager and redis.
