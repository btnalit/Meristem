# journal-query

Cross-cycle task aggregation: groups journal entries by task text and returns
counts, rejection reasons, and timestamps. Enables the circuit breaker to
detect repeated rejection patterns without carrying aggregation logic in the
kernel. Supports `aggregate_by_task` (all tasks, full aggregation with fault
exclusion and canary tracking) and `query` (single-task counts matching the
breaker's interface). Selfcheck exercises fault exclusion, case-insensitive
task matching, and reason collection with known fixtures.
