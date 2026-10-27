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
    ATR_MULTIPLIER,
    ATR_WINDOW,
    CONFIDENCE_HIGH_SESSIONS,
    CONFIDENCE_MEDIUM_SESSIONS,
    PEAK_WINDOW,
    REWARD_RISK_RATIO,
    Company,
    COMPANIES,
    Headline,
    DashboardCharts,
    FinancialNewsItem,
    GlobalMarketNews,
    MACRO_FEEDS,
    MAX_MARKET_RADAR_ITEMS,
    MarketEvent,
    PortfolioAnalysis,
    PortfolioActivity,
    PortfolioAsset,
    PortfolioPosition,
    PortfolioTransaction,
    PORTFOLIO,
    PORTFOLIO_PERIODS,
    Quote,
    RiskZones,
    StockReport,
    TIMEZONE,
    _model_pool_allows_fallback,
    _market_session_series,
    _read_gemini_cache,
    _risk_no_data_reason,
    _gemini_request_warning,
    analyze_portfolio,
    build_analysis_prompt,
    build_market_data,
    build_request_body,
    classify_peg,
    classify_sentiment,
    collect_market_events,
    collect_market_radar,
    collect_chart_data,
    calculate_risk_zones,
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
    load_portfolio_transactions,
    load_portfolio_activity,
    portfolio_assets_from_transactions,
    portfolio_transactions_from_activity,
    portfolio_history_from_transactions,
    _portfolio_transaction_chart_data,
    collect_reports,
    render_dashboard,
    translate_headline,
    main,
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

    @patch("daily_report.translate_headline")
    @patch("daily_report.time.sleep")
    @patch("daily_report.fetch_quote")
    @patch("daily_report.fetch_headlines")
    def test_collect_reports_translates_headlines_sequentially(
        self,
        headlines_mock,
        quote_mock,
        sleep_mock,
        translate_mock,
    ):
        companies = (Company("First", "ONE"), Company("Second", "TWO"))
        sequence = []

        def make_headlines(company, *_args):
            title_list = (
                ["First headline A", "First headline B"]
                if company.name == "First"
                else ["Second headline"]
            )
            return (
                [
                    Headline(
                        title,
                        f"https://news.test/{index}",
                        "Source",
                        datetime.now(timezone.utc),
                        0,
                    )
                    for index, title in enumerate(title_list)
                ],
                None,
            )

        headlines_mock.side_effect = make_headlines
        translate_mock.side_effect = lambda title: (
            sequence.append(f"translate:{title}") or f"ES {title}",
            None,
        )
        sleep_mock.side_effect = lambda delay: sequence.append(f"sleep:{delay}")
        quote_mock.return_value = Quote()

        reports = collect_reports(companies, date(2026, 10, 14))

        self.assertEqual(
            [call.args[0] for call in translate_mock.call_args_list],
            ["First headline A", "First headline B", "Second headline"],
        )
        self.assertEqual(sleep_mock.call_count, 3)
        self.assertEqual(
            sequence,
            [
                "sleep:0.2",
                "translate:First headline A",
                "sleep:0.2",
                "translate:First headline B",
                "sleep:0.2",
                "translate:Second headline",
            ],
        )
        self.assertEqual(
            [[headline.title_es for headline in report.headlines] for report in reports],
            [
                ["ES First headline A", "ES First headline B"],
                ["ES Second headline"],
            ],
        )

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

    def test_dashboard_keeps_market_summary_and_orders_heatmaps_portfolio_and_news(self):
        generated_at = datetime(2026, 10, 26, 12, 0, tzinfo=TIMEZONE)
        report = StockReport(
            Company("Example Technology", "EX"),
            headlines=[
                Headline(
                    "Quarterly results improve",
                    "https://example.com/results",
                    "Example News",
                    generated_at,
                    0.7,
                )
            ],
            quote=Quote(
                daily_change_pct=2.5,
                market_date=generated_at.date(),
                market_timezone=TIMEZONE.key,
                analyst_consensus="Compra",
                analyst_count=3,
                sector="Technology",
            ),
            news_sentiment=0.7,
        )
        position = PortfolioPosition(
            asset=PortfolioAsset("Example Technology", "EX", 2, 100, "EUR"),
            prices_eur={"Hoy": 52.5, "1D": 51.2},
            returns_pct={"1D": 2.5},
            current_value_eur=105,
            price_date=generated_at.date(),
            market_timezone=TIMEZONE.key,
        )

        html = render_dashboard(
            [report],
            generated_at=generated_at,
            portfolio=[position],
            analysis=PortfolioAnalysis(
                portfolio_assessment="Mantener diversificación.",
                last_updated_utc="2026-10-26T10:00:00+00:00",
            ),
            market_events=[
                MarketEvent(
                    generated_at.date() + timedelta(days=4),
                    "EX",
                    "Example Technology",
                    "Resultados",
                )
            ],
        )

        self.assertIn("Instrumentos seguidos", html)
        self.assertIn("Cotizaciones actuales", html)
        self.assertIn(">1 / 1</div>", html)
        self.assertIn("Titulares recogidos", html)
        self.assertIn("Empresas con noticias", html)
        self.assertIn("Consenso de analistas", html)
        self.assertIn("Technology", html)
        self.assertIn("+2.50%", html)
        self.assertIn("Próximos eventos", html)
        self.assertIn("Quarterly results improve", html)
        self.assertIn("Análisis de cartera con Gemini Flash", html)
        self.assertLess(
            html.index('id="watchlist-heatmap"'),
            html.index('aria-label="Resumen ejecutivo del mercado"'),
        )
        self.assertLess(
            html.index('aria-label="Resumen ejecutivo del mercado"'),
            html.index('id="portfolio-heatmap"'),
        )
        self.assertLess(
            html.index('id="portfolio-heatmap"'),
            html.index('id="market-events-title"'),
        )
        self.assertLess(
            html.index('id="market-events-title"'),
            html.index('id="key-title"'),
        )
        self.assertLess(
            html.index('id="key-title"'),
            html.index('id="ai-analysis-title"'),
        )
        self.assertLess(
            html.index('id="ai-analysis-title"'),
            html.index('id="stocks-title"'),
        )

    def test_peg_color_thresholds(self):
        for peg, css in [(None, ""), (-0.4, ""), (0.99, "text-bullish"), (1.0, "text-orange"),
                         (1.5, "text-orange"), (1.51, "text-bearish")]:
            with self.subTest(peg=peg):
                self.assertEqual(classify_peg(peg), css)

    def test_dashboard_has_sort_select_and_card_data(self):
        html = render_dashboard([
            StockReport(
                Company("A Co", "A"),
                quote=Quote(
                    daily_change_pct=1.5,
                    peg_ratio=0.9,
                    pe_ratio=18,
                    sector="Technology",
                    extra={
                        "market_cap": 2_000_000_000,
                        "dividend_yield_pct": 1.5,
                        "beta": 0.8,
                    },
                    analyst_consensus="Compra",
                    analyst_count=12,
                ),
                news_sentiment=0.4,
            )
        ])
        self.assertIn('<select id="sort-select"', html)
        self.assertNotIn("Ordenada por rendimiento de hoy", html)
        self.assertIn(
            'data-daily="1.5000" data-peg="0.9000" data-consensus="Compra" data-analysts="12" data-news="Alcista"',
            html,
        )
        self.assertIn('data-pe="18.0000" data-sector="Technology"', html)
        self.assertIn(
            'data-market-cap="2000000000.0000" data-dividend-yield="1.5000" data-beta="0.8000"',
            html,
        )
        self.assertIn('id="sector-filter"', html)
        self.assertIn('id="peg-filter"', html)
        self.assertIn('id="pe-filter"', html)
        self.assertIn('id="daily-filter"', html)
        self.assertIn('id="consensus-filter"', html)
        self.assertIn('id="market-cap-filter"', html)
        self.assertIn('id="dividend-filter"', html)
        self.assertIn('id="beta-filter"', html)
        self.assertIn('id="stock-search"', html)
        self.assertIn('id="drawdown-filter"', html)
        self.assertIn("matchesFilters(card)", html)
        self.assertIn('value="pe">PER (de menor a mayor)', html)
        self.assertIn('value="marketCap">Capitalización (mayor a menor)', html)
        self.assertIn('value="dividend">Rentabilidad por dividendo', html)
        self.assertIn('value="under20">Caída de al menos 20%', html)

    def test_dashboard_exposes_snapshot_delta_api_and_preserves_stale_state(self):
        html = render_dashboard(
            [
                StockReport(
                    Company("Example", "EX"),
                    quote=Quote(
                        current_price_eur=10,
                        previous_close=9,
                        daily_change_pct=11.11,
                        market_date=date(2026, 10, 18),
                        market_timezone="Europe/Madrid",
                        extra={"pct_from_52w_high": -14.2},
                    ),
                )
            ],
            generated_at=datetime(2026, 10, 18, 12, 0, tzinfo=TIMEZONE),
        )

        self.assertIn("window.DashboardMarketData", html)
        self.assertIn("applySnapshot", html)
        self.assertIn("applyDelta", html)
        self.assertIn("data-market-stale-note", html)
        self.assertIn('data-live-change', html)
        self.assertIn("data-from-high=\"-14.2000\"", html)
        self.assertIn("Desde máximo 52 sem.", html)

    def test_current_day_cards_include_a_hidden_until_render_sparkline(self):
        charts = DashboardCharts(
            sparklines={
                "EX": [
                    {"timestamp": "2026-10-18T10:00:00+02:00", "value": 9.5},
                    {"timestamp": "2026-10-18T10:05:00+02:00", "value": 10.0},
                ]
            }
        )
        report = StockReport(
            Company("Example", "EX"),
            quote=Quote(
                daily_change_pct=5,
                market_date=date(2026, 10, 18),
                market_timezone="Europe/Madrid",
            ),
        )

        html = render_dashboard(
            [report],
            generated_at=datetime(2026, 10, 18, 12, 0, tzinfo=TIMEZONE),
            chart_data=charts,
        )

        self.assertIn('data-sparkline="EX"', html)
        self.assertIn("renderSparklines()", html)
        self.assertIn("payload.sparklines?.[ticker]", html)
        self.assertIn("node.setAttribute('hidden', '')", html)
        self.assertIn("node.removeAttribute('hidden')", html)

    @patch("daily_report.yf.download")
    def test_portfolio_period_chart_payload_contains_daily_returns_and_benchmarks(
        self, download_mock
    ):
        today = datetime.now(TIMEZONE).date()
        index = pd.date_range(today - timedelta(days=400), periods=401, freq="D")
        columns = pd.MultiIndex.from_tuples(
            [
                ("AAA", "Close"),
                ("SXR8.DE", "Close"),
                ("EUNL.DE", "Close"),
            ],
            names=["Ticker", "Price"],
        )
        close_values = [100 + index_number * 0.1 for index_number in range(len(index))]
        download_mock.side_effect = [
            pd.DataFrame(
                [[101, 102, 99], [102, 103, 100]],
                index=pd.date_range(
                    today - timedelta(days=1), periods=2, freq="5min", tz="UTC"
                ),
                columns=columns,
            ),
            pd.DataFrame(
                [list(values) for values in zip(close_values, close_values, close_values, strict=True)],
                index=index,
                columns=columns,
            ),
        ]
        report = StockReport(
            Company("Alpha", "AAA"),
            quote=Quote(previous_close=close_values[-2]),
        )
        portfolio = [
            PortfolioPosition(
                PortfolioAsset("Alpha", "AAA", 1, 100, "EUR"),
                prices_eur={"Hoy": close_values[-1], "1D": close_values[-2]},
                current_value_eur=close_values[-1],
            )
        ]

        charts = collect_chart_data([report], portfolio)

        self.assertEqual(
            set(charts.portfolio_period_series),
            {"1D", "1W", "1M", "6M", "1Y"},
        )
        self.assertTrue(charts.portfolio_period_series["1Y"])
        self.assertTrue(charts.benchmark_period_series["1Y"])

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
        self.assertIn(
            '<span>Precio actual</span><strong data-live-price>20.00 €</strong>',
            html,
        )

    @patch("daily_report.yf.Ticker")
    def test_fetch_portfolio_converts_historical_prices_and_calculates_returns(self, ticker_mock):
        as_of = date(2025, 1, 11)
        asset = PortfolioAsset("Example", "EX", 2, 100, "USD")
        dates = pd.date_range("2024-01-01", "2025-01-12", freq="D")
        closes = [
            100.0
            if day.date() == date(2025, 1, 10)
            else 1000.0
            if day.date() == date(2025, 1, 12)
            else 110.0
            for day in dates
        ]
        stock_history = pd.DataFrame(
            {
                "High": [close + 2 for close in closes],
                "Low": [close - 2 for close in closes],
                "Close": closes,
            },
            index=dates,
        )
        fx_history = pd.DataFrame({"Close": [0.9] * len(dates)}, index=dates)
        history_calls = {"USDEUR=X": 0, "EX": 0}

        def ticker_factory(symbol):
            history = fx_history if symbol == "USDEUR=X" else stock_history

            def get_history(**_kwargs):
                history_calls[symbol] += 1
                return history

            return SimpleNamespace(history=get_history)

        ticker_mock.side_effect = ticker_factory

        positions = fetch_portfolio((asset,), as_of=as_of)

        position = positions[0]
        self.assertAlmostEqual(position.prices_eur["Hoy"], 99.0)
        self.assertAlmostEqual(position.prices_eur["1D"], 90.0)
        self.assertAlmostEqual(position.returns_pct["1D"], 10.0)
        self.assertAlmostEqual(position.current_value_eur, 198.0)
        self.assertAlmostEqual(position.max_profit_eur, 98.0)
        self.assertAlmostEqual(position.max_return_pct, 98.0)
        self.assertIsNotNone(position.risk_zones)
        expected_history = stock_history.loc[
            [timestamp.date() <= as_of for timestamp in stock_history.index]
        ]
        previous_close = expected_history["Close"].shift()
        true_range = pd.concat(
            [
                expected_history["High"] - expected_history["Low"],
                (expected_history["High"] - previous_close).abs(),
                (expected_history["Low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        expected_atr_pct = (
            true_range.rolling(ATR_WINDOW).mean().iloc[-1]
            / expected_history["Close"].iloc[-1]
            * 100
        )
        self.assertAlmostEqual(position.risk_zones.atr_pct, expected_atr_pct)
        self.assertAlmostEqual(
            position.risk_zones.primary_review_pct,
            -ATR_MULTIPLIER * expected_atr_pct,
        )
        self.assertAlmostEqual(
            position.risk_zones.primary_target_pct,
            REWARD_RISK_RATIO * ATR_MULTIPLIER * expected_atr_pct,
        )
        self.assertEqual(
            position.risk_zones.sessions_used,
            len(expected_history.dropna(subset=["High", "Low", "Close"])),
        )
        self.assertEqual(position.risk_zones.confidence, "high")
        self.assertEqual(history_calls, {"USDEUR=X": 1, "EX": 1})
        self.assertEqual(tuple(position.returns_pct), tuple(period for period, _ in PORTFOLIO_PERIODS))

    @patch("daily_report.yf.Ticker")
    def test_fetch_portfolio_warns_and_keeps_position_when_risk_history_is_short(
        self, ticker_mock
    ):
        as_of = date(2025, 1, 15)
        history = pd.DataFrame(
            {
                "High": [101.0] * 14,
                "Low": [99.0] * 14,
                "Close": [100.0] * 14,
            },
            index=pd.date_range("2025-01-01", periods=14, freq="D"),
        )
        ticker_mock.return_value.history.return_value = history

        with self.assertLogs(level="WARNING") as captured:
            positions = fetch_portfolio(
                (PortfolioAsset("Short History", "SHORT", 2, 180, "EUR"),),
                as_of=as_of,
            )

        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].risk_zones.status, "no_data")
        self.assertTrue(
            any("SHORT: niveles de riesgo sin datos: histórico insuficiente (14 de 15" in message
                for message in captured.output)
        )

    def test_calculate_risk_zones_uses_atr_and_trailing_peak_for_normal_position(self):
        index = pd.date_range("2025-01-01", periods=40, freq="D")
        history = pd.DataFrame(
            {
                "High": [101.0] * 40,
                "Low": [99.0] * 40,
                "Close": [100.0] * 40,
            },
            index=index,
        )

        risk = calculate_risk_zones(history, price_eur=100)

        self.assertEqual(risk.status, "ok")
        self.assertAlmostEqual(risk.atr_pct, 2.0)
        self.assertAlmostEqual(risk.review_eur, 96.0)
        self.assertAlmostEqual(risk.review_pct, -4.0)
        self.assertAlmostEqual(risk.target_eur, 108.0)
        self.assertAlmostEqual(risk.target_pct, 8.0)
        self.assertEqual(risk.atr_days, ATR_WINDOW)
        self.assertEqual(risk.atr_multiplier, ATR_MULTIPLIER)
        self.assertEqual(risk.peak_window, PEAK_WINDOW)
        self.assertEqual(risk.reward_risk, REWARD_RISK_RATIO)
        self.assertAlmostEqual(risk.primary_review_eur, 95.0)
        self.assertAlmostEqual(risk.primary_review_pct, -5.0)
        self.assertAlmostEqual(risk.primary_target_eur, 110.0)
        self.assertAlmostEqual(risk.primary_target_pct, 10.0)
        self.assertEqual(risk.sessions_used, 40)
        self.assertEqual(risk.confidence, "medium")

    def test_calculate_risk_zones_confidence_thresholds_and_actual_session_count(self):
        for sessions, expected_confidence in (
            (ATR_WINDOW + 1, "low"),
            (CONFIDENCE_MEDIUM_SESSIONS - 1, "low"),
            (CONFIDENCE_MEDIUM_SESSIONS, "medium"),
            (CONFIDENCE_HIGH_SESSIONS - 1, "medium"),
            (CONFIDENCE_HIGH_SESSIONS, "high"),
            (CONFIDENCE_HIGH_SESSIONS + 15, "high"),
        ):
            with self.subTest(sessions=sessions):
                history = pd.DataFrame(
                    {
                        "High": [101.0] * sessions,
                        "Low": [99.0] * sessions,
                        "Close": [100.0] * sessions,
                    },
                    index=pd.date_range("2025-01-01", periods=sessions, freq="D"),
                )
                risk = calculate_risk_zones(history, price_eur=100)

                self.assertEqual(risk.sessions_used, sessions)
                self.assertEqual(risk.confidence, expected_confidence)

    def test_risk_confidence_badges_and_explanation_for_each_label(self):
        for sessions, confidence in (
            (ATR_WINDOW + 1, "low"),
            (CONFIDENCE_MEDIUM_SESSIONS, "medium"),
            (CONFIDENCE_HIGH_SESSIONS, "high"),
        ):
            with self.subTest(confidence=confidence):
                history = pd.DataFrame(
                    {
                        "High": [101.0] * sessions,
                        "Low": [99.0] * sessions,
                        "Close": [100.0] * sessions,
                    },
                    index=pd.date_range("2025-01-01", periods=sessions, freq="D"),
                )
                position = PortfolioPosition(
                    asset=PortfolioAsset("Example", confidence.upper(), 1, 100, "EUR"),
                    prices_eur={"Hoy": 100},
                    current_value_eur=100,
                    risk_zones=calculate_risk_zones(history, 100),
                )
                html = render_dashboard([], portfolio=[position])

                self.assertIn(
                    f'class="portfolio-risk-confidence confidence-{confidence}">'
                    f"{confidence}</span>",
                    html,
                )
                self.assertIn(
                    f"Confianza {confidence} según las {sessions} sesiones de "
                    "histórico usadas en el cálculo.",
                    html,
                )
                self.assertNotIn("Confianza / sesiones", html)

    def test_no_data_warning_reason_distinguishes_missing_price_and_invalid_history(self):
        self.assertIn("precio ausente", _risk_no_data_reason(pd.DataFrame(), None))
        self.assertIn(
            "histórico insuficiente",
            _risk_no_data_reason(
                pd.DataFrame(
                    {"High": [101.0] * 14, "Low": [99.0] * 14, "Close": [100.0] * 14}
                ),
                100,
            ),
        )
        self.assertIn(
            "datos históricos no válidos",
            _risk_no_data_reason(pd.DataFrame({"Close": [100.0] * 30}), 100),
        )

    @patch("daily_report.yf.Ticker")
    def test_fetch_portfolio_logs_missing_current_price_reason(self, ticker_mock):
        history = pd.DataFrame(
            {
                "High": [101.0] * 20,
                "Low": [99.0] * 20,
                "Close": [100.0] * 20,
            },
            index=pd.date_range("2025-01-11", periods=20, freq="D"),
        )
        ticker_mock.return_value.history.return_value = history

        with self.assertLogs(level="WARNING") as captured:
            positions = fetch_portfolio(
                (PortfolioAsset("Future Quote", "FUTURE", 1, 100, "EUR"),),
                as_of=date(2025, 1, 10),
            )

        self.assertEqual(positions[0].risk_zones.status, "no_data")
        self.assertTrue(
            any("FUTURE: niveles de riesgo sin datos: precio ausente" in message
                for message in captured.output)
        )

    def test_calculate_risk_zones_marks_crossed_and_omits_target(self):
        history = pd.DataFrame(
            {
                "High": [120.0 if position == 20 else 101.0 for position in range(40)],
                "Low": [99.0] * 40,
                "Close": [100.0] * 40,
            },
            index=pd.date_range("2025-01-01", periods=40, freq="D"),
        )

        risk = calculate_risk_zones(history, price_eur=100)

        self.assertEqual(risk.status, "crossed")
        self.assertAlmostEqual(risk.review_eur, 115.0)
        self.assertAlmostEqual(risk.review_pct, 15.0)
        self.assertIsNone(risk.target_eur)
        self.assertIsNone(risk.target_pct)
        self.assertIsNotNone(risk.primary_review_eur)
        self.assertIsNotNone(risk.primary_target_eur)

    def test_calculate_risk_zones_returns_no_data_for_short_or_invalid_history(self):
        short_history = pd.DataFrame(
            {"High": [101.0] * 14, "Low": [99.0] * 14, "Close": [100.0] * 14}
        )
        missing_ohlc = pd.DataFrame({"Close": [100.0] * 30})

        short = calculate_risk_zones(short_history, price_eur=100)
        invalid = calculate_risk_zones(missing_ohlc, price_eur=100)
        no_price = calculate_risk_zones(short_history, price_eur=0)

        self.assertEqual(short.status, "no_data")
        self.assertEqual(invalid.status, "no_data")
        self.assertEqual(no_price.status, "no_data")
        self.assertIsNone(short.atr_pct)
        self.assertIsNone(short.review_eur)
        self.assertIsNone(short.target_eur)
        self.assertIsNone(short.primary_review_eur)
        self.assertIsNone(short.primary_target_eur)
        self.assertEqual(short.sessions_used, 14)
        self.assertIsNone(short.confidence)

    def test_portfolio_csv_import_builds_remaining_position_at_average_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "trades.csv"
            csv_path.write_text(
                "date,ticker,name,side,quantity,price_eur,currency,fees_eur\n"
                "2026-01-01,EX,Example,BUY,10,10,EUR,1\n"
                "2026-02-01,EX,Example,SELL,2,15,EUR,1\n",
                encoding="utf-8",
            )

            transactions = load_portfolio_transactions(csv_path)
            assets = portfolio_assets_from_transactions(transactions)

        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].shares, 8)
        self.assertAlmostEqual(assets[0].investment_eur, 80.8)

    def test_portfolio_activity_builds_positions_and_keeps_benefits_separate(self):
        rows = [
            {
                "date": "2026-01-01",
                "action": "BUY",
                "ticker": "NVDA",
                "name": "NVIDIA",
                "asset_class": "STOCK",
                "currency": "USD",
                "shares": 10,
                "price_eur": 10,
                "amount_eur": -100,
                "fee_eur": -1,
                "tax_eur": 0,
            },
            {
                "date": "2026-01-02",
                "action": "DIVIDEND",
                "ticker": "NVDA",
                "name": "NVIDIA",
                "asset_class": "STOCK",
                "currency": "USD",
                "shares": 10,
                "price_eur": None,
                "amount_eur": 2,
                "fee_eur": 0,
                "tax_eur": -0.3,
            },
            {
                "date": "2026-01-03",
                "action": "SELL",
                "ticker": "NVDA",
                "name": "NVIDIA",
                "asset_class": "STOCK",
                "currency": "USD",
                "shares": 2,
                "price_eur": 15,
                "amount_eur": 30,
                "fee_eur": -1,
                "tax_eur": 0,
            },
            {
                "date": "2026-01-04",
                "action": "STOCKPERK",
                "ticker": "NVDA",
                "name": "NVIDIA",
                "asset_class": "STOCK",
                "currency": "USD",
                "shares": None,
                "price_eur": None,
                "amount_eur": 5,
                "fee_eur": 0,
                "tax_eur": -0.5,
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            activity_path = Path(directory) / "portfolio_activity.json"
            activity_path.write_text(json.dumps(rows), encoding="utf-8")
            activities = load_portfolio_activity(activity_path)

        transactions = portfolio_transactions_from_activity(activities)
        assets = portfolio_assets_from_transactions(transactions)

        self.assertEqual(len(activities), 4)
        self.assertEqual(len(transactions), 2)
        self.assertEqual(assets[0].shares, 8)
        self.assertAlmostEqual(assets[0].investment_eur, 80.8)
        self.assertEqual(activities[1].action, "DIVIDEND")
        self.assertEqual(activities[3].action, "STOCKPERK")

    def test_public_portfolio_activity_matches_configured_active_tickers(self):
        activity_path = Path(__file__).parents[1] / "portfolio_activity.json"
        raw_activities = json.loads(activity_path.read_text(encoding="utf-8"))
        activities = load_portfolio_activity(activity_path)
        transactions = portfolio_transactions_from_activity(activities)
        assets = portfolio_assets_from_transactions(transactions)
        assets_by_ticker = {asset.ticker: asset for asset in assets}
        configured_tickers = {
            report.ticker for report in COMPANIES if report.ticker
        }
        public_fields = {
            "date",
            "action",
            "ticker",
            "name",
            "asset_class",
            "currency",
            "shares",
            "price_eur",
            "amount_eur",
            "fee_eur",
            "tax_eur",
        }

        self.assertEqual(len(activities), 54)
        self.assertTrue(all(set(row) == public_fields for row in raw_activities))
        self.assertTrue(all(item.ticker in configured_tickers for item in activities))
        self.assertEqual(set(assets_by_ticker), {asset.ticker for asset in PORTFOLIO})
        for configured in PORTFOLIO:
            self.assertAlmostEqual(
                assets_by_ticker[configured.ticker].shares,
                configured.shares,
            )
        self.assertIn("NKE", configured_tickers)
        self.assertIn("NVO", configured_tickers)
        dashboard = render_dashboard([], portfolio_activity=activities)
        self.assertIn("Historial de inversión · 54 movimientos", dashboard)
        self.assertEqual(dashboard.count('data-label="Movimiento"'), 54)

    def test_dashboard_renders_filtered_portfolio_activity(self):
        activities = [
            PortfolioActivity(
                trade_date=date(2026, 10, 2),
                action="DIVIDEND",
                ticker="NVDA",
                name="NVIDIA",
                asset_class="STOCK",
                currency="USD",
                shares=0.5,
                price_eur=None,
                amount_eur=1.2,
                fee_eur=0,
                tax_eur=-0.2,
            ),
            PortfolioActivity(
                trade_date=date(2026, 10, 1),
                action="BUY",
                ticker="NVDA",
                name="NVIDIA",
                asset_class="STOCK",
                currency="USD",
                shares=0.1,
                price_eur=100,
                amount_eur=-10,
                fee_eur=-1,
                tax_eur=0,
            ),
            PortfolioActivity(
                trade_date=date(2026, 9, 30),
                action="BENEFITS_SAVEBACK",
                ticker="ASML.AS",
                name="ASML",
                asset_class="STOCK",
                currency="EUR",
                shares=None,
                price_eur=None,
                amount_eur=5,
                fee_eur=0,
                tax_eur=-1,
            ),
        ]

        dashboard = render_dashboard([], portfolio_activity=activities)

        self.assertIn("Historial de inversión · 3 movimientos", dashboard)
        self.assertIn("02/10/2026", dashboard)
        self.assertIn("Dividendos netos", dashboard)
        self.assertIn("Beneficios netos", dashboard)
        self.assertIn("Beneficio Saveback", dashboard)
        self.assertIn("Flujo neto", dashboard)

    def test_portfolio_csv_rejects_sales_above_owned_quantity(self):
        transactions = [
            PortfolioTransaction(
                date(2026, 1, 1), "EX", "Example", "BUY", 1, 10, "EUR"
            ),
            PortfolioTransaction(
                date(2026, 1, 2), "EX", "Example", "SELL", 2, 12, "EUR"
            ),
        ]

        with self.assertRaisesRegex(ValueError, "supera las participaciones"):
            portfolio_assets_from_transactions(transactions)

    @patch("daily_report.yf.download")
    def test_portfolio_history_replays_imported_buys_and_sells(self, download_mock):
        index = pd.date_range("2026-01-01", periods=3, freq="D")
        columns = pd.MultiIndex.from_tuples([("EX", "Close")])
        download_mock.return_value = pd.DataFrame(
            [[10.0], [11.0], [12.0]], index=index, columns=columns
        )
        transactions = [
            PortfolioTransaction(
                date(2026, 1, 1), "EX", "Example", "BUY", 2, 9, "EUR"
            ),
            PortfolioTransaction(
                date(2026, 1, 3), "EX", "Example", "SELL", 1, 12, "EUR"
            ),
        ]

        series = portfolio_history_from_transactions(transactions)

        self.assertEqual([point["value"] for point in series], [20, 22, 12])

    def test_portfolio_profit_history_includes_fees_dividends_and_saveback(self):
        transactions = [
            PortfolioTransaction(
                date(2026, 1, 1), "EX", "Example", "BUY", 2, 10, "EUR",
                fees_eur=1, gross_amount_eur=20,
            ),
            PortfolioTransaction(
                date(2026, 1, 3), "EX", "Example", "SELL", 1, 12, "EUR",
                fees_eur=1, gross_amount_eur=12,
            ),
        ]
        activities = [
            PortfolioActivity(
                date(2026, 1, 2), "DIVIDEND", "EX", "Example", "STOCK", "EUR",
                1, None, 2, 0, -0.3,
            ),
            PortfolioActivity(
                date(2026, 1, 3), "BENEFITS_SAVEBACK", "EX", "Example", "STOCK",
                "EUR", None, None, 1, 0, 0,
            ),
        ]
        closes = {
            "EX": [
                (date(2026, 1, 1), 10),
                (date(2026, 1, 2), 11),
                (date(2026, 1, 3), 12),
            ],
            "SXR8.DE": [
                (date(2026, 1, 1), 100),
                (date(2026, 1, 2), 101),
                (date(2026, 1, 3), 102),
            ],
            "EUNL.DE": [
                (date(2026, 1, 1), 200),
                (date(2026, 1, 2), 202),
                (date(2026, 1, 3), 204),
            ],
        }

        chart_data = _portfolio_transaction_chart_data(
            transactions, activities, closes
        )

        self.assertEqual(
            [point["value"] for point in chart_data["portfolio_profit_series"]],
            [-1, 3, 5],
        )
        self.assertEqual(len(chart_data["portfolio_transaction_events"]), 4)
        self.assertEqual(chart_data["portfolio_transaction_events"][1]["action"], "DIVIDEND")
        self.assertEqual(
            [item["ticker"] for item in chart_data["benchmark_history_series"]],
            ["SXR8.DE", "EUNL.DE"],
        )

    def test_market_events_include_only_upcoming_quote_dates(self):
        reports = [
            StockReport(
                Company("Upcoming", "UP"),
                quote=Quote(
                    extra={
                        "earnings_date": "2026-10-20",
                        "ex_dividend_date": "2026-10-25",
                    }
                ),
            ),
            StockReport(
                Company("Past", "PAST"),
                quote=Quote(extra={"earnings_date": "2026-10-01"}),
            ),
        ]

        events = collect_market_events(reports, as_of=date(2026, 10, 18))
        dashboard = render_dashboard([], market_events=events)

        self.assertEqual(
            [(event.event_date, event.event_type) for event in events],
            [
                (date(2026, 10, 20), "Resultados"),
                (date(2026, 10, 25), "Fecha ex-dividendo"),
            ],
        )
        self.assertIn("Próximos eventos", dashboard)
        self.assertIn("Yahoo Finance", dashboard)

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
        self.assertIn('<th scope="col">Inversión base</th>', html)
        self.assertIn('<th scope="col">Valor actual</th>', html)
        self.assertIn(
            'data-label="Precio actual"><span data-live-price>120.00 €</span>',
            html,
        )
        self.assertIn(
            'data-label="Valor actual" data-live-value>180.00 €',
            html,
        )
        self.assertIn("80.00 €", html)
        self.assertIn('data-portfolio-kind="close">110.00 €', html)
        self.assertIn('data-portfolio-kind="close">70.00 €', html)
        self.assertIn("Precio de cierre (€)", html)
        self.assertIn("DashboardPortfolioChartSelect", html)

    def test_dashboard_shows_trailing_risk_and_marks_breached_review_level(self):
        position = PortfolioPosition(
            asset=PortfolioAsset('Example <script>alert("x")</script>', "EX", 1, 100, "EUR"),
            prices_eur={"Hoy": 90},
            risk_zones=RiskZones(
                status="crossed",
                atr_pct=2.0,
                review_eur=95.0,
                review_pct=5.56,
                target_eur=None,
                target_pct=None,
                atr_days=ATR_WINDOW,
                atr_multiplier=ATR_MULTIPLIER,
                peak_window=PEAK_WINDOW,
                reward_risk=REWARD_RISK_RATIO,
                confidence="low",
                sessions_used=PEAK_WINDOW,
            ),
        )

        html = render_dashboard([], portfolio=[position])

        self.assertIn('aria-label="Ver niveles de riesgo de Example &lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; (EX)"', html)
        self.assertIn('popovertarget="portfolio-risk-', html)
        self.assertIn('popover="auto"', html)
        self.assertIn('class="portfolio-risk-trigger"', html)
        self.assertNotIn('class="portfolio-risk-trigger is-crossed"', html)
        self.assertIn('class="portfolio-risk-confidence confidence-low">low</span>', html)
        self.assertIn("Confianza low según las 22 sesiones de histórico usadas en el cálculo.", html)
        self.assertIn('class="portfolio-risk-secondary is-crossed"', html)
        self.assertIn("Zonas de alerta · EX", html)
        self.assertIn("▼ Nivel de revisión", html)
        self.assertIn("▲ Toma de beneficios", html)
        self.assertIn("95.00 €", html)
        self.assertIn("—", html)
        self.assertIn("Desde máximos de 22 sesiones", html)
        self.assertIn("El precio está un 5.26 % por debajo de ese nivel.", html)
        self.assertIn("Precio actual", html)
        self.assertIn("Precio medio", html)
        self.assertIn("Volatilidad (ATR 14d)", html)
        self.assertIn("2.5 × ATR(14)", html)
        self.assertIn("¿Por qué estos niveles?", html)
        self.assertIn("Niveles educativos, no órdenes de compra/venta.", html)
        self.assertNotIn('<th scope="col">Riesgo ATR</th>', html)
        self.assertNotIn('<script>alert("x")</script>', html)
        self.assertIn('data-risk-atr-pct="2.0000"', html)
        self.assertIn('data-risk-secondary-level="95.0000"', html)

    def test_dashboard_renders_no_data_risk_panel(self):
        position = PortfolioPosition(
            asset=PortfolioAsset("Short History", "SHORT", 1, 100, "EUR"),
            prices_eur={"Hoy": 100},
            risk_zones=calculate_risk_zones(pd.DataFrame(), 100),
        )

        html = render_dashboard([], portfolio=[position])

        self.assertIn("▼ Nivel de revisión", html)
        self.assertIn("▲ Toma de beneficios", html)
        self.assertIn("Se necesitan al menos 15 sesiones válidas de histórico.", html)
        self.assertIn('aria-label="Ver niveles de riesgo de Short History (SHORT)"', html)
        self.assertIn('class="portfolio-risk-trigger is-no-data"', html)

    def test_risk_explanation_covers_cost_relationships_and_missing_position_data(self):
        examples = (
            (
                "near",
                100,
                100,
                "El nivel de revisión casi coincide con tu coste medio",
            ),
            (
                "below",
                100,
                110,
                "Podrías perder hasta 9.09 % sobre tu coste",
            ),
            (
                "above",
                100,
                90,
                "conservarías 11.11 % de ganancia sobre tu coste",
            ),
        )
        for ticker, price, average, expected in examples:
            with self.subTest(ticker=ticker):
                position = PortfolioPosition(
                    asset=PortfolioAsset(
                        "Example",
                        ticker,
                        1,
                        average,
                        "EUR",
                    ),
                    prices_eur={"Hoy": price},
                    current_value_eur=price,
                    risk_zones=RiskZones(
                        status="ok",
                        atr_pct=2.0,
                        review_eur=95.0,
                        review_pct=-5.0,
                        target_eur=110.0,
                        target_pct=10.0,
                        atr_days=ATR_WINDOW,
                        atr_multiplier=ATR_MULTIPLIER,
                        peak_window=PEAK_WINDOW,
                        reward_risk=REWARD_RISK_RATIO,
                        primary_review_eur=100,
                        primary_review_pct=-5.0,
                        primary_target_eur=110.0,
                        primary_target_pct=10.0,
                        confidence="low",
                        sessions_used=PEAK_WINDOW,
                    ),
                )
                html = render_dashboard([], portfolio=[position])
                self.assertIn(expected, html)
                self.assertIn("La toma de beneficios supone", html)
                self.assertIn("ratio 1:2", html)
                self.assertIn("100.00 €", html)
                self.assertIn("-5.00%", html)
                self.assertIn("+10.00%", html)
                self.assertIn("Confianza low según las 22 sesiones", html)

        missing_position = PortfolioPosition(
            asset=PortfolioAsset("No Cost", "NOCOST", 0, 0, "EUR"),
            prices_eur={"Hoy": None},
            risk_zones=calculate_risk_zones(pd.DataFrame(), None),
        )
        missing_html = render_dashboard([], portfolio=[missing_position])
        self.assertIn("Niveles educativos, no órdenes de compra/venta.", missing_html)
        self.assertNotIn("Tienes 0.000000 acciones", missing_html)
        self.assertNotIn("El nivel de revisión casi coincide", missing_html)

    def test_live_risk_script_contains_dynamic_level_and_secondary_state_updates(self):
        html = render_dashboard([])

        self.assertIn("row.dataset.riskAtrPct", html)
        self.assertIn("row.dataset.riskSecondaryLevel", html)
        self.assertIn("price - riskDistance", html)
        self.assertIn("price + rewardRatio * riskDistance", html)
        self.assertIn("(secondaryLevel / price - 1) * 100", html)
        self.assertIn("secondaryBlockNode.classList.toggle('is-crossed', crossed)", html)
        self.assertIn("riskButton.classList.toggle('is-no-data', !hasSecondaryLevel)", html)
        self.assertNotIn("riskButton.classList.toggle('is-crossed'", html)
        self.assertIn("const maxTop = Math.max(", html)
        self.assertIn(": Math.min(maxTop, Math.max(margin", html)

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
            portfolio_profit_series=[
                {"timestamp": "2026-10-07T09:00:00+00:00", "value": -3.2}
            ],
            portfolio_transaction_events=[
                {
                    "timestamp": "2026-10-07T09:00:00+00:00",
                    "ticker": "EX",
                    "action": "BUY",
                    "shares": 1,
                    "price_eur": 10,
                    "amount_eur": 10,
                }
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
        self.assertIn("Beneficio o pérdida intradía de la cartera", html)
        self.assertIn('id="portfolio-history-period"', html)
        self.assertIn('<option value="YTD">YTD</option>', html)
        self.assertIn("portfolio_transaction_events", html)
        self.assertIn("07/10/2026 · 11:15 CEST", html)

    def test_watchlist_matches_updated_company_list(self):
        companies = [(company.name, company.ticker) for company in COMPANIES]
        self.assertEqual(len(COMPANIES), 57)
        self.assertEqual(
            companies[:5],
            [
                ("SoFi Technologies", "SOFI"),
                ("Neo Performance Materials", "NEO.TO"),
                ("Tempus AI", "TEM"),
                ("Walmart", "WMT"),
                ("Dollar Tree", "DLTR"),
            ],
        )
        self.assertIn(("Space Innovators USD (A)", "JEDI"), companies)
        self.assertIn(("Amper", "AMP.MC"), companies)
        self.assertIn(("IREN", "IREN"), companies)
        self.assertIn(("Applied Optoelectronics", "AAOI"), companies)
        self.assertIn(("Core MSCI World USD (Acc)", "EUNL.DE"), companies)
        self.assertIn(("Nike (B)", "NKE"), companies)
        self.assertIn(("Novo Nordisk (ADR)", "NVO"), companies)
        self.assertEqual(
            next(company.ticker for company in COMPANIES if company.name == "SpaceX"),
            "SPCX",
        )

    def test_portfolio_positions_include_new_world_etf_and_google_purchases(self):
        portfolio_by_ticker = {asset.ticker: asset for asset in PORTFOLIO}

        world_etf = portfolio_by_ticker["EUNL.DE"]
        google = portfolio_by_ticker["GOOGL"]

        self.assertEqual(world_etf.shares, 2.367719)
        self.assertEqual(world_etf.investment_eur, 298.52)
        self.assertEqual(google.shares, 0.201365)
        self.assertEqual(google.investment_eur, 62.00)

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
    def test_macro_feed_keeps_entries_from_bozo_feed(
        self, get_mock, parse_mock
    ):
        get_mock.return_value = SimpleNamespace(
            content=b"<rss/>",
            raise_for_status=lambda: None,
        )
        parse_mock.return_value = SimpleNamespace(
            bozo=True,
            bozo_exception="minor XML parse error",
            entries=[
                SimpleNamespace(
                    title="Markets rally after earnings beat",
                    summary="<p>Global markets gain after results.</p>",
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

        with self.assertLogs(level="WARNING") as log:
            news = _fetch_macro_feed(
                "Fallback source",
                "https://feeds.example/markets.xml",
                date(2026, 10, 11),
            )

        self.assertIn("minor XML parse error", log.output[0])
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0].title, "Markets rally after earnings beat")
        self.assertEqual(news[0].source, "Example News")
        self.assertEqual(news[0].published.date(), date(2026, 10, 11))
        self.assertEqual(news[0].url, "https://news.example/earnings")
        self.assertEqual(news[0].summary, "Global markets gain after results.")

    def test_macro_feeds_include_bloomberg_and_investing_spain(self):
        self.assertIn(
            ("Yahoo Finance", "https://finance.yahoo.com/rss/topstories"),
            MACRO_FEEDS,
        )
        self.assertIn(
            ("Bloomberg Markets", "https://feeds.bloomberg.com/markets/news.rss"),
            MACRO_FEEDS,
        )
        self.assertIn(
            ("Investing.com España", "https://es.investing.com/rss/news.rss"),
            MACRO_FEEDS,
        )

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

        self.assertEqual(len(news), MAX_MARKET_RADAR_ITEMS)
        self.assertEqual(len(news), 250)
        self.assertEqual(news[0].published, reference)
        self.assertEqual(news[-1].published, reference - timedelta(minutes=49))
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
        self.assertIn(
            "global_market_radar (que contiene titulares en inglés y español)",
            prompt,
        )
        self.assertIn("external_ideas", prompt)

    def test_market_session_series_uses_exchange_date_not_a_rolling_24_hour_window(self):
        index = pd.to_datetime(
            [
                "2026-10-07 20:55:00+00:00",
                "2026-10-08 01:00:00+00:00",
                "2026-10-08 14:30:00+00:00",
            ]
        )
        series = pd.Series([101, 102, 103], index=index)

        session = _market_session_series(
            series,
            date(2026, 10, 7),
            "America/New_York",
        )

        self.assertEqual(session.tolist(), [101, 102])

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
        self.assertEqual(
            [point["value"] for point in charts.sparklines["AAA"]],
            [101, 102],
        )
        self.assertEqual(download_mock.call_count, 2)

    @patch("daily_report.yf.download")
    def test_portfolio_intraday_series_is_value_weighted_and_compared_to_benchmarks(
        self, download_mock
    ):
        intraday_index = pd.date_range(
            "2026-10-07 08:00", periods=2, freq="5min", tz="UTC"
        )
        tickers = ["AAA", "BBB", "SXR8.DE", "EUNL.DE"]
        intraday_columns = pd.MultiIndex.from_product(
            [tickers, ["Close"]], names=["Ticker", "Price"]
        )
        intraday = pd.DataFrame(
            [
                [110, 90, 102, 99],
                [111, 91, 103, 98],
            ],
            index=intraday_index,
            columns=intraday_columns,
        )
        daily_index = pd.date_range("2026-10-06", periods=2, freq="D")
        daily_columns = pd.MultiIndex.from_product(
            [["SXR8.DE", "EUNL.DE"], ["Close"]],
            names=["Ticker", "Price"],
        )
        daily = pd.DataFrame(
            [[100, 100], [101, 100]],
            index=daily_index,
            columns=daily_columns,
        )
        download_mock.side_effect = [intraday, daily]
        reports = [
            StockReport(Company("Alpha", "AAA"), quote=Quote(previous_close=100)),
            StockReport(Company("Beta", "BBB"), quote=Quote(previous_close=100)),
        ]
        portfolio = [
            PortfolioPosition(
                PortfolioAsset("Alpha", "AAA", 1, 90, "EUR"),
                prices_eur={"1D": 100},
                current_value_eur=90,
            ),
            PortfolioPosition(
                PortfolioAsset("Beta", "BBB", 1, 10, "EUR"),
                prices_eur={"1D": 100},
                current_value_eur=10,
            ),
        ]

        charts = collect_chart_data(reports, portfolio)

        self.assertAlmostEqual(charts.portfolio_series[0]["value"], 8.0)
        self.assertEqual(
            [item["name"] for item in charts.benchmark_series],
            ["S&P 500 EUR", "MSCI World EUR"],
        )

    @patch("daily_report.yf.download")
    def test_portfolio_usd_intraday_series_includes_eur_fx_move(self, download_mock):
        index = pd.date_range(
            "2026-10-07 08:00", periods=1, freq="5min", tz="UTC"
        )
        intraday_columns = pd.MultiIndex.from_tuples(
            [
                ("AAA", "Close"),
                ("USDEUR=X", "Close"),
                ("SXR8.DE", "Close"),
                ("EUNL.DE", "Close"),
            ],
            names=["Ticker", "Price"],
        )
        intraday = pd.DataFrame([[110, 0.91, 102, 99]], index=index, columns=intraday_columns)
        daily_index = pd.date_range("2026-10-06", periods=2, freq="D")
        daily_columns = pd.MultiIndex.from_tuples(
            [
                ("USDEUR=X", "Close"),
                ("SXR8.DE", "Close"),
                ("EUNL.DE", "Close"),
            ],
            names=["Ticker", "Price"],
        )
        daily = pd.DataFrame(
            [[0.90, 100, 100], [0.90, 101, 100]],
            index=daily_index,
            columns=daily_columns,
        )
        download_mock.side_effect = [intraday, daily]
        report = StockReport(
            Company("US company", "AAA"),
            quote=Quote(previous_close=100, currency="USD"),
        )
        portfolio = [
            PortfolioPosition(
                PortfolioAsset("US company", "AAA", 1, 90, "USD"),
                prices_eur={"1D": 90},
                current_value_eur=100,
            )
        ]

        charts = collect_chart_data([report], portfolio)

        self.assertAlmostEqual(charts.portfolio_series[0]["value"], (110 * 0.91 / 90 - 1) * 100)

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
        self.assertIn("const nameLabels = groups.filter((item) => item.data.current_day)", html)
        self.assertIn("card.scrollIntoView({behavior: 'smooth', block: 'start'})", html)
        self.assertIn(".attr('role', 'link')", html)

    @patch("daily_report.yf.download", return_value=pd.DataFrame())
    def test_heatmap_payload_uses_short_asset_names(self, _download_mock):
        reports = [
            StockReport(Company("Core MSCI World USD (Acc)", "EUNL.DE")),
            StockReport(Company("Palantir Technologies", "PLTR")),
            StockReport(Company("Marvell Technology", "MRVL")),
            StockReport(Company("Space Innovators USD (Acc)", "JEDI")),
        ]

        charts = collect_chart_data(reports, [])
        html = render_dashboard(reports, chart_data=charts)
        payload = json.loads(
            html.split('id="dashboard-chart-data">', 1)[1].split("</script>", 1)[0]
        )

        self.assertEqual(
            [item["name"] for item in charts.watchlist],
            ["MSCI World", "Palantir", "Marvell", "Space Innovators"],
        )
        self.assertEqual(
            [item["name"] for item in payload["watchlist"]],
            ["MSCI World", "Palantir", "Marvell", "Space Innovators"],
        )
        self.assertIn('id="card-EUNL.DE"', html)
        self.assertIn('id="card-PLTR"', html)

    @patch("daily_report.yf.download", return_value=pd.DataFrame())
    def test_portfolio_heatmap_shortens_world_information_technology(self, _download_mock):
        position = PortfolioPosition(
            asset=PortfolioAsset(
                "MSCI World Information Technology USD (Acc)",
                "XDWT.DE",
                1,
                100,
                "EUR",
            ),
            prices_eur={"Hoy": 100, "1D": 99},
            returns_pct={"1D": 1.01},
            current_value_eur=100,
            price_date=date(2026, 10, 26),
            market_timezone=TIMEZONE.key,
        )

        charts = collect_chart_data([], [position])
        html = render_dashboard([], portfolio=[position], chart_data=charts)
        payload = json.loads(
            html.split('id="dashboard-chart-data">', 1)[1].split("</script>", 1)[0]
        )

        self.assertEqual(charts.portfolio[0]["name"], "World IT")
        self.assertEqual(payload["portfolio"][0]["name"], "World IT")

    def test_risk_popover_typography_and_secondary_level_layout_are_scoped(self):
        position = PortfolioPosition(
            asset=PortfolioAsset("Example", "EX", 1, 100, "EUR"),
            prices_eur={"Hoy": 100},
            risk_zones=RiskZones(
                status="ok",
                atr_pct=2,
                review_eur=95,
                review_pct=-5,
                target_eur=110,
                target_pct=10,
                atr_days=ATR_WINDOW,
                atr_multiplier=ATR_MULTIPLIER,
                peak_window=PEAK_WINDOW,
                reward_risk=REWARD_RISK_RATIO,
            ),
        )
        html = render_dashboard([], portfolio=[position])

        self.assertIn("background: var(--panel); color: var(--text); font-weight: 400;", html)
        self.assertIn(
            ".portfolio-table tbody th > .portfolio-asset-line > span { display: block; }",
            html,
        )
        self.assertNotIn(".portfolio-table tbody th span { display: block;", html)
        self.assertIn(
            '<strong>Desde máximos de 22 sesiones:</strong> '
            '<span data-risk-secondary-level-text>95.00 €</span> '
            '(<span data-risk-secondary-pct>-5.00%</span>)',
            html,
        )

    def test_heatmap_labels_are_clipped_and_mobile_layout_is_fluid(self):
        html = render_dashboard([])

        self.assertIn("@media (max-width: 420px)", html)
        self.assertIn("width: min(100%, 1440px);", html)
        self.assertIn(".heatmap-chart { display: block; width: 100%; max-width: 100%;", html)
        self.assertNotIn("getComputedTextLength", html)
        self.assertNotIn("label.style('display', 'none')", html)
        self.assertIn("Math.max(3,", html)
        self.assertIn("nameLabels.append('tspan')", html)
        self.assertIn(".attr('clip-path', (item) => `url(#${item.clipId})`)", html)
        self.assertIn(".attr('text-anchor', 'middle')", html)

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
                summary="Markets climbed after company earnings.",
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
        self.assertIn('"summary": "Markets climbed after company earnings."', prompt)
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
        self.assertNotIn("tools", post_mock.call_args.kwargs["json"])

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
        self.assertNotIn("tools", post_mock.call_args.kwargs["json"])

    def test_yahoo_quote_enrichment_and_optional_external_ideas_are_safe(self):
        now = datetime(2026, 10, 8, 12, 0, tzinfo=TIMEZONE)
        metrics = extra_quote_fields(
            {
                "currentPrice": 90,
                "fiftyTwoWeekHigh": 100,
                "fiftyTwoWeekLow": 60,
                "fiftyDayAverage": 80,
                "twoHundredDayAverage": 75,
                "marketCap": 2_000_000_000,
                "forwardPE": 20,
                "revenueGrowth": 0.12,
                "beta": 1.25,
                "dividendYield": 0.015,
                "earningsTimestamp": datetime(
                    2026, 10, 20, tzinfo=timezone.utc
                ).timestamp(),
                "exDividendDate": datetime(
                    2026, 10, 21, tzinfo=timezone.utc
                ).timestamp(),
                "industry": "Semiconductors",
            },
            now,
        )
        self.assertEqual(metrics["pct_from_52w_high"], -10.0)
        self.assertEqual(metrics["pct_above_52w_low"], 50.0)
        self.assertEqual(metrics["pct_vs_sma50"], 12.5)
        self.assertEqual(metrics["pct_vs_sma200"], 20.0)
        self.assertEqual(metrics["revenue_growth_pct"], 12.0)
        self.assertEqual(metrics["market_cap"], 2_000_000_000)
        self.assertEqual(metrics["dividend_yield_pct"], 1.5)
        self.assertEqual(metrics["earnings_date"], "2026-10-20")
        self.assertEqual(metrics["ex_dividend_date"], "2026-10-21")
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
    def test_gemini_request_sends_standard_payload_and_returns_response(self, post_mock):
        response = SimpleNamespace(raise_for_status=lambda: None)
        post_mock.return_value = response

        actual_response = post_gemini("https://example.test", "key", "prompt")

        self.assertIs(actual_response, response)
        self.assertEqual(post_mock.call_count, 1)
        self.assertNotIn("tools", post_mock.call_args.kwargs["json"])
        self.assertEqual(post_mock.call_args.kwargs["timeout"], (10, 60))
        self.assertEqual(
            build_request_body("prompt")["generationConfig"]["thinkingConfig"],
            {"thinkingBudget": 2048},
        )

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
        post_mock.side_effect = [quota_result, success_response]

        with tempfile.TemporaryDirectory() as temp_dir:
            analysis = analyze_portfolio(
                [],
                [],
                api_key=" first-key ,second-key,first-key ",
                cache_path=Path(temp_dir) / "gemini_cache.json",
            )

        self.assertEqual(analysis.portfolio_assessment, "Evaluación válida")
        self.assertEqual(post_mock.call_count, 2)
        self.assertEqual(
            [call.kwargs["headers"]["x-goog-api-key"] for call in post_mock.call_args_list],
            ["first-key", "second-key"],
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
        self.assertEqual(post_mock.call_count, 8)
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
                "backup",
                "primary",
                "backup",
                "backup",
                "primary",
                "backup",
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
        self.assertEqual(post_mock.call_count, 1)

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
        self.assertEqual(post_mock.call_count, 4)
        self.assertIn("HTTP 429", retried.warning)

    @patch("daily_report.requests.post")
    def test_mixed_gemini_daily_quota_and_overload_caches_until_next_milestone(
        self, post_mock
    ):
        now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
        daily_quota_payload = {
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
        }

        def failed_response(status_code, payload):
            response = SimpleNamespace(
                status_code=status_code,
                headers={},
                json=lambda: payload,
            )

            def raise_for_status():
                raise requests.HTTPError(response=response)

            response.raise_for_status = raise_for_status
            return response

        mixed_failures = [
            failed_response(503, {"error": {"message": "Server overloaded"}}),
            failed_response(429, daily_quota_payload),
            failed_response(503, {"error": {"message": "Server overloaded"}}),
            failed_response(429, daily_quota_payload),
        ]
        post_mock.side_effect = mixed_failures + mixed_failures

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "gemini_cache.json"
            failed = analyze_portfolio(
                [],
                [],
                api_key="first-test-key,second-test-key",
                cache_path=cache_path,
                now_utc=now,
            )
            suppressed = analyze_portfolio(
                [],
                [],
                api_key="first-test-key,second-test-key",
                cache_path=cache_path,
                now_utc=now + timedelta(minutes=10),
            )
            cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
            retried = analyze_portfolio(
                [],
                [],
                api_key="first-test-key,second-test-key",
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
        self.assertEqual(post_mock.call_count, 3)

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

    def test_market_data_export_contains_frontend_contract_and_finite_json(self):
        generated_at = datetime(2026, 10, 26, 12, tzinfo=TIMEZONE)
        headline = Headline(
            title="Strong quarterly results",
            url="https://example.com/results",
            source="Example News",
            published=generated_at,
            sentiment=0.8,
            title_es="Fuertes resultados trimestrales",
        )
        report = StockReport(
            company=Company("Example Fund", "EXF"),
            headlines=[headline],
            quote=Quote(
                current_price_eur=11,
                daily_change_pct=10,
                market_date=generated_at.date(),
                market_timezone=TIMEZONE.key,
                currency="EUR",
                sector="Technology",
                extra={"quote_type": "ETF", "market_cap": float("nan")},
            ),
            news_sentiment=0.8,
        )
        position = PortfolioPosition(
            asset=PortfolioAsset("Example Fund", "EXF", 1, 10, "EUR"),
            prices_eur={
                "Hoy": 11,
                "1D": 10,
                "1W": 9,
                "1M": 8,
                "6M": 7,
                "1Y": 6,
            },
            returns_pct={
                "1D": 10,
                "1W": 22.2,
                "1M": 37.5,
                "6M": 57.1,
                "1Y": 83.3,
            },
            current_value_eur=11,
            max_profit_eur=1,
            max_return_pct=10,
            price_date=generated_at.date(),
            market_timezone=TIMEZONE.key,
            risk_zones=RiskZones(
                status="ok",
                atr_pct=2.0,
                review_eur=9.5,
                review_pct=-13.64,
                target_eur=14.0,
                target_pct=27.27,
                atr_days=ATR_WINDOW,
                atr_multiplier=ATR_MULTIPLIER,
                peak_window=PEAK_WINDOW,
                reward_risk=REWARD_RISK_RATIO,
                primary_review_eur=10.45,
                primary_review_pct=-5.0,
                primary_target_eur=12.1,
                primary_target_pct=10.0,
                confidence="high",
                sessions_used=CONFIDENCE_HIGH_SESSIONS,
            ),
        )
        analysis = PortfolioAnalysis(
            portfolio_assessment="Mantener la diversificación.",
            risks=["Riesgo de concentración."],
            last_updated_utc="2026-10-26T11:00:00+00:00",
        )

        payload = build_market_data(
            [report],
            [position],
            transactions=[
                PortfolioTransaction(
                    trade_date=date(2026, 1, 1),
                    ticker="EXF",
                    name="Example Fund",
                    side="BUY",
                    quantity=2,
                    price_eur=10,
                    currency="EUR",
                ),
                PortfolioTransaction(
                    trade_date=date(2026, 2, 1),
                    ticker="EXF",
                    name="Example Fund",
                    side="SELL",
                    quantity=1,
                    price_eur=12,
                    currency="EUR",
                    fees_eur=1,
                ),
            ],
            analysis=analysis,
            chart_data=DashboardCharts(watchlist=[{"value": float("inf")}]),
            generated_at=generated_at,
        )

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["watchlist"][0]["ticker"], "EXF")
        self.assertEqual(
            payload["watchlist"][0]["headlines"][0]["display_title"],
            "Fuertes resultados trimestrales",
        )
        self.assertEqual(payload["market_summary"]["sentiment_label"], "Alcista")
        self.assertEqual(payload["market_summary"]["current_quote_count"], 1)
        self.assertEqual(payload["portfolio"]["positions"][0]["asset_class"], "fund")
        self.assertEqual(payload["portfolio"]["positions"][0]["avg_cost_eur"], 10)
        self.assertEqual(payload["portfolio"]["positions"][0]["day_delta_eur"], 1)
        self.assertEqual(payload["portfolio"]["positions"][0]["invested_cost_eur"], 20)
        self.assertEqual(
            payload["portfolio"]["positions"][0]["risk_zones"],
            {
                "status": "ok",
                "atr_pct": 2.0,
                "review_eur": 9.5,
                "review_pct": -13.64,
                "target_eur": 14.0,
                "target_pct": 27.27,
                "atr_days": ATR_WINDOW,
                "atr_multiplier": ATR_MULTIPLIER,
                "peak_window": PEAK_WINDOW,
                "reward_risk": REWARD_RISK_RATIO,
                "primary_review_eur": 10.45,
                "primary_review_pct": -5.0,
                "primary_target_eur": 12.1,
                "primary_target_pct": 10.0,
                "confidence": "high",
                "sessions_used": CONFIDENCE_HIGH_SESSIONS,
            },
        )
        self.assertEqual(payload["portfolio"]["positions"][0]["realized_pnl_eur"], 1)
        self.assertEqual(payload["portfolio"]["totals"]["current_value_eur"], 11)
        self.assertEqual(payload["portfolio"]["totals"]["total_return_pct"], 10)
        self.assertEqual(
            payload["analysis"]["portfolio_assessment"],
            "Mantener la diversificación.",
        )
        self.assertIsNone(payload["watchlist"][0]["fundamentals"]["market_cap"])
        self.assertIsNone(payload["charts"]["watchlist"][0]["value"])
        json.dumps(payload, ensure_ascii=False, allow_nan=False)

    @patch("daily_report.collect_market_events", return_value=[])
    @patch("daily_report.collect_chart_data", return_value=DashboardCharts())
    @patch("daily_report.analyze_portfolio", return_value=PortfolioAnalysis())
    @patch("daily_report.fetch_portfolio", return_value=[])
    @patch("daily_report.collect_reports", return_value=[])
    def test_main_writes_data_only_without_generating_legacy_html(
        self,
        _reports_mock,
        _portfolio_mock,
        _analysis_mock,
        _charts_mock,
        _events_mock,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_path = root / "market_data.json"
            html_path = root / "dashboard.html"
            with (
                patch("daily_report.PORTFOLIO_ACTIVITY_FILE", root / "missing.json"),
                patch(
                    "sys.argv",
                    [
                        "daily_report.py",
                        "--no-html",
                        "--data-output",
                        str(data_path),
                        "--output",
                        str(html_path),
                    ],
                ),
            ):
                result = main()

            payload = json.loads(data_path.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertFalse(html_path.exists())
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["watchlist"], [])
        self.assertEqual(payload["portfolio"]["positions"], [])

    @patch("daily_report.collect_market_events", return_value=[])
    @patch("daily_report.collect_chart_data", return_value=DashboardCharts())
    @patch("daily_report.analyze_portfolio", return_value=PortfolioAnalysis())
    @patch("daily_report.fetch_portfolio", return_value=[])
    @patch("daily_report.collect_reports", return_value=[])
    def test_main_writes_original_dashboard_and_json_together(
        self,
        _reports_mock,
        _portfolio_mock,
        _analysis_mock,
        _charts_mock,
        _events_mock,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_path = root / "public" / "market_data.json"
            html_path = root / "public" / "index.html"
            with (
                patch("daily_report.PORTFOLIO_ACTIVITY_FILE", root / "missing.json"),
                patch(
                    "sys.argv",
                    [
                        "daily_report.py",
                        "--output",
                        str(html_path),
                        "--data-output",
                        str(data_path),
                    ],
                ),
            ):
                result = main()

            html = html_path.read_text(encoding="utf-8")
            payload = json.loads(data_path.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertLess(
            html.index('id="watchlist-heatmap"'),
            html.index('<h2 id="portfolio-title">Mi cartera'),
        )
        self.assertLess(
            html.index('<h2 id="portfolio-title">Mi cartera'),
            html.index('<h2 id="stocks-title">Lista de seguimiento'),
        )
        self.assertIn('id="portfolio-heatmap"', html)
        self.assertIn('id="stock-grid"', html)
        self.assertIn("data-sparkline=", html)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["watchlist"], [])


if __name__ == "__main__":
    unittest.main()
