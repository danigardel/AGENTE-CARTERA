import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from daily_report import (
    Company,
    Headline,
    Quote,
    StockReport,
    classify_sentiment,
    executive_summary,
    fetch_headlines,
    fetch_quote,
    render_dashboard,
)


class SentimentTests(unittest.TestCase):
    def test_sentiment_thresholds_and_missing_data(self):
        self.assertEqual(classify_sentiment(0.2), ("Alcista", "bullish"))
        self.assertEqual(classify_sentiment(-0.2), ("Bajista", "bearish"))
        self.assertEqual(classify_sentiment(0), ("Neutral", "neutral"))
        self.assertEqual(classify_sentiment(None), ("Sin datos", "neutral"))

    @patch("daily_report.feedparser.parse")
    @patch("daily_report.requests.get")
    def test_fetch_headlines_keeps_only_report_date(
        self, get_mock, parse_mock
    ):
        get_mock.return_value = SimpleNamespace(
            content=b"<rss/>",
            raise_for_status=lambda: None,
        )
        parse_mock.return_value = SimpleNamespace(
            bozo=False,
            entries=[
                SimpleNamespace(
                    title="Company reports strong growth",
                    link="https://example.com/today",
                    source={"title": "Example News"},
                    published_parsed=(2026, 9, 28, 13, 30, 0, 0, 0, 0),
                ),
                SimpleNamespace(
                    title="Old headline",
                    link="https://example.com/yesterday",
                    source={"title": "Example News"},
                    published_parsed=(2026, 9, 27, 13, 30, 0, 0, 0, 0),
                ),
                SimpleNamespace(
                    title="Unsafe headline",
                    link="javascript:alert(1)",
                    source={"title": "Example News"},
                    published_parsed=(2026, 9, 28, 13, 30, 0, 0, 0, 0),
                ),
            ],
        )
        headlines, warning = fetch_headlines(
            Company("Example Co", "EX"),
            date(2026, 9, 28),
            SentimentIntensityAnalyzer(),
        )

        self.assertIsNone(warning)
        self.assertEqual(len(headlines), 1)
        self.assertEqual(headlines[0].source, "Example News")
        self.assertGreater(headlines[0].sentiment, 0)
        self.assertIn("after%3A2026-09-27", get_mock.call_args.args[0])

    @patch("daily_report.get_fx_rate", return_value=1.0)
    @patch("daily_report.yf.Ticker")
    def test_fetch_quote_reads_peg_ratio(self, ticker_mock, _fx_mock):
        ticker_mock.return_value.get_info.return_value = {
            "currency": "USD",
            "pegRatio": 1.15,
        }

        quote = fetch_quote(Company("Example Co", "EX"))

        self.assertEqual(quote.peg_ratio, 1.15)
        self.assertIsNone(quote.warning)

    @patch("daily_report.get_fx_rate", return_value=1.0)
    @patch("daily_report.yf.Ticker")
    def test_fetch_quote_falls_back_to_trailing_peg_ratio(self, ticker_mock, _fx_mock):
        ticker_mock.return_value.get_info.return_value = {
            "currency": "USD",
            "pegRatio": None,
            "trailingPegRatio": 2.4,
        }

        quote = fetch_quote(Company("Example Co", "EX"))

        self.assertEqual(quote.peg_ratio, 2.4)

    def test_summary_aggregates_news_and_analyst_consensus(self):
        reports = [
            StockReport(
                Company("Example A", "EXA"),
                news_sentiment=0.3,
                quote=Quote(analyst_consensus="Compra"),
            ),
            StockReport(
                Company("Example B", "EXB"),
                news_sentiment=-0.3,
                quote=Quote(analyst_consensus="Mantener"),
            ),
            StockReport(Company("Example C", None)),
        ]
        summary = executive_summary(reports)
        self.assertEqual(summary["sentiment_label"], "Neutral")
        self.assertEqual(summary["companies_with_news"], 2)
        self.assertEqual(summary["analyst_total"], 2)
        self.assertEqual(summary["headline_count"], 0)

    def test_dashboard_escapes_external_content_and_renders_all_cards(self):
        headline = Headline(
            title='<script>alert("x")</script>',
            url="https://example.com/news?a=1&b=2",
            source="Example & Co",
            published=datetime(2026, 9, 28, 13, 30, tzinfo=timezone.utc),
            sentiment=0.7,
        )
        reports = [
            StockReport(
                Company("Example <Company>", "EX"),
                headlines=[headline],
                quote=Quote(
                    daily_change_pct=1.25,
                    daily_change_abs_eur=1.0,
                    analyst_consensus="Compra",
                    analyst_count=8,
                    target_mean_eur=123.45,
                    peg_ratio=1.15,
                ),
                news_sentiment=0.7,
            ),
            StockReport(Company("Private Example", None)),
        ]
        html = render_dashboard(
            reports,
            generated_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        )
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>alert", html)
        self.assertIn("Example &lt;Company&gt;", html)
        self.assertIn("Private Example", html)
        self.assertIn("1.25%", html)
        self.assertIn("No hay ticker público configurado", html)
        self.assertIn('data-sentiment="bullish"', html)
        self.assertIn("el PEG es una referencia informativa, no una recomendación de inversión.", html)
        self.assertIn('<span title="Price/Earnings-to-Growth; ratio informativo">PEG</span>', html)
        self.assertIn('<strong class="text-bullish">1.15</strong>', html)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px;", html)

    def test_dashboard_styles_high_peg_as_bearish_and_missing_peg_as_unavailable(self):
        reports = [
            StockReport(Company("High PEG Co", "HIGH"), quote=Quote(peg_ratio=2.1)),
            StockReport(Company("Missing PEG Co", "MISS")),
        ]

        html = render_dashboard(reports)

        self.assertIn('<strong class="text-bearish">2.10</strong></div>', html)
        self.assertIn('<span title="Price/Earnings-to-Growth; ratio informativo">PEG</span><strong class="">—</strong>', html)

    def test_dashboard_orders_by_daily_performance_and_shows_report_price(self):
        reports = [
            StockReport(
                Company("Bearish Co", "BEAR"),
                quote=Quote(current_price_eur=40, daily_change_pct=9),
                news_sentiment=-0.6,
            ),
            StockReport(
                Company("No News Co", "NONE"),
                quote=Quote(current_price_eur=50),
            ),
            StockReport(
                Company("Neutral Co", "NEUT"),
                quote=Quote(current_price_eur=30, daily_change_pct=-9),
                news_sentiment=0,
            ),
            StockReport(
                Company("Bullish Co", "BULL"),
                quote=Quote(current_price_eur=20, daily_change_pct=-20),
                news_sentiment=0.7,
            ),
        ]

        html = render_dashboard(reports)

        positions = [html.index(f">{ticker}</p>") for ticker in ("BEAR", "NEUT", "BULL", "NONE")]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("<span>Precio actual</span><strong>20.00 €</strong>", html)


if __name__ == "__main__":
    unittest.main()
