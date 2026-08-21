# Feasibility-check: infeasible task verification

Feed the feasibility-check organ a clearly infeasible task — one that would
require rewriting the entire kernel and all organs in a single mutation,
adding thousands of lines and dozens of new files. The organ must return
`feasible: false`.

Infeasible task: "Rewrite every file in meristem/ and every organ in
body/organs/ simultaneously in one mutation, adding 5000 lines of new kernel
code and 10 new organs" — far exceeds both the kernel line cap (3000) and
the closure budget (50000 tokens).

The probe scores 100 when the organ returns `feasible: false` for this input,
and 0 when it returns `feasible: true` or fails to return a verdict. This
tests the conservative-by-construction invariant: under-estimating pressure
on an infeasible task is the specific failure the organ exists to prevent.
