"""meristem.llm -- request one call through the soil's IPC boundary
(SS10.1 / SS8.1.3).

The seed holds no API key and never reads the soil-private model policy.
A call is forwarded to a soil-provided gateway subprocess; the seed learns
only one of three outcomes -- allowed / refused / deferred -- per the
call_result contract in seed/model-interface.json. It never learns
remaining quota, retry counts, or slot order.
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
_GATEWAY_TIMEOUT_SECONDS = 30

#: The soil is expected to inject the real entrypoint via
#: MERISTEM_MODEL_GATEWAY. The default below is a placeholder convention --
#: see delivery report for the integration gap this leaves open.
_DEFAULT_GATEWAY = [sys.executable, "-m", "substrate.model_gateway"]


@dataclasses.dataclass(frozen=True)
class CallResult:
    status: str  # "allowed" | "refused" | "deferred"
    content: str | None = None
    reason: str | None = None


def _roles_available() -> set[str]:
    doc = read_json_readonly(SEED_DIR / "model-interface.json") or {}
    return set(doc.get("roles_available_to_seed", []) or [])


def _gateway_argv() -> list[str]:
    raw = os.environ.get("MERISTEM_MODEL_GATEWAY")
    return raw.split() if raw else list(_DEFAULT_GATEWAY)


def call_model(role: str, prompt: str) -> CallResult:
    """Ask the soil to make one model call. The seed only branches on the
    three-state result; it never touches a secret or a quota number.
    """
    if role not in _roles_available():
        return CallResult(status="refused", reason=f"role not available to seed: {role!r}")

    try:
        proc = subprocess.run(
            _gateway_argv(),
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
