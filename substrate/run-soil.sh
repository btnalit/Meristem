#!/usr/bin/env bash
# Formal soil credential adapter / operator wrapper.
# External env -> ephemeral soil credential file -> soil gateway -> supervisor.
set -euo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
ENV_FILE="${MERISTEM_ENV_FILE:-/RSI/meristem-env}"
MODE="agnes-temporary"

usage() {
    cat <<'EOF'
usage: substrate/run-soil.sh [--mode agnes-temporary|openrouter-free|sensenova] [-- supervisor args...]

The mode is explicit. Credentials are sourced from /RSI/meristem-env (or
MERISTEM_ENV_FILE), materialized only for the soil gateway, and deleted on exit.
Arguments after -- are passed only to substrate.supervisor.
EOF
}

while (($#)); do
    case "$1" in
        --mode)
            (($# >= 2)) || { usage >&2; exit 2; }
            MODE="$2"
            shift 2
            ;;
        --)
            shift
            break
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

case "$MODE" in
    agnes-temporary) CREDENTIAL_VAR="AGNES_API_KEY" ;;
    openrouter-free) CREDENTIAL_VAR="OPENROUTER_API_KEY" ;;
    sensenova)       CREDENTIAL_VAR="SENSENOVA_API_KEY" ;;
    *)
        echo "unsupported soil model mode: $MODE" >&2
        exit 2
        ;;
esac

if [[ "$(id -u)" -ne 0 ]]; then
    echo "soil runner must start as root so it can create the soil-owned credential file" >&2
    exit 2
fi

if [[ ! -f "$ENV_FILE" || -L "$ENV_FILE" ]]; then
    echo "credential environment file is missing or symlinked: $ENV_FILE" >&2
    exit 2
fi
ENV_STAT="$(stat -c '%u:%g:%a' -- "$ENV_FILE")"
[[ "$ENV_STAT" == "0:0:600" ]] || {
    echo "credential environment file must be root:root 0600" >&2
    exit 2
}

# An inherited provider variable must never satisfy this adapter. Only the
# explicitly checked external source may provide the selected credential.
unset AGNES_API_KEY OPENROUTER_API_KEY SENSENOVA_API_KEY
# The external file is a data-only assignment file. Do not source it: sourcing
# would turn a credential source into an arbitrary root shell execution path.
declare -A SOURCE_VALUES=()
while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    if [[ "$line" =~ ^(export[[:space:]]+)?([A-Z_][A-Z0-9_]*)=(.*)$ ]]; then
        name="${BASH_REMATCH[2]}"
        raw="${BASH_REMATCH[3]}"
        case "$raw" in
            \'*\')
                [[ "${raw: -1}" == "\'" ]] || { echo "invalid credential source value" >&2; exit 2; }
                value="${raw:1:${#raw}-2}"
                ;;
            \"*\")
                [[ "${raw: -1}" == \" ]] || { echo "invalid credential source value" >&2; exit 2; }
                value="${raw:1:${#raw}-2}"
                ;;
            *) value="$raw" ;;
        esac
        if [[ "$value" == *'$('* || "$value" == *'`'* || "$value" == *';'* ||
              "$value" == *'|'* || "$value" == *'&'* || "$value" == *'<'* ||
              "$value" == *'>'* ]]; then
            echo "executable syntax in credential source" >&2
            exit 2
        fi
        case "$name" in
            MERISTEM_VAULT|MERISTEM_CONTROL|MERISTEM_PUBLISH|MERISTEM_WEBHOOK_URL|\
            AGNES_API_KEY|OPENROUTER_API_KEY|SENSENOVA_API_KEY)
                SOURCE_VALUES["$name"]="$value"
                ;;
            *)
                echo "unsupported variable in credential source" >&2
                exit 2
                ;;
        esac
    else
        echo "credential source must contain assignments/comments only" >&2
        exit 2
    fi
done < "$ENV_FILE"
CREDENTIAL_VALUE="${SOURCE_VALUES[$CREDENTIAL_VAR]:-}"
[[ -n "$CREDENTIAL_VALUE" ]] || {
    echo "required provider credential is absent for selected mode" >&2
    exit 2
}
case "$CREDENTIAL_VALUE" in
    *$'\n'*|*$'\r'*)
        echo "provider credential contains a line break" >&2
        exit 2
        ;;
esac

RUNTIME_DIR="${MERISTEM_RUNTIME_DIR:-/run/meristem}"
install -d -o root -g root -m 0700 -- "$RUNTIME_DIR"
while IFS= read -r -d '' stale; do
    if [[ -L "$stale" || ! -f "$stale" ]]; then
        echo "unsafe stale credential artifact in runtime directory" >&2
        exit 2
    fi
    rm -f -- "$stale" || {
        echo "cannot remove stale credential artifact" >&2
        exit 2
    }
done < <(find "$RUNTIME_DIR" -maxdepth 1 -type f -name 'credential.*' -print0)
if find "$RUNTIME_DIR" -maxdepth 1 -name 'credential.*' -print -quit | grep -q .; then
    echo "stale credential artifact remains in runtime directory" >&2
    exit 2
fi
CREDENTIAL_FILE="$(mktemp "$RUNTIME_DIR/credential.XXXXXX")"
CHILD_PID=""

forward_signal() {
    local signal_name="$1"
    local rc="$2"
    if [[ -n "$CHILD_PID" ]] && kill -0 "$CHILD_PID" 2>/dev/null; then
        kill -"$signal_name" "$CHILD_PID" 2>/dev/null || true
        wait "$CHILD_PID" 2>/dev/null || true
    fi
    cleanup "$rc"
}

cleanup() {
    local rc="${1:-$?}"
    trap - EXIT HUP INT TERM
    if ! rm -f -- "$CREDENTIAL_FILE"; then
        echo "credential cleanup failed" >&2
        rc=1
    fi
    exit "$rc"
}
trap 'cleanup "$?"' EXIT
trap 'forward_signal TERM 143' TERM
trap 'forward_signal INT 130' INT
trap 'forward_signal HUP 129' HUP

umask 077
printf '%s\n' "$CREDENTIAL_VALUE" > "$CREDENTIAL_FILE"
chown soil:soil -- "$CREDENTIAL_FILE"
chmod 0600 -- "$CREDENTIAL_FILE"
unset CREDENTIAL_VALUE

# Only the bounded runtime environment reaches supervisor. Source-file
# variables and all provider keys are intentionally excluded.
RUN_ENV=(
    "PATH=${PATH:-/usr/bin:/bin}"
    "PYTHONPATH=$REPO"
    "HOME=${HOME:-/root}"
    "MERISTEM_MODEL_MODE=$MODE"
    "MERISTEM_CREDENTIALS_FILE=$CREDENTIAL_FILE"
)
# MERISTEM_VAULT is a soil runtime path, not a provider secret. It may come
# from the checked source file or an explicitly supplied parent environment,
# and is visible to soil supervisor/gateway only; supervisor strips it from
# the worker/seed environment.
VAULT_PATH="${SOURCE_VALUES[MERISTEM_VAULT]:-${MERISTEM_VAULT:-}}"
[[ -n "$VAULT_PATH" ]] && RUN_ENV+=("MERISTEM_VAULT=$VAULT_PATH")
cd -- "$REPO"
env -i "${RUN_ENV[@]}" python3 -m substrate.supervisor "$@" &
CHILD_PID=$!
if wait "$CHILD_PID"; then
    rc=0
else
    rc=$?
fi
CHILD_PID=""
exit "$rc"
