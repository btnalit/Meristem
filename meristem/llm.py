"""meristem.llm -- request one call through the soil's IPC boundary
(SS10.1 / SS8.1.3).

The seed holds no API key and never reads the soil-private model policy.
A call is forwarded to a soil-provided gateway subprocess; the seed learns
only one of three outcomes -- allowed / refused / deferred -- per the
call_result contract in seed/model-interface.json. It never learns
remaining quota, retry counts, or slot order.

The seed receives only ``MERISTEM_MODEL_SOCKET``. A soil-owned gateway server
reads the external credential file and exposes only the three-state response
contract over a private Unix socket. The credential pointer is never present
in the seed environment.
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys

from meristem import SEED_DIR, read_json_readonly

#: Spine-side wall-clock bound on the gateway round trip. Not the soil's
#: retry/backoff policy, which stays invisible to the seed (SS8.1.3).
_GATEWAY_TIMEOUT_SECONDS = 4000  # 4*900s provider attempts + 15+30+60 backoff, with margin


@dataclasses.dataclass(frozen=True)
class CallResult:
    status: str  # "allowed" | "refused" | "deferred"
    content: str | None = None
    reason: str | None = None


def _roles_available() -> set[str]:
    doc = read_json_readonly(SEED_DIR / "model-interface.json") or {}
    return set(doc.get("roles_available_to_seed", []) or [])


def call_model(role: str, prompt: str) -> CallResult:
    """Ask the soil to make one model call. The seed only branches on the
    three-state result; it never touches a secret or a quota number.

    The soil must inject the real gateway entrypoint via
    MERISTEM_MODEL_GATEWAY (see docs/MERISTEM-V5-SPEC.md SS13.3, table C).
    No default entrypoint is guessed here anymore: a wrong guess and an
    absent gateway would both surface to the seed as the identical
    "refused" outcome -- the hardest kind of integration fault to diagnose,
    because a permanently-refusing gateway and a gateway that does not
    exist look the same. A missing env var is therefore its own distinct,
    greppable failure: fail closed with reason "gateway_not_injected" and a
    one-line stderr marker, instead of silently trying a guessed subprocess.
    """
    if role not in _roles_available():
        return CallResult(status="refused", reason=f"role not available to seed: {role!r}")

    raw = os.environ.get("MERISTEM_MODEL_GATEWAY")
    if not raw:
        print("GATEWAY_NOT_INJECTED", file=sys.stderr)
        return CallResult(status="refused", reason="gateway_not_injected")

    try:
        proc = subprocess.run(
            raw.split(),
            input=json.dumps({"role": role, "prompt": prompt}),
            capture_output=True,
            text=True,
            timeout=_GATEWAY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CallResult(status="refused", reason=f"gateway_unavailable: {exc}")

    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        return CallResult(status="refused", reason=f"gateway_bad_response: {exc}")

    status = payload.get("status") if isinstance(payload, dict) else None
    if status not in ("allowed", "refused", "deferred"):
        return CallResult(status="refused", reason=f"gateway_bad_status: {status!r}")
    return CallResult(status=status, content=payload.get("content"), reason=payload.get("reason"))


def parse_file_map(content: str) -> dict[str, str]:
    """Model reply -> {path: new content}. The model is instructed
    (engine.build_context) to reply with exactly one JSON object; a
    malformed reply yields an empty map rather than a guessed partial parse.
    """
    try:
        parsed = json.loads(content)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items()}
