# feasibility-check

Estimates the core and closure pressure impact of a proposed task before it
enters the agenda, rejecting tasks that would exceed the kernel line cap
(3000 lines) or closure token budget (50000 tokens). This reduces wasted
cycles by catching infeasible tasks before any model call is made.

Conservative by construction: over-estimating impact is acceptable (a false
rejection can be retried), but under-estimating is not (a false acceptance
wastes a cycle).

## Input

    {"op": "check", "task": "description of the proposed task", "repo_path": "/path/to/repo"}

## Output

    {
      "feasible": true,
      "core_pressure": 0.95,
      "closure_pressure": 0.78,
      "estimated_core_loc": 2850,
      "estimated_closure_tokens": 39000,
      "current_core_loc": 2840,
      "core_delta": 10,
      "closure_delta": 1200,
      "reason": "within caps"
    }

## Retirement path

When the kernel's own `estimate_closure` function in loop.py is externalized
or when a more sophisticated pressure estimator replaces this organ, this
organ can be pruned. Utility is measured by the number of infeasible tasks
caught before a model call was made.

## Probes

A probe must be written before this organ can advance from candidate to
calibrate. The probe should feed known feasible and infeasible tasks and
verify the organ's verdict matches expectations.
