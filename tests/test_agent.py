import json
import unittest
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agent import find_price_alerts, load_agent_state, run_agent, send_price_alerts
from daily_report import (
    Company,
    PortfolioAsset,
    PortfolioAnalysis,
    PortfolioPosition,
    Quote,
    StockReport,
    TIMEZONE,
)


class AutonomousAgentTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 26, 12, 0, tzinfo=TIMEZONE)

    def _position(self, ticker, price_date):
        return PortfolioPosition(
            asset=PortfolioAsset(ticker, ticker, 1, 100, "EUR"),
            prices_eur={"Hoy": 90},
            returns_pct={"1D": -3.0},
            price_date=price_date,
            market_timezone=TIMEZONE.key,
        )

    def _report(self, ticker, market_date, change=-3.0):
        return StockReport(
            Company(ticker, ticker),
            quote=Quote(
                current_price_eur=90,
                daily_change_pct=change,
                market_date=market_date,
                market_timezone=TIMEZONE.key,
            ),
        )

    def test_price_alerts_ignore_stale_quotes_and_realert_by_direction(self):
        fresh = self._position("FRESH", self.now.date())
        stale = self._position("STALE", date(2026, 10, 23))
        reports = [
            self._report("FRESH", self.now.date(), -3.0),
            self._report("STALE", date(2026, 10, 23), -8.0),
        ]
        state = {"date": "2026-10-26", "alerts": {}}

        initial = find_price_alerts([fresh, stale], reports, state, self.now)
        muted = find_price_alerts(
            [fresh],
            [self._report("FRESH", self.now.date(), -3.8)],
            {
                "date": "2026-10-26",
                "alerts": {"FRESH": {"last_alerted_pct": -3.0}},
            },
            self.now,
        )
        deteriorated = find_price_alerts(
            [fresh],
            [self._report("FRESH", self.now.date(), -4.1)],
            {
                "date": "2026-10-26",
                "alerts": {"FRESH": {"last_alerted_pct": -3.0}},
            },
            self.now,
        )

        self.assertEqual([item["ticker"] for item in initial], ["FRESH"])
        self.assertEqual(muted, [])
        self.assertEqual([item["ticker"] for item in deteriorated], ["FRESH"])

    def test_price_alerts_cover_portfolio_rises_and_watchlist_only_rises(self):
        holdings = [
            self._position("HOLD", self.now.date()),
            self._position("UP", self.now.date()),
            self._position("DOWN", self.now.date()),
        ]
        reports = [
            self._report("HOLD", self.now.date(), 8.0),
            self._report("UP", self.now.date(), 3.0),
            self._report("DOWN", self.now.date(), -4.0),
            self._report("WATCH-UP", self.now.date(), 3.0),
            self._report("WATCH-DOWN", self.now.date(), -8.0),
            self._report("WATCH-STALE", date(2026, 10, 23), 8.0),
            self._report("WATCH-BELOW", self.now.date(), 1.9),
        ]

        alerts = find_price_alerts(holdings, reports, {"alerts": {}}, self.now)

        self.assertEqual(
            [(item["ticker"], item["category"]) for item in alerts],
            [
                ("HOLD", "portfolio_rise"),
                ("UP", "portfolio_rise"),
                ("DOWN", "portfolio_drop"),
                ("WATCH-UP", "watchlist_rise"),
            ],
        )

    def test_realerts_when_move_strengthens_in_either_direction(self):
        position = self._position("EX", self.now.date())
        cases = (
            (3.0, 3.9, False),
            (3.0, 4.0, True),
            (-3.0, -3.9, False),
            (-3.0, -4.0, True),
            (-3.0, 2.0, True),
            (3.0, -2.0, True),
        )
        for last_value, current, expected in cases:
            with self.subTest(last_value=last_value, current=current):
                alerts = find_price_alerts(
                    [position],
                    [self._report("EX", self.now.date(), current)],
                    {
                        "alerts": {
                            "EX": {"last_alerted_pct": last_value}
                        }
                    },
                    self.now,
                )
                self.assertEqual(bool(alerts), expected)

    @patch("agent.requests.post")
    @patch.dict(
        "os.environ",
        {"TELEGRAM_BOT_TOKEN": "test-token", "TELEGRAM_CHAT_ID": "123"},
    )
    def test_price_alerts_are_grouped_into_one_telegram_message(self, post_mock):
        post_mock.return_value.raise_for_status.return_value = None
        alerts = [
            {
                "ticker": "AAA",
                "name": "Alpha",
                "daily_change_pct": -2.5,
                "current_price_eur": 12.34,
                "category": "portfolio_drop",
            },
            {
                "ticker": "BBB",
                "name": "Beta",
                "daily_change_pct": -4.0,
                "current_price_eur": 56.78,
                "category": "portfolio_drop",
            },
            {
                "ticker": "CCC",
                "name": "Gamma",
                "daily_change_pct": 3.5,
                "current_price_eur": 78.9,
                "category": "portfolio_rise",
            },
            {
                "ticker": "DDD",
                "name": "Delta",
                "daily_change_pct": 2.5,
                "current_price_eur": 10.0,
                "category": "watchlist_rise",
            },
        ]

        self.assertTrue(send_price_alerts(alerts))
        post_mock.assert_called_once()
        text = post_mock.call_args.kwargs["json"]["text"]
        self.assertIn("🔴 CAÍDAS EN CARTERA:", text)
        self.assertIn("🟢 SUBIDAS EN CARTERA:", text)
        self.assertIn("🚀 SUBIDAS EN SEGUIMIENTO:", text)
        self.assertIn("Alpha (AAA): -2.50% | 12.34 €", text)
        self.assertIn("Gamma (CCC): +3.50% | 78.90 €", text)
        self.assertIn("Delta (DDD): +2.50% | 10.00 €", text)
        self.assertEqual(text.count("\n- "), 4)

    @patch("agent.analyze_portfolio", return_value=PortfolioAnalysis(
        warning="Narrativa IA no disponible."
    ))
    @patch("agent.send_price_alerts", return_value=True)
    @patch("agent.fetch_portfolio")
    @patch("agent.collect_reports")
    def test_deterministic_alert_is_sent_when_ai_chain_is_unavailable(
        self,
        reports_mock,
        portfolio_mock,
        alert_mock,
        analysis_mock,
    ):
        reports_mock.return_value = [self._report("FRESH", self.now.date(), -3.0)]
        portfolio_mock.return_value = [self._position("FRESH", self.now.date())]
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "agent_state.json"
            with (
                patch("agent.PORTFOLIO_ACTIVITY_FILE", root / "missing.json"),
                patch.dict(
                    "os.environ",
                    {"PORTFOLIO_TRANSACTIONS_CSV": ""},
                    clear=False,
                ),
            ):
                run_agent(now=self.now, state_path=state_path)
            state = json.loads(state_path.read_text(encoding="utf-8"))

        alert_mock.assert_called_once()
        analysis_mock.assert_called_once()
        self.assertIn("FRESH", state["alerts"])
        self.assertEqual(state["alerts"]["FRESH"]["last_alerted_pct"], -3.0)
        self.assertNotIn("last_drop_pct", state["alerts"]["FRESH"])

    def test_load_state_migrates_legacy_drop_percentage(self):
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "agent_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "date": self.now.date().isoformat(),
                        "alerts": {
                            "EX": {
                                "last_drop_pct": -2.5,
                                "sent_at": self.now.isoformat(),
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            state = load_agent_state(state_path, self.now.date().isoformat())

        self.assertEqual(state["alerts"]["EX"]["last_alerted_pct"], -2.5)
        self.assertNotIn("last_drop_pct", state["alerts"]["EX"])


if __name__ == "__main__":
    unittest.main()
