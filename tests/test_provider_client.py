import types
import unittest
from unittest import mock

from substrate import provider_client


class ProviderClientTests(unittest.TestCase):
    def test_chat_once_uses_sdk_without_sdk_retries(self):
        response = types.SimpleNamespace(
            choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content="OK"))])
        fake = mock.Mock()
        fake.chat.completions.create.return_value = response
        with mock.patch.object(provider_client, "OpenAI", return_value=fake) as factory:
            result = provider_client.chat_once(
                base_url="https://example.invalid/v1", api_key="test-key",
                model="test-model", prompt="hi", max_tokens=32,
                temperature=0.0, timeout=5)
        self.assertTrue(result.ok)
        self.assertEqual(result.content, "OK")
        factory.assert_called_once_with(
            api_key="test-key", base_url="https://example.invalid/v1",
            max_retries=0, timeout=5)
        fake.chat.completions.create.assert_called_once_with(
            model="test-model", messages=[{"role": "user", "content": "hi"}],
            max_tokens=32, temperature=0.0)

    def test_empty_sdk_content_is_bad_response(self):
        response = types.SimpleNamespace(
            choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content=""))])
        fake = mock.Mock()
        fake.chat.completions.create.return_value = response
        with mock.patch.object(provider_client, "OpenAI", return_value=fake):
            result = provider_client.chat_once(
                base_url="https://example.invalid/v1", api_key="test-key",
                model="test-model", prompt="hi", max_tokens=32,
                temperature=0.0, timeout=5)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "bad_response")

    def test_json_response_format_is_forwarded_explicitly(self):
        response = types.SimpleNamespace(
            choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content='{"seed/x.py":"print(1)"}'))])
        fake = mock.Mock()
        fake.chat.completions.create.return_value = response
        with mock.patch.object(provider_client, "OpenAI", return_value=fake):
            result = provider_client.chat_once(
                base_url="https://example.invalid/v1", api_key="test-key",
                model="test-model", prompt="hi", max_tokens=32,
                temperature=0.0, timeout=5,
                response_format={"type": "json_object"})
        self.assertTrue(result.ok)
        self.assertEqual(fake.chat.completions.create.call_args.kwargs["response_format"],
                         {"type": "json_object"})


if __name__ == "__main__":
    unittest.main()
