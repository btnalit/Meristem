"""Immune tier (layer 4): the gates, and the code that defines admission.

Everything under this package is admission-defining. A mutation that relaxes
any of it is a gate weakening and must be rejected by review, per the
constitution's monotonicity clause:

    Meristem may improve the immune system; it may not weaken it.

This package is also the only place allowed to reference the eval vault
(enforced by the vault-reference invariant in deterministic.py).
"""

from . import closure, deterministic, germline_validate, probes, review

__all__ = ["closure", "deterministic", "germline_validate", "probes", "review"]
