# Human mailbox

Items awaiting a human decision. The loop does not block on these — it queues
them and continues with layer 0–1 work. This is what turns the human gate from
a synchronous wait into an asynchronous queue.

Empty.
