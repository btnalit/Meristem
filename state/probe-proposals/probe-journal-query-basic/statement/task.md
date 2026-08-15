# Probe: journal-query basic

The journal-query organ must answer two questions about `state/journal.jsonl`
over its stdin/stdout ABI:

1. **rejections_for** — given a task name, return the count of cycle records
   whose `why` equals that task and whose `outcome` is `"rejected"`, excluding
   any cycle that also has a matching fault record (a fault is a mechanism
   failure, not a gate verdict).

2. **faults_for** — given a task name, return the count of cycle records that
   do have a matching fault record.

The organ must take a `workdir` from its args (defaulting to the current
 directory) so it reads the journal from the tree being tested, not a
 hard-coded path.

A correct implementation must distinguish the two: a cycle that is both
 `"rejected"` and has a fault record counts toward `faults_for` and **not**
toward `rejections_for`. An implementation that ignores fault records and
counts every rejection indiscriminately gets that case wrong.
