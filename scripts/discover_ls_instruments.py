from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daily_report import COMPANIES, Company


SEARCH_URL = "https://www.ls-tc.de/_rpc/json/.lstc/instrument/search/main"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
SESSION_LOCAL = threading.local()


def _session() -> requests.Session:
    session = getattr(SESSION_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.ls-tc.de/",
            }
        )
        SESSION_LOCAL.session = session
    return session


def _search_company(company: Company) -> dict[str, Any]:
    query = company.isin or company.name
    try:
        response = _session().get(
            SEARCH_URL,
            params={"q": query, "localeId": 2},
            timeout=(4, 10),
        )
        response.raise_for_status()
        results = response.json()
        if not isinstance(results, list):
            raise ValueError("el buscador devolvió un formato inesperado")
    except (requests.RequestException, ValueError) as error:
        return {
            "name": company.name,
            "ticker": company.ticker,
            "query": query,
            "error": f"{type(error).__name__}: {error}",
        }

    candidates = [
        {
            "ls_id": result.get("instrumentId"),
            "displayname": result.get("displayname"),
            "isin": result.get("isin"),
            "category": result.get("categoryName"),
        }
        for result in results
        if isinstance(result, dict)
    ]
    if company.isin:
        candidates = [
            item for item in candidates if item.get("isin") == company.isin
        ]
    return {
        "name": company.name,
        "ticker": company.ticker,
        "query": query,
        "configured_ls_id": company.ls_id,
        "candidates": candidates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Busca instrumentos de la lista en el endpoint público de L&S."
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=3,
        choices=range(1, 5),
        metavar="{1,2,3,4}",
        help="concurrencia máxima (por defecto: 3)",
    )
    args = parser.parse_args()
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        for result in executor.map(_search_company, COMPANIES):
            print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
