"""Journal-reading helpers: pure functions over state/journal.jsonl.

Extracted from loop.py so the loop module imports them rather than
defining them inline. These functions read the append-only journal and
derive task state -- nothing here mutates.
"""

from __future__ import annotations

from . import read_jsonl, read_text


def next_cycle(journal_path) -> int:
    return 1 + max(
        (row.get("cycle", 0) for row in read_jsonl(journal_path) if row.get("kind") == "cycle"),
        default=0,
    )


def done_tasks(journal_path) -> set[str]:
    """Tasks whose candidate was actually promoted (or never canary-rejected).

    A candidate that the canary rejects is NOT done -- the task must be
    retried. Set algebra: (candidates - canary_rejects) | promoted.
    Backward-compatible: old candidates with no matching canary_reject
    record stay done.
    """
    candidates: set[str] = set()
    canary_rejects: set[str] = set()
    promoted: set[str] = set()
    for row in read_jsonl(journal_path):
        kind = row.get("kind", "")
        why = row.get("why", "")
        if kind == "cycle" and row.get("outcome") == "candidate":
            candidates.add(why)
        elif kind == "canary_reject" and why:
            canary_rejects.add(why)
        elif kind == "promoted" and why:
            promoted.add(why)
    return (candidates - canary_rejects) | promoted


def failure_history(journal_path, task: str, limit: int = 3) -> str:
    """Past rejection reasons for the same task, most recent first.

    Returns a formatted string suitable for engine.propose(extra=...).
    Empty string when no prior failures exist for this task.
    """
    entries: list[tuple[str, list[str]]] = []
    for row in read_jsonl(journal_path):
        kind = row.get("kind", "")
        why = row.get("why", "")
        if why != task:
            continue

        if kind == "cycle" and row.get("outcome") == "rejected":
            reasons: list[str] = []
            reason_text = row.get("reason", "")
            if reason_text and not reason_text.startswith("review rejected"):
                reasons.append(reason_text[:200])
            for rej in row.get("rejected_by") or []:
                slot = rej.get("slot", "unknown")
                for r in rej.get("reasons") or []:
                    reasons.append(f"{slot}: {r[:200]}")
            if reasons:
                entries.append((str(row.get("cycle", "?")), reasons))

        elif kind == "canary_reject":
            reason_text = row.get("reason", "")
            if reason_text:
                entries.append(("canary", [reason_text[:200]]))

    if not entries:
        return ""

    recent = entries[-limit:]
    lines = ["Previous attempts at this exact task were rejected:"]
    for cyc, reasons in recent:
        for r in reasons:
            lines.append(f"- cycle {cyc}: {r}")
    lines.append("")
    lines.append("Study these objections carefully. Do NOT repeat these mistakes.")

    text = "\n".join(lines)
    return text[:4000] + "\n[truncated]" if len(text) > 4000 else text


def parked_tasks(journal_path, repo_path) -> set[str]:
    """Tasks that are parked: have a 'parked' journal cycle record AND still
    appear in state/mailbox.md.

    A human clears parking by removing the mailbox entry. The journal record
    persists (append-only, never rewritten) but the task is then unparked and
    can be retried. Without this check, take_task would re-take a parked task
    every cycle, so parking would stall the agenda instead of advancing it.
    """
    parked_in_journal = {
        row.get("why", "")
        for row in read_jsonl(journal_path)
        if row.get("kind") == "cycle" and row.get("outcome") == "parked"
    }
    if not parked_in_journal:
        return set()
    mailbox_text = read_text(repo_path / "state" / "mailbox.md")
    return {
        task
        for task in parked_in_journal
        if any(task in line for line in mailbox_text.splitlines())
    }
