# Proposals

Tasks the seed proposed for itself. A human promotes a line into
control/agenda.md; the loop never writes to the agenda directly.

- [ ] REPAIR (G-008): Add a journal entry kind for failure aggregator runs. In meristem/loop.py _try_aggregate_failures(), wrap the organ invocation so that success, failure, timeout, and exception all produce a journal entry with kind='aggregation', recording the organ's return value, exit code, and any patterns written. This makes the aggregator's behavior observable and debuggable, closing the gap where successful runs and failures are indistinguishable in the journal.
