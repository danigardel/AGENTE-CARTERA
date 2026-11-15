from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from chat_service import main


class FakeAgent:
    def __init__(self, _config):
        self.prompt = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def chat(self, prompt):
        self.prompt = prompt

        async def response():
            yield "Respuesta "
            yield "de prueba"

        return response()


def sample_market_data():
    return {
        "schema_version": 1,
        "generated_at": "2026-11-15T12:00:00+01:00",
        "portfolio": {
            "totals": {
                "current_value_eur": 765.97,
                "daily_change_eur": 0.0,
            },
            "positions": [
                {
                    "ticker": "EXAMPLE",
                    "name": "Example Co",
                    "current_price_eur": 12.5,
                    "quote_is_stale": False,
                    "unrelated": "not returned by the snapshot tool",
                }
            ],
        },
        "analysis": {
            "action_signal": "HOLD",
            "telegram_summary": "Resumen vigente.",
            "unrelated": "not returned by the analysis tool",
        },
        "market_summary": {"key_headlines": [{"title": "Mercado estable"}]},
        "market_events": [],
        "macro_indicators": {"vix": {"price": 18.5}},
        "quant_summary": {"atr_crossed": []},
        "price_floors": {},
    }


class ChatServiceTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "CHAT_GOOGLE_CLIENT_ID": "test-client-id",
                "CHAT_ALLOWED_EMAIL": "owner@example.com",
                "GEMINI_API_KEY": "test-api-key",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = TestClient(main.app)

    def test_authentication_requires_authorized_verified_account(self):
        with patch.object(
            main.id_token,
            "verify_oauth2_token",
            return_value={
                "email": "owner@example.com",
                "email_verified": True,
            },
        ) as verify:
            unauthorized = self.client.post(
                "/chat",
                json={"messages": [{"role": "user", "content": "Resume cartera"}]},
            )
            with patch.object(
                main,
                "load_market_data",
                side_effect=ValueError("test data failure"),
            ):
                authorized = self.client.post(
                    "/chat",
                    headers={"Authorization": "Bearer valid-token"},
                    json={"messages": [{"role": "user", "content": "Resume cartera"}]},
                )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(authorized.status_code, 503)
        verify.assert_called_once()

    def test_unverified_or_other_account_is_rejected(self):
        for claims in (
            {"email": "other@example.com", "email_verified": True},
            {"email": "owner@example.com", "email_verified": False},
        ):
            with self.subTest(claims=claims):
                with patch.object(
                    main.id_token,
                    "verify_oauth2_token",
                    return_value=claims,
                ):
                    response = self.client.post(
                        "/chat",
                        headers={"Authorization": "Bearer valid-token"},
                        json={
                            "messages": [
                                {"role": "user", "content": "Consulta"}
                            ]
                        },
                    )

                self.assertEqual(response.status_code, 403)

    def test_market_data_loader_requires_https_and_expected_schema(self):
        with patch.dict(os.environ, {"MARKET_DATA_URL": "http://example.test/data"}):
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                main.load_market_data()

        payload = json.dumps(sample_market_data()).encode("utf-8")

        class FakeResponse:
            def __init__(self, content):
                self.content = content
                self.headers = {"Content-Length": str(len(content))}

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size):
                yield self.content

        with (
            patch.dict(
                os.environ,
                {"MARKET_DATA_URL": "https://example.test/data"},
            ),
            patch(
                "chat_service.main.requests.get",
                return_value=FakeResponse(payload),
            ) as get,
        ):
            self.assertEqual(main.load_market_data(), sample_market_data())

        self.assertIn("t", get.call_args.kwargs["params"])

        invalid_payload = json.dumps({"schema_version": 2}).encode("utf-8")
        with (
            patch.dict(
                os.environ,
                {"MARKET_DATA_URL": "https://example.test/data"},
            ),
            patch(
                "chat_service.main.requests.get",
                return_value=FakeResponse(invalid_payload),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "esquema compatible"):
                main.load_market_data()

    def test_tools_are_read_only_scoped_and_lookup_is_exact(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-api-key"}):
            config = main._sdk_config(sample_market_data())

        tools = {tool.__name__: tool for tool in config.tools}
        snapshot = json.loads(tools["get_portfolio_snapshot"]())
        analysis = json.loads(tools["get_latest_analysis"]())

        self.assertEqual(
            {tool for tool in tools},
            {
                "get_portfolio_snapshot",
                "get_position_details",
                "get_latest_analysis",
                "get_market_context",
            },
        )
        self.assertNotIn("unrelated", snapshot["positions"][0])
        self.assertNotIn("unrelated", analysis["analysis"])
        self.assertEqual(
            json.loads(tools["get_position_details"]("example"))["ticker"],
            "EXAMPLE",
        )
        with self.assertRaisesRegex(ValueError, "No hay una posición"):
            tools["get_position_details"]("UNKNOWN")

    def test_sdk_config_is_deepcopyable_and_disables_builtin_tools(self):
        import google.antigravity

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-api-key"}):
            config = main._sdk_config(sample_market_data())

        agent = google.antigravity.Agent(config)
        self.assertIsNotNone(agent)
        self.assertEqual(config.capabilities.enabled_tools, [])
        self.assertFalse(config.capabilities.enable_subagents)
        self.assertEqual(config.workspaces, [])

    def test_chat_streams_tokens_using_only_the_published_data(self):
        prompt_seen = []

        class RecordingAgent(FakeAgent):
            async def chat(self, prompt):
                prompt_seen.append(prompt)
                return await super().chat(prompt)

        with (
            patch.object(main, "load_market_data", return_value=sample_market_data()),
            patch("google.antigravity.Agent", RecordingAgent),
            patch.object(
                main.id_token,
                "verify_oauth2_token",
                return_value={
                    "email": "owner@example.com",
                    "email_verified": True,
                },
            ),
        ):
            response = self.client.post(
                "/chat",
                headers={"Authorization": "Bearer valid-token"},
                json={
                    "messages": [
                        {"role": "user", "content": "Resume mi cartera"},
                    ]
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn('"token": "Respuesta "', response.text)
        self.assertIn('"token": "de prueba"', response.text)
        self.assertIn("data: [DONE]", response.text)
        self.assertIn("Resume mi cartera", prompt_seen[0])

    def test_invalid_last_role_and_oversized_history_are_rejected(self):
        with patch.object(
            main.id_token,
            "verify_oauth2_token",
            return_value={
                "email": "owner@example.com",
                "email_verified": True,
            },
        ):
            invalid_last_role = self.client.post(
                "/chat",
                headers={"Authorization": "Bearer valid-token"},
                json={"messages": [{"role": "assistant", "content": "Hola"}]},
            )
            too_many_messages = self.client.post(
                "/chat",
                headers={"Authorization": "Bearer valid-token"},
                json={
                    "messages": [
                        {"role": "user", "content": "Consulta"}
                        for _ in range(main.MAX_HISTORY_MESSAGES + 1)
                    ]
                },
            )

        self.assertEqual(invalid_last_role.status_code, 422)
        self.assertEqual(too_many_messages.status_code, 422)


if __name__ == "__main__":
    unittest.main()
