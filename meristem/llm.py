"""The kernel's only model exit: OpenAI-compatible chat/completions.

Roles (mutate / review / score / escalate) map to (endpoint, model, params)
through control/models.toml, so switching providers is a config change and
never a code change. stdlib urllib only -- zero third-party runtime deps.
"""

from __future__ import annotations

import json
import os
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from . import CONTROL, MeristemError


@dataclass
class Completion:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    slot: str = ""
    raw: dict = field(default_factory=dict)


def load_models(path=None) -> dict:
    path = path or CONTROL / "models.toml"
    try:
        with open(path, "rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        raise MeristemError(f"models.toml missing at {path}") from exc


def slots_for(role: str, config: dict | None = None) -> list[dict]:
    """Return the configured slots for a role. `review` may hold several."""
    config = config or load_models()
    entry = config.get("roles", {}).get(role)
    if entry is None:
        raise MeristemError(f"role '{role}' is not configured in models.toml")
    slots = entry.get("slots") or [entry]
    return [dict(slot, id=slot.get("id") or f"{role}:{i}") for i, slot in enumerate(slots)]


def _post(url: str, payload: dict, key: str, timeout: int) -> dict:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def complete(
    role: str,
    messages: list[dict],
    *,
    slot: dict | None = None,
    config: dict | None = None,
    max_tokens: int | None = None,
    attempts: int = 3,
) -> Completion:
    """One structured model call. Raises MeristemError on exhausted retries."""
    slot = slot or slots_for(role, config)[0]
    base = slot["base_url"].rstrip("/")
    key = os.environ.get(slot.get("api_key_env", ""), "")
    if slot.get("api_key_env") and not key:
        raise MeristemError(f"env var {slot['api_key_env']} is unset (role {role})")

    payload = {
        "model": slot["model"],
        "messages": messages,
        "temperature": slot.get("temperature", 0.2),
    }
    limit = max_tokens or slot.get("max_tokens")
    if limit:
        payload["max_tokens"] = limit

    last: Exception | None = None
    for attempt in range(attempts):
        try:
            data = _post(f"{base}/chat/completions", payload, key, slot.get("timeout", 300))
            usage = data.get("usage") or {}
            choice = (data.get("choices") or [{}])[0]
            return Completion(
                text=(choice.get("message") or {}).get("content") or "",
                model=slot["model"],
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
                slot=slot["id"],
                raw=data,
            )
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise MeristemError(f"role {role} failed after {attempts} attempts: {last}")
