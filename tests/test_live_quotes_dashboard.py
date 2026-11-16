import json
import os
import unittest
from datetime import datetime
from unittest.mock import patch

from daily_report import (
    LIVE_QUOTES_MAX_TICKERS_PER_REQUEST,
    LIVE_QUOTES_POLL_INTERVAL_MS,
    Company,
    Quote,
    StockReport,
    TIMEZONE,
    render_dashboard,
)


class LiveQuotesDashboardTests(unittest.TestCase):
    def render(self):
        return render_dashboard(
            [StockReport(Company("Example", "EX"), quote=Quote())],
            generated_at=datetime(2026, 11, 16, 12, 0, tzinfo=TIMEZONE),
        )

    def live_quotes_config(self, html):
        marker = '<script type="application/json" id="live-quotes-config">'
        contents = html.split(marker, 1)[1].split("</script>", 1)[0]
        return json.loads(contents)

    @patch.dict(os.environ, {}, clear=False)
    def test_live_quote_polling_stays_disabled_without_worker_url(self):
        os.environ.pop("LIVE_QUOTES_API_URL", None)
        config = self.live_quotes_config(self.render())

        self.assertEqual(config["apiUrl"], "")
        self.assertEqual(config["tickers"], [])
        self.assertIn('id="live-quotes-status"', self.render())

    @patch.dict(
        os.environ,
        {"LIVE_QUOTES_API_URL": "https://quotes.example/quotes"},
        clear=False,
    )
    def test_live_quote_config_contains_only_supported_ls_tickers(self):
        config = self.live_quotes_config(self.render())

        self.assertEqual(config["apiUrl"], "https://quotes.example/quotes")
        self.assertIn("6B0.SG", config["tickers"])
        self.assertNotIn("EX", config["tickers"])
        self.assertEqual(
            config["maxTickersPerRequest"],
            LIVE_QUOTES_MAX_TICKERS_PER_REQUEST,
        )
        self.assertEqual(LIVE_QUOTES_POLL_INTERVAL_MS, 15_000)
        self.assertEqual(config["pollIntervalMs"], LIVE_QUOTES_POLL_INTERVAL_MS)

    @patch.dict(
        os.environ,
        {"LIVE_QUOTES_API_URL": "http://quotes.example/quotes"},
        clear=False,
    )
    def test_live_quote_config_rejects_non_https_endpoint(self):
        with self.assertRaisesRegex(ValueError, "LIVE_QUOTES_API_URL"):
            self.render()


if __name__ == "__main__":
    unittest.main()
