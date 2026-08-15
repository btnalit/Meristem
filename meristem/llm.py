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

#: Gateways rate-limit on how fast request volume RAMPS, not only on total
#: volume, and answer a too-eager client with 429. Pacing is therefore part of
#: correctness, not politeness. Module-level so every role shares one throttle.
_last_call = 0.0

#: Every settled attempt, successful or not, appended here by _complete_one and
#: drained by the ledger (P-015). Billing happens at the CALL, not at the point
#: where a caller happens to succeed: a failing call costs exactly as much as a
#: succeeding one, and a budget gate blind to failures is blind to the runaway
#: it exists to stop.
attempts_log: list = []
MIN_INTERVAL = 3.0
RATE_LIMIT_BACKOFF = (15.0, 45.0, 90.0, 180.0)


@dataclass
class Completion:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
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
    global _last_call
    gap = MIN_INTERVAL - (time.monotonic() - _last_call)
    if gap > 0:
        time.sleep(gap)
    _last_call = time.monotonic()

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
    attempts: int = 4,
) -> Completion:
    """One structured model call, falling back across a role's slots.

    A quota is a property of one model, not of the capability. When the
    preferred slot is exhausted the role is still available through another --
    so a rate limit should cost a slower cycle, never a lost one. Explicit
    slots (a named reviewer) never fall back: that would silently collapse the
    panel's independence into whichever model answered.
    """
    candidates = [slot] if slot else slots_for(role, config)
    last: Exception | None = None
    for candidate in candidates:
        try:
            return _complete_one(role, messages, candidate, max_tokens, attempts)
        except MeristemError as exc:
            last = exc
    raise last or MeristemError(f"role {role} has no configured slot")


def _complete_one(
    role: str,
    messages: list[dict],
    slot: dict,
    max_tokens: int | None,
    attempts: int,
) -> Completion:
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
            data = _post(f"{base}/chat/completions", payload, key, slot.get("timeout", 600))
            usage = data.get("usage") or {}
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            text = message.get("content") or ""
            details = usage.get("completion_tokens_details") or {}

            # These are reasoning models: the thinking trace is billed against
            # the same budget as the answer, so a tight max_tokens yields an
            # EMPTY content field with a perfectly successful HTTP 200. Reading
            # that as a valid empty answer would let a truncation masquerade as
            # a model that had nothing to say.
            completion = Completion(
                text=text,
                model=slot["model"],
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
                reasoning_tokens=int(details.get("reasoning_tokens", 0)),
                slot=slot["id"],
                raw=data,
            )
            # A reply can fail in two directions, and we only ever checked one.
            # Empty content was caught (P-004); a reply that ran INTO the cap
            # was not -- it arrives non-empty, is judged fine, and then fails
            # far downstream as "unparseable JSON" with the evidence gone
            # (P-017). finish_reason is the authoritative signal; a completion
            # exactly at max_tokens is corroboration, not proof.
            finish = choice.get("finish_reason")
            truncated = finish == "length" or (
                limit and completion.completion_tokens >= limit
            )
            attempts_log.append({"role": role, "completion": completion,
                                 "ok": bool(text.strip()) and not truncated})
            if not text.strip():
                raise ValueError(
                    f"empty content (finish_reason={finish}); "
                    "raise max_tokens -- reasoning consumed the budget"
                )
            if truncated:
                raise ValueError(
                    f"reply truncated at {completion.completion_tokens} tokens "
                    f"(finish_reason={finish}, cap={limit}); the task needs "
                    "splitting or the slot needs a larger max_tokens"
                )
            return completion
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 429 and attempt + 1 < attempts:
                time.sleep(RATE_LIMIT_BACKOFF[min(attempt, len(RATE_LIMIT_BACKOFF) - 1)])
            elif attempt + 1 < attempts:
                time.sleep(2**attempt)
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise MeristemError(f"{slot['id']} failed after {attempts} attempts: {last}")
