from __future__ import annotations

import argparse
import json
from pathlib import Path

from daily_report import COMPANIES


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "cloudflare" / "live-quotes" / "src" / "instruments.mjs"


def render_instruments_module() -> str:
    instruments = {
        company.ticker: company.ls_id
        for company in COMPANIES
        if company.ticker and company.ls_id is not None
    }
    return (
        "export default Object.freeze("
        + json.dumps(instruments, ensure_ascii=False, indent=2)
        + ");\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Genera la lista permitida de instrumentos L&S para el Worker."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="comprueba que el módulo generado coincide con daily_report.py",
    )
    args = parser.parse_args()
    expected = render_instruments_module()

    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != expected:
            parser.error(
                "instruments.mjs no coincide con COMPANIES; "
                "ejecuta scripts/generate_live_quotes_instruments.py"
            )
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(expected, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
