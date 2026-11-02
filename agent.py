from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from daily_report import (
    ALERT_DROP_PCT,
    BUY_THE_DIP_SCORE_THRESHOLD,
    TIMEZONE,
    PORTFOLIO,
    PORTFOLIO_ACTIVITY_FILE,
    PortfolioPosition,
    StockReport,
    _http_status,
    _market_data_is_current,
    analyze_portfolio,
    buy_the_dip_candidates,
    collect_reports,
    collect_chart_data,
    enrich_reports_with_rsi14,
    fetch_portfolio,
    load_portfolio_activity,
    load_portfolio_transactions,
    portfolio_assets_from_transactions,
    portfolio_transactions_from_activity,
)


REALERT_STEP_PCT = 1.0
STATE_FILE = Path(os.getenv("AGENT_STATE_PATH", "agent_state.json"))


def _empty_state(local_date: str) -> dict[str, Any]:
    return {"date": local_date, "alerts": {}, "dip_candidates": {}}


def load_agent_state(path: Path, local_date: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty_state(local_date)
    except (OSError, json.JSONDecodeError) as error:
        logging.warning("No se pudo leer el estado del agente %s: %s", path, error)
        return _empty_state(local_date)
    if not isinstance(value, dict):
        logging.warning("El estado del agente no es un objeto JSON; se reinicia.")
        return _empty_state(local_date)
    if value.get("date") != local_date:
        return _empty_state(local_date)
    alerts = value.get("alerts")
    if not isinstance(alerts, dict):
        logging.warning("Las alertas guardadas del agente no son válidas; se reinician.")
        return _empty_state(local_date)
    normalized_alerts: dict[str, dict[str, Any]] = {}
    for ticker, item in alerts.items():
        if not isinstance(ticker, str) or not isinstance(item, dict):
            logging.warning(
                "Las alertas guardadas del agente no son válidas; se reinician."
            )
            return _empty_state(local_date)
        last_alerted_pct = item.get("last_alerted_pct")
        if last_alerted_pct is None:
            last_alerted_pct = item.get("last_drop_pct")
        if (
            not isinstance(last_alerted_pct, (int, float))
            or isinstance(last_alerted_pct, bool)
            or not math.isfinite(last_alerted_pct)
        ):
            logging.warning(
                "Las alertas guardadas del agente no son válidas; se reinician."
            )
            return _empty_state(local_date)
        normalized_alert = dict(item)
        normalized_alert.pop("last_drop_pct", None)
        normalized_alert["last_alerted_pct"] = float(last_alerted_pct)
        normalized_alerts[ticker] = normalized_alert
    dip_candidates = value.get("dip_candidates", {})
    if not isinstance(dip_candidates, dict):
        logging.warning(
            "Las alertas buy-the-dip guardadas no son válidas; se reinician."
        )
        dip_candidates = {}
    normalized_dip_candidates: dict[str, dict[str, Any]] = {}
    for ticker, item in dip_candidates.items():
        if (
            not isinstance(ticker, str)
            or not isinstance(item, dict)
            or not isinstance(item.get("last_score"), int)
            or isinstance(item.get("last_score"), bool)
        ):
            logging.warning(
                "Las alertas buy-the-dip guardadas no son válidas; se reinician."
            )
            normalized_dip_candidates = {}
            break
        normalized_dip_candidates[ticker] = {
            "last_score": item["last_score"],
            "sent_at": item.get("sent_at"),
        }
    return {
        "date": local_date,
        "alerts": normalized_alerts,
        "dip_candidates": normalized_dip_candidates,
    }


def find_dip_candidate_alerts(
    portfolio: list[PortfolioPosition],
    reports: list[StockReport],
    state: dict[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    held_tickers = {position.asset.ticker for position in portfolio}
    active_reports = [
        report
        for report in reports
        if report.company.ticker
        and report.company.ticker not in held_tickers
        and _market_data_is_current(
            report.quote.market_date,
            report.quote.market_timezone,
            now,
        )
    ]
    candidates = buy_the_dip_candidates(
        active_reports,
        portfolio,
        now,
        BUY_THE_DIP_SCORE_THRESHOLD,
    )
    candidates_by_ticker = {
        candidate["ticker"]: candidate for candidate in candidates
    }
    tracked = state.setdefault("dip_candidates", {})
    active_tickers = {
        report.company.ticker for report in active_reports if report.company.ticker
    }
    for ticker in list(tracked):
        if ticker in held_tickers or (
            ticker in active_tickers and ticker not in candidates_by_ticker
        ):
            tracked.pop(ticker, None)

    alerts = []
    for ticker, candidate in candidates_by_ticker.items():
        previous = tracked.get(ticker)
        previous_score = (
            previous.get("last_score") if isinstance(previous, dict) else None
        )
        if previous_score is None or candidate["score"] > previous_score:
            alerts.append(
                {
                    **candidate,
                    "category": "dip_candidate",
                }
            )
    return alerts


def _current_daily_change(
    position: PortfolioPosition,
    reports: list[StockReport],
    now: datetime,
) -> tuple[float, float | None] | None:
    report = next(
        (
            candidate
            for candidate in reports
            if candidate.company.ticker == position.asset.ticker
            or candidate.company.name.casefold() == position.asset.name.casefold()
        ),
        None,
    )
    if report is not None:
        quote = report.quote
        if not _market_data_is_current(
            quote.market_date,
            quote.market_timezone,
            now,
        ):
            return None
        daily_change = (
            quote.daily_change_pct
            if quote.daily_change_pct is not None
            else position.returns_pct.get("1D")
        )
        current_price = quote.current_price_eur or position.prices_eur.get("Hoy")
    else:
        if not _market_data_is_current(
            position.price_date,
            position.market_timezone,
            now,
        ):
            return None
        daily_change = position.returns_pct.get("1D")
        current_price = position.prices_eur.get("Hoy")
    if (
        not isinstance(daily_change, (int, float))
        or isinstance(daily_change, bool)
        or not math.isfinite(daily_change)
    ):
        return None
    return float(daily_change), current_price


def find_price_alerts(
    portfolio: list[PortfolioPosition],
    reports: list[StockReport],
    state: dict[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    state_alerts = state.get("alerts", {})
    alerts: list[dict[str, Any]] = []
    portfolio_tickers: set[str] = set()

    def should_alert(ticker: str, daily_change: float) -> bool:
        previous = state_alerts.get(ticker)
        if not isinstance(previous, dict):
            return True
        previous_value = previous.get("last_alerted_pct")
        if previous_value is None:
            previous_value = previous.get("last_drop_pct")
        if (
            not isinstance(previous_value, (int, float))
            or isinstance(previous_value, bool)
            or not math.isfinite(previous_value)
        ):
            return True
        if (daily_change >= 0) != (previous_value >= 0):
            return True
        return abs(daily_change) >= abs(previous_value) + REALERT_STEP_PCT

    for position in portfolio:
        ticker = position.asset.ticker
        if ticker:
            portfolio_tickers.add(ticker)
        shares = position.asset.shares
        if (
            not ticker
            or not isinstance(shares, (int, float))
            or isinstance(shares, bool)
            or not math.isfinite(shares)
            or shares <= 0
        ):
            continue
        current = _current_daily_change(position, reports, now)
        if current is None:
            continue
        daily_change, current_price = current
        if abs(daily_change) < ALERT_DROP_PCT:
            continue
        if not should_alert(ticker, daily_change):
            continue
        alerts.append(
            {
                "ticker": ticker,
                "name": position.asset.name,
                "daily_change_pct": daily_change,
                "current_price_eur": current_price,
                "category": (
                    "portfolio_rise" if daily_change > 0 else "portfolio_drop"
                ),
            }
        )
    for report in reports:
        ticker = report.company.ticker
        if not ticker or ticker in portfolio_tickers:
            continue
        quote = report.quote
        if not _market_data_is_current(
            quote.market_date,
            quote.market_timezone,
            now,
        ):
            continue
        daily_change = quote.daily_change_pct
        if (
            not isinstance(daily_change, (int, float))
            or isinstance(daily_change, bool)
            or not math.isfinite(daily_change)
            or daily_change < ALERT_DROP_PCT
            or not should_alert(ticker, float(daily_change))
        ):
            continue
        alerts.append(
            {
                "ticker": ticker,
                "name": report.company.name,
                "daily_change_pct": float(daily_change),
                "current_price_eur": quote.current_price_eur,
                "category": "watchlist_rise",
            }
        )
    return alerts


def send_price_alerts(alerts: list[dict[str, Any]]) -> bool:
    if not alerts:
        return False
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        logging.warning(
            "No se enviaron avisos de precio: faltan TELEGRAM_BOT_TOKEN o "
            "TELEGRAM_CHAT_ID."
        )
        return False

    headings = (
        ("portfolio_drop", "🔴 CAÍDAS EN CARTERA:"),
        ("portfolio_rise", "🟢 SUBIDAS EN CARTERA:"),
        ("watchlist_rise", "🚀 SUBIDAS EN SEGUIMIENTO:"),
        ("dip_candidate", "🎯 CANDIDATAS BUY THE DIP:"),
    )
    lines: list[str] = []
    for category, heading in headings:
        grouped = [alert for alert in alerts if alert.get("category") == category]
        if not grouped:
            continue
        if lines:
            lines.append("")
        lines.append(heading)
        for alert in grouped:
            if category == "dip_candidate":
                score = alert["score"]
                reasons = "; ".join(alert["reasons"]) or "señales técnicas"
                lines.append(
                    f"- {alert['name']} ({alert['ticker']}): "
                    f"score {score}/5 · {reasons}"
                )
                continue
            daily_change = alert["daily_change_pct"]
            price = alert["current_price_eur"]
            price_text = (
                f"{price:.2f} €"
                if isinstance(price, (int, float))
                and not isinstance(price, bool)
                and math.isfinite(price)
                else "sin precio"
            )
            lines.append(
                f"- {alert['name']} ({alert['ticker']}): "
                f"{daily_change:+.2f}% | {price_text}"
            )
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": "\n".join(lines)},
            timeout=(10, 20),
        )
        response.raise_for_status()
    except requests.RequestException as error:
        status = _http_status(error)
        logging.warning(
            "No se pudo enviar el aviso determinista de Telegram (%s%s).",
            type(error).__name__,
            f", HTTP {status}" if status is not None else "",
        )
        return False
    return True


find_drop_alerts = find_price_alerts
send_drop_alerts = send_price_alerts


def save_agent_state(path: Path, state: dict[str, Any]) -> None:
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(
            json.dumps(state, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
        os.replace(temporary_path, path)
    except (OSError, TypeError, ValueError) as error:
        logging.error("No se pudo guardar el estado del agente %s: %s", path, error)
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError as cleanup_error:
            logging.error(
                "No se pudo limpiar el estado temporal %s: %s",
                temporary_path,
                cleanup_error,
            )


def run_agent(
    *,
    now: datetime | None = None,
    state_path: Path | None = None,
) -> None:
    now = now or datetime.now(TIMEZONE)
    state_path = state_path or STATE_FILE
    local_date = now.astimezone(TIMEZONE).date().isoformat()
    reports = collect_reports(report_date=now.astimezone(TIMEZONE).date())
    activity_path = PORTFOLIO_ACTIVITY_FILE
    transaction_path = os.getenv("PORTFOLIO_TRANSACTIONS_CSV")
    if transaction_path and activity_path.is_file():
        raise ValueError(
            "Usa PORTFOLIO_TRANSACTIONS_CSV o portfolio_activity.json, no ambos."
        )
    if transaction_path:
        assets = portfolio_assets_from_transactions(
            load_portfolio_transactions(Path(transaction_path))
        )
    elif activity_path.is_file():
        activities = load_portfolio_activity(activity_path)
        assets = portfolio_assets_from_transactions(
            portfolio_transactions_from_activity(activities)
        )
    else:
        assets = PORTFOLIO
    portfolio = fetch_portfolio(assets)
    enrich_reports_with_rsi14(reports)
    state = load_agent_state(state_path, local_date)
    risk_data = collect_chart_data(
        reports,
        portfolio,
        risk_only=True,
    ).risk_metrics
    price_alerts = find_price_alerts(portfolio, reports, state, now)
    dip_candidates = buy_the_dip_candidates(
        reports,
        portfolio,
        now,
        BUY_THE_DIP_SCORE_THRESHOLD,
    )
    dip_alerts = find_dip_candidate_alerts(portfolio, reports, state, now)
    alerts = [*price_alerts, *dip_alerts]
    if alerts and send_price_alerts(alerts):
        for alert in alerts:
            if alert["category"] == "dip_candidate":
                state["dip_candidates"][alert["ticker"]] = {
                    "last_score": alert["score"],
                    "sent_at": now.astimezone(TIMEZONE).isoformat(),
                }
                continue
            state["alerts"][alert["ticker"]] = {
                "last_alerted_pct": alert["daily_change_pct"],
                "sent_at": now.astimezone(TIMEZONE).isoformat(),
                "name": alert["name"],
                "current_price_eur": alert["current_price_eur"],
                "category": alert["category"],
            }
    save_agent_state(state_path, state)
    analyze_portfolio(
        reports,
        portfolio,
        now_utc=now.astimezone(timezone.utc),
        send_telegram=True,
        deterministic_signals={
            "dip_candidates": dip_candidates,
            "portfolio_risk_metrics": risk_data,
        },
    )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    run_agent()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
