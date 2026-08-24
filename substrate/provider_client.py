"""Soil-only OpenAI-compatible provider transport.

This module deliberately owns no policy, budget, retry, telemetry, or worker
ABI decisions.  It performs exactly one SDK request with SDK retries disabled;
the soil gateway owns all retry and accounting semantics.
"""
from __future__ import annotations

from dataclasses import dataclass

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError


@dataclass(frozen=True)
class ProviderResult:
    content: str | None = None
    error_kind: str | None = None
    http_status: int | None = None
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @property
    def ok(self) -> bool:
        return self.error_kind is None and isinstance(self.content, str) and bool(self.content.strip())


def chat_once(*, base_url: str, api_key: str, model: str, prompt: str,
              max_tokens: int, temperature: float, timeout: float,
              response_format: dict | None = None) -> ProviderResult:
    """Perform one request; never retry inside the SDK."""
    try:
        client = OpenAI(api_key=api_key, base_url=base_url,
                        max_retries=0, timeout=timeout)
        request = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if response_format is not None:
            request["response_format"] = response_format
        response = client.chat.completions.create(**request)
        choice = response.choices[0] if response.choices else None
        content = choice.message.content if choice else None
        usage = getattr(response, "usage", None)
        finish_reason = getattr(choice, "finish_reason", None) if choice else None
        prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
        if not isinstance(content, str) or not content.strip():
            return ProviderResult(error_kind="bad_response", finish_reason=finish_reason,
                                  prompt_tokens=prompt_tokens,
                                  completion_tokens=completion_tokens)
        return ProviderResult(content=content, finish_reason=finish_reason,
                              prompt_tokens=prompt_tokens,
                              completion_tokens=completion_tokens)
    except RateLimitError as exc:
        return ProviderResult(error_kind="rate_limited",
                              http_status=getattr(exc, "status_code", 429))
    except (APIConnectionError, APITimeoutError):
        return ProviderResult(error_kind="connection_error")
    except APIStatusError as exc:
        return ProviderResult(error_kind="provider_error",
                              http_status=getattr(exc, "status_code", None))
    except Exception:
        # Keep the soil ABI deterministic and avoid leaking SDK/provider details.
        return ProviderResult(error_kind="provider_error")
