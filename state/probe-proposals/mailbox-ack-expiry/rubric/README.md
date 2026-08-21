# Rubric location: vault

The scoring rubric for this probe MUST be authored directly in the eval vault,
not in this repository.

Per P-030 and Principle 4 of the constitution, rubrics are physically invisible
to the mutation engine. The vault is outside the repository and outside the
worktree. Only `meristem/gates/` may reference the vault path.

When this proposal is promoted into the vault, the gate layer will create:

    <vault>/internal/active/mailbox-ack-expiry/
    ├── probe.json
    ├── statement/task.md
    └── rubric/check.py    # authored in the vault, never in the repo

The check.py rubric receives `{"workdir": "...", "probe": "..."}` on stdin
and returns `{"score": float, "detail": "..."}` on stdout. It scores by
behaviour: it invokes the organ over its ABI and checks the response shape,
not the organ's source code.

**Do not create check.py here.** This directory exists only to document where
the rubric belongs. The vault is the authoritative home for scoring logic.
