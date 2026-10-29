import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agente_interactivo import PortfolioConsole


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

        contents = body["contents"]
        self.assertIn("2026-10-29", contents[0]["parts"][0]["text"])
        self.assertEqual(contents[1]["role"], "user")
        self.assertEqual(contents[2]["role"], "model")
        self.assertEqual(
            contents[3]["parts"][0]["text"],
            "Pregunta actual",
        )

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
            {"candidates": [{"content": {"parts": [{"text": "Respuesta 1"}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "Respuesta 2"}]}}]},
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
        self.assertEqual(console.next_api_key_index, 0)
        second_contents = post_mock.call_args_list[1].kwargs["json"]["contents"]
        self.assertIn("Primera pregunta", json.dumps(second_contents))
        self.assertIn("Respuesta 1", json.dumps(second_contents))
        self.assertEqual(len(console.history), 4)


if __name__ == "__main__":
    unittest.main()
