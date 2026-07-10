"""MarketDataPriceSource — the ADR-0039 v2 PriceSource for MarketData.app.

Read-time only (ADR-0034): nothing is persisted; caches live on the
instance. Core never imports this package — it imports core.

Channel model (what the vendor actually serves, probe-verified):

- equities REALTIME — /v1/stocks/prices (the vendor's real-time channel).
  Claimed only after a market-hours freshness check confirms it; an
  instance that has never seen the market open reports realtime=False
  rather than guess (never optimism).
- equities DELAYED — /v1/stocks/quotes; entitlement inferred by dating
  the probe quote to the current session (a UTP-less token gets
  previous-session quotes there, which is not "delayed").
- equities EOD + history — /v1/stocks/candles (daily), sessions resolved
  via the vendor calendar; weekly/monthly aggregate from daily candles
  per ADR-0039 §5 (aggregate_candles), so contract semantics hold
  exactly.
- options REALTIME/DELAYED — /v1/options/quotes; the entitlement comes
  from the vendor's own x-options-data-permissions header. Quotes carry
  greeks/IV as OptionQuote.
- options EOD + dated closes — the vendor's end-of-day quote series
  (options have no bar archive: capabilities report closes without
  ohlcv).

Price semantics: a quote's price is the vendor's midpoint (`mid`, the
mark — also the only price the realtime channel serves), falling back to
`last` when no market exists. Candle-derived quotes are official closes.

The quote `kind` names the channel (ADR-0039 §1); staleness is visible
on as_of — a delayed quote fetched overnight is honestly stamped with
last session's time.
"""

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from django_assets.core.models import Instrument
from django_assets.core.prices import (
    Candle,
    DateRange,
    OHLCVSeries,
    OptionQuote,
    PriceCapabilities,
    PriceKind,
    PriceQuote,
    Resolution,
    aggregate_candles,
)
from django_assets_prices_marketdata.calendar import EASTERN, TradingCalendar
from django_assets_prices_marketdata.client import (
    MarketDataBadRequest,
    MarketDataClient,
    MarketDataEntitlementError,
    NoData,
)
from django_assets_prices_marketdata.mapping import (
    DEFAULT_CURRENCY_CODES,
    VendorSymbol,
    map_instrument,
)

SOURCE_LABEL = "marketdata"
_REALTIME_MAX_AGE = datetime.timedelta(seconds=90)
_DELAYED_GRACE = datetime.timedelta(minutes=30)
_EARLIEST_PLAUSIBLE_YEAR = 1930
_OPTION_SERIES_FLOOR = "1990-01-01"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _as_of(epoch: int | Decimal) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(int(epoch), tz=datetime.UTC)


def _session_of(epoch: int | Decimal) -> datetime.date:
    return datetime.datetime.fromtimestamp(int(epoch), tz=EASTERN).date()


def _first(payload: dict[str, Any], key: str) -> Any:
    values = payload.get(key)
    return values[0] if isinstance(values, list) and values else None


@dataclass(frozen=True)
class _Entitlements:
    stocks_realtime: bool
    stocks_delayed: bool
    options_realtime: bool
    options_delayed: bool


class MarketDataPriceSource:
    """PriceSource (ADR-0039) over MarketData.app.

    Args:
        client: a configured MarketDataClient; built from `token`/env
            when omitted.
        token: MarketData token; falls back to $MARKETDATA_TOKEN.
        transport: httpx transport seam (recorded-fixture replay/tests).
        probe_symbol: liquid symbol used for entitlement probes.
        currency_codes: price currencies this source will quote in
            (MarketData is USD markets).
    """

    def __init__(
        self,
        *,
        client: MarketDataClient | None = None,
        token: str | None = None,
        transport: httpx.BaseTransport | None = None,
        probe_symbol: str = "SPY",
        currency_codes: tuple[str, ...] = DEFAULT_CURRENCY_CODES,
    ) -> None:
        self._client = client or MarketDataClient(token=token, transport=transport)
        self._calendar = TradingCalendar(self._client)
        self._probe_symbol = probe_symbol
        self._currency_codes = currency_codes
        self._entitlements: _Entitlements | None = None
        self._stocks_realtime_confirmed = False
        self._realtime_probed_at: datetime.datetime | None = None
        self._equity_bounds: dict[int, DateRange | None] = {}
        self._option_series: dict[int, dict[datetime.date, dict[str, Any]]] = {}
        # Instance-lifetime caches (same posture as the series/bounds
        # caches): the wide bound-discovery fetch doubles as the daily
        # candle store, and symbol mapping is resolved once per
        # instrument. Long-lived processes should wrap the source in
        # CachedPriceSource and recycle it per their freshness policy.
        self._daily: dict[int, dict[datetime.date, Candle]] = {}
        self._mapped: dict[int, VendorSymbol | None] = {}

    def _map(self, instrument: Instrument) -> VendorSymbol | None:
        if instrument.pk not in self._mapped:
            self._mapped[instrument.pk] = map_instrument(
                instrument, currency_codes=self._currency_codes
            )
        return self._mapped[instrument.pk]

    # -- entitlement discovery ------------------------------------------------

    def _reference_session(self, now: datetime.datetime) -> datetime.date:
        """The session a delayed-entitled quote should date to, with a
        grace window for the 15-minute lag around the open."""
        graced = (now - _DELAYED_GRACE).astimezone(EASTERN)
        candidate = graced.date()
        if self._calendar.is_session(candidate) and graced.time() >= datetime.time(9, 30):
            return candidate
        candidate -= datetime.timedelta(days=1)
        while self._calendar.is_session(candidate) is False:
            candidate -= datetime.timedelta(days=1)
        return candidate

    def _probe_stocks_delayed(self, now: datetime.datetime) -> bool:
        payload = self._client.get(f"/v1/stocks/quotes/{self._probe_symbol}/")
        if isinstance(payload, NoData):
            return False
        updated = _first(payload, "updated")
        if updated is None:
            return False
        return _session_of(updated) >= self._reference_session(now)

    def _probe_stocks_realtime(self, now: datetime.datetime) -> bool:
        """Realtime is claimed only when confirmed: a prices-channel
        quote observed fresh (≤90s) while the market is open. Sticky
        once True; False simply means 'not yet confirmable'."""
        if self._stocks_realtime_confirmed:
            return True
        if not self._calendar.is_open_now(now):
            return False
        if (
            self._realtime_probed_at is not None
            and now - self._realtime_probed_at < datetime.timedelta(minutes=15)
        ):
            return False  # recently probed and unconfirmed; don't burn credits
        self._realtime_probed_at = now
        try:
            payload = self._client.get(f"/v1/stocks/prices/{self._probe_symbol}/")
        except MarketDataEntitlementError:
            return False
        if isinstance(payload, NoData):
            return False
        updated = _first(payload, "updated")
        if updated is not None and now - _as_of(updated) <= _REALTIME_MAX_AGE:
            self._stocks_realtime_confirmed = True
        return self._stocks_realtime_confirmed

    def _probe_options(self) -> tuple[bool, bool]:
        """Options entitlement from the vendor's own permissions header
        (captured on every response); /user/ as fallback."""
        perms = self._client.permissions
        if not perms:
            payload = self._client.get("/user/")
            if not isinstance(payload, NoData):
                human = str(payload.get("x-options-data-permissions", "")).lower()
                realtime = "real-time" in human or "realtime" in human
                delayed = "delayed" in human
                return realtime, delayed
            return False, False
        realtime = any("realtime" in perm for perm in perms)
        delayed = "delayed_quotes_permission" in perms
        return realtime, delayed

    def _ensure_entitlements(self) -> _Entitlements:
        now = _now()
        if self._entitlements is None:
            stocks_delayed = self._probe_stocks_delayed(now)
            options_realtime, options_delayed = self._probe_options()
            self._entitlements = _Entitlements(
                stocks_realtime=self._probe_stocks_realtime(now),
                stocks_delayed=stocks_delayed,
                options_realtime=options_realtime,
                options_delayed=options_delayed,
            )
        elif not self._entitlements.stocks_realtime and self._probe_stocks_realtime(now):
            self._entitlements = _Entitlements(
                stocks_realtime=True,
                stocks_delayed=self._entitlements.stocks_delayed,
                options_realtime=self._entitlements.options_realtime,
                options_delayed=self._entitlements.options_delayed,
            )
        return self._entitlements

    # -- history bounds ---------------------------------------------------------

    def _candles_payload(
        self, symbol: str, start: datetime.date, end: datetime.date
    ) -> dict[str, Any] | NoData:
        return self._client.get(
            f"/v1/stocks/candles/D/{symbol}/",
            {"from": start.isoformat(), "to": end.isoformat()},
        )

    def _equity_bound(self, instrument: Instrument, symbol: str) -> DateRange | None:
        if instrument.pk in self._equity_bounds:
            return self._equity_bounds[instrument.pk]
        last_session = self._calendar.last_completed_session(_now())
        first_payloads: dict[int, dict[str, Any] | NoData] = {}

        def year_has_data(year: int) -> bool:
            start = datetime.date(year, 1, 1)
            end = min(datetime.date(year, 12, 31), last_session)
            try:
                payload = self._candles_payload(symbol, start, end)
            except MarketDataEntitlementError:
                return False  # deeper than the token's plan — that IS the bound
            first_payloads[year] = payload
            return not isinstance(payload, NoData)

        bound: DateRange | None
        # Fast path: daily candles have no range limit, so ONE wide
        # request discovers the earliest session in a single round-trip
        # (costs a few credits once per instrument; cached). Plans that
        # cap history answer 402 — fall back to year-bisection, whose
        # denials map the plan's floor honestly.
        try:
            wide = self._candles_payload(
                symbol, datetime.date(_EARLIEST_PLAUSIBLE_YEAR, 1, 1), last_session
            )
        except MarketDataEntitlementError:
            wide = None
        if wide is not None:
            if isinstance(wide, NoData):
                bound = None  # vendor has no candles at all
            else:
                bound = DateRange(_session_of(wide["t"][0]), last_session)
                # The discovery response IS the daily archive: keep it so
                # closes/ohlcv/eod never re-fetch what we already hold.
                self._daily[instrument.pk] = {
                    candle.session: candle for candle in self._candles_from_payload(wide)
                }
        elif not year_has_data(last_session.year):
            bound = None
        else:
            lo, hi = _EARLIEST_PLAUSIBLE_YEAR, last_session.year
            while lo < hi:
                mid = (lo + hi) // 2
                if year_has_data(mid):
                    hi = mid
                else:
                    lo = mid + 1
            payload = first_payloads[lo]
            assert not isinstance(payload, NoData)
            earliest = _session_of(payload["t"][0])
            bound = DateRange(earliest, last_session)
        self._equity_bounds[instrument.pk] = bound
        return bound

    def _option_rows(
        self, instrument: Instrument, symbol: str
    ) -> dict[datetime.date, dict[str, Any]]:
        if instrument.pk in self._option_series:
            return self._option_series[instrument.pk]
        tomorrow = (_now().astimezone(EASTERN).date() + datetime.timedelta(days=1)).isoformat()
        payload: dict[str, Any] | NoData
        try:
            payload = self._client.get(
                f"/v1/options/quotes/{symbol}/",
                {"from": _OPTION_SERIES_FLOOR, "to": tomorrow},
            )
        except MarketDataEntitlementError:
            try:  # shallower archive tier
                floor = (
                    _now().astimezone(EASTERN).date() - datetime.timedelta(days=366)
                ).isoformat()
                payload = self._client.get(
                    f"/v1/options/quotes/{symbol}/", {"from": floor, "to": tomorrow}
                )
            except MarketDataEntitlementError:
                payload = NoData()
        rows: dict[datetime.date, dict[str, Any]] = {}
        if not isinstance(payload, NoData):
            # An in-progress session is not an official close yet: clip
            # to sessions the calendar says are complete.
            last_complete = self._calendar.last_completed_session(_now())
            count = len(payload.get("updated", []))
            for index in range(count):
                row = {
                    key: values[index]
                    for key, values in payload.items()
                    if isinstance(values, list) and len(values) == count
                }
                updated = row.get("updated")
                if updated is not None and _session_of(updated) <= last_complete:
                    rows[_session_of(updated)] = row
        self._option_series[instrument.pk] = rows
        return rows

    # -- quote builders ---------------------------------------------------------

    @staticmethod
    def _pick_price(payload_row: dict[str, Any]) -> Decimal | None:
        for key in ("mid", "last"):
            value = payload_row.get(key)
            if isinstance(value, Decimal | int):
                return Decimal(value)
        return None

    def _option_quote_from_row(
        self, row: dict[str, Any], currency: Instrument, kind: PriceKind
    ) -> OptionQuote | None:
        price = self._pick_price(row)
        if price is None:
            return None

        def dec(key: str) -> Decimal | None:
            value = row.get(key)
            return Decimal(value) if isinstance(value, Decimal | int) else None

        updated = row.get("updated")
        return OptionQuote(
            price=price,
            currency=currency,
            as_of=_as_of(updated) if updated is not None else None,
            source=SOURCE_LABEL,
            kind=kind,
            iv=dec("iv"),
            delta=dec("delta"),
            gamma=dec("gamma"),
            theta=dec("theta"),
            vega=dec("vega"),
            underlying_price=dec("underlyingPrice"),
            open_interest=dec("openInterest"),
            volume=dec("volume"),
            intrinsic_value=dec("intrinsicValue"),
            extrinsic_value=dec("extrinsicValue"),
        )

    @staticmethod
    def _row_at(payload: dict[str, Any], index: int) -> dict[str, Any]:
        return {
            key: values[index]
            for key, values in payload.items()
            if isinstance(values, list) and len(values) > index
        }

    def _candles_from_payload(self, payload: dict[str, Any]) -> list[Candle]:
        candles: list[Candle] = []
        stamps = payload.get("t", [])
        for index, stamp in enumerate(stamps):
            volume = payload.get("v", [None] * len(stamps))[index]
            candles.append(
                Candle(
                    session=_session_of(stamp),
                    open=Decimal(payload["o"][index]),
                    high=Decimal(payload["h"][index]),
                    low=Decimal(payload["l"][index]),
                    close=Decimal(payload["c"][index]),
                    volume=Decimal(volume) if isinstance(volume, Decimal | int) else None,
                )
            )
        return candles

    def _equity_eod_quote(self, symbol: str, currency: Instrument) -> PriceQuote | None:
        """Most recent official close: the last completed session's daily
        candle; steps back if the vendor hasn't published it yet."""
        session = self._calendar.last_completed_session(_now())
        cached = None
        for pk, days in self._daily.items():
            mapped_pk = self._mapped.get(pk)
            if mapped_pk is not None and mapped_pk.symbol == symbol:
                cached = days
                break
        for _ in range(3):
            if cached is not None and session in cached:
                candle = cached[session]
                return PriceQuote(
                    price=candle.close,
                    currency=currency,
                    as_of=datetime.datetime.combine(
                        session, datetime.time(), tzinfo=EASTERN
                    ).astimezone(datetime.UTC),
                    source=SOURCE_LABEL,
                    kind=PriceKind.EOD,
                )
            try:
                payload = self._candles_payload(symbol, session, session)
            except (MarketDataBadRequest, MarketDataEntitlementError):
                return None
            if not isinstance(payload, NoData) and payload.get("c"):
                return PriceQuote(
                    price=Decimal(payload["c"][-1]),
                    currency=currency,
                    as_of=_as_of(payload["t"][-1]),
                    source=SOURCE_LABEL,
                    kind=PriceKind.EOD,
                )
            previous = session - datetime.timedelta(days=1)
            while self._calendar.is_session(previous) is False:
                previous -= datetime.timedelta(days=1)
            session = previous
        return None

    def _live_option_ok(self, instrument: Instrument) -> bool:
        """Live quote channels only make sense for unexpired contracts."""
        meta = getattr(instrument, "option_meta", None)
        expiry = meta.expiry if meta is not None else None
        return expiry is None or expiry >= _now().astimezone(EASTERN).date()

    # -- protocol: capabilities ---------------------------------------------------

    def capabilities(self, instrument: Instrument) -> PriceCapabilities | None:
        mapped = self._map(instrument)
        if mapped is None:
            return None
        ent = self._ensure_entitlements()
        try:
            if mapped.is_option:
                rows = self._option_rows(instrument, mapped.symbol)
                if not rows and isinstance(
                    self._client.get(f"/v1/options/quotes/{mapped.symbol}/"), NoData
                ):
                    # No EOD archive AND no live quote: the vendor does
                    # not know this contract — unpriceable, honestly.
                    return None
                sessions = sorted(rows)
                closes = DateRange(sessions[0], sessions[-1]) if sessions else None
                live = self._live_option_ok(instrument)
                return PriceCapabilities(
                    realtime=ent.options_realtime and live,
                    delayed=ent.options_delayed and live,
                    eod=bool(sessions),
                    closes=closes,
                    ohlcv=None,  # MarketData has no option bar archive
                    greeks=True,
                )
            bound = self._equity_bound(instrument, mapped.symbol)
            return PriceCapabilities(
                realtime=ent.stocks_realtime,
                delayed=ent.stocks_delayed,
                eod=bound is not None,
                closes=bound,
                ohlcv=bound,
                greeks=False,
            )
        except MarketDataBadRequest:
            return None  # the vendor doesn't know the symbol — unpriceable

    # -- protocol: quotes ----------------------------------------------------------

    def _kinds_for(self, mapped: VendorSymbol, instrument: Instrument) -> list[PriceKind]:
        ent = self._ensure_entitlements()
        if mapped.is_option:
            live = self._live_option_ok(instrument)
            kinds = [
                kind
                for kind, enabled in (
                    (PriceKind.REALTIME, ent.options_realtime and live),
                    (PriceKind.DELAYED, ent.options_delayed and live),
                )
                if enabled
            ]
            kinds.append(PriceKind.EOD)
            return kinds
        kinds = [
            kind
            for kind, enabled in (
                (PriceKind.REALTIME, ent.stocks_realtime),
                (PriceKind.DELAYED, ent.stocks_delayed),
            )
            if enabled
        ]
        kinds.append(PriceKind.EOD)
        return kinds

    def _stock_quote(self, symbol: str, currency: Instrument, kind: PriceKind) -> PriceQuote | None:
        try:
            if kind is PriceKind.REALTIME:
                payload = self._client.get(f"/v1/stocks/prices/{symbol}/")
            elif kind is PriceKind.DELAYED:
                payload = self._client.get(f"/v1/stocks/quotes/{symbol}/")
            else:
                return self._equity_eod_quote(symbol, currency)
        except MarketDataBadRequest:
            return None  # unknown symbol
        if isinstance(payload, NoData):
            return None
        row = self._row_at(payload, 0)
        price = self._pick_price(row)
        if price is None:
            return None
        updated = row.get("updated")
        return PriceQuote(
            price=price,
            currency=currency,
            as_of=_as_of(updated) if updated is not None else None,
            source=SOURCE_LABEL,
            kind=kind,
        )

    def _option_quote(
        self, instrument: Instrument, symbol: str, currency: Instrument, kind: PriceKind
    ) -> PriceQuote | None:
        if kind is PriceKind.EOD:
            rows = self._option_rows(instrument, symbol)
            if not rows:
                return None
            last_session = max(rows)
            return self._option_quote_from_row(rows[last_session], currency, PriceKind.EOD)
        try:
            payload = self._client.get(f"/v1/options/quotes/{symbol}/")
        except MarketDataBadRequest:
            return None
        if isinstance(payload, NoData):
            return None
        return self._option_quote_from_row(self._row_at(payload, 0), currency, kind)

    def get_quote(
        self, instrument: Instrument, *, kind: PriceKind | None = None
    ) -> PriceQuote | None:
        mapped = self._map(instrument)
        currency = instrument.price_currency
        if mapped is None or currency is None:
            return None
        allowed = self._kinds_for(mapped, instrument)
        kinds = [kind] if kind is not None else allowed
        for candidate in kinds:
            if candidate not in allowed:
                return None  # a specific kind is exact: not entitled → None
            quote = (
                self._option_quote(instrument, mapped.symbol, currency, candidate)
                if mapped.is_option
                else self._stock_quote(mapped.symbol, currency, candidate)
            )
            if quote is not None:
                return quote
        return None

    def get_quotes(
        self,
        instruments: "list[Instrument] | tuple[Instrument, ...] | Any",
        *,
        kind: PriceKind | None = None,
    ) -> dict[Instrument, PriceQuote | None]:
        result: dict[Instrument, PriceQuote | None] = {}
        batches: dict[PriceKind, list[tuple[Instrument, Instrument, str]]] = {}
        for instrument in instruments:
            mapped = self._map(instrument)
            currency = instrument.price_currency
            if mapped is None or currency is None:
                result[instrument] = None
                continue
            if mapped.is_option:
                result[instrument] = self.get_quote(instrument, kind=kind)
                continue
            allowed = self._kinds_for(mapped, instrument)
            resolved = kind if kind is not None else allowed[0]
            if resolved not in allowed:
                result[instrument] = None
                continue
            batches.setdefault(resolved, []).append((instrument, currency, mapped.symbol))

        for resolved, members in batches.items():
            if resolved is PriceKind.EOD:
                for instrument, currency, symbol in members:
                    result[instrument] = self._stock_quote(symbol, currency, resolved)
                continue
            path = "/v1/stocks/prices/" if resolved is PriceKind.REALTIME else "/v1/stocks/quotes/"
            symbols = ",".join(symbol for _, _, symbol in members)
            try:
                payload = self._client.get(path, {"symbols": symbols})
            except MarketDataBadRequest:
                for instrument, currency, symbol in members:  # isolate the bad symbol
                    result[instrument] = self._stock_quote(symbol, currency, resolved)
                continue
            by_symbol: dict[str, tuple[Decimal, int | None]] = {}
            if not isinstance(payload, NoData):
                returned = payload.get("symbol", [])
                for index, symbol in enumerate(returned):
                    row = self._row_at(payload, index)
                    price = self._pick_price(row)
                    updated = row.get("updated")
                    if price is not None:
                        by_symbol[symbol] = (
                            price,
                            int(updated) if updated is not None else None,
                        )
            for instrument, currency, symbol in members:
                hit = by_symbol.get(symbol)
                if hit is None:
                    result[instrument] = None
                    continue
                price, updated_epoch = hit
                result[instrument] = PriceQuote(
                    price=price,
                    currency=currency,
                    as_of=_as_of(updated_epoch) if updated_epoch is not None else None,
                    source=SOURCE_LABEL,
                    kind=resolved,
                )
        return result

    # -- protocol: history -----------------------------------------------------------

    def get_close(self, instrument: Instrument, on: datetime.date) -> PriceQuote | None:
        mapped = self._map(instrument)
        currency = instrument.price_currency
        if mapped is None or currency is None:
            return None
        try:
            if mapped.is_option:
                rows = self._option_rows(instrument, mapped.symbol)
                row = rows.get(on)
                if row is None:
                    return None
                return self._option_quote_from_row(row, currency, PriceKind.EOD)
            bound = self._equity_bound(instrument, mapped.symbol)
            if bound is None or on not in bound or self._calendar.is_session(on) is False:
                return None
            cached_days = self._daily.get(instrument.pk)
            if cached_days is not None:
                candle = cached_days.get(on)
                if candle is None:
                    return None  # a non-session inside the archive
                return PriceQuote(
                    price=candle.close,
                    currency=currency,
                    as_of=datetime.datetime.combine(on, datetime.time(), tzinfo=EASTERN).astimezone(
                        datetime.UTC
                    ),
                    source=SOURCE_LABEL,
                    kind=PriceKind.EOD,
                )
            payload = self._candles_payload(mapped.symbol, on, on)
            if isinstance(payload, NoData) or not payload.get("c"):
                return None
            if _session_of(payload["t"][-1]) != on:
                return None  # vendor answered a different session — not this close
            return PriceQuote(
                price=Decimal(payload["c"][-1]),
                currency=currency,
                as_of=_as_of(payload["t"][-1]),
                source=SOURCE_LABEL,
                kind=PriceKind.EOD,
            )
        except (MarketDataBadRequest, MarketDataEntitlementError):
            return None

    def get_ohlcv(
        self,
        instrument: Instrument,
        *,
        start: datetime.date,
        end: datetime.date,
        resolution: Resolution = Resolution.DAY,
    ) -> OHLCVSeries | None:
        if start > end:
            raise ValueError(f"start {start} is after end {end}")
        mapped = self._map(instrument)
        currency = instrument.price_currency
        if mapped is None or currency is None:
            return None
        if mapped.is_option:
            return None  # no option bar archive (capabilities say so)
        try:
            bound = self._equity_bound(instrument, mapped.symbol)
        except MarketDataBadRequest:
            return None
        if bound is None:
            return None
        clipped_start = max(start, bound.min)
        clipped_end = min(end, bound.max)
        daily: list[Candle] = []
        if clipped_start <= clipped_end:
            cached_days = self._daily.get(instrument.pk)
            if cached_days is not None:
                daily = [
                    candle
                    for session, candle in sorted(cached_days.items())
                    if clipped_start <= session <= clipped_end
                ]
            else:
                payload = self._candles_payload(mapped.symbol, clipped_start, clipped_end)
                if not isinstance(payload, NoData):
                    daily = [
                        candle
                        for candle in self._candles_from_payload(payload)
                        if clipped_start <= candle.session <= clipped_end
                    ]
        return OHLCVSeries(
            instrument=instrument,
            currency=currency,
            resolution=resolution,
            source=SOURCE_LABEL,
            candles=aggregate_candles(daily, resolution),
        )
