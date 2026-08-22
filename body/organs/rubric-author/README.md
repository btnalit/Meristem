# Rubric Author

Writes probe rubrics directly into the vault, ensuring no scoring logic
resides in the repository. The organ receives the vault path as an input
parameter — it never references the vault by env var or constant, preserving
the vault-reference invariant. This closes P-030 structurally: rubrics
go to the vault, not to state/probe-proposals/.
