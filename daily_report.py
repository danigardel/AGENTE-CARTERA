from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import math
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from statistics import fmean
from typing import Any
from urllib.parse import quote, urlencode, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import feedparser
import pandas as pd
import requests
import yfinance as yf
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer


TIMEZONE = ZoneInfo(os.getenv("REPORT_TIMEZONE", "Europe/Madrid"))
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")
GEMINI_CACHE_FILE = Path("gemini_cache.json")
TARGET_HOURS = (0, 9, 12, 15, 18, 21)
MAX_HEADLINES = 4
MAX_WORKERS = 8
PORTFOLIO_PERIODS = (("1D", 1), ("1W", 7), ("1M", 30), ("6M", 180), ("1Y", 365))

# 1. CACHÉ PARA TIPOS DE CAMBIO
FX_CACHE: dict[str, float] = {"EUR": 1.0}
FX_LOCK = threading.Lock()

def get_fx_rate(currency: str) -> float:
    if not currency:
        return 1.0
    currency = currency.upper()
    
    with FX_LOCK:
        if currency in FX_CACHE:
            return FX_CACHE[currency]
            
    try:
        ticker = f"{currency}EUR=X"
        rate = yf.Ticker(ticker).history(period="1d")["Close"].iloc[-1]
    except Exception as e:
        logging.warning("Error obteniendo tipo de cambio para %s: %s", currency, e)
        rate = 1.0
        
    with FX_LOCK:
        FX_CACHE[currency] = rate
    return float(rate)


@dataclass(frozen=True)
class Company:
    name: str
    ticker: str | None


@dataclass
class Quote:
    current_price_native: float | None = None
    previous_close: float | None = None
    current_price_eur: float | None = None
    daily_change_pct: float | None = None
    market_date: date | None = None
    market_timezone: str | None = None
    daily_change_abs_eur: float | None = None
    peg_ratio: float | None = None
    pe_ratio: float | None = None
    analyst_consensus: str | None = None
    analyst_count: int | None = None
    target_mean_eur: float | None = None
    sector: str | None = None
    warning: str | None = None


@dataclass
class Headline:
    title: str
    url: str
    source: str
    published: datetime
    sentiment: float
    title_es: str | None = None


@dataclass
class StockReport:
    company: Company
    headlines: list[Headline] = field(default_factory=list)
    quote: Quote = field(default_factory=Quote)
    news_sentiment: float | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PortfolioAsset:
    name: str
    ticker: str
    shares: float
    investment_eur: float
    currency: str


@dataclass
class PortfolioPosition:
    asset: PortfolioAsset
    prices_eur: dict[str, float | None] = field(default_factory=dict)
    returns_pct: dict[str, float | None] = field(default_factory=dict)
    current_value_eur: float | None = None
    max_profit_eur: float | None = None
    max_return_pct: float | None = None
    warning: str | None = None
    price_date: date | None = None
    market_timezone: str | None = None


@dataclass
class PortfolioAnalysis:
    portfolio_assessment: str | None = None
    diversification: str | None = None
    recommended_changes: str | None = None
    top_buys: list[dict[str, str]] = field(default_factory=list)
    sell_candidates: list[dict[str, str]] = field(default_factory=list)
    market_context: str | None = None
    risks: list[str] = field(default_factory=list)
    warning: str | None = None
    last_updated_utc: str | None = None


@dataclass
class DashboardCharts:
    watchlist: list[dict[str, Any]] = field(default_factory=list)
    portfolio: list[dict[str, Any]] = field(default_factory=list)
    watchlist_series: list[dict[str, float | str]] = field(default_factory=list)
    portfolio_series: list[dict[str, float | str]] = field(default_factory=list)
    timezone: str = TIMEZONE.key


PORTFOLIO = (
    PortfolioAsset("Core MSCI World USD (Acc)", "EUNL.DE", 1.985433, 248.52, "EUR"),
    PortfolioAsset("ASML", "ASML.AS", 0.046588, 61.65, "EUR"),
    PortfolioAsset("Vistra", "VST", 0.407497, 50.00, "USD"),
    PortfolioAsset("GE Vernova", "GEV", 0.038649, 30.00, "USD"),
    PortfolioAsset("MSCI World Information Tech.", "XDWT.DE", 0.218023, 30.00, "EUR"),
    PortfolioAsset("Broadcom", "AVGO", 0.084104, 25.00, "USD"),
    PortfolioAsset("NVIDIA", "NVDA", 0.098541, 17.00, "USD"),
    PortfolioAsset("Alphabet (A)", "GOOGL", 0.038737, 12.00, "USD"),
    PortfolioAsset("MercadoLibre", "MELI", 0.002776, 5.00, "USD"),
)


# LISTA DE SEGUIMIENTO
COMPANIES = (
    Company("Intel", "INTC"),
    Company("Netflix", "NFLX"),
    Company("Microsoft", "MSFT"),
    Company("Amazon.com", "AMZN"),
    Company("Apple", "AAPL"),
    Company("S&P 500 Equal Weight US", "RSP"),
    Company("Meta Platforms (A)", "META"),
    Company("Uber", "UBER"),
    Company("S&P 500 EUR (Acc)", "SXR8.DE"),
    Company("Alphabet (A)", "GOOGL"),
    Company("MP Materials", "MP"),
    Company("Walt Disney", "DIS"),
    Company("Palantir Technologies", "PLTR"),
    Company("MercadoLibre", "MELI"),
    Company("NVIDIA", "NVDA"),
    Company("EQQQ Nasdaq 100 USD (Acc)", "EQQU.L"),
    Company("Core MSCI World USD (Acc)", "SWDA.L"),
    Company("MSCI World Information Tech.", "XDWT.DE"),
    Company("FTSE All-World USD (Acc)", "VWRP.L"),
    Company("NASDAQ 100 USD (Acc)", "CNDX.L"),
    Company("Semiconductor USD (Acc)", "VVSM.DE"),
    Company("SoFi Technologies", "SOFI"),
    Company("Smart Overnight Return ETF", "CSH2.PA"),
    Company("Modine Manufacturing", "MOD"),
    Company("Goldman Sachs", "GS"),
    Company("Quantum Computing USD", "QTUM"),
    Company("MSCI Emerging Markets (Acc)", "IS3N.DE"),
    # Company("Space Innovators ETF", "YODA.L"),  # Yahoo Finance no reconoce este ticker.
    Company("Caterpillar", "CAT"),
    Company("Neo Performance Materials", "NEO.TO"),
    Company("Tempus AI", "TEM"),
    Company("GE Vernova", "GEV"),
    Company("Oracle", "ORCL"),
    Company("AMD", "AMD"),
    Company("Marvell Technology", "MRVL"),
    Company("Vistra", "VST"),
    Company("Dell Technologies", "DELL"),
    Company("Super Micro Computer", "SMCI"),
    Company("Broadcom", "AVGO"),
    Company("Vertiv", "VRT"),
    Company("ASML", "ASML.AS"),
    Company("TSMC (ADR)", "TSM"),
    Company("Moderna", "MRNA"),
    Company("Micron Technology", "MU"),
    Company("Rocket Lab", "RKLB"),
    Company("SpaceX", None),
    Company("Nebius Group (A)", "NBIS"),
    Company("Bloom Energy", "BE"),
    Company("Lumentum Holdings", "LITE"),
)

ANALYST_LABELS = {
    "strong_buy": "Compra fuerte",
    "buy": "Compra",
    "hold": "Mantener",
    "underperform": "Rendimiento inferior",
    "sell": "Venta",
}


def classify_sentiment(score: float | None) -> tuple[str, str]:
    if score is None:
        return "Sin datos", "neutral"
    if score >= 0.05:
        return "Alcista", "bullish"
    if score <= -0.05:
        return "Bajista", "bearish"
    return "Neutral", "neutral"


def classify_peg(peg: float | None) -> str:
    """Verde < 1,0 · naranja 1,0–1,5 · rojo > 1,5 · sin color si falta o es negativo."""
    if peg is None or peg < 0:
        return ""
    if peg < 1.0:
        return "text-bullish"
    return "text-orange" if peg <= 1.5 else "text-bearish"


def _num_attr(value: float | None) -> str:
    return "" if value is None else f"{value:.4f}"


def _parse_published(entry: Any) -> datetime | None:
    parsed = getattr(entry, "published_parsed", None)
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=timezone.utc).astimezone(TIMEZONE)


def fetch_headlines(
    company: Company,
    report_date: date,
    analyzer: SentimentIntensityAnalyzer,
) -> tuple[list[Headline], str | None]:
    query_date = report_date - timedelta(days=1)
    query = f'"{company.name}" after:{query_date.isoformat()}'
    params = {
        "q": query,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    }
    url = f"{GOOGLE_NEWS_RSS}?{urlencode(params)}"
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "DailyStockReport/1.0 (RSS reader)"},
            timeout=(5, 20),
        )
        response.raise_for_status()
    except requests.RequestException as error:
        message = f"Noticias: {error}"
        logging.warning("%s\n%s", company.name, message)
        return [], message

    feed = feedparser.parse(response.content)
    headlines: list[Headline] = []
    seen_urls: set[str] = set()
    for entry in feed.entries:
        published = _parse_published(entry)
        link = getattr(entry, "link", "")
        title = getattr(entry, "title", "").strip()
        if not title or not link or link in seen_urls:
            continue
        parsed_link = urlsplit(link)
        if parsed_link.scheme not in {"http", "https"} or not parsed_link.netloc:
            continue
        if published is None or published.date() != report_date:
            continue
        seen_urls.add(link)
        source = (getattr(entry, "source", None) or {}).get("title", "Google News")
        score = analyzer.polarity_scores(title)["compound"]
        headlines.append(Headline(title, link, source, published, score))
        if len(headlines) == MAX_HEADLINES:
            break

    if getattr(feed, "bozo", False):
        message = f"El feed RSS se recibió con un formato inesperado: {feed.bozo_exception}"
        logging.warning("%s\n%s", company.name, message)
        return headlines, message
    return headlines, None


def fetch_quote(company: Company) -> Quote:
    if company.ticker is None:
        return Quote(warning="Sin ticker público configurado")
    try:
        info = yf.Ticker(company.ticker).get_info()
    except Exception as error:
        message = f"Yahoo Finance: {type(error).__name__}: {error}"
        logging.warning("%s (%s) - %s", company.name, company.ticker, message)
        return Quote(warning=message)

    currency = info.get("currency", "USD")
    fx_rate = get_fx_rate(currency)
    market_date = _market_date(info)

    current_price = info.get("currentPrice") or info.get("regularMarketPrice")
    previous_close = info.get("previousClose")
    daily_change_abs = info.get("regularMarketChange")
    daily_change_pct = info.get("regularMarketChangePercent")
    target_mean = info.get("targetMeanPrice")
    peg_ratio = info.get("pegRatio")
    if peg_ratio is None:
        peg_ratio = info.get("trailingPegRatio")
    peg_ratio_value = _finite_float(peg_ratio)
    pe_ratio_value = _finite_float(info.get("trailingPE"))
    recommendation = info.get("recommendationKey")
    analyst_count = info.get("numberOfAnalystOpinions")
    
    return Quote(
        current_price_native=_finite_float(current_price),
        previous_close=_finite_float(previous_close),
        current_price_eur=_finite_float(current_price, multiplier=fx_rate),
        daily_change_pct=_finite_float(daily_change_pct, multiplier=1.0),
        market_date=market_date,
        market_timezone=_market_timezone(info).key,
        daily_change_abs_eur=_finite_float(daily_change_abs, multiplier=fx_rate),
        peg_ratio=peg_ratio_value,
        pe_ratio=pe_ratio_value,
        analyst_consensus=ANALYST_LABELS.get(str(recommendation).lower()),
        analyst_count=_positive_int(analyst_count),
        target_mean_eur=_finite_float(target_mean, multiplier=fx_rate),
        sector=(str(info["sector"]).strip() if info.get("sector") else None),
        warning=(
            None
            if (
                recommendation
                or daily_change_pct is not None
                or current_price is not None
                or peg_ratio_value is not None
                or pe_ratio_value is not None
            )
            else "Yahoo Finance no devolvió cotización, consenso, PEG ni PER"
        ),
    )


def _market_date(info: dict[str, Any]) -> date | None:
    market_time = info.get("regularMarketTime")
    if market_time is None:
        return None
    try:
        if isinstance(market_time, datetime):
            market_datetime = market_time
            if market_datetime.tzinfo is None or market_datetime.utcoffset() is None:
                market_datetime = market_datetime.replace(tzinfo=timezone.utc)
        else:
            market_datetime = datetime.fromtimestamp(
                float(market_time),
                tz=timezone.utc,
            )
        market_datetime = market_datetime.astimezone(_market_timezone(info))
        return market_datetime.date()
    except (OverflowError, OSError, TypeError, ValueError, ZoneInfoNotFoundError):
        return None


def _market_timezone(info: dict[str, Any]) -> ZoneInfo:
    timezone_name = info.get("exchangeTimezoneName")
    if timezone_name:
        try:
            return ZoneInfo(str(timezone_name))
        except ZoneInfoNotFoundError:
            logging.warning("Zona horaria de mercado no reconocida: %s", timezone_name)
    return TIMEZONE


def _market_data_is_current(
    market_date: date | None,
    market_timezone: str | None,
    generated_at: datetime,
) -> bool:
    if market_date is None:
        return False
    try:
        market_zone = ZoneInfo(market_timezone) if market_timezone else TIMEZONE
    except ZoneInfoNotFoundError:
        market_zone = TIMEZONE
    return market_date == generated_at.astimezone(market_zone).date()


def translate_headline(title: str) -> tuple[str | None, str | None]:
    try:
        response = requests.get(
            GOOGLE_TRANSLATE_URL,
            params={"client": "gtx", "sl": "auto", "tl": "es", "dt": "t", "q": title},
            headers={"User-Agent": "DailyStockReport/1.0"},
            timeout=(5, 20),
        )
        response.raise_for_status()
        result = response.json()
        translated = "".join(part[0] for part in result[0] if part and part[0])
        if not translated:
            raise ValueError("el servicio no devolvió una traducción")
        return translated, None
    except (requests.RequestException, ValueError, TypeError, IndexError) as error:
        message = f"Traducción al español: {type(error).__name__}: {error}"
        logging.warning("No se pudo traducir el titular %r: %s", title, message)
        return None, message


def _finite_float(value: Any, multiplier: float = 1.0) -> float | None:
    try:
        number = float(value) * multiplier
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _dated_closes(history: Any) -> list[tuple[date, float]]:
    if history is None or history.empty or "Close" not in history:
        return []
    closes = []
    for timestamp, value in history["Close"].items():
        close = _finite_float(value)
        if close is None or close <= 0:
            continue
        closes.append((timestamp.date(), close))
    return sorted(closes)


def _close_on_or_before(
    closes: list[tuple[date, float]], target: date
) -> tuple[date, float] | None:
    return next(((day, value) for day, value in reversed(closes) if day <= target), None)


def fetch_portfolio(
    assets: tuple[PortfolioAsset, ...] = PORTFOLIO,
    as_of: date | None = None,
) -> list[PortfolioPosition]:
    as_of = as_of or datetime.now(TIMEZONE).date()
    usd_assets = any(asset.currency == "USD" for asset in assets)
    fx_closes: list[tuple[date, float]] = []
    fx_warning = None
    if usd_assets:
        try:
            fx_history = yf.Ticker("USDEUR=X").history(period="2y", auto_adjust=False)
            fx_closes = _dated_closes(fx_history)
            if not fx_closes:
                raise ValueError("Yahoo Finance no devolvió cierres históricos")
        except (
            yf.exceptions.YFException,
            requests.RequestException,
            TimeoutError,
            OSError,
            ValueError,
            KeyError,
        ) as error:
            fx_warning = f"Tipo de cambio USD/EUR: {type(error).__name__}: {error}"
            logging.warning("%s", fx_warning)

    def fetch_position(asset: PortfolioAsset) -> PortfolioPosition:
        position = PortfolioPosition(
            asset=asset,
            prices_eur={"Hoy": None, **{period: None for period, _ in PORTFOLIO_PERIODS}},
            returns_pct={period: None for period, _ in PORTFOLIO_PERIODS},
        )
        if asset.currency == "USD" and fx_warning:
            position.warning = fx_warning
            return position
        try:
            history = yf.Ticker(asset.ticker).history(period="2y", auto_adjust=False)
            closes = _dated_closes(history)
            exchange_timezone = getattr(history.index, "tz", None)
            if exchange_timezone is not None:
                position.market_timezone = str(
                    getattr(exchange_timezone, "zone", exchange_timezone)
                )
        except (
            yf.exceptions.YFException,
            requests.RequestException,
            TimeoutError,
            OSError,
            ValueError,
            KeyError,
        ) as error:
            position.warning = (
                f"{asset.ticker}: {type(error).__name__}: {error}"
            )
            logging.warning("No se pudo obtener el histórico de %s: %s", asset.ticker, error)
            return position

        if not closes:
            position.warning = f"{asset.ticker}: Yahoo Finance no devolvió cierres históricos"
            logging.warning("%s", position.warning)
            return position

        targets = {"Hoy": as_of}
        targets.update(
            {period: as_of - timedelta(days=days) for period, days in PORTFOLIO_PERIODS}
        )
        for label, target in targets.items():
            dated_close = _close_on_or_before(closes, target)
            if dated_close is None:
                continue
            price_date, close = dated_close
            if label == "Hoy":
                position.price_date = price_date
            if asset.currency == "USD":
                dated_fx_rate = _close_on_or_before(fx_closes, price_date)
                if dated_fx_rate is None:
                    continue
                _, fx_rate = dated_fx_rate
                close *= fx_rate
            position.prices_eur[label] = close

        current_price = position.prices_eur["Hoy"]
        if current_price is not None:
            position.current_value_eur = current_price * asset.shares
            position.max_profit_eur = position.current_value_eur - asset.investment_eur
            position.max_return_pct = (
                (position.current_value_eur / asset.investment_eur - 1) * 100
            )
        for period, _ in PORTFOLIO_PERIODS:
            historical_price = position.prices_eur[period]
            if current_price is not None and historical_price:
                position.returns_pct[period] = (
                    (current_price - historical_price) / historical_price * 100
                )
        if position.prices_eur["Hoy"] is None:
            position.warning = f"{asset.ticker}: no hay un cierre convertible a euros"
            logging.warning("%s", position.warning)
        else:
            missing_periods = [
                period
                for period, _ in PORTFOLIO_PERIODS
                if position.prices_eur[period] is None
            ]
            if missing_periods:
                position.warning = (
                    f"{asset.ticker}: sin cierre histórico disponible para "
                    f"{', '.join(missing_periods)}"
                )
                logging.warning("%s", position.warning)
        return position

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        return list(executor.map(fetch_position, assets))


def _analysis_inputs(
    reports: list[StockReport],
    portfolio: list[PortfolioPosition],
) -> dict[str, Any]:
    reports_by_ticker = {
        report.company.ticker: report
        for report in reports
        if report.company.ticker
    }
    reports_by_name = {report.company.name.casefold(): report for report in reports}
    portfolio_data = []
    for position in portfolio:
        report = reports_by_ticker.get(position.asset.ticker) or reports_by_name.get(
            position.asset.name.casefold()
        )
        quote = report.quote if report else Quote()
        portfolio_data.append(
            {
                "ticker": position.asset.ticker,
                "name": position.asset.name,
                "quantity": position.asset.shares,
                "current_price_eur": position.prices_eur.get("Hoy"),
                "current_value_eur": position.current_value_eur,
                "daily_change_pct": quote.daily_change_pct,
                "peg": quote.peg_ratio,
                "analyst_consensus": quote.analyst_consensus,
                "analyst_target_eur": quote.target_mean_eur,
            }
        )

    watchlist = []
    for report in reports:
        quote = report.quote
        watchlist.append(
            {
                "ticker": report.company.ticker,
                "name": report.company.name,
                "price_eur": quote.current_price_eur,
                "daily_change_pct": quote.daily_change_pct,
                "news_sentiment": (
                    classify_sentiment(report.news_sentiment)[0]
                    if report.news_sentiment is not None
                    else None
                ),
                "news_sentiment_score": report.news_sentiment,
                "peg": quote.peg_ratio,
                "analyst_consensus": quote.analyst_consensus,
                "analyst_target_eur": quote.target_mean_eur,
            }
        )

    summary = executive_summary(reports)
    sector_changes: dict[str, list[float]] = {}
    for report in reports:
        if report.quote.sector and report.quote.daily_change_pct is not None:
            sector_changes.setdefault(report.quote.sector, []).append(
                report.quote.daily_change_pct
            )
    sector_performance = [
        {
            "sector": sector,
            "average_daily_change_pct": fmean(changes),
            "companies": len(changes),
        }
        for sector, changes in sector_changes.items()
    ]
    sector_performance.sort(
        key=lambda item: item["average_daily_change_pct"], reverse=True
    )
    observed_sectors = len(sector_performance)

    return {
        "portfolio": portfolio_data,
        "watchlist": watchlist,
        "market_context": {
            "global_news_sentiment": summary["sentiment_label"],
            "global_news_sentiment_score": summary["sentiment_score"],
            "companies_with_news": summary["companies_with_news"],
            "sector_performance": sector_performance,
            "strongest_followed_sectors": sector_performance[:3],
            "weakest_followed_sectors": list(reversed(sector_performance[-3:])),
            "sector_data_note": (
                "Promedios del cambio diario de las empresas seguidas con sector disponible; "
                "no son índices sectoriales de mercado."
                if observed_sectors
                else "No se recibieron sectores de Yahoo Finance."
            ),
        },
    }


def _build_analysis_prompt(inputs: dict[str, Any]) -> str:
    serialized_inputs = json.dumps(inputs, ensure_ascii=False, allow_nan=False)
    return f"""Eres un analista financiero prudente. Analiza exclusivamente los datos de entrada y responde en español.

Tareas:
1. Analiza la cartera actual, sus concentraciones, solapamientos y diversificación geográfica/sectorial.
2. Explica qué cambios considerarías y por qué, distinguiendo hechos de inferencias.
3. Clasifica y ordena las 3 a 5 mejores candidatas de compra de la lista de seguimiento, si hay suficientes datos; usa únicamente tickers de esa lista. Si los datos no bastan para recomendar 3, explica la limitación.
4. Señala posibles ventas únicamente entre los tickers que ya están en cartera. Si no hay una razón basada en los datos para vender, devuelve una lista vacía.
5. Usa el sentimiento agregado y los sectores seguidos más fuertes/débiles cuando estén disponibles. Son promedios de la lista seguida, no índices de mercado. Si falta un dato, indícalo en lugar de inventarlo.
6. Considera precio, cambio diario, sentimiento de noticias, PEG, consenso y objetivos analistas cuando existan. No presentes el consenso ni el sentimiento como garantías.

Devuelve solo un objeto JSON válido, sin Markdown, con esta forma exacta:
{{
  "portfolio_assessment": "resumen del estado de la cartera",
  "diversification": "evaluación de diversificación y solapamientos",
  "recommended_changes": "cambios posibles y sus motivos",
  "top_buys": [{{"ticker": "TICKER", "name": "nombre", "reason": "motivo y riesgos"}}],
  "sell_candidates": [{{"ticker": "TICKER", "name": "nombre", "reason": "motivo y riesgos"}}],
  "market_context": "lectura del sentimiento global y sectores disponibles",
  "risks": ["limitación o riesgo relevante"]
}}

Es un análisis informativo, no asesoramiento financiero personalizado. El horizonte temporal, tolerancia al riesgo, objetivos y situación fiscal del usuario no están disponibles. No inventes precios ni hechos externos y no afirmes conocer el futuro.

Datos actuales:
{serialized_inputs}"""


def _read_gemini_cache(
    cache_path: Path,
    now_utc: datetime,
    api_key_fingerprint: str | None = None,
) -> PortfolioAnalysis | None:
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc debe incluir una zona horaria")
    now_local = now_utc.astimezone(TIMEZONE)
    latest_target_hour = max(hour for hour in TARGET_HOURS if hour <= now_local.hour)
    latest_target = now_local.replace(
        hour=latest_target_hour,
        minute=0,
        second=0,
        microsecond=0,
    )

    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        timestamp = datetime.fromisoformat(cached.pop("timestamp"))
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("timestamp sin zona horaria")
        cached_fingerprint = cached.get("api_key_fingerprint")
        if (
            "retry_after_utc" in cached
            and (
                "api_key_fingerprint" not in cached
                or cached_fingerprint != api_key_fingerprint
            )
        ):
            logging.info(
                "La clave Gemini cambió o la caché es anterior al control de claves; "
                "se omite la pausa asociada al fallo anterior."
            )
            return None
        retry_after = cached.get("retry_after_utc")
        if retry_after is not None:
            retry_after_datetime = datetime.fromisoformat(retry_after)
            if (
                retry_after_datetime.tzinfo is None
                or retry_after_datetime.utcoffset() is None
            ):
                raise ValueError("retry_after_utc sin zona horaria")
            if now_utc.astimezone(timezone.utc) < retry_after_datetime.astimezone(
                timezone.utc
            ):
                return PortfolioAnalysis(
                    warning=(
                        f"{cached.get('failure_warning', 'Gemini no está disponible.')}"
                        " Se aplaza el siguiente intento hasta "
                        f"{retry_after_datetime.astimezone(TIMEZONE).strftime('%d/%m/%Y %H:%M')} "
                        f"({TIMEZONE.key})."
                    ),
                    last_updated_utc=timestamp.astimezone(timezone.utc).isoformat(),
                )
            if "failure_warning" in cached:
                return None

        timestamp_local = timestamp.astimezone(TIMEZONE)
        if timestamp_local < latest_target:
            logging.info(
                "La caché Gemini es anterior al último hito horario (%s).",
                latest_target.isoformat(),
            )
            return None
        cached["last_updated_utc"] = timestamp.astimezone(timezone.utc).isoformat()
        analysis = PortfolioAnalysis(**cached)
        if (
            analysis.warning is not None
            or not all(
                isinstance(value, str)
                for value in (
                    analysis.portfolio_assessment,
                    analysis.diversification,
                    analysis.recommended_changes,
                    analysis.market_context,
                )
            )
            or not all(
                isinstance(items, list)
                for items in (
                    analysis.top_buys,
                    analysis.sell_candidates,
                    analysis.risks,
                )
            )
            or any(
                not isinstance(item, dict)
                or any(
                    not isinstance(item.get(key), str)
                    for key in ("ticker", "name", "reason")
                )
                for items in (analysis.top_buys, analysis.sell_candidates)
                for item in items
            )
            or any(not isinstance(item, str) for item in analysis.risks)
        ):
            raise ValueError("los datos de análisis almacenados no son válidos")
        return analysis
    except FileNotFoundError:
        return None
    except (
        OSError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        KeyError,
        AttributeError,
    ) as error:
        logging.warning(
            "No se pudo usar la caché Gemini %s: %s",
            cache_path,
            error,
        )
        return None


def _write_gemini_cache(
    cache_path: Path,
    analysis: PortfolioAnalysis,
    now_utc: datetime,
) -> None:
    cached = asdict(analysis)
    timestamp = now_utc.astimezone(timezone.utc).isoformat()
    cached["timestamp"] = timestamp
    cached["last_updated_utc"] = timestamp
    temporary_path = cache_path.with_name(f".{cache_path.name}.tmp")
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(
            json.dumps(cached, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
        os.replace(temporary_path, cache_path)
    except (OSError, TypeError, ValueError) as error:
        logging.error("No se pudo guardar la caché Gemini %s: %s", cache_path, error)
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError as cleanup_error:
            logging.error(
                "No se pudo limpiar el archivo temporal de caché %s: %s",
                temporary_path,
                cleanup_error,
            )


def _gemini_retry_delay(
    error: requests.RequestException,
    now_utc: datetime,
) -> timedelta:
    response = error.response if isinstance(error, requests.HTTPError) else None
    if response is not None:
        retry_after = (getattr(response, "headers", None) or {}).get("Retry-After")
        if retry_after:
            try:
                return max(timedelta(0), timedelta(seconds=float(retry_after)))
            except (TypeError, ValueError, OverflowError):
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    return max(
                        timedelta(0),
                        retry_at.astimezone(timezone.utc)
                        - now_utc.astimezone(timezone.utc),
                    )
                except (TypeError, ValueError, OverflowError):
                    pass

        try:
            body = response.json()
        except (ValueError, AttributeError):
            body = {}
        serialized_body = json.dumps(body, ensure_ascii=False)
        retry_match = re.search(
            r"retry in\s+((?:\d+(?:\.\d+)?h)?(?:\d+(?:\.\d+)?m)?"
            r"(?:\d+(?:\.\d+)?s)?)",
            serialized_body,
            re.IGNORECASE,
        )
        if retry_match:
            duration_match = re.fullmatch(
                r"(?:(\d+(?:\.\d+)?)h)?"
                r"(?:(\d+(?:\.\d+)?)m)?"
                r"(?:(\d+(?:\.\d+)?)s)?",
                retry_match.group(1),
                re.IGNORECASE,
            )
            if duration_match:
                hours, minutes, seconds = (
                    float(value or 0) for value in duration_match.groups()
                )
                return timedelta(
                    hours=hours,
                    minutes=minutes,
                    seconds=seconds,
                )

        if response.status_code == 429:
            return timedelta(hours=3)
    return timedelta(minutes=15)


def _write_gemini_failure_cache(
    cache_path: Path,
    warning: str,
    now_utc: datetime,
    retry_delay: timedelta,
    api_key_fingerprint: str | None,
) -> None:
    timestamp = now_utc.astimezone(timezone.utc)
    cached = {
        "timestamp": timestamp.isoformat(),
        "failure_warning": warning,
        "retry_after_utc": (timestamp + retry_delay).isoformat(),
        "api_key_fingerprint": api_key_fingerprint,
    }
    temporary_path = cache_path.with_name(f".{cache_path.name}.tmp")
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(
            json.dumps(cached, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
        os.replace(temporary_path, cache_path)
    except (OSError, TypeError, ValueError) as error:
        logging.error("No se pudo guardar la pausa de Gemini %s: %s", cache_path, error)
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError as cleanup_error:
            logging.error(
                "No se pudo limpiar el archivo temporal de caché %s: %s",
                temporary_path,
                cleanup_error,
            )


def analyze_portfolio(
    reports: list[StockReport],
    portfolio: list[PortfolioPosition],
    api_key: str | None = None,
    model: str = GEMINI_MODEL,
    *,
    cache_path: Path | None = None,
    now_utc: datetime | None = None,
) -> PortfolioAnalysis:
    analysis = PortfolioAnalysis()
    cache_path = cache_path or GEMINI_CACHE_FILE
    now_utc = now_utc or datetime.now(timezone.utc)
    api_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY")
    api_key_fingerprint = (
        hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        if api_key
        else None
    )
    cached_analysis = _read_gemini_cache(
        cache_path,
        now_utc,
        api_key_fingerprint,
    )
    if cached_analysis is not None:
        logging.info("Se reutiliza el análisis Gemini almacenado en %s.", cache_path)
        return cached_analysis

    if not api_key:
        analysis.warning = (
            "Análisis IA no disponible: configura el secreto GEMINI_API_KEY "
            "en GitHub Actions o la variable de entorno local."
        )
        logging.warning("%s", analysis.warning)
        return analysis

    if not model or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in model):
        analysis.warning = "Análisis IA no disponible: el nombre del modelo Gemini no es válido."
        logging.error("%s", analysis.warning)
        return analysis

    inputs = _analysis_inputs(reports, portfolio)
    request_body = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": _build_analysis_prompt(inputs)}],
            }
        ],
        "generationConfig": {
            "temperature": 0.3,
            "responseMimeType": "application/json",
        },
    }
    analysis.last_updated_utc = datetime.now(timezone.utc).isoformat()
    try:
        response = requests.post(
            f"{GEMINI_API_URL}/{quote(model, safe='')}:generateContent",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            json=request_body,
            timeout=(10, 45),
        )
        response.raise_for_status()
    except requests.RequestException as error:
        status = (
            f" (HTTP {error.response.status_code})"
            if isinstance(error, requests.HTTPError) and error.response is not None
            else ""
        )
        provider_message = ""
        if isinstance(error, requests.HTTPError) and error.response is not None:
            try:
                provider_error = error.response.json().get("error", {})
                provider_message = (
                    f" {provider_error['message']}"
                    if isinstance(provider_error, dict) and provider_error.get("message")
                    else ""
                )
            except (ValueError, AttributeError) as detail_error:
                provider_message = (
                    f" (no se pudo leer el detalle del proveedor: "
                    f"{type(detail_error).__name__})"
                )
        analysis.warning = (
            f"Análisis IA no disponible{status}: error de conexión con Gemini."
            f"{provider_message}"
        )
        logging.warning("%s (%s)", analysis.warning, type(error).__name__)
        _write_gemini_failure_cache(
            cache_path,
            analysis.warning,
            now_utc,
            _gemini_retry_delay(error, now_utc),
            api_key_fingerprint,
        )
        return analysis

    try:
        response_body = response.json()
        text = "".join(
            part["text"]
            for part in response_body["candidates"][0]["content"]["parts"]
            if "text" in part
        )
        result = json.loads(text)
        if not isinstance(result, dict):
            raise ValueError("la respuesta no es un objeto JSON")
        required_strings = (
            "portfolio_assessment",
            "diversification",
            "recommended_changes",
            "market_context",
        )
        if any(not isinstance(result.get(key), str) for key in required_strings):
            raise ValueError("faltan campos de texto obligatorios")
        for key in ("top_buys", "sell_candidates", "risks"):
            if not isinstance(result.get(key), list):
                raise ValueError(f"el campo {key} no es una lista")
        if any(
            not isinstance(item, dict)
            or any(not isinstance(item.get(field), str) for field in ("ticker", "name", "reason"))
            for key in ("top_buys", "sell_candidates")
            for item in result[key]
        ):
            raise ValueError("las recomendaciones tienen un formato no válido")
        if any(not isinstance(item, str) for item in result["risks"]):
            raise ValueError("los riesgos tienen un formato no válido")
    except (KeyError, IndexError, TypeError, ValueError) as error:
        analysis.warning = (
            "Análisis IA no disponible: Gemini devolvió una respuesta incompleta "
            "o no válida."
        )
        logging.warning("%s (%s)", analysis.warning, error)
        _write_gemini_failure_cache(
            cache_path,
            analysis.warning,
            now_utc,
            timedelta(minutes=15),
            api_key_fingerprint,
        )
        return analysis

    allowed_buys = {
        report.company.ticker
        for report in reports
        if report.company.ticker
    }
    allowed_sells = {position.asset.ticker for position in portfolio}
    invalid_recommendations = False
    analysis.top_buys = [
        item for item in result["top_buys"] if item["ticker"] in allowed_buys
    ][:5]
    analysis.sell_candidates = [
        item for item in result["sell_candidates"] if item["ticker"] in allowed_sells
    ]
    invalid_recommendations = (
        len(analysis.top_buys) != min(len(result["top_buys"]), 5)
        or len(analysis.sell_candidates) != len(result["sell_candidates"])
    )
    analysis.portfolio_assessment = result["portfolio_assessment"]
    analysis.diversification = result["diversification"]
    analysis.recommended_changes = result["recommended_changes"]
    analysis.market_context = result["market_context"]
    analysis.risks = result["risks"]
    if invalid_recommendations:
        analysis.risks.append(
            "Se omitieron recomendaciones que no correspondían a tickers de la "
            "lista de seguimiento o de la cartera."
        )
        logging.warning("Gemini devolvió recomendaciones fuera de los tickers autorizados.")
    updated_at = datetime.now(timezone.utc)
    analysis.last_updated_utc = updated_at.isoformat()
    _write_gemini_cache(cache_path, analysis, updated_at)
    return analysis


def collect_reports(
    companies: tuple[Company, ...] = COMPANIES,
    report_date: date | None = None,
) -> list[StockReport]:
    report_date = report_date or datetime.now(TIMEZONE).date()
    analyzer = SentimentIntensityAnalyzer()
    reports = [StockReport(company) for company in companies]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        news_tasks = {
            executor.submit(fetch_headlines, report.company, report_date, analyzer): report
            for report in reports
        }
        quote_tasks = {
            executor.submit(fetch_quote, report.company): report
            for report in reports
        }
        for task in as_completed(news_tasks):
            report = news_tasks[task]
            headlines, warning = task.result()
            report.headlines = headlines
            report.news_sentiment = (
                fmean(headline.sentiment for headline in headlines) if headlines else None
            )
            if warning:
                report.warnings.append(warning)
        for task in as_completed(quote_tasks):
            report = quote_tasks[task]
            report.quote = task.result()
            if report.quote.warning:
                report.warnings.append(report.quote.warning)

    headline_tasks = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for report in reports:
            for headline in report.headlines:
                headline_tasks[executor.submit(translate_headline, headline.title)] = (
                    report,
                    headline,
                )
        for task in as_completed(headline_tasks):
            report, headline = headline_tasks[task]
            headline.title_es, warning = task.result()
            if warning and warning not in report.warnings:
                report.warnings.append(warning)
    return reports


def _intraday_close_series(history: Any, ticker: str) -> Any | None:
    if history is None or getattr(history, "empty", True):
        return None
    if isinstance(history.columns, pd.MultiIndex):
        for column in history.columns:
            if ticker in column and "Close" in column:
                return history[column].dropna()
        return None
    if "Close" in history.columns:
        return history["Close"].dropna()
    return None


def collect_chart_data(
    reports: list[StockReport],
    portfolio: list[PortfolioPosition],
) -> DashboardCharts:
    report_by_ticker = {
        report.company.ticker: report
        for report in reports
        if report.company.ticker
    }
    portfolio_by_ticker = {position.asset.ticker: position for position in portfolio}
    tickers = list(
        dict.fromkeys(
            [
                *report_by_ticker,
                *portfolio_by_ticker,
            ]
        )
    )

    chart_data = DashboardCharts()
    if not tickers:
        return chart_data

    closes_by_ticker: dict[str, Any] = {}
    try:
        history = yf.download(
            tickers=tickers,
            period="1d",
            interval="5m",
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=True,
            timeout=20,
        )
        for ticker in tickers:
            close_series = _intraday_close_series(history, ticker)
            if close_series is not None:
                closes_by_ticker[ticker] = close_series
    except (
        yf.exceptions.YFException,
        requests.RequestException,
        TimeoutError,
        OSError,
        ValueError,
        KeyError,
    ) as error:
        logging.warning(
            "No se pudieron obtener los datos intradía para los gráficos: %s: %s",
            type(error).__name__,
            error,
        )

    report_descriptors: list[
        tuple[str | None, str, float | None, float | None, date | None, str | None]
    ] = []
    for report in reports:
        ticker = report.company.ticker
        quote = report.quote
        previous_close = quote.previous_close
        if (
            previous_close is None
            and quote.current_price_native is not None
            and quote.daily_change_pct is not None
            and quote.daily_change_pct != -100
        ):
            previous_close = quote.current_price_native / (
                1 + quote.daily_change_pct / 100
            )
        report_descriptors.append(
            (
                ticker,
                report.company.name,
                quote.daily_change_pct,
                previous_close,
                quote.market_date,
                quote.market_timezone,
            )
        )

    portfolio_descriptors: list[
        tuple[
            str,
            str,
            float | None,
            float | None,
            date | None,
            str | None,
            float | None,
        ]
    ] = []
    for position in portfolio:
        ticker = position.asset.ticker
        report = report_by_ticker.get(ticker)
        if report is None:
            report = next(
                (
                    candidate
                    for candidate in reports
                    if candidate.company.name.casefold()
                    == position.asset.name.casefold()
                ),
                None,
            )
        daily_return = position.returns_pct.get("1D")
        current_price = position.prices_eur.get("Hoy")
        previous_close = position.prices_eur.get("1D")
        market_date = position.price_date
        market_timezone = position.market_timezone
        if report is not None and report.company.ticker == ticker:
            market_date = report.quote.market_date or market_date
            market_timezone = report.quote.market_timezone or market_timezone
        if report is not None and report.company.ticker == ticker:
            if daily_return is None:
                daily_return = report.quote.daily_change_pct
            previous_close = report.quote.previous_close
            if (
                previous_close is None
                and report.quote.current_price_native is not None
                and report.quote.daily_change_pct is not None
                and report.quote.daily_change_pct != -100
            ):
                previous_close = report.quote.current_price_native / (
                    1 + report.quote.daily_change_pct / 100
                )
        if (
            previous_close is None
            and current_price is not None
            and daily_return is not None
            and daily_return != -100
        ):
            previous_close = current_price / (1 + daily_return / 100)
        portfolio_descriptors.append(
            (
                ticker,
                position.asset.name,
                daily_return,
                previous_close,
                market_date,
                market_timezone,
                position.current_value_eur,
            )
        )

    portfolio_value = sum(
        max(value or 0, 0) for *_, value in portfolio_descriptors
    )
    watchlist_samples: dict[str, list[float]] = {}
    portfolio_samples: dict[str, list[float]] = {}
    for group, descriptors, samples in (
        ("watchlist", report_descriptors, watchlist_samples),
        ("portfolio", portfolio_descriptors, portfolio_samples),
    ):
        for descriptor in descriptors:
            (
                ticker,
                name,
                current_change,
                previous_close,
                market_date,
                market_timezone,
            ) = descriptor[:6]
            allocation_value = descriptor[6] if group == "portfolio" else None
            if current_change is None:
                current_change = (
                    report_by_ticker[ticker].quote.daily_change_pct
                    if ticker is not None and ticker in report_by_ticker
                    else None
                )
            chart_item = {
                "ticker": ticker or name,
                "name": name,
                "change_pct": current_change,
                "market_date": market_date.isoformat() if market_date else None,
                "market_timezone": market_timezone,
            }
            if allocation_value is not None and portfolio_value > 0:
                chart_item["weight_pct"] = (
                    max(allocation_value, 0) / portfolio_value * 100
                )
            else:
                chart_item["weight_pct"] = 0.0
            getattr(chart_data, group).append(chart_item)

            close_series = closes_by_ticker.get(ticker) if ticker is not None else None
            if close_series is None or previous_close is None or previous_close <= 0:
                continue
            for timestamp, close in close_series.items():
                close_value = _finite_float(close)
                if close_value is None or close_value <= 0:
                    continue
                date_time = timestamp.to_pydatetime() if hasattr(timestamp, "to_pydatetime") else timestamp
                if date_time.tzinfo is None:
                    date_time = date_time.replace(tzinfo=timezone.utc)
                date_time = date_time.astimezone(timezone.utc)
                timestamp_key = date_time.isoformat()
                samples.setdefault(timestamp_key, []).append(
                    (close_value / previous_close - 1) * 100
                )

    chart_data.watchlist_series = [
        {"timestamp": timestamp, "value": fmean(values)}
        for timestamp, values in sorted(watchlist_samples.items())
    ]
    chart_data.portfolio_series = [
        {"timestamp": timestamp, "value": fmean(values)}
        for timestamp, values in sorted(portfolio_samples.items())
    ]
    return chart_data


def executive_summary(reports: list[StockReport]) -> dict[str, Any]:
    scored = [report.news_sentiment for report in reports if report.news_sentiment is not None]
    overall_score = fmean(scored) if scored else None
    overall_label, overall_signal = classify_sentiment(overall_score)

    analyst_counts: dict[str, int] = {}
    for report in reports:
        if report.quote.analyst_consensus:
            label = report.quote.analyst_consensus
            analyst_counts[label] = analyst_counts.get(label, 0) + 1
    analyst_total = sum(analyst_counts.values())
    if analyst_counts:
        top_consensus = max(analyst_counts, key=analyst_counts.get)
        consensus_summary = (
            f"{top_consensus} es la recomendación más frecuente "
            f"({analyst_counts[top_consensus]} de {analyst_total} empresas con datos)."
        )
    else:
        consensus_summary = "Yahoo Finance no devolvió consenso de analistas."

    key_headlines = sorted(
        (headline for report in reports for headline in report.headlines),
        key=lambda headline: abs(headline.sentiment),
        reverse=True,
    )[:5]
    return {
        "sentiment_label": overall_label,
        "sentiment_signal": overall_signal,
        "sentiment_score": overall_score,
        "companies_with_news": len(scored),
        "headline_count": sum(len(report.headlines) for report in reports),
        "analyst_counts": analyst_counts,
        "analyst_total": analyst_total,
        "consensus_summary": consensus_summary,
        "key_headlines": key_headlines,
    }


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _format_number(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{decimals}f}"


def _headline_html(headline: Headline) -> str:
    label, signal = classify_sentiment(headline.sentiment)
    display_title = headline.title_es or headline.title
    original_title = (
        f'<span class="headline-original">Original: {_esc(headline.title)}</span>'
        if headline.title_es and headline.title_es != headline.title
        else ""
    )
    return (
        '<li class="headline">'
        f'<a href="{_esc(headline.url)}" target="_blank" rel="noopener noreferrer">'
        f"{_esc(display_title)}</a>{original_title}"
        f'<span class="headline-meta">{_esc(headline.source)} · '
        f'{_esc(headline.published.strftime("%H:%M"))} · '
        f'<span class="text-{signal}">{label}</span></span></li>'
    )


def _portfolio_cell(value: float | None, suffix: str = "", decimals: int = 2) -> str:
    if value is None:
        return "—"
    return f"{_format_number(value, decimals)}{suffix}"


def _portfolio_change(value: float | None) -> str:
    if value is None:
        return '<td class="text-neutral">—</td>'
    css_class = "text-bullish" if value > 0 else "text-bearish" if value < 0 else "text-neutral"
    sign = "+" if value > 0 else ""
    return f'<td class="{css_class}">{sign}{_format_number(value)}%</td>'


def _portfolio_value(value: float | None, kind: str) -> str:
    if kind == "return":
        return _portfolio_change(value)
    if kind == "profit":
        css_class = (
            "text-bullish" if value is not None and value > 0
            else "text-bearish" if value is not None and value < 0
            else ""
        )
        return f'<td class="{css_class}">{_portfolio_cell(value, " €")}</td>'
    return f"<td>{_portfolio_cell(value, ' €')}</td>"


def _analysis_paragraph(value: str | None) -> str:
    return _esc(value or "Sin datos en la respuesta.").replace("\n", "<br>")


def _analysis_recommendations(
    recommendations: list[dict[str, str]],
    empty_text: str,
) -> str:
    if not recommendations:
        return f"<li>{_esc(empty_text)}</li>"
    return "".join(
        f"<li><strong>{_esc(item['ticker'])} · {_esc(item['name'])}</strong>"
        f"<p>{_analysis_paragraph(item['reason'])}</p></li>"
        for item in recommendations
    )


def _analysis_section(analysis: PortfolioAnalysis | None) -> str:
    if analysis is None:
        return ""
    last_updated_text = "Sin consulta confirmada"
    if analysis.last_updated_utc:
        try:
            last_updated = datetime.fromisoformat(analysis.last_updated_utc)
            if last_updated.tzinfo is None or last_updated.utcoffset() is None:
                raise ValueError("timestamp sin zona horaria")
            last_updated_text = last_updated.astimezone(TIMEZONE).strftime(
                "%d/%m/%Y · %H:%M %Z"
            )
        except ValueError as error:
            logging.warning("Timestamp de análisis IA no válido: %s", error)
    if analysis.warning:
        content = (
            '<div class="analysis-warning" role="status">'
            f"{_esc(analysis.warning)}</div>"
        )
    else:
        risks_html = "".join(
            f"<li>{_analysis_paragraph(risk)}</li>" for risk in analysis.risks
        ) or "<li>No se señalaron riesgos adicionales.</li>"
        content = (
            '<div class="analysis-grid">'
            f'<article><h3>Evaluación de la cartera</h3><p>{_analysis_paragraph(analysis.portfolio_assessment)}</p></article>'
            f'<article><h3>Diversificación</h3><p>{_analysis_paragraph(analysis.diversification)}</p></article>'
            f'<article><h3>Cambios posibles</h3><p>{_analysis_paragraph(analysis.recommended_changes)}</p></article>'
            f'<article><h3>Contexto de mercado</h3><p>{_analysis_paragraph(analysis.market_context)}</p></article>'
            "</div>"
            '<div class="analysis-recommendations">'
            "<div><h3>Mejores candidatas de compra</h3><ol>"
            f"{_analysis_recommendations(analysis.top_buys, 'No hay candidatas suficientes con los datos actuales.')}"
            "</ol></div>"
            "<div><h3>Posibles ventas de la cartera</h3><ul>"
            f"{_analysis_recommendations(analysis.sell_candidates, 'No se identificaron posiciones con motivos suficientes para vender.')}"
            "</ul></div></div>"
            f'<div class="analysis-risks"><h3>Riesgos y limitaciones</h3><ul>{risks_html}</ul></div>'
        )
    return (
        '<section aria-labelledby="ai-analysis-title">'
        '<div class="section-heading"><h2 id="ai-analysis-title">Análisis de cartera con Gemini Flash</h2>'
        f'<span>Última consulta: {_esc(last_updated_text)}</span></div>'
        f'<div class="analysis-panel">{content}'
        '<p class="portfolio-note">Análisis informativo generado por IA; no constituye asesoramiento financiero personalizado. '
        "Puede contener errores y no conoce tus objetivos, horizonte ni tolerancia al riesgo.</p></div></section>"
    )


SORT_SCRIPT = """<script>
(() => {
  const grid = document.getElementById('stock-grid');
  const select = document.getElementById('sort-select');
  if (!grid || !select) return;

  const cards = Array.from(grid.querySelectorAll('.stock-card'));
  const origin = new Map(cards.map((card, i) => [card, i]));
  const divider = document.getElementById('stale-market-divider');

  // Pesos: menor = mejor. Lo que no esté en el mapa ("Sin datos") va al final.
  const CONSENSUS = { 'Compra fuerte': 1, 'Compra': 2, 'Mantener': 3, 'Rendimiento inferior': 4, 'Venta': 5, 'Venta fuerte': 6 };
  const SENTIMENT = { 'Alcista': 1, 'Neutral': 2, 'Bajista': 3 };

  const num = (v) => (v === undefined || v === '' || Number.isNaN(Number(v)) ? null : Number(v));

  // Cada criterio devuelve un número (menor = antes) o null (sin datos = al final).
  const keys = {
    daily: (c) => { const v = num(c.dataset.daily); return v === null ? null : -v; },
    peg: (c) => { const v = num(c.dataset.peg); return v === null || v < 0 ? null : v; },
    consensus: (c) => {
      const rank = CONSENSUS[c.dataset.consensus];
      if (rank === undefined) return null;
      const analysts = num(c.dataset.analysts) || 0;
      // Restamos el (nº de analistas / 100.000) para priorizar los mayores dentro del mismo ranking
      return rank - (analysts / 100000);
    },
    sentiment: (c) => SENTIMENT[c.dataset.news] ?? null,
  };

  function sortCards(mode) {
    const key = keys[mode] || keys.daily;
    const sorted = cards.slice().sort((a, b) => {
      const aCurrent = a.dataset.currentDay === 'true';
      const bCurrent = b.dataset.currentDay === 'true';
      if (aCurrent !== bCurrent) return aCurrent ? -1 : 1;
      const ka = key(a), kb = key(b);
      if (ka === null || kb === null) return ka === kb ? origin.get(a) - origin.get(b) : (ka === null ? 1 : -1);
      return ka - kb || origin.get(a) - origin.get(b);
    });
    const frag = document.createDocumentFragment();
    const fresh = sorted.filter((card) => card.dataset.currentDay === 'true');
    const stale = sorted.filter((card) => card.dataset.currentDay !== 'true');
    fresh.forEach((card) => frag.appendChild(card));
    if (stale.length && divider) {
      divider.hidden = false;
      frag.appendChild(divider);
    } else if (divider) {
      divider.hidden = true;
    }
    stale.forEach((card) => frag.appendChild(card));
    grid.appendChild(frag);
  }

  select.addEventListener('change', () => sortCards(select.value));
  sortCards(select.value);
})();
</script>"""

PORTFOLIO_SCRIPT = """<script>
(() => {
  const periodSelect = document.getElementById('portfolio-period');
  const metricSelect = document.getElementById('portfolio-metric');
  const heading = document.getElementById('portfolio-selected-heading');
  const cells = Array.from(document.querySelectorAll('[data-portfolio-period]'));
  const maxOnlyCells = Array.from(document.querySelectorAll('[data-portfolio-max-only]'));
  if (!periodSelect || !metricSelect || !heading) return;

  const historicalOptions = [['return', 'Rendimiento (%)']];
  const maxOptions = [
    ['profit', 'Ganancia absoluta (€)'],
    ['return', 'Rendimiento (%)'],
  ];

  function updatePortfolioView() {
    const period = periodSelect.value;
    const options = period === 'MAX' ? maxOptions : historicalOptions;
    const previousValue = metricSelect.value;
    metricSelect.replaceChildren(...options.map(([value, label]) => {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      return option;
    }));
    metricSelect.value = options.some(([value]) => value === previousValue)
      ? previousValue
      : options[0][0];
    const kind = metricSelect.value;
    heading.textContent = period === 'MAX'
      ? (kind === 'profit' ? 'Ganancia MAX' : 'Rendimiento MAX')
      : `Rendimiento ${period}`;
    maxOnlyCells.forEach((cell) => {
      cell.hidden = period !== 'MAX';
    });
    cells.forEach((cell) => {
      cell.hidden = cell.dataset.portfolioPeriod !== period
        || cell.dataset.portfolioKind !== kind;
    });
  }

  periodSelect.addEventListener('change', updatePortfolioView);
  metricSelect.addEventListener('change', updatePortfolioView);
  updatePortfolioView();
})();
</script>"""

CHARTS_SCRIPT = """<script>
(() => {
  const payloadNode = document.getElementById('dashboard-chart-data');
  if (!payloadNode || !window.d3) return;
  const payload = JSON.parse(payloadNode.textContent);
  const d3 = window.d3;

  function returnColor(value, extent) {
    if (value === null || !Number.isFinite(value)) return '#354052';
    const magnitude = Math.max(extent, 0.5);
    return d3.scaleDiverging()
      .domain([-magnitude, 0, magnitude])
      .interpolator(d3.interpolateRdYlGn)
      (Math.max(-magnitude, Math.min(magnitude, value)));
  }

  function renderHeatmap(id, items, proportional = false) {
    const node = document.getElementById(id);
    if (!node) return;
    const width = Math.max(280, node.clientWidth || 280);
    const height = Math.max(240, Math.min(420, Math.ceil(items.length / Math.max(4, Math.floor(width / 110))) * 56));
    const svg = d3.select(node).attr('viewBox', `0 0 ${width} ${height}`).attr('height', height);
    svg.selectAll('*').remove();
    if (!items.length) {
      svg.append('text').attr('x', 12).attr('y', 28).attr('class', 'chart-empty')
        .text('No hay instrumentos para mostrar.');
      return;
    }

    const extent = d3.max(items, (item) => Math.abs(item.change_pct || 0)) || 0.5;
    const root = d3.hierarchy({children: items}).sum((item) => (
      proportional ? Math.max(0, Number(item.weight_pct) || 0) : 1
    ));
    d3.treemap().size([width, height]).paddingInner(3).round(true)(root);
    const groups = svg.selectAll('g').data(root.leaves()).join('g')
      .attr('transform', (item) => `translate(${item.x0},${item.y0})`);
    groups.append('rect')
      .attr('width', (item) => Math.max(0, item.x1 - item.x0))
      .attr('height', (item) => Math.max(0, item.y1 - item.y0))
      .attr('rx', 5)
      .attr('fill', (item) => item.data.current_day
        ? returnColor(item.data.change_pct, extent)
        : 'var(--bg)');
    groups.append('title').text((item) => {
      const change = item.data.change_pct;
      return `${item.data.name} (${item.data.ticker}): ${
        change === null ? 'sin datos' : `${change > 0 ? '+' : ''}${change.toFixed(2)}%`
      }`;
    });
    groups.filter((item) => item.data.current_day
      && item.x1 - item.x0 >= 54 && item.y1 - item.y0 >= 38)
      .append('text').attr('x', 8).attr('y', 18).attr('class', 'heatmap-name')
      .text((item) => item.data.name);
    groups.filter((item) => item.data.current_day
      && item.x1 - item.x0 >= 54 && item.y1 - item.y0 >= 55)
      .append('text').attr('x', 8).attr('y', 37).attr('class', 'heatmap-return')
      .text((item) => item.data.change_pct === null
        ? '—'
        : `${item.data.change_pct > 0 ? '+' : ''}${item.data.change_pct.toFixed(2)}%`);
  }

  function renderAverageLine(id, series) {
    const node = document.getElementById(id);
    if (!node) return;
    const width = Math.max(280, node.clientWidth || 280);
    const height = 230;
    const margin = {top: 14, right: 12, bottom: 23, left: 12};
    const svg = d3.select(node).attr('viewBox', `0 0 ${width} ${height}`);
    svg.selectAll('*').remove();
    const validSeries = series.filter((point) => Number.isFinite(point.value));
    const status = document.getElementById(`${id}-status`);
    if (!validSeries.length) {
      if (status) status.textContent = 'No hay histórico intradía disponible.';
      svg.append('text').attr('x', margin.left).attr('y', 28).attr('class', 'chart-empty')
        .text('Sin datos intradía.');
      return;
    }

    const lastPoint = validSeries[validSeries.length - 1];
    const mean = lastPoint.value;
    const color = mean > 0 ? '#20d69a' : mean < 0 ? '#ff647c' : '#8b98ac';
    if (status) {
      status.textContent = `Media actual ${mean > 0 ? '+' : ''}${mean.toFixed(2)}% · ${
        validSeries.length
      } intervalos`;
      status.style.color = color;
    }
    const x = d3.scaleUtc()
      .domain(d3.extent(validSeries, (point) => new Date(point.timestamp)))
      .range([margin.left, width - margin.right]);
    const min = d3.min(validSeries, (point) => point.value);
    const max = d3.max(validSeries, (point) => point.value);
    const padding = Math.max((max - min) * 0.15, 0.1);
    const y = d3.scaleLinear()
      .domain([Math.min(0, min - padding), Math.max(0, max + padding)])
      .range([height - margin.bottom, margin.top]);

    const gradientId = `${id}-gradient`;
    const defs = svg.append('defs');
    const gradient = defs.append('linearGradient')
      .attr('id', gradientId).attr('x1', '0').attr('x2', '0').attr('y1', '0').attr('y2', '1');
    gradient.append('stop').attr('offset', '0%').attr('stop-color', color).attr('stop-opacity', 0.3);
    gradient.append('stop').attr('offset', '100%').attr('stop-color', color).attr('stop-opacity', 0);

    const area = d3.area()
      .x((point) => x(new Date(point.timestamp)))
      .y0(height - margin.bottom)
      .y1((point) => y(point.value))
      .curve(d3.curveMonotoneX);
    const line = d3.line()
      .x((point) => x(new Date(point.timestamp)))
      .y((point) => y(point.value))
      .curve(d3.curveMonotoneX);
    svg.append('path').datum(validSeries).attr('d', area).attr('fill', `url(#${gradientId})`);
    svg.append('path').datum(validSeries).attr('d', line).attr('fill', 'none')
      .attr('stroke', color).attr('stroke-width', 2.5).attr('stroke-linecap', 'round');
    const firstTime = new Date(validSeries[0].timestamp);
    const lastTime = new Date(lastPoint.timestamp);
    const formatTime = (date) => new Intl.DateTimeFormat('es-ES', {
      timeZone: payload.timezone,
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    }).format(date);
    svg.append('text').attr('x', margin.left).attr('y', height - 4)
      .attr('class', 'chart-time').text(formatTime(firstTime));
    svg.append('text').attr('x', width - margin.right).attr('y', height - 4)
      .attr('text-anchor', 'end').attr('class', 'chart-time').text(formatTime(lastTime));
  }

  function renderAll() {
    renderHeatmap('watchlist-heatmap', payload.watchlist);
    renderHeatmap('portfolio-heatmap', payload.portfolio, true);
    renderAverageLine('watchlist-average-chart', payload.watchlist_series);
    renderAverageLine('portfolio-average-chart', payload.portfolio_series);
  }

  renderAll();
  if ('ResizeObserver' in window) {
    const observer = new ResizeObserver(renderAll);
    document.querySelectorAll('.chart-resize-target').forEach((node) => observer.observe(node));
  } else {
    window.addEventListener('resize', renderAll, {passive: true});
  }
})();
</script>"""


def render_dashboard(
    reports: list[StockReport],
    generated_at: datetime | None = None,
    portfolio: list[PortfolioPosition] | None = None,
    analysis: PortfolioAnalysis | None = None,
    chart_data: DashboardCharts | None = None,
) -> str:
    generated_at = generated_at or datetime.now(TIMEZONE)
    portfolio = portfolio or []
    chart_data = chart_data or DashboardCharts()
    
    def quote_is_current(report: StockReport) -> bool:
        return _market_data_is_current(
            report.quote.market_date,
            report.quote.market_timezone,
            generated_at,
        ) and report.quote.daily_change_pct is not None

    reports = sorted(
        reports,
        key=lambda report: (
            quote_is_current(report),
            report.quote.daily_change_pct
            if report.quote.daily_change_pct is not None
            else -math.inf,
        ),
        reverse=True,
    )

    summary = executive_summary(reports)
    label = _esc(summary["sentiment_label"])
    signal = summary["sentiment_signal"]
    score = summary["sentiment_score"]
    score_text = f"{score:+.2f}" if score is not None else "sin señal"

    cards: list[str] = []
    for report in reports:
        company = report.company
        sentiment_label, sentiment_signal = classify_sentiment(report.news_sentiment)
        quote = report.quote
        ticker_text = company.ticker or "Sin ticker"
        
        price_text = (
            f'{_format_number(quote.current_price_eur)} €'
            if quote.current_price_eur is not None
            else "—"
        )
        
        if quote.daily_change_abs_eur is not None and quote.daily_change_pct is not None:
            sign = "+" if quote.daily_change_abs_eur > 0 else ""
            rendimiento_text = f'{sign}{_format_number(quote.daily_change_abs_eur)} € ({sign}{_format_number(quote.daily_change_pct)}%)'
            rendimiento_class = "text-bullish" if quote.daily_change_abs_eur > 0 else "text-bearish" if quote.daily_change_abs_eur < 0 else "text-neutral"
        else:
            rendimiento_text = "—"
            rendimiento_class = ""
            
        target_text = (
            f'{_format_number(quote.target_mean_eur)} €'
            if quote.target_mean_eur is not None
            else "—"
        )
        peg_text = _format_number(quote.peg_ratio)
        peg_class = classify_peg(quote.peg_ratio)
        pe_text = _format_number(quote.pe_ratio)
        analyst_text = quote.analyst_consensus or "Sin datos"
        if quote.analyst_count:
            analyst_text += f" · {quote.analyst_count} analistas"
            
        if report.news_sentiment is None:
            news_note = "No hay titulares de hoy; sentimiento no disponible."
        else:
            news_note = f"Sentimiento de titulares · puntuación {report.news_sentiment:+.2f}"
            
        headline_list = "".join(_headline_html(item) for item in report.headlines)
        if not headline_list:
            headline_list = '<li class="empty">No se encontraron titulares de hoy.</li>'
        warning_list = "".join(f"<li>{_esc(item)}</li>" for item in report.warnings)
        warnings_html = (
            f'<ul class="warnings" aria-label="Avisos de datos">{warning_list}</ul>'
            if warning_list
            else ""
        )
        private_note = (
            '<p class="private-note">No hay ticker público configurado; no se puede consultar '
            "cotización ni consenso de analistas.</p>"
            if company.ticker is None
            else ""
        )
        quote_is_current_today = quote_is_current(report)
        quote_stale_note = (
            ""
            if quote_is_current_today
            else (
                '<p class="quote-stale-note">Cotización pendiente de actualización'
                + (
                    f' · último dato: {_esc(quote.market_date.strftime("%d/%m/%Y"))}'
                    if quote.market_date
                    else " · no hay datos de hoy"
                )
                + "</p>"
            )
        )
        
        cards.append(
            f'<article class="stock-card" data-current-day="{str(quote_is_current_today).lower()}" '
            f'data-sentiment="{sentiment_signal}" '
            f'data-daily="{_num_attr(quote.daily_change_pct)}" '
            f'data-peg="{_num_attr(quote.peg_ratio)}" '
            f'data-consensus="{_esc(quote.analyst_consensus or "")}" '
            f'data-analysts="{quote.analyst_count or 0}" '
            f'data-news="{_esc(sentiment_label)}">'
            '<div class="card-top"><div>'
            f'<p class="ticker">{_esc(ticker_text)}</p>'
            f'<h2>{_esc(company.name)}</h2></div>'
            f'<span class="badge {sentiment_signal}"><i></i>{_esc(sentiment_label)}</span></div>'
            f'<p class="sentiment-note">{_esc(news_note)}</p>'
            '<div class="metrics">'
            f'<div><span>Precio actual</span><strong>{_esc(price_text)}</strong></div>'
            f'<div><span>Rendimiento diario</span><strong class="{rendimiento_class}">{_esc(rendimiento_text)}</strong></div>'
            f'<div><span>Consenso analistas</span><strong>{_esc(analyst_text)}</strong></div>'
            f'<div><span>Precio objetivo</span><strong>{_esc(target_text)}</strong></div>'
            f'<div><span title="Price/Earnings-to-Growth; ratio informativo">PEG</span>'
            f'<strong class="{peg_class}">{_esc(peg_text)}</strong></div>'
            f'<div><span title="PER basado en beneficios de los últimos doce meses">PER (TTM)</span>'
            f'<strong>{_esc(pe_text)}</strong></div>'
            "</div>"
            f"{quote_stale_note}"
            f"{private_note}<h3>Titulares clave</h3><ul class=\"headlines\">{headline_list}</ul>"
            f"{warnings_html}</article>"
        )

    analyst_breakdown = " · ".join(
        f"{_esc(name)}: {count}" for name, count in summary["analyst_counts"].items()
    ) or "Sin datos"
    key_headlines_html = "".join(
        f'<li><a href="{_esc(item.url)}" target="_blank" rel="noopener noreferrer">'
        f"{_esc(item.title_es or item.title)}</a>"
        + (
            f'<span class="headline-original">Original: {_esc(item.title)}</span>'
            if item.title_es and item.title_es != item.title
            else ""
        )
        + f"<span>{_esc(item.source)}</span></li>"
        for item in summary["key_headlines"]
    ) or "<li>Aún no hay titulares con fecha de hoy.</li>"
    portfolio_rows = "".join(
        "<tr>"
        f'<th scope="row" data-label="Activo">{_esc(position.asset.name)}'
        f'<span>{_esc(position.asset.ticker)}</span></th>'
        f'<td data-label="Posición">{_format_number(position.asset.shares, 6)}</td>'
        f'<td data-label="Inversión base" data-portfolio-max-only="true" hidden>'
        f"{_portfolio_cell(position.asset.investment_eur, ' €')}</td>"
        f'<td data-label="Precio actual">{_portfolio_cell(position.prices_eur.get("Hoy"), " €")}</td>'
        f'<td data-label="Valor actual" data-portfolio-max-only="true" hidden>'
        f"{_portfolio_cell(position.current_value_eur, ' €')}</td>"
        + "".join(
            _portfolio_value(position.returns_pct.get(period), "return").replace(
                "<td",
                f'<td hidden data-label="Rendimiento {period}" data-portfolio-period="{period}" '
                'data-portfolio-kind="return"',
            )
            for period, _ in PORTFOLIO_PERIODS
        )
        + _portfolio_value(position.max_profit_eur, "profit").replace(
            "<td",
            '<td hidden data-label="Ganancia MAX" data-portfolio-period="MAX" '
            'data-portfolio-kind="profit"',
        )
        + _portfolio_value(position.max_return_pct, "return").replace(
            "<td",
            '<td hidden data-label="Rendimiento MAX" data-portfolio-period="MAX" '
            'data-portfolio-kind="return"',
        )
        + "</tr>"
        for position in portfolio
    )
    portfolio_warnings = [
        position.warning for position in portfolio if position.warning
    ]
    portfolio_warning_html = (
        '<ul class="warnings" aria-label="Avisos de datos de cartera">'
        + "".join(f"<li>{_esc(warning)}</li>" for warning in portfolio_warnings)
        + "</ul>"
        if portfolio_warnings
        else ""
    )
    available_positions = sum(
        position.current_value_eur is not None for position in portfolio
    )
    if portfolio and available_positions == len(portfolio):
        total_invested = sum(position.asset.investment_eur for position in portfolio)
        total_value = sum(position.current_value_eur or 0 for position in portfolio)
        total_profit = total_value - total_invested
        total_return = (total_value / total_invested - 1) * 100
        portfolio_total = (
            f'<p class="portfolio-total">Valor actual: <strong>{_portfolio_cell(total_value, " €")}</strong>'
            f' · Inversión base: {_portfolio_cell(total_invested, " €")}'
            f' · Ganancia MAX: <strong>{_portfolio_cell(total_profit, " €")} '
            f'({_portfolio_cell(total_return, "%")})</strong></p>'
        )
    elif portfolio:
        portfolio_total = (
            f'<p class="portfolio-total">Cotización disponible para {available_positions} '
            f'de {len(portfolio)} posiciones; los totales se muestran cuando están todas disponibles.</p>'
        )
    else:
        portfolio_total = '<p class="portfolio-total">No hay datos de cartera disponibles.</p>'
    warnings_count = sum(len(report.warnings) for report in reports)
    stale_report_count = sum(not quote_is_current(report) for report in reports)
    stale_divider_html = (
        '<div id="stale-market-divider" class="stock-stale-divider" hidden>'
        "Cotizaciones pendientes de actualización: mercados aún no abiertos o sin datos de hoy"
        "</div>"
        if stale_report_count
        else ""
    )
    chart_payload_data = asdict(chart_data)
    for group in ("watchlist", "portfolio"):
        for item in chart_payload_data[group]:
            market_date = (
                date.fromisoformat(item["market_date"])
                if item.get("market_date")
                else None
            )
            current_day = _market_data_is_current(
                market_date,
                item.get("market_timezone"),
                generated_at,
            ) and item.get("change_pct") is not None
            item["current_day"] = current_day
            if not current_day:
                item["change_pct"] = None
    chart_payload = json.dumps(
        chart_payload_data,
        ensure_ascii=False,
        allow_nan=False,
    ).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Informe diario de noticias y sentimiento para la lista de seguimiento de acciones.">
  <title>Radar bursátil · Informe diario</title>
  <script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"></script>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0a0e17; --panel: #111827; --panel-2: #172033; --line: #253149;
      --text: #edf2fb; --muted: #95a3b8; --blue: #8cb8ff;
      --green: #4ade80; --yellow: #facc15; --red: #fb7185; --orange: #fb923c;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: radial-gradient(ellipse at top, #15223a 0, var(--bg) 52rem);
      color: var(--text); font: 15px/1.55 Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }}
    a {{ color: var(--blue); text-decoration: none; }} a:hover {{ text-decoration: underline; }}
    .shell {{ max-width: 1440px; margin: 0 auto; padding: 36px 26px 60px; }}
    header {{ display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; margin-bottom: 26px; }}
    .eyebrow, .ticker {{ color: var(--blue); font-size: 11px; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; }}
    h1 {{ font-size: clamp(28px, 4vw, 42px); letter-spacing: -.04em; line-height: 1.1; margin: 7px 0; }}
    .subtitle, .muted {{ color: var(--muted); }}
    .updated {{ color: var(--muted); font-size: 13px; text-align: right; }}
    .summary {{ display: grid; grid-template-columns: 1.2fr 1fr 1fr; gap: 14px; margin-bottom: 30px; }}
    .summary-card {{ background: linear-gradient(140deg, #172238, #111827); border: 1px solid var(--line);
      border-radius: 16px; padding: 20px; min-height: 130px; }}
    .summary-label {{ color: var(--muted); font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; }}
    .summary-value {{ display: flex; align-items: center; gap: 10px; font-size: 24px; font-weight: 750; margin: 9px 0 3px; }}
    .summary-detail {{ color: var(--muted); font-size: 13px; }}
    .dot, .badge i {{ width: 9px; height: 9px; display: inline-block; border-radius: 50%; background: currentColor; }}
    .bullish {{ color: var(--green); }} .bearish {{ color: var(--red); }} .neutral {{ color: var(--yellow); }}
    .disclaimer {{ border: 1px solid var(--line); border-radius: 12px; background: #0e1522; color: var(--muted);
      padding: 13px 16px; margin: 0 0 25px; font-size: 13px; }}
    .section-heading {{ display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline; gap: 8px 16px; margin: 32px 0 13px; }}
    .section-heading h2 {{ margin: 0; font-size: 20px; }}
    .section-heading span {{ color: var(--muted); font-size: 13px; }}
    .sort-select {{ appearance: none; -webkit-appearance: none; max-width: 100%; cursor: pointer; font: inherit; font-size: 13px;
      color: var(--text); border: 1px solid var(--line); border-radius: 10px; padding: 8px 34px 8px 12px;
      background: var(--panel-2) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' fill='none' stroke='%2395a3b8' stroke-width='2' stroke-linecap='round'%3E%3Cpath d='M1 1.5l5 5 5-5'/%3E%3C/svg%3E") no-repeat right 12px center; }}
    .sort-select:hover {{ border-color: #3a4a6b; }}
    .sort-select:focus-visible {{ outline: 2px solid var(--blue); outline-offset: 2px; }}
    .sort-select option {{ background: var(--panel); color: var(--text); }}
    .key-headlines {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px; padding: 0; list-style: none; }}
    .key-headlines li {{ background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 14px; }}
    .key-headlines span {{ display: block; color: var(--muted); margin-top: 7px; font-size: 12px; }}
    .portfolio-panel {{ border: 1px solid var(--line); background: rgba(17, 24, 39, .94); border-radius: 14px; padding: 15px; }}
    .portfolio-controls {{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }}
    .portfolio-controls label {{ color: var(--muted); font-size: 12px; }}
    .portfolio-controls .sort-select {{ min-width: 125px; }}
    .portfolio-total {{ color: var(--muted); font-size: 13px; margin: 0 0 12px; }}
    .portfolio-table-wrap {{ overflow-x: auto; }}
    .portfolio-table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
    .portfolio-table th, .portfolio-table td {{ border-bottom: 1px solid var(--line); padding: 9px 10px; text-align: right; }}
    .portfolio-table thead th {{ color: var(--muted); font-size: 10px; line-height: 1.35; position: sticky; top: 0; background: var(--panel); }}
    .portfolio-table th:first-child, .portfolio-table td:first-child {{ text-align: left; }}
    .portfolio-table tbody th {{ font-weight: 650; }}
    .portfolio-table [hidden] {{ display: none; }}
    .portfolio-table tbody th span {{ display: block; color: var(--muted); font-size: 10px; font-weight: 500; }}
    .portfolio-note {{ color: var(--muted); font-size: 11px; margin: 11px 0 0; }}
    .market-charts {{ margin: 0 0 28px; }}
    .market-visualization-grid {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(250px, 340px); gap: 12px; }}
    .market-chart-card {{ border: 1px solid var(--line); background: rgba(17, 24, 39, .94); border-radius: 14px; padding: 14px; min-width: 0; }}
    .market-chart-card h3 {{ margin: 0 0 4px; font-size: 14px; }}
    .chart-subtitle, .chart-status {{ color: var(--muted); font-size: 11px; margin: 0 0 9px; }}
    .chart-status {{ font-variant-numeric: tabular-nums; }}
    .heatmap-chart {{ display: block; width: 100%; min-height: 240px; overflow: visible; }}
    .average-chart {{ display: block; width: 100%; height: auto; overflow: visible; }}
    .heatmap-name, .heatmap-return {{ fill: #000; stroke: #fff; stroke-width: 2px; paint-order: stroke;
      stroke-linejoin: round; pointer-events: none; }}
    .heatmap-name {{ font-size: 10px; font-weight: 750; }}
    .heatmap-return {{ font-size: 10px; font-variant-numeric: tabular-nums; }}
    .chart-empty {{ fill: var(--muted); font-size: 12px; }}
    .chart-time {{ fill: var(--muted); font-size: 10px; font-variant-numeric: tabular-nums; }}
    .portfolio-visualizations {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(230px, 300px); gap: 12px; margin: 0 0 14px; }}
    .analysis-panel {{ border: 1px solid var(--line); background: rgba(17, 24, 39, .94); border-radius: 14px; padding: 16px; }}
    .analysis-warning {{ border: 1px solid #fb923c66; border-radius: 10px; background: #fb923c12; color: #fdba74; padding: 12px; }}
    .analysis-grid, .analysis-recommendations {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr)); gap: 12px; }}
    .analysis-grid article, .analysis-recommendations > div, .analysis-risks {{ border: 1px solid var(--line); border-radius: 10px; background: var(--panel-2); padding: 13px; }}
    .analysis-panel h3 {{ margin: 0 0 7px; font-size: 14px; }}
    .analysis-panel p, .analysis-panel li {{ color: var(--muted); font-size: 13px; }}
    .analysis-panel p {{ margin: 0; }}
    .analysis-recommendations, .analysis-risks {{ margin-top: 12px; }}
    .analysis-recommendations ol, .analysis-recommendations ul, .analysis-risks ul {{ margin: 0; padding-left: 20px; }}
    .analysis-recommendations li + li, .analysis-risks li + li {{ margin-top: 9px; }}
    .analysis-recommendations strong {{ color: var(--text); }}
    .analysis-recommendations li p {{ margin-top: 3px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 350px), 1fr)); gap: 15px; }}
    .stock-stale-divider {{ grid-column: 1 / -1; border-top: 1px dashed var(--muted); color: var(--muted);
      font-size: 12px; padding-top: 10px; }}
    .stock-stale-divider[hidden] {{ display: none; }}
    .quote-stale-note {{ border-left: 2px solid var(--yellow); color: var(--muted); font-size: 11px;
      margin: 10px 0 0; padding-left: 8px; }}
    .stock-card {{ border: 1px solid var(--line); border-radius: 16px; background: rgba(17, 24, 39, .94); padding: 18px; min-width: 0; }}
    .card-top {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }}
    .ticker {{ margin: 0 0 3px; }} .stock-card h2 {{ font-size: 17px; line-height: 1.3; margin: 0; }}
    .badge {{ flex: 0 0 auto; display: inline-flex; align-items: center; gap: 7px; border-radius: 99px;
      background: #ffffff0c; padding: 5px 10px; font-size: 12px; font-weight: 750; }}
    .badge i {{ width: 7px; height: 7px; }}
    .sentiment-note {{ color: var(--muted); font-size: 12px; margin: 12px 0; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 125px), 1fr)); gap: 8px; }}
    .metrics div {{ border: 1px solid var(--line); background: var(--panel-2); border-radius: 10px; padding: 9px; min-width: 0; }}
    .metrics span {{ color: var(--muted); display: block; font-size: 10px; line-height: 1.3; min-height: 26px; }}
    .metrics strong {{ display: block; font-size: 12px; margin-top: 4px; overflow-wrap: anywhere; }}
    .stock-card h3 {{ font-size: 13px; margin: 17px 0 7px; }}
    .headlines {{ margin: 0; padding-left: 17px; }} .headline {{ padding: 0 0 9px 1px; }}
    .headline a {{ font-size: 13px; }} .headline-meta {{ display: block; color: var(--muted); font-size: 11px; margin-top: 2px; }}
    .headline-original {{ display: block; color: var(--muted); font-size: 11px; margin-top: 2px; }}
    .text-bullish {{ color: var(--green); }} .text-bearish {{ color: var(--red); }} .text-neutral {{ color: var(--yellow); }} .text-orange {{ color: var(--orange); }}
    .empty {{ color: var(--muted); font-size: 13px; list-style: none; margin-left: -17px; }}
    .warnings {{ border-top: 1px solid var(--line); color: #fbbf24; font-size: 11px; margin: 10px 0 0; padding: 9px 0 0 16px; }}
    .private-note {{ color: #fbbf24; font-size: 11px; margin: 10px 0 0; }}
    footer {{ border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; margin-top: 34px; padding-top: 16px; }}
    @media (max-width: 720px) {{ .shell {{ padding: 25px 15px 40px; }} header {{ display: block; }}
      .updated {{ text-align: left; margin-top: 12px; }} .summary {{ grid-template-columns: 1fr; gap: 9px; }}
      .summary-card {{ min-height: 0; }} .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }}
      .metrics div {{ padding: 8px; }} .sort-select {{ width: 100%; }}
      .portfolio-controls .sort-select {{ width: auto; }}
      .market-visualization-grid, .portfolio-visualizations {{ grid-template-columns: minmax(0, 1fr); }}
      .market-chart-card {{ padding: 11px; }}
      .portfolio-table-wrap {{ overflow: visible; }}
      .portfolio-table, .portfolio-table tbody {{ display: block; width: 100%; }}
      .portfolio-table thead {{ position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }}
      .portfolio-table tbody tr {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 8px; border: 1px solid var(--line); border-radius: 11px; background: var(--panel-2); margin: 0 0 10px; padding: 8px; }}
      .portfolio-table tbody th, .portfolio-table tbody td {{ display: flex; justify-content: space-between; align-items: baseline; gap: 8px; border: 0; padding: 7px 3px; text-align: right; font-size: 11px; min-width: 0; overflow-wrap: anywhere; }}
      .portfolio-table tbody th[data-label="Activo"] {{ display: block; grid-column: 1 / -1; border-bottom: 1px solid var(--line); text-align: left; font-size: 13px; }}
      .portfolio-table tbody td::before {{ content: attr(data-label); color: var(--muted); text-align: left; font-size: 10px; }}
      .portfolio-table tbody th span {{ font-size: 9px; }} }}
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div><div class="eyebrow">Mercados · seguimiento diario</div>
        <h1>Radar bursátil</h1>
        <div class="subtitle">Noticias y sentimiento para {len(reports)} empresas</div></div>
      <div class="updated">Actualizado<br><strong>{_esc(generated_at.strftime("%d/%m/%Y · %H:%M %Z"))}</strong></div>
    </header>
    <section class="market-charts" aria-label="Visualizaciones del mercado">
      <div class="section-heading"><h2>Mercado de un vistazo</h2>
        <span>{len(reports)} instrumentos seguidos</span></div>
      <div class="market-visualization-grid">
        <article class="market-chart-card chart-resize-target">
          <h3>Mapa de calor · Lista de seguimiento</h3>
          <p class="chart-subtitle">Rendimiento de hoy · verde: subida · rojo: bajada · datos antiguos o ausentes: fondo oculto</p>
          <svg id="watchlist-heatmap" class="heatmap-chart" role="img" aria-label="Mapa de calor de rendimientos diarios de la lista de seguimiento"></svg>
        </article>
        <article class="market-chart-card chart-resize-target">
          <h3>Media intradía · Lista de seguimiento</h3>
          <p id="watchlist-average-chart-status" class="chart-status" aria-live="polite">Calculando media…</p>
          <svg id="watchlist-average-chart" class="average-chart" role="img" aria-label="Rendimiento medio de la lista de seguimiento a lo largo del día"></svg>
        </article>
      </div>
    </section>
    <section class="summary" aria-label="Resumen ejecutivo">
      <article class="summary-card"><div class="summary-label">Sentimiento de noticias</div>
        <div class="summary-value"><span class="dot {signal}"></span>{label}</div>
        <div class="summary-detail">Puntuación media VADER {score_text} · {summary["companies_with_news"]} empresas con titulares</div></article>
      <article class="summary-card"><div class="summary-label">Consenso de analistas</div>
        <div class="summary-value">{_esc(summary["analyst_total"])} empresas</div>
        <div class="summary-detail">{_esc(summary["consensus_summary"])}<br>{analyst_breakdown}</div></article>
      <article class="summary-card"><div class="summary-label">Cobertura de hoy</div>
        <div class="summary-value">{summary["headline_count"]} titulares</div>
        <div class="summary-detail">{len(reports)} empresas seguidas · {warnings_count} avisos de datos</div></article>
    </section>
    <p class="disclaimer"><strong>Metodología:</strong> la señal de sentimiento se calcula sobre los titulares del día con VADER;
      no es una recomendación de inversión. El PEG, consenso y objetivos provienen de Yahoo Finance cuando están disponibles;
      el PEG es una referencia informativa, no una recomendación de inversión.
      La cartera mostrada se basa en posiciones configuradas manualmente; no se consultan las tenencias internas de fondos.
      Los feeds y datos pueden faltar o retrasarse.</p>
    <section aria-labelledby="key-title">
      <div class="section-heading"><h2 id="key-title">Titulares clave</h2><span>Los de mayor polaridad detectados hoy</span></div>
      <ul class="key-headlines">{key_headlines_html}</ul>
    </section>
    <section aria-labelledby="portfolio-title">
      <div class="section-heading"><h2 id="portfolio-title">Mi cartera</h2>
        <div class="portfolio-controls">
          <label for="portfolio-period">Periodo</label>
          <select id="portfolio-period" class="sort-select">
            <option value="1D" selected>1D</option><option value="1W">1W</option>
            <option value="1M">1M</option><option value="6M">6M</option>
            <option value="1Y">1Y</option><option value="MAX">MAX</option>
          </select>
          <label for="portfolio-metric">Mostrar</label>
          <select id="portfolio-metric" class="sort-select">
            <option value="return" selected>Rendimiento (%)</option>
            <option value="close">Precio de cierre</option>
          </select>
        </div></div>
      <div class="portfolio-panel">
        {portfolio_total}
        <div class="portfolio-visualizations">
          <article class="market-chart-card chart-resize-target">
            <h3>Mapa de calor · Mi cartera</h3>
            <p class="chart-subtitle">Tamaño proporcional al peso actual de cada posición · rendimiento de hoy</p>
            <svg id="portfolio-heatmap" class="heatmap-chart" role="img" aria-label="Mapa de calor de rendimientos diarios de la cartera"></svg>
          </article>
          <article class="market-chart-card chart-resize-target">
            <h3>Media intradía · Mi cartera</h3>
            <p id="portfolio-average-chart-status" class="chart-status" aria-live="polite">Calculando media…</p>
            <svg id="portfolio-average-chart" class="average-chart" role="img" aria-label="Evolución intradía del rendimiento medio de la cartera"></svg>
          </article>
        </div>
        <div class="portfolio-table-wrap">
          <table class="portfolio-table">
            <thead><tr>
              <th scope="col">Activo</th><th scope="col">Posición</th>
              <th scope="col" data-portfolio-max-only="true" hidden>Inversión base</th>
              <th scope="col">Precio actual</th>
              <th scope="col" data-portfolio-max-only="true" hidden>Valor actual</th>
              <th scope="col" id="portfolio-selected-heading">Rendimiento 1D</th>
            </tr></thead>
            <tbody>{portfolio_rows or '<tr><td colspan="6">No hay posiciones definidas.</td></tr>'}</tbody>
          </table>
        </div>
        <p class="portfolio-note">Selecciona periodo y métrica para ver cierres históricos, rendimiento porcentual o ganancia MAX. Cierres históricos de Yahoo Finance; los tickers estadounidenses se convierten a EUR con el cambio USD/EUR de cada fecha.</p>
        {portfolio_warning_html}
      </div>
    </section>
    {_analysis_section(analysis)}
    <section aria-labelledby="stocks-title">
      <div class="section-heading"><h2 id="stocks-title">Lista de seguimiento</h2>
        <select id="sort-select" class="sort-select" aria-label="Ordenar la lista de seguimiento" aria-controls="stock-grid">
          <option value="daily" selected>Rendimiento diario</option>
          <option value="peg">Valoración PEG (de bueno a malo)</option>
          <option value="consensus">Consenso de analistas</option>
          <option value="sentiment">Sentimiento de noticias</option>
        </select></div>
      <div class="grid" id="stock-grid">{stale_divider_html}{"".join(cards)}</div>
    </section>
    <footer>Fuentes: Google News RSS, Google Translate, Yahoo Finance (vía yfinance) y Gemini API. Datos informativos, no asesoramiento financiero.</footer>
  </main>
<script type="application/json" id="dashboard-chart-data">{chart_payload}</script>
{SORT_SCRIPT}
{PORTFOLIO_SCRIPT}
{CHARTS_SCRIPT}
</body>
</html>
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Genera un informe diario de sentimiento bursátil.")
    parser.add_argument(
        "--output",
        default="dashboard.html",
        help="Ruta del dashboard HTML resultante (por defecto: dashboard.html).",
    )
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args()
    reports = collect_reports()
    portfolio = fetch_portfolio()
    analysis = analyze_portfolio(reports, portfolio)
    chart_data = collect_chart_data(reports, portfolio)
    report_html = render_dashboard(
        reports,
        portfolio=portfolio,
        analysis=analysis,
        chart_data=chart_data,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        output_file.write(report_html)
    summary = executive_summary(reports)
    logging.info(
        "Dashboard generado en %s: %s empresas, %s titulares, sentimiento %s.",
        args.output,
        len(reports),
        summary["headline_count"],
        summary["sentiment_label"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
