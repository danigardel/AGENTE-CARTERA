import asyncio
import importlib.util
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import daily_report as report
from agente_interactivo import (
    PortfolioConsole,
    _build_sdk_config,
    _run_sdk_console,
    main,
)

try:
    ANTIGRAVITY_SDK_AVAILABLE = (
        importlib.util.find_spec("google.antigravity") is not None
    )
except ModuleNotFoundError:
    ANTIGRAVITY_SDK_AVAILABLE = False


class PortfolioConsoleTests(unittest.TestCase):
    def create_console(self, temp_dir: str) -> PortfolioConsole:
        return PortfolioConsole(
            api_key_value="first-key, second-key,first-key",
            cache_path=Path(temp_dir) / "missing-cache.json",
            activity_path=Path(temp_dir) / "missing-activity.json",
            fetch_on_start=False,
        )

    def test_request_body_serializes_dates_and_contains_previous_turns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            console = self.create_console(temp_dir)
            console.context["portfolio_activity"] = [
                {"trade_date": date(2026, 10, 29)}
            ]
            console.history = [("user", "Pregunta anterior"), ("model", "Respuesta previa")]

            body = console._request_body("Pregunta actual")

        self.assertIn("2026-10-29", body["input"])
        self.assertIn("Pregunta anterior", body["input"])
        self.assertIn("Respuesta previa", body["input"])
        self.assertIn("Pregunta actual", body["input"])
        self.assertEqual(body["model"], "gemini-4-hydrogen")
        self.assertEqual(body["generation_config"]["thinking_level"], "high")
        self.assertFalse(body["store"])

    def test_sdk_portfolio_tools_return_only_requested_read_only_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            console = self.create_console(temp_dir)
            console.positions = [
                report.PortfolioPosition(
                    asset=report.PortfolioAsset(
                        ticker="ABC.SG",
                        name="Example Company",
                        shares=2.0,
                        investment_eur=20.0,
                        currency="EUR",
                    ),
                    prices_eur={"Hoy": 12.0, "1M": 11.0},
                    returns_pct={"1D": 1.5, "MAX": 20.0},
                    current_value_eur=24.0,
                    max_return_pct=20.0,
                    price_date=date(2026, 11, 14),
                    market_timezone="Europe/Berlin",
                )
            ]

            snapshot = json.loads(console.sdk_get_portfolio_snapshot())
            detail = json.loads(console.sdk_get_position_details("abc.sg"))
            analysis = json.loads(console.sdk_get_latest_analysis())
            radar = json.loads(console.sdk_get_market_radar())

        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["current_value_eur"], 24.0)
        self.assertEqual(snapshot["priced_positions_value_eur"], 24.0)
        self.assertEqual(snapshot["unpriced_positions"], 0)
        self.assertEqual(snapshot["positions"][0]["price_date"], "2026-11-14")
        self.assertEqual(detail["ticker"], "ABC.SG")
        self.assertFalse(analysis["available"])
        self.assertFalse(radar["available"])
        self.assertEqual(radar["refresh_command"], "/radar")

    def test_sdk_snapshot_does_not_report_an_incomplete_total(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            console = self.create_console(temp_dir)
            console.positions = [
                report.PortfolioPosition(
                    asset=report.PortfolioAsset(
                        name="Example Company",
                        ticker="ABC.SG",
                        shares=2.0,
                        investment_eur=20.0,
                        currency="EUR",
                    ),
                    current_value_eur=None,
                )
            ]

            snapshot = json.loads(console.sdk_get_portfolio_snapshot())

        self.assertIsNone(snapshot["current_value_eur"])
        self.assertIsNone(snapshot["priced_positions_value_eur"])
        self.assertEqual(snapshot["unpriced_positions"], 1)

    def test_sdk_position_lookup_rejects_unknown_tickers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            console = self.create_console(temp_dir)
            with self.assertRaisesRegex(ValueError, "No hay una posición"):
                console.sdk_get_position_details("UNKNOWN")

    @unittest.skipUnless(
        ANTIGRAVITY_SDK_AVAILABLE,
        "Google Antigravity SDK es una dependencia opcional.",
    )
    def test_sdk_config_disables_builtin_tools_and_limits_custom_tools(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            console = self.create_console(temp_dir)
            config = _build_sdk_config(console)

        self.assertEqual(config.capabilities.enabled_tools, [])
        self.assertFalse(config.capabilities.enable_subagents)
        self.assertEqual(config.workspaces, [])
        self.assertEqual(
            config.model.name,
            "gemini-4-hydrogen",
        )
        self.assertEqual(
            {tool.__name__ for tool in config.tools},
            {
                "sdk_get_portfolio_snapshot",
                "sdk_get_position_details",
                "sdk_get_latest_analysis",
                "sdk_get_market_radar",
            },
        )
        self.assertEqual(
            {policy.tool for policy in config.policies},
            {
                "*",
                "sdk_get_portfolio_snapshot",
                "sdk_get_position_details",
                "sdk_get_latest_analysis",
                "sdk_get_market_radar",
            },
        )

    @unittest.skipUnless(
        ANTIGRAVITY_SDK_AVAILABLE,
        "Google Antigravity SDK es una dependencia opcional.",
    )
    def test_sdk_mode_streams_response_without_live_api_calls(self):
        class FakeAgent:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def chat(self, prompt):
                self.prompt = prompt

                async def response():
                    yield "Respuesta "
                    yield "progresiva"

                return response()

        with tempfile.TemporaryDirectory() as temp_dir:
            console = self.create_console(temp_dir)
            fake_agent = FakeAgent()
            output = StringIO()
            with patch("google.antigravity.Agent", return_value=fake_agent):
                with patch(
                    "builtins.input",
                    side_effect=["Consulta de prueba", "/salir"],
                ):
                    with redirect_stdout(output):
                        status = asyncio.run(_run_sdk_console(console))

        self.assertEqual(status, 0)
        self.assertEqual(fake_agent.prompt, "Consulta de prueba")
        self.assertIn("Respuesta progresiva", output.getvalue())

    def test_main_keeps_existing_console_as_default_and_adds_sdk_flag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            console = self.create_console(temp_dir)
            with patch("agente_interactivo.PortfolioConsole", return_value=console):
                with patch.object(console, "cmdloop") as cmdloop:
                    self.assertEqual(main([]), 0)
            cmdloop.assert_called_once_with()

            async def run_sdk(received_console):
                self.assertIs(received_console, console)
                return 7

            with patch("agente_interactivo.PortfolioConsole", return_value=console):
                with patch("agente_interactivo._run_sdk_console", run_sdk):
                    self.assertEqual(main(["--sdk"]), 7)

    def test_slash_commands_are_dispatched_by_the_console(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            console = self.create_console(temp_dir)
            output = StringIO()
            with redirect_stdout(output):
                console.onecmd("/cartera")
                should_exit = console.onecmd("/salir")

        self.assertIn("No hay datos de cartera", output.getvalue())
        self.assertTrue(should_exit)

    @patch("agente_interactivo.requests.post")
    def test_chat_rotates_keys_and_keeps_history(self, post_mock):
        post_mock.return_value.raise_for_status.return_value = None
        post_mock.return_value.json.side_effect = [
            {
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": "Respuesta 1"}],
                    }
                ]
            },
            {
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": "Respuesta 2"}],
                    }
                ]
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            console = self.create_console(temp_dir)
            with redirect_stdout(StringIO()):
                console._chat("Primera pregunta")
                console._chat("Segunda pregunta")

        self.assertEqual(
            [
                call.kwargs["headers"]["x-goog-api-key"]
                for call in post_mock.call_args_list
            ],
            ["first-key", "second-key"],
        )
        self.assertEqual(
            [call.args[0] for call in post_mock.call_args_list],
            [
                "https://generativelanguage.googleapis.com/v1beta/interactions",
                "https://generativelanguage.googleapis.com/v1beta/interactions",
            ],
        )
        self.assertEqual(console.next_api_key_index, 0)
        second_input = post_mock.call_args_list[1].kwargs["json"]["input"]
        self.assertIn("Primera pregunta", second_input)
        self.assertIn("Respuesta 1", second_input)
        self.assertEqual(len(console.history), 4)


if __name__ == "__main__":
    unittest.main()
