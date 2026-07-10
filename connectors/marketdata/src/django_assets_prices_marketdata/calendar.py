"""US trading calendar via the vendor's /v1/markets/status endpoint.

The source owns the trading-calendar math (ADR-0039 §1): callers never
compute "the last trading day". Sessions come from the vendor — open /
closed per calendar date — fetched in year-sized chunks (one credit
each) and cached for the client's lifetime. The one piece of local
knowledge is the 16:00 America/New_York closing bell, used to decide
whether *today's* session is complete; on early-close days this is
conservative (the close exists but is reported only after 16:00).
"""

import datetime
from zoneinfo import ZoneInfo

from django_assets_prices_marketdata.client import MarketDataClient, NoData

EASTERN = ZoneInfo("America/New_York")
CLOSING_BELL = datetime.time(16, 0)


class TradingCalendar:
    def __init__(self, client: MarketDataClient) -> None:
        self._client = client
        self._status: dict[datetime.date, bool] = {}
        self._loaded_years: set[int] = set()

    def _ensure_year(self, year: int) -> None:
        if year in self._loaded_years:
            return
        payload = self._client.get(
            "/v1/markets/status/",
            {"from": f"{year}-01-01", "to": f"{year}-12-31"},
        )
        if not isinstance(payload, NoData):
            dates = payload.get("date", [])
            statuses = payload.get("status", [])
            for stamp, status in zip(dates, statuses, strict=True):
                session = datetime.datetime.fromtimestamp(int(stamp), tz=EASTERN).date()
                if status is not None:
                    self._status[session] = status == "open"
        self._loaded_years.add(year)

    def is_session(self, on: datetime.date) -> bool | None:
        """True/False per the vendor; None when the vendor has no answer
        (dates beyond its calendar)."""
        self._ensure_year(on.year)
        return self._status.get(on)

    def last_completed_session(self, now: datetime.datetime) -> datetime.date:
        """The most recent session whose official close exists at `now`:
        today (ET) if open and past the bell, else the nearest open day
        before today."""
        now_east = now.astimezone(EASTERN)
        candidate = now_east.date()
        if not (self.is_session(candidate) and now_east.time() >= CLOSING_BELL):
            candidate -= datetime.timedelta(days=1)
            while self.is_session(candidate) is False:
                candidate -= datetime.timedelta(days=1)
        return candidate

    def is_open_now(self, now: datetime.datetime) -> bool:
        """Rough regular-hours check for freshness probes: an open
        session day, 09:30–16:00 ET."""
        now_east = now.astimezone(EASTERN)
        return bool(
            self.is_session(now_east.date())
            and datetime.time(9, 30) <= now_east.time() < CLOSING_BELL
        )
