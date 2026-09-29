from __future__ import annotations

import argparse
import html
import logging
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean
from typing import Any
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

import feedparser
import requests
import yfinance as yf
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer


TIMEZONE = ZoneInfo(os.getenv("REPORT_TIMEZONE", "Europe/Madrid"))
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
MAX_HEADLINES = 4
MAX_WORKERS = 8

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
    current_price_eur: float | None = None
    daily_change_pct: float | None = None
    daily_change_abs_eur: float | None = None
    analyst_consensus: str | None = None
    analyst_count: int | None = None
    target_mean_eur: float | None = None
    warning: str | None = None


@dataclass
class Headline:
    title: str
    url: str
    source: str
    published: datetime
    sentiment: float


@dataclass
class StockReport:
    company: Company
    headlines: list[Headline] = field(default_factory=list)
    quote: Quote = field(default_factory=Quote)
    news_sentiment: float | None = None
    warnings: list[str] = field(default_factory=list)


# LISTA ACTUALIZADA CON SPCX
COMPANIES = (
    Company("Tempus AI", "TEM"),
    Company("AMD", "AMD"),
    Company("Dell Technologies", "DELL"),
    Company("ASML", "ASML"),
    Company("Meta Platforms (A)", "META"),
    Company("Bloom Energy", "BE"),
    Company("GE Vernova", "GEV"),
    Company("Vistra", "VST"),
    Company("Neo Performance Materials", "NEO.TO"),
    Company("NVIDIA", "NVDA"),
    Company("Micron Technology", "MU"),
    Company("Caterpillar", "CAT"),
    Company("Apple", "AAPL"),
    Company("Intel", "INTC"),
    Company("Palantir Technologies", "PLTR"),
    Company("Tesla", "TSLA"),
    Company("Alphabet (A)", "GOOGL"),
    Company("Uber", "UBER"),
    Company("TSMC (ADR)", "TSM"),
    Company("Microsoft", "MSFT"),
    Company("Broadcom", "AVGO"),
    Company("Modine Manufacturing", "MOD"),
    Company("Moderna", "MRNA"),
    Company("Amazon.com", "AMZN"),
    Company("Walt Disney", "DIS"),
    Company("Netflix", "NFLX"),
    Company("Oracle", "ORCL"),
    Company("MP Materials", "MP"),
    Company("SoFi Technologies", "SOFI"),
    Company("SpaceX", "SPCX"), 
    Company("MercadoLibre", "MELI"),
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
    except (
        yf.exceptions.YFException,
        requests.RequestException,
        TimeoutError,
        OSError,
    ) as error:
        message = f"Yahoo Finance: {type(error).__name__}: {error}"
        logging.warning("%s (%s) - %s", company.name, company.ticker, message)
        return Quote(warning=message)

    currency = info.get("currency", "USD")
    fx_rate = get_fx_rate(currency)

    current_price = info.get("currentPrice") or info.get("regularMarketPrice")
    daily_change_abs = info.get("regularMarketChange")
    daily_change_pct = info.get("regularMarketChangePercent")
    target_mean = info.get("targetMeanPrice")
    recommendation = info.get("recommendationKey")
    analyst_count = info.get("numberOfAnalystOpinions")
    
    return Quote(
        current_price_eur=_finite_float(current_price, multiplier=fx_rate),
        daily_change_pct=_finite_float(daily_change_pct, multiplier=100),
        daily_change_abs_eur=_finite_float(daily_change_abs, multiplier=fx_rate),
        analyst_consensus=ANALYST_LABELS.get(str(recommendation).lower()),
        analyst_count=_positive_int(analyst_count),
        target_mean_eur=_finite_float(target_mean, multiplier=fx_rate),
        warning=(
            None
            if recommendation or daily_change_pct is not None or current_price is not None
            else "Yahoo Finance no devolvió cotización ni consenso"
        ),
    )


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
    return reports


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
    return (
        '<li class="headline">'
        f'<a href="{_esc(headline.url)}" target="_blank" rel="noopener noreferrer">'
        f"{_esc(headline.title)}</a>"
        f'<span class="headline-meta">{_esc(headline.source)} · '
        f'{_esc(headline.published.strftime("%H:%M"))} · '
        f'<span class="text-{signal}">{label}</span></span></li>'
    )


def render_dashboard(
    reports: list[StockReport],
    generated_at: datetime | None = None,
) -> str:
    generated_at = generated_at or datetime.now(TIMEZONE)
    
    # ORDENACIÓN POR RENDIMIENTO DIARIO (Extraído del mercado)
    def performance_sort_key(report: StockReport) -> float:
        if report.quote.daily_change_pct is None:
            return -math.inf
        return report.quote.daily_change_pct
        
    reports = sorted(reports, key=performance_sort_key, reverse=True)

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
        
        cards.append(
            f'<article class="stock-card" data-sentiment="{sentiment_signal}">'
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
            "</div>"
            f"{private_note}<h3>Titulares clave</h3><ul class=\"headlines\">{headline_list}</ul>"
            f"{warnings_html}</article>"
        )

    analyst_breakdown = " · ".join(
        f"{_esc(name)}: {count}" for name, count in summary["analyst_counts"].items()
    ) or "Sin datos"
    key_headlines_html = "".join(
        f'<li><a href="{_esc(item.url)}" target="_blank" rel="noopener noreferrer">'
        f"{_esc(item.title)}</a><span>{_esc(item.source)}</span></li>"
        for item in summary["key_headlines"]
    ) or "<li>Aún no hay titulares con fecha de hoy.</li>"
    warnings_count = sum(len(report.warnings) for report in reports)

    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Informe diario de noticias y sentimiento para la lista de seguimiento de acciones.">
  <title>Radar bursátil · Informe diario</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0a0e17; --panel: #111827; --panel-2: #172033; --line: #253149;
      --text: #edf2fb; --muted: #95a3b8; --blue: #8cb8ff;
      --green: #4ade80; --yellow: #facc15; --red: #fb7185;
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
    .section-heading {{ display: flex; justify-content: space-between; align-items: baseline; margin: 32px 0 13px; }}
    .section-heading h2 {{ margin: 0; font-size: 20px; }}
    .section-heading span {{ color: var(--muted); font-size: 13px; }}
    .key-headlines {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px; padding: 0; list-style: none; }}
    .key-headlines li {{ background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 14px; }}
    .key-headlines span {{ display: block; color: var(--muted); margin-top: 7px; font-size: 12px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 350px), 1fr)); gap: 15px; }}
    .stock-card {{ border: 1px solid var(--line); border-radius: 16px; background: rgba(17, 24, 39, .94); padding: 18px; min-width: 0; }}
    .card-top {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }}
    .ticker {{ margin: 0 0 3px; }} .stock-card h2 {{ font-size: 17px; line-height: 1.3; margin: 0; }}
    .badge {{ flex: 0 0 auto; display: inline-flex; align-items: center; gap: 7px; border-radius: 99px;
      background: #ffffff0c; padding: 5px 10px; font-size: 12px; font-weight: 750; }}
    .badge i {{ width: 7px; height: 7px; }}
    .sentiment-note {{ color: var(--muted); font-size: 12px; margin: 12px 0; }}
    .metrics {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }}
    .metrics div {{ border: 1px solid var(--line); background: var(--panel-2); border-radius: 10px; padding: 9px; min-width: 0; }}
    .metrics span {{ color: var(--muted); display: block; font-size: 10px; line-height: 1.3; min-height: 26px; }}
    .metrics strong {{ display: block; font-size: 12px; margin-top: 4px; overflow-wrap: anywhere; }}
    .stock-card h3 {{ font-size: 13px; margin: 17px 0 7px; }}
    .headlines {{ margin: 0; padding-left: 17px; }} .headline {{ padding: 0 0 9px 1px; }}
    .headline a {{ font-size: 13px; }} .headline-meta {{ display: block; color: var(--muted); font-size: 11px; margin-top: 2px; }}
    .text-bullish {{ color: var(--green); }} .text-bearish {{ color: var(--red); }} .text-neutral {{ color: var(--yellow); }}
    .empty {{ color: var(--muted); font-size: 13px; list-style: none; margin-left: -17px; }}
    .warnings {{ border-top: 1px solid var(--line); color: #fbbf24; font-size: 11px; margin: 10px 0 0; padding: 9px 0 0 16px; }}
    .private-note {{ color: #fbbf24; font-size: 11px; margin: 10px 0 0; }}
    footer {{ border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; margin-top: 34px; padding-top: 16px; }}
    @media (max-width: 720px) {{ .shell {{ padding: 25px 15px 40px; }} header {{ display: block; }}
      .updated {{ text-align: left; margin-top: 12px; }} .summary {{ grid-template-columns: 1fr; gap: 9px; }}
      .summary-card {{ min-height: 0; }} }}
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
      no es una recomendación de inversión. El consenso y los objetivos provienen de Yahoo Finance cuando están disponibles.
      No se dispone de datos de carteras/tenencias; no se deben interpretar las recomendaciones como posiciones de fondos.
      Los feeds y datos pueden faltar o retrasarse.</p>
    <section aria-labelledby="key-title">
      <div class="section-heading"><h2 id="key-title">Titulares clave</h2><span>Los de mayor polaridad detectados hoy</span></div>
      <ul class="key-headlines">{key_headlines_html}</ul>
    </section>
    <section aria-labelledby="stocks-title">
      <div class="section-heading"><h2 id="stocks-title">Lista de seguimiento</h2><span>Ordenada por rendimiento de hoy</span></div>
      <div class="grid">{"".join(cards)}</div>
    </section>
    <footer>Fuentes: Google News RSS y Yahoo Finance (vía yfinance). Datos informativos, no asesoramiento financiero.</footer>
  </main>
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
    report_html = render_dashboard(reports)
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