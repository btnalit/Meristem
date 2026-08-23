"""Tests for substrate/model_gateway.py (S7's execution end, v5 spec §8.1.3 /
§10.2 / §18 v5.9 row).

Two layers:

  * `handle()` unit tests -- policy/ledger/cycle injected directly, no
    subprocess, no real soil/model-policy.toml touched.
  * one true end-to-end test that goes through `meristem.llm.call_model()`
    exactly as the seed would: spawns the real gateway entrypoint string
    supervisor injects, over the real stdin/stdout subprocess boundary.

The two contract properties this module exists to hold (§8.1.3):
  1. the seed never receives quota numbers -- only one of
     allowed/refused/deferred, plus content/reason;
  2. a missing credential fails closed with its own distinct, greppable
     reason, never a fabricated response.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from substrate import budget  # noqa: E402
from substrate import model_gateway  # noqa: E402


def _policy(*, calls_per_cycle=100, window_cycles=2, calls_per_window=100,
           api_key_env="MERISTEM_TEST_UNSET_KEY") -> dict:
    return {
        "budget": {"window_cycles": window_cycles, "calls_per_cycle": calls_per_cycle,
                   "calls_per_window": calls_per_window},
        "roles": {
            "mutate": {"slots": [{"id": "mutate:glm", "api_key_env": api_key_env,
                                  "base_url": "https://example.invalid/v1",
                                  "model": "glm-5.2", "max_tokens": 100, "temperature": 0.2,
                                  "timeout": 5}]},
            "review": {"slots": [{"id": "review:deepseek", "api_key_env": api_key_env,
                                  "base_url": "https://example.invalid/v1",
                                  "model": "deepseek-v4-flash"}]},
        },
    }


class HandleBasicShapeTests(unittest.TestCase):
    def test_malformed_request_is_bad_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-model-calls.jsonl"
            resp = model_gateway.handle("not a dict", policy=_policy(), calls_ledger=ledger, cycle=1)
            self.assertEqual(resp, {"status": "refused", "reason": "bad_request"})

    def test_missing_prompt_is_bad_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-model-calls.jsonl"
            resp = model_gateway.handle({"role": "mutate"}, policy=_policy(),
                                        calls_ledger=ledger, cycle=1)
            self.assertEqual(resp, {"status": "refused", "reason": "bad_request"})

    def test_status_is_always_one_of_the_three_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-model-calls.jsonl"
            resp = model_gateway.handle({"role": "mutate", "prompt": "x"}, policy=_policy(),
                                        calls_ledger=ledger, cycle=1)
            self.assertIn(resp["status"], ("allowed", "refused", "deferred"))


class RoleBoundaryTests(unittest.TestCase):
    """§8.1.3 / §16: `review` is soil-only. The gateway must enforce this
    independently of meristem/llm.py's own client-side check (defense in
    depth -- a hand-crafted request bypassing llm.py must not reach the
    review slot's real credentials)."""

    def test_review_role_is_refused_even_though_it_has_a_configured_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-model-calls.jsonl"
            resp = model_gateway.handle({"role": "review", "prompt": "x"}, policy=_policy(),
                                        calls_ledger=ledger, cycle=1)
            self.assertEqual(resp, {"status": "refused", "reason": "role_unavailable"})

    def test_unknown_role_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-model-calls.jsonl"
            resp = model_gateway.handle({"role": "escalate", "prompt": "x"}, policy=_policy(),
                                        calls_ledger=ledger, cycle=1)
            self.assertEqual(resp, {"status": "refused", "reason": "role_unavailable"})


class NoCredentialsFailsClosedTests(unittest.TestCase):
    """The seam: no API key configured -> refused with its own distinct
    reason, and the provider is never actually contacted (do not fake a
    response)."""

    def test_missing_credential_env_var_is_refused_with_a_distinct_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-model-calls.jsonl"
            os.environ.pop("MERISTEM_TEST_UNSET_KEY", None)
            resp = model_gateway.handle({"role": "mutate", "prompt": "x"}, policy=_policy(),
                                        calls_ledger=ledger, cycle=1)
            self.assertEqual(resp, {"status": "refused", "reason": "no_credentials"})

    def test_no_credentials_never_reaches_the_network(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("urllib.request.urlopen") as urlopen:
            ledger = Path(tmp) / "soil-model-calls.jsonl"
            os.environ.pop("MERISTEM_TEST_UNSET_KEY", None)
            model_gateway.handle({"role": "mutate", "prompt": "x"}, policy=_policy(),
                                 calls_ledger=ledger, cycle=1)
            urlopen.assert_not_called()

    def test_no_credentials_does_not_consume_budget(self):
        """A refusal that never touched the provider must not cost a call --
        otherwise 'refused' would silently start rationing itself."""
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-model-calls.jsonl"
            os.environ.pop("MERISTEM_TEST_UNSET_KEY", None)
            policy = _policy(calls_per_cycle=1, calls_per_window=1)
            for _ in range(5):
                resp = model_gateway.handle({"role": "mutate", "prompt": "x"}, policy=policy,
                                            calls_ledger=ledger, cycle=1)
                self.assertEqual(resp["reason"], "no_credentials")
            self.assertEqual(budget.ModelCallLedger(ledger).read(), [])


class SeedNeverReceivesQuotaNumbersTests(unittest.TestCase):
    """§8.1.3: 'the seed learns only the outcome -- never remaining quota,
    retry counts, or slot order.' The budget-refused response in particular
    must not leak the counts/caps that budget.check() computed internally."""

    def test_budget_refusal_response_has_no_numeric_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-model-calls.jsonl"
            policy = _policy(calls_per_cycle=1, calls_per_window=1)
            budget.ModelCallLedger(ledger).record(cycle=1, role="mutate", slot_id="mutate:glm")

            resp = model_gateway.handle({"role": "mutate", "prompt": "x"}, policy=policy,
                                        calls_ledger=ledger, cycle=1)
            self.assertEqual(resp, {"status": "refused", "reason": "budget"})
            # The exact contract: only status/content/reason keys, ever.
            self.assertEqual(set(resp) - {"status", "content", "reason"}, set())
            for value in resp.values():
                self.assertNotIsInstance(value, (int, float))

    def test_response_never_contains_slot_id_or_role_or_ledger_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-model-calls.jsonl"
            policy = _policy(calls_per_cycle=1, calls_per_window=1)
            budget.ModelCallLedger(ledger).record(cycle=1, role="mutate", slot_id="mutate:glm")
            resp = model_gateway.handle({"role": "mutate", "prompt": "x"}, policy=policy,
                                        calls_ledger=ledger, cycle=1)
            serialized = json.dumps(resp)
            self.assertNotIn("mutate:glm", serialized)
            self.assertNotIn(str(ledger), serialized)


class BudgetGatingUsesTheDedicatedModuleTests(unittest.TestCase):
    """§18 v5.9 row: budget must not be folded into the gateway's call
    execution -- verify the gateway actually calls budget.check() as a
    separate, mockable step rather than reimplementing the cap logic."""

    def test_gateway_defers_the_budget_decision_to_budget_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "soil-model-calls.jsonl"
            with mock.patch.object(budget, "check", return_value="forced refusal for test"):
                resp = model_gateway.handle({"role": "mutate", "prompt": "x"}, policy=_policy(),
                                            calls_ledger=ledger, cycle=1)
            self.assertEqual(resp, {"status": "refused", "reason": "budget"})


class _FakeHTTPResponse:
    """Minimal context-manager stand-in for `urllib.request.urlopen`'s
    return value -- just enough of the shape `_call_provider` reads."""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


class CallProviderSeamWithMockedTransportTests(unittest.TestCase):
    """`_call_provider()`'s HTTP-parsing logic, exercised with a mocked
    `urlopen` -- never a real network call, and never a fabricated model
    reply: these tests only check that *given* a transport response, the
    parsing/mapping logic does the right thing. They are independent of
    the no-credentials gate, which is covered separately above."""

    SLOT = {"id": "mutate:glm", "api_key_env": "MERISTEM_TEST_UNSET_KEY",
            "base_url": "https://example.invalid/v1", "model": "glm-5.2",
            "max_tokens": 100, "temperature": 0.2, "timeout": 5}

    def setUp(self):
        self._patcher = mock.patch.dict(os.environ, {"MERISTEM_TEST_UNSET_KEY": "fake-key-for-test"})
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_successful_response_is_mapped_to_allowed_with_content(self):
        body = json.dumps({"choices": [{"message": {"content": "hello back"}}]}).encode()
        with mock.patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(body)):
            status, content, reason = model_gateway._call_provider(self.SLOT, "hi")
        self.assertEqual((status, content, reason), ("allowed", "hello back", None))

    def test_empty_content_is_refused_not_allowed(self):
        """soil/model-policy.toml's own documented gotcha: a too-tight
        max_tokens can yield HTTP 200 with an empty content string."""
        body = json.dumps({"choices": [{"message": {"content": ""}}]}).encode()
        with mock.patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(body)):
            status, content, reason = model_gateway._call_provider(self.SLOT, "hi")
        self.assertEqual(status, "refused")
        self.assertEqual(reason, "provider_bad_response")

    def test_malformed_response_shape_is_refused_not_crashes(self):
        body = json.dumps({"unexpected": "shape"}).encode()
        with mock.patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(body)):
            status, content, reason = model_gateway._call_provider(self.SLOT, "hi")
        self.assertEqual((status, reason), ("refused", "provider_bad_response"))

    def test_http_429_is_deferred_not_refused(self):
        import urllib.error
        err = urllib.error.HTTPError("https://example.invalid/v1/chat/completions", 429,
                                     "Too Many Requests", hdrs=None, fp=None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            status, content, reason = model_gateway._call_provider(self.SLOT, "hi")
        self.assertEqual((status, reason), ("deferred", "rate_limited"))

    def test_network_error_is_refused_not_crashes(self):
        import urllib.error
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("connection refused")):
            status, content, reason = model_gateway._call_provider(self.SLOT, "hi")
        self.assertEqual((status, reason), ("refused", "provider_error"))


class EndToEndThroughLlmPyTests(unittest.TestCase):
    """The real subprocess round trip: meristem.llm.call_model() spawning
    the exact MERISTEM_MODEL_GATEWAY command supervisor.py injects, talking
    to the real substrate/model_gateway.py over stdin/stdout."""

    def test_seed_side_round_trip_fails_closed_without_credentials(self):
        from substrate.supervisor import MODEL_GATEWAY_ENTRYPOINT

        env = {**os.environ, "MERISTEM_MODEL_GATEWAY": MODEL_GATEWAY_ENTRYPOINT,
               "MERISTEM_SOIL_CYCLE": "1"}
        # This repo's real soil/model-policy.toml points api_key_env at
        # SENSENOVA_API_KEY; make sure this test is not accidentally
        # sensitive to whatever the ambient shell happens to have set.
        env.pop("SENSENOVA_API_KEY", None)

        script = (
            "import sys; sys.path.insert(0, r'" + str(REPO) + "'); "
            "from meristem import llm; "
            "r = llm.call_model('mutate', 'hello'); "
            "print(r.status, r.reason)"
        )
        result = subprocess.run([sys.executable, "-c", script], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "refused no_credentials")

    def test_seed_side_round_trip_never_sees_a_role_it_should_not(self):
        from substrate.supervisor import MODEL_GATEWAY_ENTRYPOINT

        env = {**os.environ, "MERISTEM_MODEL_GATEWAY": MODEL_GATEWAY_ENTRYPOINT,
               "MERISTEM_SOIL_CYCLE": "1"}
        script = (
            "import sys; sys.path.insert(0, r'" + str(REPO) + "'); "
            "from meristem import llm; "
            "r = llm.call_model('review', 'hello'); "
            "print(r.status)"
        )
        result = subprocess.run([sys.executable, "-c", script], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        # llm.py itself already refuses 'review' client-side (not in
        # seed/model-interface.json's roles_available_to_seed) before the
        # gateway is ever spawned -- see llm._roles_available().
        self.assertEqual(result.stdout.strip(), "refused")


if __name__ == "__main__":
    unittest.main()
