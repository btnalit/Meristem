# Feasibility-check: feasible task verification

Feed the feasibility-check organ a clearly feasible task — a small one-file
edit well within both the kernel line cap and the closure budget. The organ
must return `feasible: true`.

Feasible task: "Fix a typo in README.md" — touches one file, adds zero
kernel lines, and the closure impact is one file plus the kernel baseline.

The probe scores 100 when the organ returns `feasible: true` for this input,
and 0 when it returns `feasible: false` or fails to return a verdict. This
tests the organ's ability to recognise work that fits within the caps — the
basic positive case.
