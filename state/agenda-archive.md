# Completed agenda items

Moved out of control/agenda.md, which is sent to the engine in full on
every mutation: finished tasks are history, and history does not belong
in a prompt that is charged by the token. The journal remains the record
of what was actually done.

- [x] Make body/organs/memory-graph/extract.py carry the fields the edge rules need. Every node keeps id, kind, title and last_seen_cycle, and additionally: pattern and gap nodes carry "text" holding the body of their entry; cycle nodes carry "changed" holding the list of files that cycle changed and "why" holding its task text. Verify by running the build op and confirming the edge count is greater than zero. Change nothing outside body/organs/memory-graph/.
- [x] Make the dating in body/organs/memory-graph/extract.py actually take effect: every pattern and gap node currently gets last_seen_cycle 0. Find the highest cycle number among journal records whose "why" text contains that node's id, and use it; when none mentions it, use the highest cycle whose changed-file list includes the register file that holds it. Verify by building and confirming that P-018 has a higher last_seen_cycle than P-001. Change nothing outside body/organs/memory-graph/.
