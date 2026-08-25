#!/usr/bin/env bash
# P0-c unattended pulse: one bounded `manual-cycle --autonomous` beat per
# invocation, driven by a systemd timer (substrate/systemd/meristem-pulse.*).
# Root-only. Ships with the timer NOT enabled -- see
# docs/MERISTEM-P0C-RUNBOOK.md. Enabling the timer is a separate owner
# decision, not part of this wrapper.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
ENV_FILE="${MERISTEM_ENV_FILE:-/RSI/meristem-env}"
RUNTIME_DIR="${MERISTEM_RUNTIME_DIR:-/run/meristem}"
LOCK_FILE="$RUNTIME_DIR/pulse.lock"
WEBHOOK_URL=""

usage() {
    cat <<'EOF'
usage: substrate/pulse-beat.sh [--task <text>]

One bounded `manual-cycle --autonomous` beat. Intended to be invoked only by
meristem-pulse.timer/.service (systemd, root). See docs/MERISTEM-P0C-RUNBOOK.md.
EOF
}

TASK_ARG=()
while (($#)); do
    case "$1" in
        --task)
            (($# >= 2)) || { usage >&2; exit 2; }
            TASK_ARG=(--task "$2")
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
done

# --- JSON escaping + webhook notification -----------------------------------
# WeChat qiyeweixin webhook format. `notify` never propagates a failure to
# the caller: a broken webhook must never turn into a broken beat, and in
# the one place that matters (the non-normal-rc path below) it must never
# be the thing that stops the human from being pinged.
json_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    printf '"%s"' "$s"
}

notify() {
    local content="$1"
    if [[ -z "$WEBHOOK_URL" ]]; then
        echo "pulse-beat: no webhook configured, skipping notification: $content" >&2
        return 0
    fi
    if ! curl --max-time 10 -sS -o /dev/null -X POST \
            -H 'Content-Type: application/json' \
            -d "{\"msgtype\":\"text\",\"text\":{\"content\":$(json_escape "$content")}}" \
            -- "$WEBHOOK_URL"; then
        echo "pulse-beat: webhook notification failed (non-fatal): $content" >&2
    fi
    return 0
}

# --- 1. non-blocking exclusive lock. Busy means a previous beat is still
# running -- redundant, not an error (systemd oneshot normally prevents
# overlap anyway; this is defense in depth). Directory creation mirrors
# run-soil.sh's RUNTIME_DIR handling; unlike run-soil.sh's credential
# directory, this one is not a trust boundary (it only ever holds the lock
# file) -- run-soil.sh (invoked in step 3) re-asserts strict root:soil 0710
# ownership on this same directory before it ever touches a credential, so
# a failure here to chown to root:soil is not fatal to this script.
if ! install -d -o root -g soil -m 0710 -- "$RUNTIME_DIR" 2>/dev/null; then
    mkdir -p -- "$RUNTIME_DIR"
    chmod 0710 -- "$RUNTIME_DIR" 2>/dev/null || true
fi
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    exit 0
fi

# --- 2. webhook lookup. Same parsing discipline as run-soil.sh: assignment-
# only lines, quote stripping, reject executable syntax. Never sourced.
# Never exported into the beat's child environment -- run-soil.sh's own
# allowlist already excludes MERISTEM_WEBHOOK_URL from the supervisor
# environment (tests/test_credential_adapter.py asserts this), and this
# wrapper uses the value only for its own curl call. A missing/invalid
# webhook is non-fatal: notification is best-effort, the beat itself is not.
if [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line#"${line%%[![:space:]]*}"}"
        [[ -z "$line" || "$line" == \#* ]] && continue
        [[ "$line" =~ ^(export[[:space:]]+)?([A-Z_][A-Z0-9_]*)=(.*)$ ]] || continue
        name="${BASH_REMATCH[2]}"
        [[ "$name" == "MERISTEM_WEBHOOK_URL" ]] || continue
        raw="${BASH_REMATCH[3]}"
        case "$raw" in
            \'*\')
                if [[ ${#raw} -ge 2 && "${raw: -1}" == "'" ]]; then
                    value="${raw:1:${#raw}-2}"
                else
                    echo "pulse-beat: malformed quoting on webhook source value, ignoring" >&2
                    continue
                fi
                ;;
            \"*\")
                if [[ ${#raw} -ge 2 && "${raw: -1}" == '"' ]]; then
                    value="${raw:1:${#raw}-2}"
                else
                    echo "pulse-beat: malformed quoting on webhook source value, ignoring" >&2
                    continue
                fi
                ;;
            *) value="$raw" ;;
        esac
        case "$value" in
            *'$('*|*'`'*|*';'*|*'|'*|*'&'*|*'<'*|*'>'*)
                echo "pulse-beat: executable syntax in webhook source value, ignoring" >&2
                continue
                ;;
        esac
        WEBHOOK_URL="$value"
    done < "$ENV_FILE"
else
    echo "pulse-beat: webhook source file missing or symlinked, notifications disabled: $ENV_FILE" >&2
fi

# --- ignition-status is read-only and needs no credential (§1.2's sole
# evaluation point); call supervisor directly rather than through
# run-soil.sh's credential-gated path. Best-effort: a failure here must
# never affect the beat. ---
ignition_first_line() {
    env -i PATH="${PATH:-/usr/bin:/bin}" PYTHONPATH="$REPO_ROOT" \
        python3 -m substrate.supervisor ignition-status 2>/dev/null | head -n1
}

PRE_IGNITION="$(ignition_first_line || true)"

# --- 3. run the beat. `set -e` must not abort here -- the real exit code is
# the point, not a script abort. ---
set +e
"$SCRIPT_DIR/run-soil.sh" --mode "${MERISTEM_MODEL_MODE:-agnes-temporary}" \
    -- manual-cycle --autonomous "${TASK_ARG[@]}"
rc=$?
set -e

# --- 4. policy on rc. ---
if [[ "$rc" -eq 0 || "$rc" -eq 1 ]]; then
    if [[ "$rc" -eq 0 ]]; then
        POST_IGNITION="$(ignition_first_line || true)"
        if [[ -n "$PRE_IGNITION" && -n "$POST_IGNITION" && "$PRE_IGNITION" != "$POST_IGNITION" ]]; then
            notify "meristem ignition changed: ${PRE_IGNITION} -> ${POST_IGNITION}"
        fi
    fi
    exit 0
fi

# Any other rc (2 refused, 3 latched, 4 breaker tripped, or unexpected):
# stop the timer and notify the human. CRITICAL: every step from here on is
# individually error-tolerant -- the human must get the ping even if
# systemctl or curl are themselves broken.
set +e

if command -v systemctl >/dev/null 2>&1; then
    if ! systemctl disable --now meristem-pulse.timer >/dev/null 2>&1; then
        echo "pulse-beat: systemctl disable --now meristem-pulse.timer failed (non-fatal)" >&2
    fi
else
    echo "pulse-beat: systemctl unavailable, could not disable the timer (non-fatal)" >&2
fi

case "$rc" in
    2) meaning="refused: needs operator (task guarded / latch unlocatable / dangling rollback / bad invocation)" ;;
    3) meaning="panic latch engaged" ;;
    4) meaning="breaker tripped: consecutive futile autonomous beats" ;;
    *) meaning="unexpected exit code" ;;
esac
host="$(hostname 2>/dev/null || echo unknown-host)"
stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown-time)"
notify "meristem pulse stopped: rc=${rc} (${meaning}) host=${host} at ${stamp}"
exit "$rc"
