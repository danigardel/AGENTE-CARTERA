import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from daily_report import (
    Company,
    COMPANIES,
    Headline,
    DashboardCharts,
    FinancialNewsItem,
    GlobalMarketNews,
    MACRO_FEEDS,
    PortfolioAnalysis,
    PortfolioAsset,
    PortfolioPosition,
    PORTFOLIO_PERIODS,
    Quote,
    StockReport,
    TIMEZONE,
    _grounding_rejected,
    _model_pool_allows_fallback,
    _read_gemini_cache,
    _gemini_request_warning,
    analyze_portfolio,
    build_analysis_prompt,
    build_request_body,
    classify_peg,
    classify_sentiment,
    collect_market_radar,
    collect_chart_data,
    extra_quote_fields,
    external_ideas_html,
    executive_summary,
    parse_external_ideas,
    post_gemini,
    fetch_portfolio,
    fetch_headlines,
    fetch_financial_news,
    _fetch_macro_feed,
    fetch_quote,
    render_dashboard,
    translate_headline,
)


class SentimentTests(unittest.TestCase):
    def setUp(self):
        news_patcher = patch("daily_report.collect_financial_news", return_value=[])
        self.collect_news_mock = news_patcher.start()
        self.addCleanup(news_patcher.stop)
        radar_patcher = patch("daily_report.collect_market_radar", return_value=[])
        self.collect_radar_mock = radar_patcher.start()
        self.addCleanup(radar_patcher.stop)

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
            "regularMarketTime": datetime(
                2026, 10, 9, 15, 30, tzinfo=timezone.utc
            ).timestamp(),
            "exchangeTimezoneName": "America/New_York",
        }

        quote = fetch_quote(Company("Example Co", "EX"))

        self.assertEqual(quote.peg_ratio, 1.15)
        self.assertEqual(quote.pe_ratio, 28.5)
        self.assertEqual(quote.market_date, date(2026, 10, 9))
        self.assertEqual(quote.market_timezone, "America/New_York")
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

    @patch("daily_report.yf.Ticker")
    def test_fetch_quote_turns_json_decode_error_into_quote_warning(self, ticker_mock):
        ticker_mock.return_value.get_info.side_effect = json.JSONDecodeError(
            "Invalid JSON", "{", 1
        )

        quote = fetch_quote(Company("Invalid ticker", "INVALID"))

        self.assertIn("JSONDecodeError", quote.warning)
        self.assertIn("Yahoo Finance", quote.warning)

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
        self.assertLess(
            html.index('<h2 id="portfolio-title">Mi cartera'),
            html.index('<h2 id="stocks-title">Lista de seguimiento'),
        )
        self.assertIn("d3@7.9.0/dist/d3.min.js", html)
        self.assertIn('id="watchlist-heatmap"', html)
        self.assertIn('id="portfolio-heatmap"', html)

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

    def test_dashboard_separates_stale_market_quotes_after_fresh_quotes(self):
        generated_at = datetime(2026, 10, 9, 10, 0, tzinfo=TIMEZONE)
        reports = [
            StockReport(
                Company("US company", "US"),
                quote=Quote(
                    daily_change_pct=10,
                    market_date=date(2026, 10, 8),
                    market_timezone="America/New_York",
                ),
            ),
            StockReport(
                Company("European company", "EU"),
                quote=Quote(
                    daily_change_pct=-1,
                    market_date=date(2026, 10, 9),
                    market_timezone="Europe/Paris",
                ),
            ),
            StockReport(Company("Unknown quote", "NONE")),
        ]

        html = render_dashboard(reports, generated_at=generated_at)

        self.assertLess(html.index(">EU</p>"), html.index(">US</p>"))
        self.assertLess(html.index(">US</p>"), html.index(">NONE</p>"))
        self.assertIn('data-current-day="true"', html)
        self.assertIn('data-current-day="false"', html)
        self.assertIn("Cotizaciones pendientes de actualización", html)
        self.assertIn("último dato: 08/10/2026", html)

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
        self.assertIn('data-portfolio-period="1D" data-portfolio-kind="return" class="text-bullish">+9.09%', html)
        self.assertIn('data-portfolio-period="1Y" data-portfolio-kind="return" class="text-bullish">+71.43%', html)
        self.assertIn('data-portfolio-period="1W" data-portfolio-kind="return" class="text-bullish">+20.00%', html)
        self.assertIn('data-portfolio-period="MAX" data-portfolio-kind="profit" class="text-bullish">80.00 €', html)
        self.assertIn('<select id="portfolio-period" class="sort-select">', html)
        self.assertIn('<option value="MAX">MAX</option>', html)
        self.assertIn("portfolio-period", html)
        self.assertIn("portfolio-metric", html)
        self.assertIn('<th scope="col" data-portfolio-max-only="true" hidden>Inversión base</th>', html)
        self.assertIn('<th scope="col" data-portfolio-max-only="true" hidden>Valor actual</th>', html)
        self.assertIn('data-label="Precio actual">120.00 €', html)
        self.assertIn('data-label="Valor actual" data-portfolio-max-only="true" hidden>180.00 €', html)
        self.assertIn("80.00 €", html)

    def test_dashboard_embeds_chart_data_safely_and_shows_last_ai_query_time(self):
        charts = DashboardCharts(
            watchlist=[
                {"ticker": "</script>", "name": "Unsafe", "change_pct": 1.2}
            ],
            portfolio=[
                {"ticker": "EX", "name": "Example", "change_pct": -0.5}
            ],
            watchlist_series=[
                {"timestamp": "2026-10-07T09:00:00+00:00", "value": 0.25}
            ],
            portfolio_series=[
                {"timestamp": "2026-10-07T09:00:00+00:00", "value": -0.5}
            ],
        )
        analysis = PortfolioAnalysis(
            portfolio_assessment="Evaluación",
            diversification="Diversificación",
            recommended_changes="Cambios",
            market_context="Mercado",
            last_updated_utc="2026-10-07T09:15:00+00:00",
        )

        html = render_dashboard([], analysis=analysis, chart_data=charts)

        self.assertIn('id="dashboard-chart-data"', html)
        self.assertIn(r"\u003c/script\u003e", html)
        self.assertIn("Rendimiento medio de la lista de seguimiento", html)
        self.assertIn("07/10/2026 · 11:15 CEST", html)

    def test_watchlist_has_all_companies_except_unavailable_space_innovators(self):
        tickers = {company.ticker for company in COMPANIES}
        self.assertEqual(len(COMPANIES), 48)
        self.assertEqual(
            [(company.name, company.ticker) for company in COMPANIES[:3]],
            [("Intel", "INTC"), ("Netflix", "NFLX"), ("Microsoft", "MSFT")],
        )
        self.assertNotIn("YODA.L", tickers)
        self.assertIsNone(next(company.ticker for company in COMPANIES if company.name == "SpaceX"))

    @patch("daily_report.feedparser.parse")
    @patch("daily_report.requests.get")
    def test_yahoo_finance_rss_collects_only_today_and_cleans_summaries(
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
                    title="Company reports <b>growth</b>",
                    link="https://finance.yahoo.com/news/example",
                    published_parsed=(2026, 10, 11, 9, 0, 0, 0, 0, 0),
                    summary="<p>Revenue &amp; profits increased.</p>",
                ),
                SimpleNamespace(
                    title="Old story",
                    link="https://finance.yahoo.com/news/old",
                    published_parsed=(2026, 10, 10, 12, 0, 0, 0, 0, 0),
                    summary="Yesterday",
                ),
            ],
        )

        news = fetch_financial_news(
            Company("Example Corp", "EX"),
            date(2026, 10, 11),
        )

        self.assertEqual(len(news), 1)
        self.assertEqual(news[0].ticker, "EX")
        self.assertEqual(news[0].title, "Company reports growth")
        self.assertEqual(news[0].summary, "Revenue & profits increased.")
        self.assertIn("s=EX", get_mock.call_args.args[0])

    @patch("daily_report.requests.get", side_effect=requests.Timeout("RSS timeout"))
    def test_yahoo_finance_rss_failure_returns_no_news(self, get_mock):
        news = fetch_financial_news(
            Company("Example Corp", "EX"),
            date(2026, 10, 11),
        )

        self.assertEqual(news, [])
        get_mock.assert_called_once()

    @patch("daily_report.feedparser.parse")
    @patch("daily_report.requests.get")
    def test_macro_feed_keeps_english_title_source_and_today_date(
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
                    title="Markets rally after earnings beat",
                    link="https://news.example/earnings",
                    source={"title": "Example News"},
                    published_parsed=(2026, 10, 11, 9, 0, 0, 0, 0, 0),
                ),
                SimpleNamespace(
                    title="Yesterday story",
                    link="https://news.example/yesterday",
                    published_parsed=(2026, 10, 10, 9, 0, 0, 0, 0, 0),
                ),
            ],
        )

        news = _fetch_macro_feed(
            "Fallback source",
            "https://feeds.example/markets.xml",
            date(2026, 10, 11),
        )

        self.assertEqual(len(news), 1)
        self.assertEqual(news[0].title, "Markets rally after earnings beat")
        self.assertEqual(news[0].source, "Example News")
        self.assertEqual(news[0].published.date(), date(2026, 10, 11))
        self.assertEqual(news[0].url, "https://news.example/earnings")

    @patch("daily_report._fetch_macro_feed")
    def test_collect_market_radar_sorts_newest_and_caps_total(self, fetch_mock):
        reference = datetime(2026, 10, 11, 12, 0, tzinfo=TIMEZONE)
        fetch_mock.side_effect = [
            [
                GlobalMarketNews(
                    title=f"Headline {index}",
                    source=source,
                    published=reference - timedelta(minutes=index),
                    url=f"https://news.example/{index}",
                )
                for index in range(100)
            ]
            for source, _ in MACRO_FEEDS
        ]

        news = collect_market_radar(reference)

        self.assertEqual(len(news), 180)
        self.assertEqual(news[0].published, reference)
        self.assertEqual(news[-1].published, reference - timedelta(minutes=59))
        self.assertEqual(fetch_mock.call_count, len(MACRO_FEEDS))

    @patch(
        "daily_report._fetch_macro_feed",
        side_effect=[requests.Timeout("feed timeout"), []],
    )
    def test_collect_market_radar_omits_failed_feed_and_continues(self, fetch_mock):
        two_feeds_patch = patch(
            "daily_report.MACRO_FEEDS",
            MACRO_FEEDS[:2],
        )
        two_feeds_patch.start()
        self.addCleanup(two_feeds_patch.stop)

        news = collect_market_radar(
            datetime(2026, 10, 11, 12, 0, tzinfo=TIMEZONE)
        )

        self.assertEqual(news, [])
        self.assertEqual(fetch_mock.call_count, 2)

    def test_analysis_prompt_uses_technical_fallback_when_news_are_missing(self):
        prompt = build_analysis_prompt(
            {},
            datetime(2026, 10, 11, 12, 0, tzinfo=TIMEZONE),
        )

        self.assertIn("Aquí tienes las noticias de hoy:", prompt)
        self.assertIn("No se recibieron noticias recientes del RSS.", prompt)
        self.assertIn("haz un análisis técnico básico", prompt)
        self.assertIn("global_market_radar", prompt)
        self.assertIn("external_ideas", prompt)

    @patch("daily_report.yf.download")
    def test_collect_chart_data_aggregates_intraday_returns_for_watchlist_and_portfolio(self, download_mock):
        index = pd.date_range(
            "2026-10-07 08:00",
            periods=2,
            freq="5min",
            tz="UTC",
        )
        columns = pd.MultiIndex.from_product(
            [["AAA", "BBB"], ["Close"]],
            names=["Ticker", "Price"],
        )
        history = pd.DataFrame(
            [[101, 99], [102, 98]],
            index=index,
            columns=columns,
        )
        download_mock.return_value = history
        reports = [
            StockReport(
                Company("Alpha", "AAA"),
                quote=Quote(
                    current_price_eur=102,
                    previous_close=100,
                    daily_change_pct=2,
                ),
            ),
            StockReport(
                Company("Beta", "BBB"),
                quote=Quote(
                    current_price_eur=98,
                    previous_close=100,
                    daily_change_pct=-2,
                ),
            ),
        ]
        portfolio = [
            PortfolioPosition(
                asset=PortfolioAsset("Alpha", "AAA", 1, 100, "EUR"),
                prices_eur={"Hoy": 102, "1D": 100},
                returns_pct={"1D": 2},
            )
        ]

        charts = collect_chart_data(reports, portfolio)

        self.assertEqual(len(charts.watchlist), 2)
        self.assertEqual([point["value"] for point in charts.watchlist_series], [0, 0])
        for actual, expected in zip(
            [point["value"] for point in charts.portfolio_series],
            [1, 2],
            strict=True,
        ):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(charts.portfolio[0]["ticker"], "AAA")
        download_mock.assert_called_once()

    @patch("daily_report.yf.download", return_value=pd.DataFrame())
    def test_portfolio_heatmap_items_use_portfolio_weights_and_quote_freshness(
        self, _download_mock
    ):
        reports = [
            StockReport(
                Company("Current asset", "CUR"),
                quote=Quote(
                    daily_change_pct=1.5,
                    market_date=date(2026, 10, 9),
                    market_timezone="Europe/Paris",
                ),
            ),
            StockReport(
                Company("Stale asset", "OLD"),
                quote=Quote(
                    daily_change_pct=9,
                    market_date=date(2026, 10, 8),
                    market_timezone="America/New_York",
                ),
            ),
        ]
        portfolio = [
            PortfolioPosition(
                asset=PortfolioAsset("Current asset", "CUR", 1, 50, "EUR"),
                current_value_eur=50,
                price_date=date(2026, 10, 9),
            ),
            PortfolioPosition(
                asset=PortfolioAsset("Stale asset", "OLD", 1, 50, "USD"),
                current_value_eur=50,
                price_date=date(2026, 10, 8),
            ),
        ]

        charts = collect_chart_data(reports, portfolio)
        html = render_dashboard(
            reports,
            generated_at=datetime(2026, 10, 9, 10, 0, tzinfo=TIMEZONE),
            portfolio=portfolio,
            chart_data=charts,
        )
        payload = json.loads(
            html.split('id="dashboard-chart-data">', 1)[1].split("</script>", 1)[0]
        )

        self.assertEqual(
            [item["weight_pct"] for item in charts.portfolio],
            [50, 50],
        )
        self.assertEqual(
            [item["current_day"] for item in payload["portfolio"]],
            [True, False],
        )
        self.assertIsNone(payload["portfolio"][1]["change_pct"])
        self.assertIn(".attr('class', 'heatmap-name')", html)
        self.assertIn("Number(item.weight_pct)", html)
        self.assertIn("'var(--bg)'", html)
        self.assertIn(".heatmap-name, .heatmap-return { fill: #000;", html)

    @patch("daily_report.requests.post")
    def test_analyze_portfolio_calls_standard_flash_api_with_portfolio_and_watchlist(self, post_mock):
        reports = [
            StockReport(
                Company("Example Inc.", "EX"),
                quote=Quote(
                    current_price_eur=42,
                    daily_change_pct=1.2,
                    peg_ratio=1.4,
                    analyst_consensus="Compra",
                    target_mean_eur=50,
                    sector="Technology",
                    extra={
                        "earnings_date": "2026-10-20",
                        "pct_from_52w_high": -12.5,
                    },
                ),
                news_sentiment=0.3,
            )
        ]
        portfolio = [
            PortfolioPosition(
                asset=PortfolioAsset("Example Inc.", "EX", 2, 80, "EUR"),
                prices_eur={"Hoy": 42},
                current_value_eur=84,
            )
        ]
        result = {
            "portfolio_assessment": "Posición pequeña y positiva.",
            "diversification": "Falta diversificación.",
            "recommended_changes": "Evitar concentrar más en tecnología.",
            "top_buys": [
                {"ticker": "EX", "name": "Example Inc.", "reason": "Datos favorables."},
                {"ticker": "FAKE", "name": "Invented Co.", "reason": "No está en lista."},
            ],
            "sell_candidates": [
                {"ticker": "FAKE", "name": "Invented Co.", "reason": "No está en cartera."}
            ],
            "market_context": "Sentimiento positivo.",
            "risks": [],
            "external_ideas": [
                {
                    "category": "<script>alert(1)</script>",
                    "examples": "ETF global & defensivo",
                    "reason": "Diversifica <b>riesgo</b>.",
                }
            ],
        }
        post_mock.return_value = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "candidates": [
                    {"content": {"parts": [{"text": json.dumps(result)}]}}
                ]
            },
        )
        self.collect_news_mock.return_value = [
            FinancialNewsItem(
                ticker="EX",
                company="Example Inc.",
                title="Example announces quarterly results",
                summary="Revenue rises year over year.",
                url="https://finance.yahoo.com/news/example",
                published=datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc),
            )
        ]
        self.collect_radar_mock.return_value = [
            GlobalMarketNews(
                title="Global markets set new record",
                source="CNBC Top News",
                published=datetime(2026, 10, 11, 11, 0, tzinfo=timezone.utc),
                url="https://www.cnbc.com/example",
            )
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            analysis = analyze_portfolio(
                reports,
                portfolio,
                api_key="secret-test-key",
                cache_path=Path(temp_dir) / "gemini_cache.json",
            )

        self.assertEqual(analysis.portfolio_assessment, "Posición pequeña y positiva.")
        self.assertEqual([item["ticker"] for item in analysis.top_buys], ["EX"])
        self.assertEqual(analysis.sell_candidates, [])
        self.assertEqual(len(analysis.external_ideas), 1)
        self.assertTrue(any("Se omitieron recomendaciones" in risk for risk in analysis.risks))
        endpoint, = post_mock.call_args.args
        self.assertEqual(
            endpoint,
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent",
        )
        self.assertNotIn("secret-test-key", endpoint)
        self.assertEqual(post_mock.call_args.kwargs["headers"]["x-goog-api-key"], "secret-test-key")
        prompt = post_mock.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"]
        self.assertIn("Aquí tienes las noticias de hoy:", prompt)
        self.assertIn("Example announces quarterly results", prompt)
        self.assertIn("Revenue rises year over year.", prompt)
        self.assertIn("https://finance.yahoo.com/news/example", prompt)
        self.assertIn('"global_market_radar": [', prompt)
        self.assertIn('"title": "Global markets set new record"', prompt)
        self.assertIn('"source": "CNBC Top News"', prompt)
        self.assertIn('"url": "https://www.cnbc.com/example"', prompt)
        self.assertIn('"quantity": 2', prompt)
        self.assertIn('"price_eur": 42', prompt)
        self.assertIn('"global_news_sentiment": "Alcista"', prompt)
        self.assertIn('"sector": "Technology"', prompt)
        self.assertIn("3 a 5 mejores candidatas", prompt)
        self.assertIn('"external_ideas"', prompt)
        self.assertIn('"investment_base_eur": 80', prompt)
        self.assertIn('"weight_pct": 100.0', prompt)
        self.assertIn('"earnings_date"', prompt)
        self.assertEqual(
            post_mock.call_args.kwargs["json"]["generationConfig"]["thinkingConfig"],
            {"thinkingBudget": 2048},
        )
        self.assertIn("google_search", post_mock.call_args.kwargs["json"]["tools"][0])

        html = render_dashboard(reports, portfolio=portfolio, analysis=analysis)
        self.assertIn("Análisis de cartera con Gemini Flash", html)
        self.assertIn("Posición pequeña y positiva.", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("Invented Co.", html)

    @patch("daily_report.requests.post")
    def test_gemini_model_not_found_retries_with_fallback_model(self, post_mock):
        unavailable = SimpleNamespace(
            status_code=404,
            json=lambda: {
                "error": {"message": "models/gemini-3.8-flash is not found"}
            },
        )
        failed_response = SimpleNamespace(
            raise_for_status=lambda: (_ for _ in ()).throw(
                requests.HTTPError(response=unavailable)
            )
        )
        result = {
            "portfolio_assessment": "Evaluación válida",
            "diversification": "Diversificada",
            "recommended_changes": "Sin cambios",
            "top_buys": [],
            "sell_candidates": [],
            "market_context": "Contexto actual",
            "risks": [],
        }
        successful_response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "candidates": [
                    {"content": {"parts": [{"text": json.dumps(result)}]}}
                ]
            },
        )
        post_mock.side_effect = [failed_response, successful_response]

        with tempfile.TemporaryDirectory() as temp_dir:
            analysis = analyze_portfolio(
                [],
                [],
                api_key="test-key",
                cache_path=Path(temp_dir) / "gemini_cache.json",
            )

        self.assertEqual(analysis.portfolio_assessment, "Evaluación válida")
        self.assertEqual(
            [call.args[0] for call in post_mock.call_args_list],
            [
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-3.8-flash:generateContent",
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-3.7-flash:generateContent",
            ],
        )
        self.assertEqual(
            post_mock.call_args.kwargs["json"]["generationConfig"]["thinkingConfig"],
            {"thinkingBudget": 2048},
        )
        self.assertIn("google_search", post_mock.call_args.kwargs["json"]["tools"][0])

    def test_yahoo_quote_enrichment_and_optional_external_ideas_are_safe(self):
        now = datetime(2026, 10, 8, 12, 0, tzinfo=TIMEZONE)
        metrics = extra_quote_fields(
            {
                "currentPrice": 90,
                "fiftyTwoWeekHigh": 100,
                "fiftyTwoWeekLow": 60,
                "fiftyDayAverage": 80,
                "twoHundredDayAverage": 75,
                "forwardPE": 20,
                "revenueGrowth": 0.12,
                "beta": 1.25,
                "industry": "Semiconductors",
            },
            now,
        )
        self.assertEqual(metrics["pct_from_52w_high"], -10.0)
        self.assertEqual(metrics["pct_above_52w_low"], 50.0)
        self.assertEqual(metrics["pct_vs_sma50"], 12.5)
        self.assertEqual(metrics["pct_vs_sma200"], 20.0)
        self.assertEqual(metrics["revenue_growth_pct"], 12.0)
        self.assertEqual(metrics["industry"], "Semiconductors")

        valid = {
            "category": "Salud",
            "examples": "<img src=x>",
            "reason": "Un hueco diversificador.",
        }
        ideas = parse_external_ideas(
            {"external_ideas": [valid, {"category": "inválida"}]}
        )
        self.assertEqual(len(ideas), 1)
        safe_html = external_ideas_html(ideas)
        self.assertIn("&lt;img src=x&gt;", safe_html)
        self.assertNotIn("<img src=x>", safe_html)

    @patch("daily_report.requests.post")
    def test_gemini_request_uses_google_search_and_returns_response(self, post_mock):
        response = SimpleNamespace(raise_for_status=lambda: None)
        post_mock.return_value = response

        actual_response = post_gemini("https://example.test", "key", "prompt")

        self.assertIs(actual_response, response)
        self.assertEqual(post_mock.call_count, 1)
        self.assertIn("google_search", post_mock.call_args.kwargs["json"]["tools"][0])
        self.assertEqual(post_mock.call_args.kwargs["timeout"], (10, 60))
        self.assertNotIn("tools", build_request_body("prompt", grounded=False))
        self.assertEqual(
            build_request_body("prompt")["generationConfig"]["thinkingConfig"],
            {"thinkingBudget": 2048},
        )

    @patch("daily_report.requests.post")
    def test_grounding_quota_rejection_retries_without_grounding_same_key(
        self, post_mock
    ):
        rejection = SimpleNamespace(
            status_code=429,
            json=lambda: {
                "error": {
                    "message": "Google Search tool quota exceeded (limit: 0)"
                }
            },
        )
        grounded_failure = SimpleNamespace(
            raise_for_status=lambda: (_ for _ in ()).throw(
                requests.HTTPError(response=rejection)
            )
        )
        plain_success = SimpleNamespace(raise_for_status=lambda: None)
        post_mock.side_effect = [grounded_failure, plain_success]

        result = post_gemini(
            "https://example.test/model:generateContent",
            "test-key",
            "prompt",
        )

        self.assertIs(result, plain_success)
        self.assertEqual(post_mock.call_count, 2)
        grounded_call, plain_call = post_mock.call_args_list
        self.assertEqual(grounded_call.args, plain_call.args)
        self.assertEqual(
            grounded_call.kwargs["headers"]["x-goog-api-key"],
            plain_call.kwargs["headers"]["x-goog-api-key"],
        )
        self.assertIn("tools", grounded_call.kwargs["json"])
        self.assertNotIn("tools", plain_call.kwargs["json"])
        self.assertEqual(grounded_call.kwargs["timeout"], (10, 60))
        self.assertEqual(plain_call.kwargs["timeout"], (10, 45))

    @patch("daily_report.requests.post")
    def test_generic_429_triggers_grounding_retry_without_keyword_match(
        self, post_mock
    ):
        quota = SimpleNamespace(
            status_code=429,
            json=lambda: {"error": {"message": "You exceeded your current quota"}},
        )
        grounded_failure = SimpleNamespace(
            raise_for_status=lambda: (_ for _ in ()).throw(
                requests.HTTPError(response=quota)
            )
        )
        fallback_quota = SimpleNamespace(
            status_code=429,
            json=lambda: {"error": {"message": "You exceeded your current quota"}},
        )
        fallback_failure = SimpleNamespace(
            raise_for_status=lambda: (_ for _ in ()).throw(
                requests.HTTPError(response=fallback_quota)
            )
        )
        post_mock.side_effect = [grounded_failure, fallback_failure]

        with self.assertRaises(requests.HTTPError):
            post_gemini("https://example.test/model:generateContent", "test-key", "prompt")

        self.assertEqual(post_mock.call_count, 2)
        self.assertIn("tools", post_mock.call_args_list[0].kwargs["json"])
        self.assertNotIn("tools", post_mock.call_args_list[1].kwargs["json"])

    def test_grounding_rejection_uses_only_http_status(self):
        for status_code in (400, 403, 429):
            with self.subTest(status_code=status_code):
                response = SimpleNamespace(status_code=status_code)
                error = requests.HTTPError(response=response)
                self.assertTrue(_grounding_rejected(error))

        for status_code in (None, 500, 503):
            with self.subTest(status_code=status_code):
                response = (
                    None if status_code is None else SimpleNamespace(status_code=status_code)
                )
                error = requests.HTTPError(response=response)
                self.assertFalse(_grounding_rejected(error))

    def test_gemini_quota_warning_includes_provider_quota_dimensions(self):
        response = SimpleNamespace(
            status_code=429,
            json=lambda: {
                "error": {
                    "message": "Quota exceeded",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                            "violations": [
                                {
                                    "quotaMetric": "generate_content_free_tier_requests",
                                    "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                                    "quotaValue": "20",
                                    "quotaDimensions": {
                                        "model": "gemini-3.8-flash",
                                        "location": "global",
                                    },
                                }
                            ],
                        },
                        {
                            "@type": "type.googleapis.com/google.rpc.RetryInfo",
                            "retryDelay": "10h",
                        },
                    ],
                }
            },
        )
        error = requests.HTTPError(response=response)

        warning = _gemini_request_warning(
            error,
            api_keys=("secret-key",),
            request_errors=[error],
        )

        self.assertIn("generate_content_free_tier_requests", warning)
        self.assertIn("model=gemini-3.8-flash", warning)
        self.assertIn("valor=20", warning)
        self.assertIn("espera indicada por Google=10h", warning)
        self.assertNotIn("secret-key", warning)

    @patch("daily_report.requests.post")
    def test_analyze_portfolio_reports_missing_api_key_without_calling_provider(self, post_mock):
        with tempfile.TemporaryDirectory() as temp_dir:
            analysis = analyze_portfolio(
                [],
                [],
                api_key="",
                cache_path=Path(temp_dir) / "gemini_cache.json",
            )

        self.assertIn("GEMINI_API_KEY", analysis.warning)
        post_mock.assert_not_called()

        html = render_dashboard([], analysis=analysis)
        self.assertIn("configura el secreto GEMINI_API_KEY", html)

    @patch("daily_report.requests.post")
    def test_analyze_portfolio_shows_gemini_http_error_without_exposing_api_key(self, post_mock):
        response = SimpleNamespace(
            status_code=429,
            json=lambda: {"error": {"message": "Quota exceeded"}},
        )
        post_mock.return_value.raise_for_status.side_effect = requests.HTTPError(
            response=response
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            analysis = analyze_portfolio(
                [],
                [],
                api_key="secret-test-key",
                cache_path=Path(temp_dir) / "gemini_cache.json",
            )

        self.assertIn("HTTP 429", analysis.warning)
        self.assertIn("Quota exceeded", analysis.warning)
        self.assertNotIn("secret-test-key", analysis.warning)

    @patch("daily_report.requests.post")
    def test_gemini_pool_rotates_after_429_and_succeeds_with_next_key(self, post_mock):
        quota_response = SimpleNamespace(
            status_code=429,
            headers={},
            json=lambda: {"error": {"message": "Quota exceeded"}},
        )
        quota_result = SimpleNamespace(
            raise_for_status=lambda: (_ for _ in ()).throw(
                requests.HTTPError(response=quota_response)
            )
        )
        success_data = {
            "portfolio_assessment": "Evaluación válida",
            "diversification": "Diversificada",
            "recommended_changes": "Sin cambios",
            "top_buys": [],
            "sell_candidates": [],
            "market_context": "Mixto",
            "risks": [],
        }
        success_response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "candidates": [
                    {"content": {"parts": [{"text": json.dumps(success_data)}]}}
                ]
            },
        )
        post_mock.side_effect = [quota_result, quota_result, success_response]

        with tempfile.TemporaryDirectory() as temp_dir:
            analysis = analyze_portfolio(
                [],
                [],
                api_key=" first-key ,second-key,first-key ",
                cache_path=Path(temp_dir) / "gemini_cache.json",
            )

        self.assertEqual(analysis.portfolio_assessment, "Evaluación válida")
        self.assertEqual(post_mock.call_count, 3)
        self.assertEqual(
            [call.kwargs["headers"]["x-goog-api-key"] for call in post_mock.call_args_list],
            ["first-key", "first-key", "second-key"],
        )

    @patch("daily_report.requests.post")
    def test_gemini_pool_exhausts_keys_on_503_and_negative_caches_all_429(
        self, post_mock
    ):
        now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
        service_error_response = SimpleNamespace(
            status_code=503,
            headers={},
            json=lambda: {"error": {"message": "Temporary outage"}},
        )
        post_mock.return_value.raise_for_status.side_effect = requests.HTTPError(
            response=service_error_response
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "gemini_cache.json"
            service_failure = analyze_portfolio(
                [],
                [],
                api_key="primary,backup",
                cache_path=cache_path,
                now_utc=now,
            )
            self.assertIn("se agotaron los intentos", service_failure.warning)
            self.assertIn("HTTP 503", service_failure.warning)
            self.assertEqual(post_mock.call_count, 4)

            quota_response = SimpleNamespace(
                status_code=429,
                headers={},
                json=lambda: {"error": {"message": "Quota exceeded"}},
            )
            post_mock.reset_mock()
            post_mock.return_value.raise_for_status.side_effect = requests.HTTPError(
                response=quota_response
            )
            quota_cache_path = Path(temp_dir) / "quota-cache.json"
            all_quota = analyze_portfolio(
                [],
                [],
                api_key="primary,backup",
                cache_path=quota_cache_path,
                now_utc=now + timedelta(minutes=1),
            )
            cached = json.loads(quota_cache_path.read_text(encoding="utf-8"))
            suppressed = analyze_portfolio(
                [],
                [],
                api_key="primary,backup",
                cache_path=quota_cache_path,
                now_utc=now + timedelta(minutes=4),
            )
            retried = analyze_portfolio(
                [],
                [],
                api_key="primary,backup",
                cache_path=quota_cache_path,
                now_utc=now + timedelta(minutes=6),
            )

        self.assertIn("las 2 claves configuradas", all_quota.warning)
        self.assertIn("por proyecto", all_quota.warning)
        self.assertIn("no por clave API", all_quota.warning)
        self.assertIn("límite por minuto o por tokens", all_quota.warning)
        self.assertIn("Reintento breve pendiente", suppressed.warning)
        self.assertEqual(post_mock.call_count, 16)
        self.assertIn("HTTP 429", retried.warning)
        self.assertEqual(cached["next_api_key_index"], 1)
        self.assertEqual(cached["failure_policy"], "short_retry")
        self.assertEqual(
            [
                call.kwargs["headers"]["x-goog-api-key"]
                for call in post_mock.call_args_list
            ],
            [
                "primary",
                "primary",
                "backup",
                "backup",
                "primary",
                "primary",
                "backup",
                "backup",
                "backup",
                "backup",
                "primary",
                "primary",
                "backup",
                "backup",
                "primary",
                "primary",
            ],
        )
        self.assertIn("retry_after_utc", cached)
        self.assertNotIn("primary", json.dumps(cached))
        self.assertNotIn("backup", json.dumps(cached))
        self.assertIn("api_key_fingerprint", cached)

    @patch("daily_report.requests.post")
    def test_exhausted_primary_model_retries_pool_on_fallback_model(self, post_mock):
        unavailable = SimpleNamespace(
            status_code=503,
            json=lambda: {"error": {"message": "Service unavailable"}},
        )
        failed_response = SimpleNamespace(
            raise_for_status=lambda: (_ for _ in ()).throw(
                requests.HTTPError(response=unavailable)
            )
        )
        result = {
            "portfolio_assessment": "Evaluación válida",
            "diversification": "Diversificada",
            "recommended_changes": "Sin cambios",
            "top_buys": [],
            "sell_candidates": [],
            "market_context": "Contexto actual",
            "risks": [],
        }
        successful_response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "candidates": [
                    {"content": {"parts": [{"text": json.dumps(result)}]}}
                ]
            },
        )
        post_mock.side_effect = [
            failed_response,
            failed_response,
            successful_response,
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            analysis = analyze_portfolio(
                [],
                [],
                api_key="first,second",
                cache_path=Path(temp_dir) / "gemini_cache.json",
            )

        self.assertEqual(analysis.portfolio_assessment, "Evaluación válida")
        self.assertEqual(
            [
                call.args[0].split("/models/")[-1]
                for call in post_mock.call_args_list
            ],
            [
                "gemini-3.8-flash:generateContent",
                "gemini-3.8-flash:generateContent",
                "gemini-3.7-flash:generateContent",
            ],
        )
        self.assertEqual(
            [
                call.kwargs["headers"]["x-goog-api-key"]
                for call in post_mock.call_args_list
            ],
            ["first", "second", "first"],
        )

    @patch("daily_report.requests.post")
    def test_timeout_in_primary_pool_retries_with_fallback_model(self, post_mock):
        result = {
            "portfolio_assessment": "Evaluación válida",
            "diversification": "Diversificada",
            "recommended_changes": "Sin cambios",
            "top_buys": [],
            "sell_candidates": [],
            "market_context": "Contexto actual",
            "risks": [],
        }
        successful_response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "candidates": [
                    {"content": {"parts": [{"text": json.dumps(result)}]}}
                ]
            },
        )
        post_mock.side_effect = [
            requests.Timeout("Connection timed out"),
            successful_response,
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            analysis = analyze_portfolio(
                [],
                [],
                api_key="test-key",
                cache_path=Path(temp_dir) / "gemini_cache.json",
            )

        self.assertEqual(analysis.portfolio_assessment, "Evaluación válida")
        self.assertEqual(
            [call.args[0].split("/models/")[-1] for call in post_mock.call_args_list],
            [
                "gemini-3.8-flash:generateContent",
                "gemini-3.7-flash:generateContent",
            ],
        )

    def test_model_pool_allows_network_and_server_errors_for_fallback(self):
        for error in (
            requests.Timeout("Connection timed out"),
            requests.ConnectionError("Connection failed"),
            *(
                requests.HTTPError(
                    response=SimpleNamespace(status_code=status_code)
                )
                for status_code in (429, 500, 502, 503, 504)
            ),
        ):
            with self.subTest(error=error):
                self.assertTrue(_model_pool_allows_fallback([error]))

        client_error = requests.HTTPError(
            response=SimpleNamespace(status_code=400)
        )
        self.assertFalse(_model_pool_allows_fallback([client_error]))

    @patch("daily_report.requests.post")
    def test_failure_warning_lists_only_models_actually_attempted(self, post_mock):
        bad_request = SimpleNamespace(
            status_code=400,
            json=lambda: {"error": {"message": "Invalid request"}},
        )
        failed_response = SimpleNamespace(
            raise_for_status=lambda: (_ for _ in ()).throw(
                requests.HTTPError(response=bad_request)
            )
        )
        post_mock.return_value = failed_response

        with tempfile.TemporaryDirectory() as temp_dir:
            analysis = analyze_portfolio(
                [],
                [],
                api_key="single-key",
                cache_path=Path(temp_dir) / "gemini_cache.json",
            )

        self.assertIn("en los modelos gemini-3.8-flash.", analysis.warning)
        self.assertNotIn("gemini-3.8-flash y gemini-3.8-flash", analysis.warning)
        self.assertEqual(post_mock.call_count, 2)

    @patch("daily_report.requests.post")
    def test_gemini_successful_requests_rotate_starting_key_across_milestones(
        self, post_mock
    ):
        success_data = {
            "portfolio_assessment": "Evaluación",
            "diversification": "Diversificación",
            "recommended_changes": "Cambios",
            "top_buys": [],
            "sell_candidates": [],
            "market_context": "Contexto",
            "risks": [],
        }
        post_mock.return_value.raise_for_status.return_value = None
        post_mock.return_value.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": json.dumps(success_data)}]}}
            ]
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "gemini_cache.json"
            key_pool = "key1,key2,key3,key4,key5"
            first_time = datetime(2026, 10, 7, 8, 55, tzinfo=TIMEZONE)
            analyze_portfolio(
                [],
                [],
                api_key=key_pool,
                cache_path=cache_path,
                now_utc=first_time,
            )
            stored = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["next_api_key_index"], 1)

            stored["timestamp"] = datetime(
                2026, 10, 7, 8, 59, tzinfo=TIMEZONE
            ).isoformat()
            cache_path.write_text(json.dumps(stored), encoding="utf-8")
            analyze_portfolio(
                [],
                [],
                api_key=key_pool,
                cache_path=cache_path,
                now_utc=datetime(2026, 10, 7, 9, 5, tzinfo=TIMEZONE),
            )
            stored = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["next_api_key_index"], 2)

            stored["timestamp"] = datetime(
                2026, 10, 7, 11, 59, tzinfo=TIMEZONE
            ).isoformat()
            cache_path.write_text(json.dumps(stored), encoding="utf-8")
            analyze_portfolio(
                [],
                [],
                api_key=key_pool,
                cache_path=cache_path,
                now_utc=datetime(2026, 10, 7, 12, 5, tzinfo=TIMEZONE),
            )

        self.assertEqual(
            [
                call.kwargs["headers"]["x-goog-api-key"]
                for call in post_mock.call_args_list
            ],
            ["key1", "key2", "key3"],
        )

    @patch("daily_report.requests.post")
    def test_gemini_429_without_daily_detail_retries_after_five_minutes(
        self, post_mock
    ):
        now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
        response = SimpleNamespace(
            status_code=429,
            headers={},
            json=lambda: {
                "error": {
                    "message": (
                        "Quota exceeded. Please retry in 10h49m15.795500506s."
                    )
                }
            },
        )
        post_mock.return_value.raise_for_status.side_effect = requests.HTTPError(
            response=response
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "gemini_cache.json"
            failed = analyze_portfolio(
                [],
                [],
                api_key="secret-test-key",
                cache_path=cache_path,
                now_utc=now,
            )
            suppressed = analyze_portfolio(
                [],
                [],
                api_key="secret-test-key",
                cache_path=cache_path,
                now_utc=now + timedelta(minutes=4),
            )

            cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
            retried = analyze_portfolio(
                [],
                [],
                api_key="secret-test-key",
                cache_path=cache_path,
                now_utc=now + timedelta(minutes=5),
            )

        self.assertIn("HTTP 429", failed.warning)
        self.assertIn("10h49m15.795500506s", cache_data["failure_warning"])
        self.assertIn("retry_after_utc", cache_data)
        self.assertIn("Reintento breve pendiente", suppressed.warning)
        self.assertEqual(post_mock.call_count, 8)
        self.assertIn("HTTP 429", retried.warning)

    @patch("daily_report.requests.post")
    def test_gemini_explicit_daily_quota_caches_until_next_milestone(self, post_mock):
        now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
        response = SimpleNamespace(
            status_code=429,
            headers={},
            json=lambda: {
                "error": {
                    "message": "Daily quota exceeded",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                            "violations": [
                                {
                                    "quotaMetric": (
                                        "generativelanguage.googleapis.com/"
                                        "generate_content_free_tier_requests"
                                    ),
                                    "quotaId": (
                                        "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
                                    ),
                                }
                            ],
                        }
                    ],
                }
            },
        )
        post_mock.return_value.raise_for_status.side_effect = requests.HTTPError(
            response=response
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "gemini_cache.json"
            failed = analyze_portfolio(
                [],
                [],
                api_key="secret-test-key",
                cache_path=cache_path,
                now_utc=now,
            )
            suppressed = analyze_portfolio(
                [],
                [],
                api_key="secret-test-key",
                cache_path=cache_path,
                now_utc=now + timedelta(minutes=10),
            )
            cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
            retried = analyze_portfolio(
                [],
                [],
                api_key="secret-test-key",
                cache_path=cache_path,
                now_utc=datetime(2026, 10, 7, 13, 1, tzinfo=timezone.utc),
            )

        self.assertIn("HTTP 429", failed.warning)
        self.assertEqual(cache_data["warning"], failed.warning)
        self.assertNotIn("retry_after_utc", cache_data)
        self.assertEqual(cache_data["failure_policy"], "daily")
        self.assertIn("siguiente hito de análisis: 07/10/2026 15:00", suppressed.warning)
        self.assertEqual(post_mock.call_count, 8)
        self.assertIn("HTTP 429", retried.warning)

    @patch("daily_report.requests.post")
    def test_gemini_503_retries_after_five_minutes_not_next_milestone(self, post_mock):
        now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
        response = SimpleNamespace(
            status_code=503,
            headers={},
            json=lambda: {"error": {"message": "Service unavailable"}},
        )
        post_mock.return_value.raise_for_status.side_effect = requests.HTTPError(
            response=response
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "gemini_cache.json"
            failed = analyze_portfolio(
                [],
                [],
                api_key="first,second",
                cache_path=cache_path,
                now_utc=now,
            )
            cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
            before_retry = analyze_portfolio(
                [],
                [],
                api_key="first,second",
                cache_path=cache_path,
                now_utc=now + timedelta(minutes=4),
            )
            retried = analyze_portfolio(
                [],
                [],
                api_key="first,second",
                cache_path=cache_path,
                now_utc=now + timedelta(minutes=5),
            )

        self.assertIn("HTTP 503", failed.warning)
        self.assertIn("retry_after_utc", cache_data)
        self.assertEqual(cache_data["failure_policy"], "short_retry")
        self.assertIn("Reintento breve pendiente", before_retry.warning)
        self.assertIn("HTTP 503", retried.warning)
        self.assertEqual(post_mock.call_count, 8)

    @patch("daily_report.requests.post")
    def test_new_api_key_bypasses_failure_cooldown(self, post_mock):
        now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
        error_response = SimpleNamespace(
            status_code=429,
            headers={},
            json=lambda: {
                "error": {
                    "message": "Quota exceeded. Please retry in 10h."
                }
            },
        )
        post_mock.return_value.raise_for_status.side_effect = requests.HTTPError(
            response=error_response
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "gemini_cache.json"
            analyze_portfolio(
                [],
                [],
                api_key="old-account-key",
                cache_path=cache_path,
                now_utc=now,
            )

            success = {
                "portfolio_assessment": "Evaluación",
                "diversification": "Diversificación",
                "recommended_changes": "Sin cambios",
                "top_buys": [],
                "sell_candidates": [],
                "market_context": "Contexto",
                "risks": [],
            }
            post_mock.return_value.raise_for_status.side_effect = None
            post_mock.return_value.json.return_value = {
                "candidates": [
                    {"content": {"parts": [{"text": json.dumps(success)}]}}
                ]
            }
            result = analyze_portfolio(
                [],
                [],
                api_key="new-account-key",
                cache_path=cache_path,
                now_utc=now + timedelta(minutes=1),
            )

        self.assertEqual(result.portfolio_assessment, "Evaluación")
        self.assertEqual(post_mock.call_count, 5)

    def test_legacy_failure_cache_is_not_allowed_to_block_new_key(self):
        now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
        cached_failure = {
            "timestamp": now.isoformat(),
            "warning": "Análisis IA no disponible (HTTP 503).",
            "failure_warning": "Análisis IA no disponible (HTTP 429).",
            "retry_after_utc": (now + timedelta(hours=10)).isoformat(),
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "gemini_cache.json"
            cache_path.write_text(json.dumps(cached_failure), encoding="utf-8")

            result = _read_gemini_cache(
                cache_path,
                now + timedelta(minutes=1),
                api_key_fingerprint="new-key-fingerprint",
            )

        self.assertIsNone(result)

    def test_legacy_failure_cache_without_policy_expires_after_five_minutes(self):
        now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
        cached_failure = {
            "timestamp": now.isoformat(),
            "warning": "Análisis IA no disponible (HTTP 429).",
            "failure_warning": "Análisis IA no disponible (HTTP 429).",
            "api_key_fingerprint": "same-key-fingerprint",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "gemini_cache.json"
            cache_path.write_text(json.dumps(cached_failure), encoding="utf-8")

            waiting = _read_gemini_cache(
                cache_path,
                now + timedelta(minutes=4),
                api_key_fingerprint="same-key-fingerprint",
            )
            retry_allowed = _read_gemini_cache(
                cache_path,
                now + timedelta(minutes=5),
                api_key_fingerprint="same-key-fingerprint",
            )

        self.assertIn("Reintento breve pendiente", waiting.warning)
        self.assertIsNone(retry_allowed)

    @patch("daily_report.requests.post")
    def test_analyze_portfolio_returns_fresh_cache_without_calling_gemini(self, post_mock):
        now_utc = datetime(2026, 10, 6, 10, 5, tzinfo=TIMEZONE).astimezone(
            timezone.utc
        )
        cached_data = {
            "timestamp": datetime(2026, 10, 6, 9, 1, tzinfo=TIMEZONE).isoformat(),
            "portfolio_assessment": "Análisis desde caché.",
            "diversification": "Diversificación desde caché.",
            "recommended_changes": "Sin cambios.",
            "top_buys": [
                {"ticker": "EX", "name": "Example", "reason": "Razón almacenada."}
            ],
            "sell_candidates": [],
            "market_context": "Contexto almacenado.",
            "risks": ["Riesgo almacenado."],
            "warning": None,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "gemini_cache.json"
            cache_path.write_text(json.dumps(cached_data), encoding="utf-8")

            analysis = analyze_portfolio(
                [],
                [],
                api_key="unused-test-key",
                cache_path=cache_path,
                now_utc=now_utc,
            )

        self.assertEqual(analysis.portfolio_assessment, "Análisis desde caché.")
        self.assertEqual(analysis.top_buys[0]["ticker"], "EX")
        self.assertIsNone(analysis.warning)
        post_mock.assert_not_called()

    def test_gemini_cache_schedule_uses_local_midnight_and_evening_milestones(self):
        cached_data = {
            "timestamp": datetime(2026, 10, 6, 0, 0, tzinfo=TIMEZONE).isoformat(),
            "portfolio_assessment": "Análisis almacenado.",
            "diversification": "Diversificación almacenada.",
            "recommended_changes": "Sin cambios.",
            "top_buys": [],
            "sell_candidates": [],
            "market_context": "Contexto almacenado.",
            "risks": [],
            "warning": None,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "gemini_cache.json"
            cache_path.write_text(json.dumps(cached_data), encoding="utf-8")

            cached_data["timestamp"] = datetime(
                2026, 10, 6, 9, 0, tzinfo=TIMEZONE
            ).isoformat()
            cache_path.write_text(json.dumps(cached_data), encoding="utf-8")
            exactly_at_nine = _read_gemini_cache(
                cache_path,
                datetime(2026, 10, 6, 9, 0, tzinfo=TIMEZONE),
            )

            cached_data["timestamp"] = datetime(
                2026, 10, 6, 0, 0, tzinfo=TIMEZONE
            ).isoformat()
            cache_path.write_text(json.dumps(cached_data), encoding="utf-8")
            before_nine_am = _read_gemini_cache(
                cache_path,
                datetime(2026, 10, 6, 7, 30, tzinfo=TIMEZONE),
            )
            cached_data["timestamp"] = datetime(
                2026, 10, 6, 21, 0, tzinfo=TIMEZONE
            ).isoformat()
            cache_path.write_text(json.dumps(cached_data), encoding="utf-8")
            at_2350 = _read_gemini_cache(
                cache_path,
                datetime(2026, 10, 6, 23, 50, tzinfo=TIMEZONE),
            )

            cached_data["timestamp"] = datetime(
                2026, 10, 6, 20, 59, 59, tzinfo=TIMEZONE
            ).isoformat()
            cache_path.write_text(json.dumps(cached_data), encoding="utf-8")
            just_before_nine_pm = _read_gemini_cache(
                cache_path,
                datetime(2026, 10, 6, 23, 50, tzinfo=TIMEZONE),
            )

        self.assertIsNotNone(exactly_at_nine)
        self.assertIsNotNone(before_nine_am)
        self.assertIsNotNone(at_2350)
        self.assertIsNone(just_before_nine_pm)

    @patch("daily_report.requests.post")
    def test_analyze_portfolio_refreshes_expired_cache_and_persists_success(self, post_mock):
        now_utc = datetime(2026, 10, 6, 9, 5, tzinfo=TIMEZONE).astimezone(
            timezone.utc
        )
        cached_data = {
            "timestamp": datetime(
                2026, 10, 6, 8, 59, 59, tzinfo=TIMEZONE
            ).isoformat(),
            "portfolio_assessment": "Expirado.",
            "diversification": "Expirado.",
            "recommended_changes": "Expirado.",
            "top_buys": [],
            "sell_candidates": [],
            "market_context": "Expirado.",
            "risks": [],
            "warning": None,
        }
        result = {
            "portfolio_assessment": "Actualizado por Gemini.",
            "diversification": "Diversificación actualizada.",
            "recommended_changes": "Mantener las posiciones.",
            "top_buys": [],
            "sell_candidates": [],
            "market_context": "Mercado mixto.",
            "risks": ["No se conoce el horizonte de inversión."],
        }
        post_mock.return_value = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "candidates": [
                    {"content": {"parts": [{"text": json.dumps(result)}]}}
                ]
            },
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "gemini_cache.json"
            cache_path.write_text(json.dumps(cached_data), encoding="utf-8")

            analysis = analyze_portfolio(
                [],
                [],
                api_key="test-key",
                cache_path=cache_path,
                now_utc=now_utc,
            )
            saved_data = json.loads(cache_path.read_text(encoding="utf-8"))

        self.assertEqual(analysis.portfolio_assessment, "Actualizado por Gemini.")
        saved_timestamp = datetime.fromisoformat(saved_data["timestamp"])
        self.assertEqual(saved_timestamp.tzinfo, timezone.utc)
        self.assertLess(abs((datetime.now(timezone.utc) - saved_timestamp).total_seconds()), 5)
        self.assertEqual(saved_data["portfolio_assessment"], "Actualizado por Gemini.")
        post_mock.assert_called_once()

    @patch("daily_report.requests.post")
    def test_analyze_portfolio_ignores_corrupt_cache_and_rewrites_on_success(self, post_mock):
        now_utc = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)
        result = {
            "portfolio_assessment": "Caché reparada.",
            "diversification": "Evaluada.",
            "recommended_changes": "Ninguno.",
            "top_buys": [],
            "sell_candidates": [],
            "market_context": "Neutral.",
            "risks": [],
        }
        post_mock.return_value = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "candidates": [
                    {"content": {"parts": [{"text": json.dumps(result)}]}}
                ]
            },
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "gemini_cache.json"
            cache_path.write_text("{corrupt", encoding="utf-8")

            analysis = analyze_portfolio(
                [],
                [],
                api_key="test-key",
                cache_path=cache_path,
                now_utc=now_utc,
            )
            saved_data = json.loads(cache_path.read_text(encoding="utf-8"))

        self.assertEqual(analysis.portfolio_assessment, "Caché reparada.")
        saved_timestamp = datetime.fromisoformat(saved_data["timestamp"])
        self.assertEqual(saved_timestamp.tzinfo, timezone.utc)
        self.assertLess(abs((datetime.now(timezone.utc) - saved_timestamp).total_seconds()), 5)
        post_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
