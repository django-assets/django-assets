"""A scripted MarketData.app for tests — response shapes copied from
recorded live traffic (tests/fixtures/live/). Serves an in-memory
dataset through httpx.MockTransport so the connector's full HTTP path
(status codes, headers, Decimal parsing) is exercised without quota.
"""

import datetime
import json
import urllib.parse
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx

EASTERN = ZoneInfo("America/New_York")

PERMISSIONS = "delayed_quotes_permission,historical_quotes_permission"


def epoch(dt: datetime.datetime) -> int:
    return int(dt.timestamp())


def session_midnight(session: datetime.date) -> int:
    return epoch(datetime.datetime.combine(session, datetime.time(), tzinfo=EASTERN))


class FakeVendor:
    """In-memory dataset + request handler.

    stocks: symbol -> list of dicts {session: date, o,h,l,c: str, v: int}
    stock_quotes: symbol -> {bid,ask,mid,last: str|None, updated: int}
    stock_prices: symbol -> {mid: str, updated: int}
    option_series: symbol -> list of per-session rows (mid/bid/ask/last/
        greeks as str, updated: int); the LIVE quote is `option_live`.
    holidays: closed weekdays.
    """

    def __init__(
        self,
        *,
        stocks=None,
        stock_quotes=None,
        stock_prices=None,
        option_series=None,
        option_live=None,
        holidays=(),
        calendar_start=datetime.date(2020, 1, 1),
        calendar_end=datetime.date(2027, 12, 31),
        permissions=PERMISSIONS,
        realtime_available=True,
    ):
        self.stocks = stocks or {}
        self.stock_quotes = stock_quotes or {}
        self.stock_prices = stock_prices or {}
        self.option_series = option_series or {}
        self.option_live = option_live or {}
        self.holidays = set(holidays)
        self.calendar_start = calendar_start
        self.calendar_end = calendar_end
        self.permissions = permissions
        self.realtime_available = realtime_available
        self.calls: list[str] = []

    # -- plumbing ---------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def _respond(self, status: int, body: dict) -> httpx.Response:
        def wire(value):
            # The real vendor sends JSON numbers; render Decimals as
            # numbers so the client's parse_float=Decimal path is the
            # one being exercised. (float exists only on the fake wire.)
            if isinstance(value, Decimal):
                return float(value)
            if isinstance(value, list):
                return [wire(v) for v in value]
            if isinstance(value, dict):
                return {k: wire(v) for k, v in value.items()}
            return value

        return httpx.Response(
            status,
            headers={
                "x-options-data-permissions": self.permissions,
                "x-api-ratelimit-consumed": "1" if status in (200, 203) else "0",
                "content-type": "application/json",
            },
            text=json.dumps(wire(body)),
        )

    def _no_data(self) -> httpx.Response:
        return self._respond(404, {"s": "no_data", "nextTime": None, "prevTime": None})

    def _bad(self) -> httpx.Response:
        return self._respond(
            400, {"s": "error", "errmsg": "Bad parameters, please check API documentation."}
        )

    def is_session(self, on: datetime.date) -> bool:
        return on.weekday() < 5 and on not in self.holidays

    # -- handler -----------------------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(urllib.parse.parse_qsl(request.url.query.decode()))
        self.calls.append(f"{path}?{request.url.query.decode()}")

        if path == "/user/":
            return self._respond(
                200,
                {
                    "x-ratelimit-requests-remaining": 99999,
                    "x-ratelimit-requests-limit": 100000,
                    "x-options-data-permissions": "OPRA data delayed 15 minutes",
                },
            )

        if path == "/v1/markets/status/":
            start = datetime.date.fromisoformat(params["from"])
            end = datetime.date.fromisoformat(params["to"])
            dates, statuses = [], []
            day = start
            while day <= end:
                dates.append(session_midnight(day))
                if self.calendar_start <= day <= self.calendar_end:
                    statuses.append("open" if self.is_session(day) else "closed")
                else:
                    statuses.append(None)
                day += datetime.timedelta(days=1)
            return self._respond(200, {"s": "ok", "date": dates, "status": statuses})

        if path.startswith("/v1/stocks/candles/"):
            parts = path.strip("/").split("/")
            resolution, symbol = parts[3], parts[4]
            if resolution != "D":
                return self._bad()
            candles = self.stocks.get(symbol)
            if candles is None:
                return self._bad()
            start = datetime.date.fromisoformat(params["from"])
            end = datetime.date.fromisoformat(params["to"])
            rows = [c for c in candles if start <= c["session"] <= end]
            if not rows:
                return self._no_data()
            return self._respond(
                203,
                {
                    "s": "ok",
                    "t": [session_midnight(c["session"]) for c in rows],
                    "o": [Decimal(c["o"]) for c in rows],
                    "h": [Decimal(c["h"]) for c in rows],
                    "l": [Decimal(c["l"]) for c in rows],
                    "c": [Decimal(c["c"]) for c in rows],
                    "v": [c["v"] for c in rows],
                },
            )

        if path.startswith("/v1/stocks/quotes"):
            if path == "/v1/stocks/quotes/":
                symbols = params.get("symbols", "").split(",")
            else:
                symbols = [path.strip("/").split("/")[-1]]
            known = [s for s in symbols if s in self.stock_quotes]
            if not known:
                return self._bad() if symbols else self._no_data()
            body = {
                "s": "ok",
                "symbol": [],
                "bid": [],
                "ask": [],
                "mid": [],
                "last": [],
                "updated": [],
            }
            for sym in known:
                q = self.stock_quotes[sym]
                body["symbol"].append(sym)
                for key in ("bid", "ask", "mid", "last"):
                    value = q.get(key)
                    body[key].append(Decimal(value) if value is not None else None)
                body["updated"].append(q["updated"])
            return self._respond(203, body)

        if path.startswith("/v1/stocks/prices"):
            if not self.realtime_available:
                return self._respond(402, {"s": "error", "errmsg": "upgrade required"})
            if path == "/v1/stocks/prices/":
                symbols = params.get("symbols", "").split(",")
            else:
                symbols = [path.strip("/").split("/")[-1]]
            known = [s for s in symbols if s in self.stock_prices]
            if not known:
                return self._bad()
            return self._respond(
                203,
                {
                    "s": "ok",
                    "symbol": known,
                    "mid": [Decimal(self.stock_prices[s]["mid"]) for s in known],
                    "updated": [self.stock_prices[s]["updated"] for s in known],
                },
            )

        if path.startswith("/v1/options/quotes/"):
            symbol = path.strip("/").split("/")[-1]
            series = self.option_series.get(symbol)
            live = self.option_live.get(symbol)
            if series is None and live is None:
                return self._bad()
            if "from" in params:  # EOD series; vendor's `to` is EXCLUSIVE
                start = datetime.date.fromisoformat(params["from"])
                end = datetime.date.fromisoformat(params["to"])
                rows = [r for r in (series or []) if start <= r["session"] < end]
                if not rows:
                    return self._no_data()
                return self._respond(203, self._option_body(rows))
            if live is None:
                return self._no_data()
            return self._respond(203, self._option_body([live]))

        if path == "/v1/options/chain/" or path.startswith("/v1/options/chain/"):
            return self._no_data()

        return self._respond(500, {"s": "error", "errmsg": f"unrouted {path}"})

    @staticmethod
    def _option_body(rows: list[dict]) -> dict:
        keys = (
            "bid",
            "ask",
            "mid",
            "last",
            "iv",
            "delta",
            "gamma",
            "theta",
            "vega",
            "underlyingPrice",
            "openInterest",
            "volume",
        )
        body: dict = {"s": "ok", "updated": [r["updated"] for r in rows]}
        for key in keys:
            body[key] = [
                (Decimal(r[key]) if not isinstance(r.get(key), int) else r[key])
                if r.get(key) is not None
                else None
                for r in rows
            ]
        return body


def make_daily_candles(start: datetime.date, end: datetime.date, holidays=()) -> list[dict]:
    """Deterministic OHLCV rows for every open session in [start, end]."""
    rows = []
    day = start
    index = 0
    holiday_set = set(holidays)
    while day <= end:
        if day.weekday() < 5 and day not in holiday_set:
            base = Decimal(100) + Decimal(index) * Decimal("0.25")
            rows.append(
                {
                    "session": day,
                    "o": str(base),
                    "h": str(base + Decimal("1.10")),
                    "l": str(base - Decimal("0.90")),
                    "c": str(base + Decimal("0.55")),
                    "v": 1_000_000 + index,
                }
            )
            index += 1
        day += datetime.timedelta(days=1)
    return rows
