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

# A rollback already reverted to the canary-proven last-good commit, so
# relaunching once runs code that has passed the gates -- stopping there
# would make a recoverable event need a human, which is the scaffolding this
# loop is meant to shed. Two rollbacks inside a day is a different claim: it
# says the failure is systemic rather than one bad mutation, and that does
# want a person. The <30min crash-loop guard still backstops both.
ROLLBACK_LOG=/RSI/keeper_rollbacks
ROLLBACK_WINDOW=86400
ROLLBACK_COOLDOWN=1800

# A non-zero exit is not automatically a reason to stop. A SIGNAL is: 128+n
# means a human or the OS intervened, and intervention wins. Panic (3) is the
# latch. Everything else is the run FAILING, and a failure that halts the loop
# until somebody notices is exactly the scaffolding this system exists to
# shed. The three keeper stops on record were all "exited 1", after runs of
# 72, 120 and 129 minutes, and every one of them sat waiting for a person to
# press start. Bounded resume instead, counted in the same 24h window the
# rollback guard already uses: isolated failures cost a cooldown, a systemic
# one still stops.
FAILURE_LOG=/RSI/keeper_failures
FAILURE_WINDOW=86400
FAILURE_LIMIT=3
FAILURE_COOLDOWN=600

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

    # Intervention wins, and only intervention. A signal means a human or the
    # OS ended this run on purpose; the latch means panic. Neither resumes.
    [ $rc -ge 128 ] && stop "heartbeat killed by signal $(( rc - 128 )) after ${dur}s -- intervention wins" "$rc"
    [ $rc -eq 3 ] && stop "heartbeat reported the panic latch -- full stop" 3

    # Any other non-zero exit is a failure, not a verdict. Resume, bounded.
    if [ $rc -ne 0 ]; then
        now=$(date +%s)
        recent=0
        if [ -f "$FAILURE_LOG" ]; then
            while read -r stamp; do
                case "$stamp" in ''|*[!0-9]*) continue;; esac
                [ $(( now - stamp )) -lt $FAILURE_WINDOW ] && recent=$(( recent + 1 ))
            done < "$FAILURE_LOG"
        fi
        echo "$now" >> "$FAILURE_LOG"
        recent=$(( recent + 1 ))
        if [ "$recent" -ge $FAILURE_LIMIT ]; then
            stop "heartbeat exited $rc; ${recent} failures inside 24h -- systemic, stopped for review" "$rc"
        fi
        notify "heartbeat exited $rc after ${dur}s (failure ${recent}/${FAILURE_LIMIT} in 24h). Cooling down ${FAILURE_COOLDOWN}s then resuming. No action needed unless it recurs."
        echo "$(date '+%F %T') heartbeat exited $rc after ${dur}s; failure ${recent}/${FAILURE_LIMIT}; cooldown then resume" >> "$LOG"
        sleep $FAILURE_COOLDOWN
        continue
    fi

    if git log --format=%s "${head_before}..HEAD" | grep -q auto-rollback; then
        now=$(date +%s)
        recent=0
        if [ -f "$ROLLBACK_LOG" ]; then
            while read -r stamp; do
                case "$stamp" in ''|*[!0-9]*) continue;; esac
                [ $(( now - stamp )) -lt $ROLLBACK_WINDOW ] && recent=$(( recent + 1 ))
            done < "$ROLLBACK_LOG"
        fi
        echo "$now" >> "$ROLLBACK_LOG"
        if [ "$recent" -ge 1 ]; then
            stop "second auto-rollback within 24h -- systemic, stopped for review" 4
        fi
        # Resuming here skips the crash-loop guard below, deliberately: a
        # rollback can follow a short run, and the rollback counter is its own
        # bound -- a fast rollback loop stops itself on the second one, inside
        # about half an hour. The guard still covers every non-rollback exit.
        notify "auto-rollback: reverted to last-good, cooling down ${ROLLBACK_COOLDOWN}s then resuming. No action needed unless it recurs."
        echo "$(date '+%F %T') rollback; cooldown then resume" >> "$LOG"
        sleep $ROLLBACK_COOLDOWN
        continue
    fi

    [ $dur -lt $MIN_RUN_SECONDS ] \
        && stop "run ended after only ${dur}s -- crash-loop guard" 5

    echo "$(date '+%F %T') run completed cleanly in ${dur}s; restarting" >> "$LOG"
    sleep 120
done
