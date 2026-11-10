#!/bin/bash
# Start redis if needed, then the RE Manager for one session.
#   qserver/start-k2460-re-manager.sh bench/b2/session-cell-neg.toml
#       runs it in this terminal (stop: `qserver environment close`, then
#       `qserver manager stop`).
#   qserver/start-k2460-re-manager.sh --detach bench/b3/session-b3-p0.toml
#       runs it in its own session and process group, logging to
#       ~/k2460-data/re-manager.log; the group ID goes to
#       ~/k2460-data/re-manager.pgid for qserver/kill-re-manager.sh, which
#       detaches from a running sequence without cleanup (the 2460 continues).
# The session sets the voltage window, so it has no default. `redis-cli shutdown`
# stops redis.
set -euo pipefail

detach=0
if [[ ${1:-} == --detach ]]; then
    detach=1
    shift
fi
if [[ $# -ne 1 || ! -f $1 ]]; then
    echo "usage: $0 [--detach] SESSION.toml   (a session file that exists)" >&2
    exit 2
fi
session=$(realpath "$1")
cd "$(dirname "$0")/.."

if pgrep -f 'bin/start-re-manager' >/dev/null; then
    echo "An RE Manager is already running: stop it first (qserver manager stop)." >&2
    exit 1
fi

if ! redis-cli ping >/dev/null 2>&1; then
    redis-server --daemonize yes --dir "$PWD" >/dev/null
    until redis-cli ping >/dev/null 2>&1; do sleep 0.1; done
    echo "redis started"
fi

echo "Session: $session"
grep -E '^(v_min_abs|v_max_abs|i_max_abs)' "$session"
echo "Next, in another terminal: qserver environment open"

export OE_SESSION=$session
manager=(.venv/bin/start-re-manager --startup-dir qserver/startup
    --existing-plans-devices ~/k2460-data/existing_plans_and_devices.yaml
    --user-group-permissions qserver/startup/user_group_permissions.yaml)

if [[ $detach -eq 0 ]]; then
    exec "${manager[@]}"
fi

state=~/k2460-data
mkdir -p "$state"
# setsid: a new session and process group whose ID is this child's PID, so the
# manager, its API server and its worker can be killed together.
nohup .venv/bin/python -c 'import os, sys; os.setsid(); os.execv(sys.argv[1], sys.argv[1:])' \
    "${manager[@]}" >>"$state/re-manager.log" 2>&1 </dev/null &
pgid=$!
echo "$pgid" >"$state/re-manager.pgid"
echo "RE Manager detached: process group $pgid, log $state/re-manager.log"
