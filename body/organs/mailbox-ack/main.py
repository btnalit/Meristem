#!/usr/bin/env python3
"""Mailbox acknowledgment protocol organ.

Provides structured tracking of mailbox entries with timestamp, status,
and expiry. Each entry carries:
  - id: unique 12-char hex identifier
  - timestamp: when created (ISO 8601 UTC)
  - status: pending | acknowledged | expired
  - expiry: when the entry should be considered stale (ISO 8601 UTC)

Operations: add, ack, list, expire, selfcheck.
State persists in state.json within the organ's own directory. Each
invocation is a fresh process; the state file is the continuity.

Retirement path: if the kernel grows its own mailbox tracking, this
organ's pattern can be internalized (budget-neutral) or pruned.
Utility is measured by invocation count and success rate from the
journal (see meristem/journal.py print_utility).
"""

import json
import sys
import uuid
import datetime
import pathlib

STATE_FILE = pathlib.Path("state.json")


def utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def load_state() -> dict:
    """Load persistent state. Returns {"entries": []} if none exists."""
    if not STATE_FILE.exists():
        return {"entries": []}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"entries": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _parse_iso(ts: str) -> datetime.datetime:
    """Parse an ISO 8601 timestamp; returns epoch on failure."""
    try:
        return datetime.datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return datetime.datetime.fromtimestamp(0, tz=datetime.UTC)


# --- Pure logic (no I/O) ---

def do_add(state: dict, text: str, ttl_hours: float = 24.0) -> dict:
    """Create a new tracked mailbox entry."""
    text = text.strip()
    if not text:
        return {"ok": False, "error": "text is required"}
    now = datetime.datetime.now(datetime.UTC)
    expiry = now + datetime.timedelta(hours=ttl_hours)
    entry = {
        "id": uuid.uuid4().hex[:12],
        "text": text,
        "timestamp": now.isoformat(timespec="seconds"),
        "status": "pending",
        "expiry": expiry.isoformat(timespec="seconds"),
    }
    state["entries"].append(entry)
    return {"ok": True, "result": entry}


def do_ack(state: dict, entry_id: str) -> dict:
    """Acknowledge an entry by id. Idempotent for already-acknowledged entries."""
    if not entry_id:
        return {"ok": False, "error": "id is required"}
    for entry in state["entries"]:
        if entry["id"] == entry_id:
            if entry["status"] == "expired":
                return {"ok": False, "error": f"entry {entry_id} has expired"}
            if entry["status"] == "acknowledged":
                return {"ok": True, "result": entry}
            entry["status"] = "acknowledged"
            entry["acknowledged_at"] = utc_now()
            return {"ok": True, "result": entry}
    return {"ok": False, "error": f"entry {entry_id} not found"}


def do_list(state: dict, status_filter: str = "") -> dict:
    """Return all entries, optionally filtered by status."""
    entries = state["entries"]
    if status_filter:
        entries = [e for e in entries if e["status"] == status_filter]
    return {"ok": True, "result": {"entries": entries, "count": len(entries)}}


def do_expire(state: dict) -> dict:
    """Mark entries past their expiry as expired. Returns newly expired."""
    now = _parse_iso(utc_now())
    newly_expired = []
    for entry in state["entries"]:
        if entry["status"] != "pending":
            continue
        expiry = _parse_iso(entry.get("expiry", ""))
        if expiry < now:
            entry["status"] = "expired"
            entry["expired_at"] = utc_now()
            newly_expired.append(entry)
    return {"ok": True, "result": {"expired": newly_expired,
                                   "count": len(newly_expired)}}


# --- Op handlers (with persistence) ---

def op_add(state, args):
    result = do_add(state, str(args.get("text", "")),
                    float(args.get("ttl_hours", 24.0)))
    if result["ok"]:
        save_state(state)
    return result


def op_ack(state, args):
    result = do_ack(state, str(args.get("id", "")))
    if result["ok"]:
        save_state(state)
    return result


def op_list(state, args):
    return do_list(state, str(args.get("status", "")))


def op_expire(state, args):
    result = do_expire(state)
    if result["ok"] and result["result"]["count"] > 0:
        save_state(state)
    return result


def op_selfcheck(state, args):
    """Exercise every entry point with tiny fixtures.

    Uses in-memory state only — does not touch the real state file.
    """
    results = []
    failures = []

    # Test add
    ts = {"entries": []}
    r = do_add(ts, "test entry", 1.0)
    results.append({"op": "add", "ok": r["ok"]})
    if not r["ok"]:
        failures.append("add failed")
    entry_id = r.get("result", {}).get("id", "")

    # Test list
    r = do_list(ts)
    results.append({"op": "list", "ok": r["ok"],
                   "count": r["result"]["count"]})
    if not r["ok"] or r["result"]["count"] != 1:
        failures.append("list should show 1 entry")

    # Test ack
    r = do_ack(ts, entry_id)
    results.append({"op": "ack", "ok": r["ok"]})
    if not r["ok"]:
        failures.append("ack failed")

    # Test ack idempotency
    r = do_ack(ts, entry_id)
    results.append({"op": "ack (idempotent)", "ok": r["ok"]})
    if not r["ok"]:
        failures.append("ack should be idempotent")

    # Test expire with past-expiry entry
    ts2 = {"entries": []}
    do_add(ts2, "expired entry", -1.0)
    r = do_expire(ts2)
    results.append({"op": "expire", "ok": r["ok"],
                   "count": r["result"]["count"]})
    if not r["ok"] or r["result"]["count"] != 1:
        failures.append("expire should mark 1 entry")

    # Test ack of expired entry fails
    expired_id = ts2["entries"][0]["id"]
    r = do_ack(ts2, expired_id)
    results.append({"op": "ack expired", "ok": r["ok"]})
    if r["ok"]:
        failures.append("ack of expired entry should fail")

    # Test list with status filter
    r = do_list(ts2, "expired")
    results.append({"op": "list (filter=expired)", "ok": r["ok"],
                   "count": r["result"]["count"]})
    if not r["ok"] or r["result"]["count"] != 1:
        failures.append("list filter=expired should show 1 entry")

    return {"ok": len(failures) == 0, "results": results,
            "failures": failures}


OPS = {
    "add": op_add,
    "ack": op_ack,
    "list": op_list,
    "expire": op_expire,
    "selfcheck": op_selfcheck,
}


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"invalid JSON: {exc}"}))
        return 1

    op = payload.get("op", "")
    handler = OPS.get(op)
    if not handler:
        print(json.dumps({"ok": False, "error": f"unknown op '{op}'"}))
        return 1

    state = load_state()
    result = handler(state, payload)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
