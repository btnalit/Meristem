#!/bin/bash
# Heartbeat keeper -- restart the heartbeat between runs, and ONLY between
# clean runs.
#
# REFERENCE COPY. The live script runs from /RSI/run_meristem.sh, outside the
# repository, for two reasons: the seed cannot reach it there, and bash
# re-reads a script file mid-execution -- a git merge rewriting an in-repo
# keeper while it runs would corrupt it. Deploy with scp, never by editing
# the running copy.
#
# WHY EXIT CODES ARE NOT ENOUGH: rollback() returns 0 on success, so a
# heartbeat that auto-reverted after three consecutive failures exits with
# the SAME code as a clean fourteen-beat run. Restarting on exit 0 alone
# would relaunch the seed at exactly the moment a human should be looking at
# it. The keeper therefore checks two signals -- the exit code AND whether a
# revert commit appeared during the run.
#
# PANIC IS NOT A RESTART. root/panic.py: "Panic is a full stop. Only a human
# clears the latch." The latch is checked before every launch and again after
# every exit. Nothing below may restart over an engaged latch.

set -u

exec 9>/RSI/keeper.lock
flock -n 9 || { echo "another keeper holds the lock; exiting"; exit 0; }

source /RSI/meristem-env

# Resolve the latch exactly as panic.py does, and only AFTER sourcing the
# environment: if MERISTEM_CONTROL is set there and we hardcoded the default,
# the keeper would watch the wrong file and silently defeat the latch.
LATCH="${MERISTEM_CONTROL:-$HOME/.meristem-control}/PANIC"

REPO=/RSI/Meristem
LOG="$REPO/heartbeat_keeper.log"
BEATS=14

# A complete run cannot be short: thirteen sleeps of 15-45 minutes each put
# the floor above three hours. Anything under thirty minutes means the run
# died rather than finished, and relaunching it would be a crash loop.
MIN_RUN_SECONDS=1800

cd "$REPO" || exit 1

notify() {
    [ -n "${MERISTEM_WEBHOOK_URL:-}" ] || return 0
    curl -s -m 10 -X POST -H 'Content-Type: application/json' \
        -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"[meristem:keeper] $1\"}}" \
        "$MERISTEM_WEBHOOK_URL" >/dev/null 2>&1 || true
}

stop() {
    echo "$(date '+%F %T') KEEPER STOP: $1" >> "$LOG"
    notify "$1"
    exit "$2"
}

# Stand aside until the run that is already in flight has finished. Starting a
# second heartbeat alongside it would have two supervisors promoting into the
# same repository.
while pgrep -f 'substrate.supervisor heartbeat' >/dev/null 2>&1; do
    sleep 60
done

echo "$(date '+%F %T') keeper armed" >> "$LOG"

while true; do
    [ -f "$LATCH" ] && stop "panic latch engaged -- full stop" 3

    head_before=$(git rev-parse HEAD)
    start=$(date +%s)

    echo "$(date '+%F %T') --- starting heartbeat ($BEATS beats)" >> "$LOG"
    python3 -m substrate.supervisor heartbeat --beats "$BEATS" >> "$LOG" 2>&1
    rc=$?
    dur=$(( $(date +%s) - start ))

    # The latch may have been engaged while the run was in flight.
    [ -f "$LATCH" ] && stop "panic latch engaged during run -- full stop" 3

    # A human killing the heartbeat lands here too: intervention wins.
    [ $rc -ne 0 ] && stop "heartbeat exited $rc after ${dur}s -- stopped" "$rc"

    if git log --format=%s "${head_before}..HEAD" | grep -q auto-rollback; then
        stop "auto-rollback occurred during run -- stopped for human review" 4
    fi

    [ $dur -lt $MIN_RUN_SECONDS ] \
        && stop "run ended after only ${dur}s -- crash-loop guard" 5

    echo "$(date '+%F %T') run completed cleanly in ${dur}s; restarting" >> "$LOG"
    sleep 120
done
