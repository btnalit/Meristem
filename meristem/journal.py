"""Journal-reading helpers and task lifecycle: functions over
state/journal.jsonl plus task selection, parking, and aggregation commands.

Extracted from loop.py so the loop module orchestrates rather than
implementing journal-derived logic inline. These functions read the
append-only journal and derive task state. park_task writes the parking
record (append-only, never rewrites); the rest are pure readers.
"""

from __future__ import annotations

from . import append_jsonl, read_jsonl, read_text
from . import breaker as breaker_mod


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
    entries, faulted = [], {r.get("cycle") for r in read_jsonl(journal_path) if r.get("kind") == "fault"}
    for row in read_jsonl(journal_path):
        kind = row.get("kind", "")
        why = row.get("why", "")
        if why != task:
            continue

        if kind == "cycle" and row.get("outcome") == "rejected" and row.get("cycle") not in faulted:
            reasons: list[str] = []
            reason_text = row.get("reason", "")
            if reason_text and not reason_text.startswith("review rejected"):
                reasons.append(reason_text[:500])
            for rej in row.get("rejected_by") or []:
                slot = rej.get("slot", "unknown")
                for r in rej.get("reasons") or []:
                    reasons.append(f"{slot}: {r[:500]}")
            if reasons:
                entries.append((str(row.get("cycle", "?")), reasons))

        elif kind == "canary_reject":
            reason_text = row.get("reason", "")
            if reason_text:
                entries.append(("canary", [reason_text[:500]]))

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


def take_task(journal_path, repo_path, control_path) -> str | None:
    """P0: the human is the first reflect. Tasks come from control/agenda.md,
    which stays pure human-authored source -- the loop never writes to it.

    P1 grows reflect, which proposes its own -- but the seed is not handed a
    parts list; it is handed the capability gap and proposes the decomposition.
    A rejected task stays open, so it is retried. A parked task is skipped
    until a human clears it from state/mailbox.md.
    """
    done = done_tasks(journal_path)
    parked = parked_tasks(journal_path, repo_path)
    for line in read_text(control_path / "agenda.md").splitlines():
        line = line.strip()
        if line.startswith("- [ ] "):
            task = line[6:].strip()
            if task not in done and task not in parked:
                return task
    return None


def park_task(task: str, journal_path, repo_path) -> str:
    """Park a task that has been rejected too many times.

    Writes the mailbox entry and the journal record. Returns the reason
    string so the caller can notify (webhook) and print.

    Unbounded retry on the same task is not progress, it is a loop -- the
    breaker turns that loop into a stop. This function is the write half;
    the decision to park lives in breaker.should_park().
    """
    rejection_cycles = [
        row.get("cycle")
        for row in read_jsonl(journal_path)
        if row.get("kind") == "cycle"
        and row.get("why") == task
        and row.get("outcome") == "rejected"
    ]
    canary_count = breaker_mod.canary_rejects_for(task)
    cycle = next_cycle(journal_path)
    parts = []
    if rejection_cycles:
        parts.append(f"rejected in cycles: {', '.join(str(c) for c in rejection_cycles)}")
    if canary_count:
        parts.append(f"canary rejects: {canary_count}")
    reason_str = "; ".join(parts) or "breaker limit reached"
    mailbox = repo_path / "state" / "mailbox.md"
    mailbox.parent.mkdir(parents=True, exist_ok=True)
    with mailbox.open("a", encoding="utf-8") as handle:
        handle.write(f"- PARKED: {task} ({reason_str})\n")
    append_jsonl(journal_path, {
        "kind": "cycle",
        "cycle": cycle,
        "outcome": "parked",
        "why": task,
    })
    return reason_str


def print_agenda(journal_path, repo_path, control_path) -> int:
    """Live status of every agenda item as a VIEW, never an edit.

    agenda.md stays pure human-authored source (P-011) -- marking it in
    place dirtied the checkout every cycle and blocked git twice. State is
    derived from the journal, which already knows it.

    `done` uses done_tasks() -- the same function take_task() uses -- so
    the agenda view and the task selector agree on what is finished. A
    candidate that the canary rejected is NOT done: the task must be
    retried. The old inline check (outcome=='candidate') missed canary
    rejections and showed such tasks as done.
    """
    rows = read_jsonl(journal_path)
    done = done_tasks(journal_path)
    parked, rejects = set(), {}
    for row in rows:
        if row.get("kind") != "cycle":
            continue
        why, outcome = row.get("why", ""), row.get("outcome")
        if outcome == "parked":
            parked.add(why)
        elif outcome == "rejected":
            rejects[why] = rejects.get(why, 0) + 1
    mailbox = read_text(repo_path / "state" / "mailbox.md")
    open_count = 0
    for line in read_text(control_path / "agenda.md").splitlines():
        line = line.strip()
        if not line.startswith("- [ ] "):
            continue
        task = line[6:].strip()
        n = rejects.get(task, 0)
        if task in done:
            mark = "done"
        elif task in parked and task in mailbox:
            mark = "PARKED (clear it from mailbox.md to retry)"
        else:
            open_count += 1
            mark = "next" if open_count == 1 else "open"
            if n:
                mark += f" ({n} rejection{'s' if n > 1 else ''})"
        print(f"  [{mark:<12}] {task[:88]}")
    return 0


def print_utility(journal_path) -> int:
    """Print per-organ utility: total invocations, successful invocations,
    and the cycle it was last used.

    Reads only state/journal.jsonl -- the same append-only record the ledger
    and germline.invoke write to. An organ nobody calls is a pruning candidate,
    and until this exists no pruning decision can rest on evidence.
    """
    rows = [r for r in read_jsonl(journal_path) if r.get("kind") == "organ_call"]
    if not rows:
        print("no organ calls recorded")
        return 0

    by_organ: dict[str, dict] = {}
    for row in rows:
        callee = row.get("callee", "?")
        if callee not in by_organ:
            by_organ[callee] = {"total": 0, "success": 0, "last_cycle": 0}
        by_organ[callee]["total"] += 1
        if row.get("success"):
            by_organ[callee]["success"] += 1
        cycle = row.get("cycle", 0)
        if cycle > by_organ[callee]["last_cycle"]:
            by_organ[callee]["last_cycle"] = cycle

    print(f"{'organ':20s} {'total calls':>12s} {'successful':>12s} {'last cycle':>12s}")
    print(f"{'-----':20s} {'-----------':>12s} {'----------':>12s} {'----------':>12s}")
    for organ in sorted(by_organ):
        info = by_organ[organ]
        print(f"{organ:20s} {info['total']:12d} {info['success']:12d} {info['last_cycle']:12d}")
    return 0


def print_spend(journal_path) -> int:
    """Print total calls and tokens grouped by role and by model.

    Reads only state/journal.jsonl -- the same append-only record the ledger
    writes to. No new state, no new files; the data was already collected,
    it just was not queryable.
    """
    rows = [r for r in read_jsonl(journal_path) if r.get("kind") == "usage"]
    if not rows:
        print("no usage recorded")
        return 0

    by_role: dict[str, dict[str, int]] = {}
    by_model: dict[str, dict[str, int]] = {}
    for row in rows:
        role = row.get("role", "?")
        model = row.get("model", "?")
        prompt = int(row.get("prompt_tokens", 0))
        completion = int(row.get("completion_tokens", 0))
        reasoning = int(row.get("reasoning_tokens", 0))
        total = prompt + completion + reasoning
        for key, bucket in ((role, by_role), (model, by_model)):
            entry = bucket.setdefault(
                key, {"calls": 0, "prompt": 0, "completion": 0,
                      "reasoning": 0, "total": 0})
            entry["calls"] += 1
            entry["prompt"] += prompt
            entry["completion"] += completion
            entry["reasoning"] += reasoning
            entry["total"] += total

    total_calls = len(rows)
    total_prompt = sum(int(r.get("prompt_tokens", 0)) for r in rows)
    total_completion = sum(int(r.get("completion_tokens", 0)) for r in rows)
    total_reasoning = sum(int(r.get("reasoning_tokens", 0)) for r in rows)
    total_tokens = total_prompt + total_completion + total_reasoning

    print(f"total calls    : {total_calls}")
    print(f"total tokens   : {total_tokens} "
          f"(prompt {total_prompt}, completion {total_completion}, "
          f"reasoning {total_reasoning})")

    def _table(title: str, bucket: dict) -> None:
        print(f"\n{title}")
        print(f"{'name':24s} {'calls':>6s} {'prompt':>8s} "
              f"{'compl':>8s} {'reason':>8s} {'total':>8s}")
        print(f"{'----':24s} {'-----':>6s} {'------':>8s} "
              f"{'-----':>8s} {'------':>8s} {'-----':>8s}")
        for name in sorted(bucket):
            e = bucket[name]
            print(f"{name:24s} {e['calls']:6d} {e['prompt']:8d} "
                  f"{e['completion']:8d} {e['reasoning']:8d} {e['total']:8d}")

    _table("By role", by_role)
    _table("By model", by_model)
    return 0
