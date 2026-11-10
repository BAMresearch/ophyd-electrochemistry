#!/bin/bash
# Start redis if needed, then the RE Manager in this terminal, for one session.
#   qserver/start-k2460-re-manager.sh bench/b2/session-cell-neg.toml
# The session sets the voltage window, so it has no default. Stop with
# `qserver environment close`, then `qserver manager stop`; `redis-cli shutdown`
# stops redis.
set -euo pipefail

if [[ $# -ne 1 || ! -f $1 ]]; then
    echo "usage: $0 SESSION.toml   (a session file that exists)" >&2
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
exec .venv/bin/start-re-manager --startup-dir qserver/startup \
    --existing-plans-devices ~/k2460-data/existing_plans_and_devices.yaml \
    --user-group-permissions qserver/startup/user_group_permissions.yaml
