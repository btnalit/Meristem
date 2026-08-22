# Cross-task failure aggregation

Invoke the journal-query organ with op=aggregate_cross_task on a journal
containing rejections for two distinct tasks that share a failure class
(e.g., closure-budget). Verify the result includes the shared class with
both tasks listed, and that a class appearing in only one task is excluded.
