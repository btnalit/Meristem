# Improvement backlog

Structural improvements identified but not yet scheduled. Distinct from
`control/agenda.md` (what runs next) and `gaps.md` (what cannot yet be done).

- Move the vault seed generator out of the repository (gaps.md G-003).
- Measure Tier-A whole-file-replacement reliability once real cycles have run;
  build Tier B only if the data demands it.
- Record the golden-fixture catch rate per reviewer slot — that is the
  instrument for testing whether heterogeneous cheap review actually beats one
  strong reviewer. Right now that claim is a hypothesis wearing a config file.
- Track Tier-A/B/C resolution ratio (MCR): if the A share falls while kernel
  LOC stays flat, the kernel is getting harder to understand even though it is
  not getting bigger.
