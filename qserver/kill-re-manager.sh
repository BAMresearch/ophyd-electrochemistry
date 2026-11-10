#!/bin/bash
# Kill a detached RE Manager (start-k2460-re-manager.sh --detach) and every
# process it spawned, without cleanup: no abort or other command reaches the
# 2460, which finishes a running sequence on its own (B2 Q8, K3). Afterwards the
# run file stays `incomplete`; save the instrument's readings later with
# `oe-archive-retained --session S.toml` once the run has ended.
#   qserver/kill-re-manager.sh
set -euo pipefail

file=~/k2460-data/re-manager.pgid
if [[ ! -f $file ]]; then
    echo "No $file: was the RE Manager started with --detach?" >&2
    exit 1
fi
pgid=$(<"$file")
if [[ ! $pgid =~ ^[0-9]+$ || $pgid -le 1 ]]; then
    echo "Invalid process group '$pgid' in $file" >&2
    exit 1
fi
if ! pgrep -g "$pgid" >/dev/null; then
    echo "Process group $pgid has no processes left."
    rm -f "$file"
    exit 0
fi
pgrep -lg "$pgid" | sed 's/^/killing /'
kill -KILL -- "-$pgid"
for _ in $(seq 50); do
    pgrep -g "$pgid" >/dev/null || break
    sleep 0.1
done
if pgrep -g "$pgid" >/dev/null || pgrep -f 'bin/start-re-manager' >/dev/null; then
    echo "STILL RUNNING: do not disconnect; check 'ps -o pid,pgid,command -g $pgid'" >&2
    pgrep -lg "$pgid" >&2 || true
    pgrep -fl 'bin/start-re-manager' >&2 || true
    exit 1
fi
rm -f "$file"
echo "RE Manager group $pgid killed; nothing left. The 2460 continues on its own."
