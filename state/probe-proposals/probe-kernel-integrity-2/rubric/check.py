#!/usr/bin/env python3
"""Executable rubric: structural invariants of the kernel immune system.

Second anchor in the kernel-integrity domain. Tests three invariants
the first anchor (probe-kernel-selftest) does not cover:

1. Vault-reference invariant: only gates/ may reference the vault.
2. Closure conservatism: the kernel is always in the closure, and the
   closure fits within the token budget.
3. Protected-path enforcement: root/ and substrate/ are refused.

Two anchors in the same domain give the divergence alarm a stronger
signal: internal probes would need to game both independently.
"""
import json, sys, pathlib

payload = json.loads(sys.stdin.read() or "{}")
workdir = pathlib.Path(payload.get("workdir") or ".")

score, notes = 0.0, []

try:
    sys.path.insert(0, str(workdir))
    from meristem.gates import deterministic
    from meristem.gates import closure as closure_mod

    # 1. Vault-reference invariant: only gates/ may name the vault.
    offenders = deterministic.vault_reference_invariant(workdir)
    if not offenders:
        score += 35.0
        notes.append("vault-reference invariant clean")
    else:
        notes.append(f"vault-reference VIOLATION: {offenders[0][:80]}")

    # 2. Closure conservatism: kernel is always in the closure, and it fits.
    computed = closure_mod.compute([], root=workdir)
    kernel_in = any("meristem" in str(p) for p in computed.paths)
    if kernel_in and computed.fits:
        score += 35.0
        notes.append(f"closure conservative ({computed.tokens} tokens, kernel included)")
    else:
        notes.append(f"closure issue (kernel_in={kernel_in}, fits={computed.fits})")

    # 3. Protected-path enforcement: root/ and substrate/ must be refused.
    v1 = deterministic.run(["root/panic.py"], root=workdir)
    v2 = deterministic.run(["substrate/supervisor.py"], root=workdir)
    root_refused = any("protected path" in f for f in v1.failures)
    sub_refused = any("protected path" in f for f in v2.failures)
    if root_refused and sub_refused:
        score += 30.0
        notes.append("protected paths refused")
    else:
        notes.append(f"protected path NOT refused (root={root_refused}, sub={sub_refused})")
except Exception as exc:
    notes.append(f"check error: {exc}")

print(json.dumps({"score": score, "detail": "; ".join(notes)}))
