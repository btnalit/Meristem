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
    """Tasks that already produced a candidate, read from the journal.

    Completion is a RECORD, not an edit. Marking the agenda file in place made
    the loop dirty its own checkout every cycle without ever committing --
    which blocked git operations and left the tree in a state no transaction
    owned. The journal already knows what succeeded; ask it.
    """
    return {
        row.get("why", "")
        for row in read_jsonl(journal_path)
        if row.get("kind") == "cycle" and row.get("outcome") == "candidate"
    }


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
