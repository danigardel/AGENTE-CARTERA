import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from daily_report import (
    Company,
    Headline,
    PortfolioAsset,
    PortfolioPosition,
    PORTFOLIO_PERIODS,
    Quote,
    StockReport,
    classify_peg,
    classify_sentiment,
    executive_summary,
    fetch_portfolio,
    fetch_headlines,
    fetch_quote,
    render_dashboard,
    translate_headline,
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
            "trailingPE": 28.5,
        }

        quote = fetch_quote(Company("Example Co", "EX"))

        self.assertEqual(quote.peg_ratio, 1.15)
        self.assertEqual(quote.pe_ratio, 28.5)
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

    @patch("daily_report.requests.get")
    def test_translate_headline_requests_spanish_and_joins_translation_parts(self, get_mock):
        get_mock.return_value = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: [[["Las acciones", "Stocks"], [" suben", " rise"]]],
        )

        translated, warning = translate_headline("Stocks rise")

        self.assertEqual(translated, "Las acciones suben")
        self.assertIsNone(warning)
        self.assertEqual(get_mock.call_args.kwargs["params"]["tl"], "es")

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
            title_es="Titular traducido",
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
                    pe_ratio=27.4,
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
        self.assertIn("Titular traducido", html)
        self.assertIn('Original: &lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;', html)
        self.assertIn("Example &lt;Company&gt;", html)
        self.assertIn("Private Example", html)
        self.assertIn("1.25%", html)
        self.assertIn("No hay ticker público configurado", html)
        self.assertIn('data-sentiment="bullish"', html)
        self.assertIn("el PEG es una referencia informativa, no una recomendación de inversión.", html)
        self.assertIn('<span title="Price/Earnings-to-Growth; ratio informativo">PEG</span>', html)
        self.assertIn('<strong class="text-orange">1.15</strong>', html)
        self.assertIn('<span title="PER basado en beneficios de los últimos doce meses">PER (TTM)</span>', html)
        self.assertIn('<span title="PER basado en beneficios de los últimos doce meses">PER (TTM)</span><strong>27.40</strong>', html)
        self.assertIn("<strong>—</strong></div>", html)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px;", html)
        self.assertIn('<section aria-labelledby="portfolio-title">', html)
        self.assertLess(html.index("Mi cartera"), html.index("Lista de seguimiento"))

    def test_dashboard_styles_high_peg_as_bearish_and_missing_peg_as_unavailable(self):
        reports = [
            StockReport(Company("High PEG Co", "HIGH"), quote=Quote(peg_ratio=2.1)),
            StockReport(Company("Missing PEG Co", "MISS")),
        ]

        html = render_dashboard(reports)

        self.assertIn('<strong class="text-bearish">2.10</strong></div>', html)
        self.assertIn('<span title="Price/Earnings-to-Growth; ratio informativo">PEG</span><strong class="">—</strong>', html)

    def test_peg_color_thresholds(self):
        for peg, css in [(None, ""), (-0.4, ""), (0.99, "text-bullish"), (1.0, "text-orange"),
                         (1.5, "text-orange"), (1.51, "text-bearish")]:
            with self.subTest(peg=peg):
                self.assertEqual(classify_peg(peg), css)

    def test_dashboard_has_sort_select_and_card_data(self):
        html = render_dashboard([
            StockReport(
                Company("A Co", "A"),
                quote=Quote(daily_change_pct=1.5, peg_ratio=0.9, analyst_consensus="Compra", analyst_count=12),
                news_sentiment=0.4,
            )
        ])
        self.assertIn('<select id="sort-select"', html)
        self.assertNotIn("Ordenada por rendimiento de hoy", html)
        self.assertIn(
            'data-daily="1.5000" data-peg="0.9000" data-consensus="Compra" data-analysts="12" data-news="Alcista"',
            html,
        )

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

    @patch("daily_report.yf.Ticker")
    def test_fetch_portfolio_converts_historical_prices_and_calculates_returns(self, ticker_mock):
        as_of = date(2025, 1, 11)
        asset = PortfolioAsset("Example", "EX", 2, 100, "USD")
        dates = pd.date_range("2024-01-01", "2025-01-11", freq="D")
        stock_history = pd.DataFrame(
            {"Close": [100.0 if day.date() == date(2025, 1, 10) else 110.0 for day in dates]},
            index=dates,
        )
        fx_history = pd.DataFrame({"Close": [0.9] * len(dates)}, index=dates)

        def ticker_factory(symbol):
            history = fx_history if symbol == "USDEUR=X" else stock_history
            return SimpleNamespace(history=lambda **_kwargs: history)

        ticker_mock.side_effect = ticker_factory

        positions = fetch_portfolio((asset,), as_of=as_of)

        position = positions[0]
        self.assertAlmostEqual(position.prices_eur["Hoy"], 99.0)
        self.assertAlmostEqual(position.prices_eur["1D"], 90.0)
        self.assertAlmostEqual(position.returns_pct["1D"], 10.0)
        self.assertAlmostEqual(position.current_value_eur, 198.0)
        self.assertAlmostEqual(position.max_profit_eur, 98.0)
        self.assertAlmostEqual(position.max_return_pct, 98.0)
        self.assertEqual(tuple(position.returns_pct), tuple(period for period, _ in PORTFOLIO_PERIODS))

    def test_dashboard_renders_portfolio_price_history_and_max_returns(self):
        asset = PortfolioAsset("Example ETF", "ETF.DE", 1.5, 100, "EUR")
        position = PortfolioPosition(
            asset=asset,
            prices_eur={"Hoy": 120, "1D": 110, "1W": 100, "1M": 90, "6M": 80, "1Y": 70},
            returns_pct={"1D": 9.09, "1W": 20, "1M": 33.33, "6M": 50, "1Y": 71.43},
            current_value_eur=180,
            max_profit_eur=80,
            max_return_pct=80,
        )

        html = render_dashboard([], portfolio=[position])

        self.assertIn('<th scope="col" id="portfolio-selected-heading">Rendimiento 1D</th>', html)
        self.assertIn('data-portfolio-period="1D" data-portfolio-kind="close">110.00 €</td>', html)
        self.assertIn('data-portfolio-period="1Y" data-portfolio-kind="close">70.00 €</td>', html)
        self.assertIn('data-portfolio-period="1W" data-portfolio-kind="return" class="text-bullish">+20.00%', html)
        self.assertIn('data-portfolio-period="MAX" data-portfolio-kind="profit" class="text-bullish">80.00 €', html)
        self.assertIn('<select id="portfolio-period" class="sort-select">', html)
        self.assertIn('<option value="MAX">MAX</option>', html)
        self.assertIn("portfolio-period", html)
        self.assertIn("portfolio-metric", html)
        self.assertIn('<th scope="col">Valor actual</th>', html)
        self.assertIn("80.00 €", html)


if __name__ == "__main__":
    unittest.main()
