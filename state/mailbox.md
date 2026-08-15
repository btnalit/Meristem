# Human mailbox

Items awaiting a human decision. The loop does not block on these — it queues
them and continues with layer 0–1 work. This is what turns the human gate from
a synchronous wait into an asynchronous queue.

Empty.
- PARKED: Write body/organs/memory-graph/edges.py: given the node list from extract.py, derive typed edges. A pattern that names another pattern id in its text gets a "relates_to" edge. A cycle whose "why" text names a gap or pattern id gets "addresses". A cycle that changed a file under body/organs/<name>/ gets "touched" to that organ node. An organ gets "measured_by" to each probe id its organ.json declares. Return a list of {"from", "to", "type", "weight"} with weight 1.0. Change nothing outside body/organs/memory-graph/. (rejected in cycles: 40, 41, 42)
