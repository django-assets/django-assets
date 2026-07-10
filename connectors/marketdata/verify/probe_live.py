"""One-time live probe: record what THIS token actually serves.

Captures raw responses (status, rate-limit headers, body) for the core
endpoints into tests/fixtures/live/. Metered — run sparingly. The token
comes from .env / the environment and is never written to the fixtures.

Usage: uv run python connectors/marketdata/verify/probe_live.py
"""

import datetime
import json
import os
import pathlib
import sys
from decimal import Decimal

import httpx

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "live"
BASE = "https://api.marketdata.app"


def load_token() -> str:
    if "MARKETDATA_TOKEN" not in os.environ:
        env_path = pathlib.Path(__file__).resolve().parents[3] / ".env"
        for line in env_path.read_text().splitlines():
            if line.startswith("MARKETDATA_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"')
        sys.exit("MARKETDATA_TOKEN not found")
    return os.environ["MARKETDATA_TOKEN"]


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return str(o)
        return super().default(o)


def main() -> None:
    token = load_token()
    FIXTURES.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {token}"}, timeout=30.0)

    calls = [
        ("user", "GET", "/user/", {}),
        ("stocks_quotes_spy", "GET", "/v1/stocks/quotes/SPY/", {}),
        ("stocks_quotes_bulk", "GET", "/v1/stocks/quotes/", {"symbols": "SPY,AAPL"}),
        ("stocks_prices_spy", "GET", "/v1/stocks/prices/SPY/", {}),
        (
            "stocks_candles_daily",
            "GET",
            "/v1/stocks/candles/D/SPY/",
            {"from": "2026-06-01", "to": "2026-07-09"},
        ),
        (
            "stocks_candles_weekly_native",
            "GET",
            "/v1/stocks/candles/W/SPY/",
            {"from": "2026-06-01", "to": "2026-07-09"},
        ),
        (
            "stocks_candles_ancient_probe",
            "GET",
            "/v1/stocks/candles/D/SPY/",
            {"from": "1900-01-01", "to": "1900-12-31"},
        ),
        (
            "markets_status",
            "GET",
            "/v1/markets/status/",
            {"from": "2026-06-01", "to": "2026-07-10"},
        ),
        (
            "options_chain_probe",
            "GET",
            "/v1/options/chain/SPY/",
            {"strikeLimit": "1", "side": "call", "dte": "30"},
        ),
        ("stocks_quotes_unknown", "GET", "/v1/stocks/quotes/ZZZZZZZZ/", {}),
        ("stocks_candles_unknown", "GET", "/v1/stocks/candles/D/ZZZZZZZZ/", {}),
    ]

    results = {}
    option_symbol = None
    for name, method, path, params in calls:
        response = client.request(method, path, params=params)
        try:
            body = json.loads(response.text, parse_float=str)
        except json.JSONDecodeError:
            body = {"_raw_text": response.text[:2000]}
        record = {
            "method": method,
            "path": path,
            "params": params,
            "status": response.status_code,
            "ratelimit": {
                k: v for k, v in response.headers.items() if k.lower().startswith("x-api-ratelimit")
            },
            "body": body,
        }
        results[name] = record
        (FIXTURES / f"{name}.json").write_text(json.dumps(record, indent=2, cls=DecimalEncoder))
        print(f"{name}: HTTP {response.status_code}", flush=True)
        if name == "options_chain_probe" and response.status_code in (200, 203):
            symbols = body.get("optionSymbol") or []
            option_symbol = symbols[0] if symbols else None

    if option_symbol:
        extra = [
            ("options_quotes_single", "GET", f"/v1/options/quotes/{option_symbol}/", {}),
            (
                "options_quotes_series",
                "GET",
                f"/v1/options/quotes/{option_symbol}/",
                {"from": "2026-06-25", "to": "2026-07-09"},
            ),
            (
                "options_quotes_ancient",
                "GET",
                f"/v1/options/quotes/{option_symbol}/",
                {"from": "2020-01-01", "to": "2020-03-01"},
            ),
        ]
        for name, method, path, params in extra:
            response = client.request(method, path, params=params)
            try:
                body = json.loads(response.text, parse_float=str)
            except json.JSONDecodeError:
                body = {"_raw_text": response.text[:2000]}
            record = {
                "method": method,
                "path": path,
                "params": params,
                "status": response.status_code,
                "ratelimit": {
                    k: v
                    for k, v in response.headers.items()
                    if k.lower().startswith("x-api-ratelimit")
                },
                "body": body,
            }
            results[name] = record
            (FIXTURES / f"{name}.json").write_text(json.dumps(record, indent=2, cls=DecimalEncoder))
            print(f"{name}: HTTP {response.status_code}", flush=True)

    meta = {
        "recorded_at_utc": datetime.datetime.now(datetime.UTC).isoformat(),
        "option_symbol": option_symbol,
    }
    (FIXTURES / "_meta.json").write_text(json.dumps(meta, indent=2))
    print("meta:", meta)


if __name__ == "__main__":
    main()
