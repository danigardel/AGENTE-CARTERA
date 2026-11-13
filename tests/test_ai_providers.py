import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

from ai_providers import (
    AIProviderConfig,
    InvalidProviderResponse,
    OpenAICompatibleProvider,
    PersistentCircuitBreaker,
    PromptTooLarge,
    ProviderCircuitOpen,
    classify_error,
    count_gemini_tokens,
    load_ai_config,
    post_gemini_request,
)


class AIProviderTests(unittest.TestCase):
    def test_classifies_rate_limits_as_retryable_and_auth_errors_as_permanent(self):
        rate_limit = requests.HTTPError(
            response=SimpleNamespace(status_code=429, headers={})
        )
        unauthorized = requests.HTTPError(
            response=SimpleNamespace(status_code=401, headers={})
        )
        prompt_limited = requests.HTTPError(
            response=SimpleNamespace(
                status_code=429,
                headers={},
                json=lambda: {"error": {"message": "Prompt too large for context window"}},
            )
        )

        self.assertEqual(classify_error(rate_limit), "retryable")
        self.assertEqual(classify_error(unauthorized), "permanent")
        self.assertEqual(classify_error(prompt_limited), "prompt_too_large")
        self.assertEqual(classify_error(requests.Timeout()), "retryable")
        self.assertEqual(
            classify_error(
                requests.HTTPError(
                    response=SimpleNamespace(status_code=413, headers={})
                )
            ),
            "prompt_too_large",
        )

    @patch("ai_providers.time.sleep")
    @patch("ai_providers.requests.post")
    def test_retries_transient_error_and_honors_retry_after(
        self,
        post_mock,
        sleep_mock,
    ):
        response_error = requests.HTTPError(
            response=SimpleNamespace(
                status_code=429,
                headers={"Retry-After": "4"},
            )
        )
        successful_response = SimpleNamespace(raise_for_status=lambda: None)
        post_mock.side_effect = [response_error, successful_response]
        config = AIProviderConfig(
            max_retries=1,
            base_backoff_seconds=1,
            max_backoff_seconds=2,
            circuit_failure_threshold=3,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "provider_state.json"
            result = post_gemini_request(
                "https://example.test/generate",
                "test-key",
                {"contents": []},
                config=config,
                state_path=state_path,
            )

        self.assertIs(result, successful_response)
        self.assertEqual(post_mock.call_count, 2)
        sleep_mock.assert_called_once_with(2)

    def test_circuit_breaker_persists_open_state(self):
        config = AIProviderConfig(
            circuit_failure_threshold=1,
            circuit_cooldown_seconds=60,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "provider_state.json"
            breaker = PersistentCircuitBreaker(state_path, config)
            breaker.record_failure()

            stored = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIn("opened_until", stored["gemini"])
            with self.assertRaises(ProviderCircuitOpen):
                breaker.ensure_closed()

    def test_circuit_breaker_rearms_after_cooldown(self):
        config = AIProviderConfig(circuit_cooldown_seconds=60)
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "provider_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "test-provider": {
                            "failures": 3,
                            "opened_until": "2000-01-01T00:00:00+00:00",
                        }
                    }
                ),
                encoding="utf-8",
            )
            breaker = PersistentCircuitBreaker(
                state_path,
                config,
                provider="test-provider",
            )

            breaker.ensure_closed()

            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8")),
                {},
            )

    def test_load_ai_config_accepts_legacy_gemini_only_configuration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ai_config.json"
            config_path.write_text(
                json.dumps({"provider_order": ["gemini"]}),
                encoding="utf-8",
            )
            with patch.dict("os.environ", {}, clear=True):
                config = load_ai_config(config_path)

        self.assertEqual(config.provider_order, ("gemini",))

    def test_repository_ai_config_defines_free_fallback_chain(self):
        config_path = Path(__file__).resolve().parents[1] / "ai_config.json"
        with patch.dict("os.environ", {}, clear=True):
            config = load_ai_config(config_path)

        self.assertEqual(
            config.provider_order,
            (
                "gemini",
                "gemini_fallback",
                "gemini_37_fallback",
                "gemini_flash_lite",
                "groq",
                "openrouter",
            ),
        )
        self.assertEqual(
            config.providers["gemini"]["default_model"],
            "gemini-4-hydrogen",
        )
        self.assertEqual(
            config.providers["gemini_fallback"]["default_model"],
            "gemini-3.8-flash",
        )
        self.assertEqual(
            config.providers["gemini_37_fallback"]["default_model"],
            "gemini-3.7-flash",
        )
        self.assertEqual(config.gemini_tpm_safety_limit_tokens, 200_000)
        self.assertFalse(config.providers["cerebras"]["enabled"])
        self.assertFalse(config.providers["mistral"]["enabled"])
        self.assertEqual(
            config.providers["openrouter"]["default_model"],
            "openrouter/free",
        )

    @patch("ai_providers.requests.post")
    def test_count_gemini_tokens_uses_count_tokens_endpoint_payload(self, post_mock):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"totalTokens": 234_567},
        )
        post_mock.return_value = response

        result = count_gemini_tokens(
            "https://example.test/models/gemini-4-hydrogen:countTokens",
            "test-key",
            "portfolio context",
        )

        self.assertEqual(result, 234_567)
        post_mock.assert_called_once()
        self.assertEqual(
            post_mock.call_args.kwargs["json"],
            {"contents": [{"parts": [{"text": "portfolio context"}]}]},
        )
        self.assertEqual(
            post_mock.call_args.kwargs["headers"]["x-goog-api-key"],
            "test-key",
        )

    @patch.dict("os.environ", {"TEST_OPENAI_KEY": "first-secret,second-secret"})
    @patch("ai_providers.requests.post")
    def test_openai_compatible_sends_json_mode_and_returns_actual_model(
        self,
        post_mock,
    ):
        post_mock.return_value.raise_for_status.return_value = None
        post_mock.return_value.json.return_value = {
            "model": "actual/free-model",
            "choices": [{"message": {"content": "```json\n{\"ok\": true}\n```"}}],
        }
        provider = OpenAICompatibleProvider(
            "test",
            "https://api.example.test/v1/",
            "TEST_OPENAI_KEY",
            "TEST_OPENAI_MODEL",
            "configured-model",
            {"X-Title": "Test"},
            True,
            2_000,
        )

        result = provider.complete("{}")

        self.assertEqual(result.content, '{"ok": true}')
        self.assertEqual(result.model_used, "actual/free-model")
        post_mock.assert_called_once()
        self.assertEqual(
            post_mock.call_args.args[0],
            "https://api.example.test/v1/chat/completions",
        )
        self.assertEqual(
            post_mock.call_args.kwargs["headers"]["Authorization"],
            "Bearer first-secret",
        )
        body = post_mock.call_args.kwargs["json"]
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["temperature"], 0.1)
        self.assertNotIn("first-secret", json.dumps(body))

    @patch.dict("os.environ", {"TEST_OPENAI_KEY": "invalid-key,valid-key"})
    @patch("ai_providers.requests.post")
    def test_openai_compatible_rotates_keys_after_authentication_failure(
        self,
        post_mock,
    ):
        unauthorized = requests.HTTPError(
            response=SimpleNamespace(status_code=401, headers={})
        )

        def raise_unauthorized():
            raise unauthorized

        post_mock.side_effect = [
            SimpleNamespace(raise_for_status=raise_unauthorized),
            SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {
                    "model": "configured-model",
                    "choices": [{"message": {"content": '{"ok": true}'}}],
                },
            ),
        ]
        provider = OpenAICompatibleProvider(
            "test",
            "https://api.example.test/v1",
            "TEST_OPENAI_KEY",
            "TEST_OPENAI_MODEL",
            "configured-model",
            config=AIProviderConfig(max_retries=0),
        )

        result = provider.complete("{}")

        self.assertEqual(result.content, '{"ok": true}')
        self.assertEqual(post_mock.call_count, 2)
        self.assertEqual(
            post_mock.call_args_list[0].kwargs["headers"]["Authorization"],
            "Bearer invalid-key",
        )
        self.assertEqual(
            post_mock.call_args_list[1].kwargs["headers"]["Authorization"],
            "Bearer valid-key",
        )

    @patch.dict(
        "os.environ",
        {
            "OPENROUTER_API_KEY": "router-secret",
            "OPENROUTER_MODEL": "vendor/model:paid",
        },
        clear=False,
    )
    @patch("ai_providers.requests.post")
    def test_openrouter_blocks_paid_models_unless_explicitly_allowed(
        self,
        post_mock,
    ):
        provider = OpenAICompatibleProvider(
            "openrouter",
            "https://openrouter.ai/api/v1",
            "OPENROUTER_API_KEY",
            "OPENROUTER_MODEL",
            "openrouter/free",
        )
        with patch.dict("os.environ", {"OPENROUTER_ALLOW_PAID": "false"}):
            with self.assertRaisesRegex(ValueError, "salvaguarda"):
                provider.complete("{}")
        post_mock.assert_not_called()

        post_mock.return_value.raise_for_status.return_value = None
        post_mock.return_value.json.return_value = {
            "model": "vendor/model:paid",
            "choices": [{"message": {"content": '{"ok": true}'}}],
        }
        with patch.dict("os.environ", {"OPENROUTER_ALLOW_PAID": "true"}):
            result = provider.complete("{}")
        self.assertEqual(result.model_used, "vendor/model:paid")
        post_mock.assert_called_once()

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "router-secret"})
    @patch("ai_providers.requests.post")
    def test_openrouter_accepts_free_router_and_preserves_returned_model(
        self,
        post_mock,
    ):
        post_mock.return_value.raise_for_status.return_value = None
        post_mock.return_value.json.return_value = {
            "model": "provider/random-model:free",
            "choices": [{"message": {"content": '{"ok": true}'}}],
        }
        provider = OpenAICompatibleProvider(
            "openrouter",
            "https://openrouter.ai/api/v1",
            "OPENROUTER_API_KEY",
            "OPENROUTER_MODEL",
            "openrouter/free",
            max_prompt_chars=100,
        )

        result = provider.complete("{}")

        self.assertEqual(result.model_used, "provider/random-model:free")
        self.assertEqual(post_mock.call_args.kwargs["json"]["model"], "openrouter/free")

    @patch.dict("os.environ", {"TEST_OPENAI_KEY": "key"})
    @patch("ai_providers.requests.post")
    def test_rejects_prompts_above_provider_character_limit(self, post_mock):
        provider = OpenAICompatibleProvider(
            "test",
            "https://api.example.test/v1",
            "TEST_OPENAI_KEY",
            "TEST_OPENAI_MODEL",
            "configured-model",
            max_prompt_chars=1,
        )
        with self.assertRaises(PromptTooLarge):
            provider.complete("too long")
        post_mock.assert_not_called()

    @patch.dict("os.environ", {"TEST_OPENAI_KEY": ""})
    @patch("ai_providers.requests.post")
    def test_provider_without_keys_makes_no_request(self, post_mock):
        provider = OpenAICompatibleProvider(
            "test",
            "https://api.example.test/v1",
            "TEST_OPENAI_KEY",
            "TEST_OPENAI_MODEL",
            "configured-model",
        )
        with self.assertRaisesRegex(ValueError, "No hay claves"):
            provider.complete("{}")
        post_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
