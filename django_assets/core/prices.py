"""PriceSource protocol + reference implementations (ADR-0034, ADR-0039).

Core stores no prices and ships no real providers. The v2 contract
(ADR-0039) speaks a fixed vocabulary: three quote kinds on the
freshness-of-now axis (REALTIME | DELAYED | EOD), history as a separate
bounded axis (dated closes + OHLCV series at DAY | WEEK | MONTH), and
capability discovery so a consumer can ask "can I render this?" before
firing a data request. None = unpriced, surfaced honestly, never guessed.
Symbol mapping is the source's job (via Identifier). Real providers are
host or sibling implementations.
"""

import csv
import datetime
import enum
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import IO, Protocol, runtime_checkable

from django.utils import timezone

from django_assets.core.intake import to_decimal
from django_assets.core.models import Instrument


class PriceKind(enum.Enum):
    """The freshness-of-now vocabulary (ADR-0039 §1). A kind names the
    channel a quote came from; actual staleness is visible on `as_of`."""

    REALTIME = "realtime"
    DELAYED = "delayed"
    EOD = "eod"


class Resolution(enum.Enum):
    """OHLCV bar durations (ADR-0039 §5). Intraday deliberately absent."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"


@dataclass(frozen=True)
class DateRange:
    """Inclusive per-instrument history bound (ADR-0039 §2)."""

    min: datetime.date
    max: datetime.date

    def __post_init__(self) -> None:
        if self.min > self.max:
            raise ValueError(f"DateRange min {self.min} is after max {self.max}")

    def __contains__(self, on: datetime.date) -> bool:
        return self.min <= on <= self.max


@dataclass(frozen=True)
class PriceCapabilities:
    """A source's honest, entitlement-derived answer for one instrument
    (ADR-0039 §3). `greeks` reports whether option quotes carry the
    OptionQuote greek fields."""

    realtime: bool
    delayed: bool
    eod: bool
    historical: DateRange | None
    greeks: bool = False


@dataclass(frozen=True)
class PriceQuote:
    """One observed price. `kind` is the fixed ADR-0039 vocabulary —
    free-form provider strings fail loudly."""

    price: Decimal
    currency: Instrument
    as_of: datetime.datetime | None
    source: str
    kind: PriceKind

    def __post_init__(self) -> None:
        # PADR-0006 Rule 3: a float-built quote fails here, loudly.
        object.__setattr__(self, "price", to_decimal(self.price, param="price"))
        object.__setattr__(self, "kind", PriceKind(self.kind))


@dataclass(frozen=True)
class OptionQuote(PriceQuote):
    """A PriceQuote for an option contract, carrying the market-observed
    greeks (ADR-0039 as amended). None = the vendor didn't supply the
    field — honest absence, never zero. `volume`/`open_interest` are
    Decimal for uniform arithmetic (contracts are whole numbers)."""

    iv: Decimal | None = None
    delta: Decimal | None = None
    gamma: Decimal | None = None
    theta: Decimal | None = None
    vega: Decimal | None = None
    underlying_price: Decimal | None = None
    open_interest: Decimal | None = None
    volume: Decimal | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in (
            "iv",
            "delta",
            "gamma",
            "theta",
            "vega",
            "underlying_price",
            "open_interest",
            "volume",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, to_decimal(value, param=name))


@dataclass(frozen=True)
class Candle:
    """One OHLCV row (ADR-0039 §5). `session` is the exchange session
    date; `volume` is None where the asset class has no volume."""

    session: datetime.date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None

    def __post_init__(self) -> None:
        for name in ("open", "high", "low", "close"):
            object.__setattr__(self, name, to_decimal(getattr(self, name), param=name))
        if self.volume is not None:
            object.__setattr__(self, "volume", to_decimal(self.volume, param="volume"))


@dataclass(frozen=True)
class OHLCVSeries:
    """OHLCV bars ascending by session, single-currency (ADR-0013 — the
    instrument's price_currency, stated once at series level)."""

    instrument: Instrument
    currency: Instrument
    resolution: Resolution
    source: str
    candles: list[Candle] = field(default_factory=list)

    def __iter__(self) -> Iterator[Candle]:
        return iter(self.candles)

    def __len__(self) -> int:
        return len(self.candles)


def aggregate_candles(daily: Iterable[Candle], resolution: Resolution) -> list[Candle]:
    """Aggregate daily candles to weekly/monthly per ADR-0039 §5: open =
    first session's open, high/low = period extremes, close = last
    session's close, volume = sum (None if any constituent lacks volume),
    session = the LAST trading session in the period. Partial periods at
    range edges are emitted as-is, honestly partial. Shared by
    CSVPriceSource and provider connectors so every source aggregates
    identically."""
    ordered = sorted(daily, key=lambda candle: candle.session)
    if resolution is Resolution.DAY:
        return ordered

    def period(session: datetime.date) -> tuple[int, int]:
        if resolution is Resolution.WEEK:
            iso = session.isocalendar()
            return (iso.year, iso.week)
        return (session.year, session.month)

    grouped: dict[tuple[int, int], list[Candle]] = {}
    for candle in ordered:
        grouped.setdefault(period(candle.session), []).append(candle)

    aggregated: list[Candle] = []
    for _, members in sorted(grouped.items(), key=lambda item: item[1][0].session):
        volumes = [candle.volume for candle in members]
        volume = (
            None
            if any(v is None for v in volumes)
            else sum((v for v in volumes if v is not None), Decimal(0))
        )
        aggregated.append(
            Candle(
                session=members[-1].session,
                open=members[0].open,
                high=max(candle.high for candle in members),
                low=min(candle.low for candle in members),
                close=members[-1].close,
                volume=volume,
            )
        )
    return aggregated


@runtime_checkable
class PriceSource(Protocol):
    """Structural v2 contract (ADR-0039 §4): capability discovery, kinded
    quotes, dated closes, bounded OHLCV."""

    def capabilities(self, instrument: Instrument) -> PriceCapabilities | None: ...

    def get_quote(
        self, instrument: Instrument, *, kind: PriceKind | None = None
    ) -> PriceQuote | None:
        """Current price at the requested freshness. kind=None means best
        available — realtime → delayed → eod — with the downgrade visible
        on quote.kind. A specific kind is exact: unavailable → None."""
        ...

    def get_quotes(
        self, instruments: Iterable[Instrument], *, kind: PriceKind | None = None
    ) -> dict[Instrument, PriceQuote | None]:
        """Batch form for portfolio valuation; sources use vendor batch
        endpoints, never a hidden per-instrument loop."""
        ...

    def get_close(self, instrument: Instrument, on: datetime.date) -> PriceQuote | None:
        """Official close on a specific past session. None for
        non-sessions and out-of-bounds dates; never interpolated."""
        ...

    def get_ohlcv(
        self,
        instrument: Instrument,
        *,
        start: datetime.date,
        end: datetime.date,
        resolution: Resolution = Resolution.DAY,
    ) -> OHLCVSeries | None:
        """OHLCV bars clipped to the discoverable bound; holidays absent,
        no gap-filling. None = no historical capability."""
        ...


class StaticPriceSource:
    """Fixed prices from a dict — tests, docs, and demos (ADR-0034,
    updated per ADR-0039 §6): the minimal honest implementation. Declares
    eod only; quotes carry kind=EOD; no history.

    Prices are per-instrument in the instrument's own price_currency;
    Decimal/int/str only (the intake guard applies at construction).
    """

    def __init__(self, prices: dict[Instrument, Decimal | int | str]) -> None:
        self._prices = {
            inst: to_decimal(price, param=f"price[{inst.code}]") for inst, price in prices.items()
        }

    def capabilities(self, instrument: Instrument) -> PriceCapabilities | None:
        if instrument not in self._prices or instrument.price_currency is None:
            return None
        return PriceCapabilities(realtime=False, delayed=False, eod=True, historical=None)

    def get_quote(
        self, instrument: Instrument, *, kind: PriceKind | None = None
    ) -> PriceQuote | None:
        if kind not in (None, PriceKind.EOD):
            return None
        price = self._prices.get(instrument)
        currency = instrument.price_currency
        if price is None or currency is None:
            return None
        return PriceQuote(
            price=price, currency=currency, as_of=None, source="static", kind=PriceKind.EOD
        )

    def get_quotes(
        self, instruments: Iterable[Instrument], *, kind: PriceKind | None = None
    ) -> dict[Instrument, PriceQuote | None]:
        return {inst: self.get_quote(inst, kind=kind) for inst in instruments}

    def get_close(self, instrument: Instrument, on: datetime.date) -> PriceQuote | None:
        return None  # no archive (historical=None) — honest, not lazy

    def get_ohlcv(
        self,
        instrument: Instrument,
        *,
        start: datetime.date,
        end: datetime.date,
        resolution: Resolution = Resolution.DAY,
    ) -> OHLCVSeries | None:
        return None


class CSVPriceSource:
    """The example connector (ADR-0039 §6): the full v2 contract from
    per-instrument OHLCV rows — the pedagogical template a provider-
    connector author copies (swap CSV reads for HTTP calls).

    `data` maps Instrument → CSV source: a path (str/Path) or an open
    text stream. Columns: session,open,high,low,close[,volume] — ISO
    dates, decimal strings (never parsed through float). Capabilities
    derive from the data: eod + historical bounds, realtime/delayed
    honestly False. Quotes are the last close (kind=EOD, as_of = midnight
    UTC of the session date — it identifies the session, not the closing
    bell). Weekly/monthly bars aggregate daily per aggregate_candles.
    """

    def __init__(self, data: Mapping[Instrument, str | Path | IO[str]]) -> None:
        self._candles: dict[Instrument, list[Candle]] = {}
        for instrument, source in data.items():
            self._candles[instrument] = self._load(instrument, source)

    @staticmethod
    def _load(instrument: Instrument, source: str | Path | IO[str]) -> list[Candle]:
        if isinstance(source, str | Path):
            with open(source, newline="") as handle:
                rows = list(csv.DictReader(handle))
        else:
            rows = list(csv.DictReader(source))
        candles: list[Candle] = []
        for row in rows:
            raw_volume = row.get("volume")
            volume = to_decimal(raw_volume, param="volume") if raw_volume else None
            candles.append(
                Candle(
                    session=datetime.date.fromisoformat(row["session"]),
                    open=to_decimal(row["open"], param="open"),
                    high=to_decimal(row["high"], param="high"),
                    low=to_decimal(row["low"], param="low"),
                    close=to_decimal(row["close"], param="close"),
                    volume=volume,
                )
            )
        candles.sort(key=lambda candle: candle.session)
        sessions = [candle.session for candle in candles]
        if len(set(sessions)) != len(sessions):
            raise ValueError(f"duplicate sessions in CSV data for {instrument.code}")
        return candles

    def _bound(self, instrument: Instrument) -> DateRange | None:
        candles = self._candles.get(instrument)
        if not candles or instrument.price_currency is None:
            return None
        return DateRange(candles[0].session, candles[-1].session)

    def capabilities(self, instrument: Instrument) -> PriceCapabilities | None:
        bound = self._bound(instrument)
        if bound is None:
            return None
        return PriceCapabilities(realtime=False, delayed=False, eod=True, historical=bound)

    @staticmethod
    def _session_as_of(session: datetime.date) -> datetime.datetime:
        return datetime.datetime.combine(session, datetime.time(), tzinfo=datetime.UTC)

    def get_quote(
        self, instrument: Instrument, *, kind: PriceKind | None = None
    ) -> PriceQuote | None:
        if kind not in (None, PriceKind.EOD):
            return None
        candles = self._candles.get(instrument)
        currency = instrument.price_currency
        if not candles or currency is None:
            return None
        last = candles[-1]
        return PriceQuote(
            price=last.close,
            currency=currency,
            as_of=self._session_as_of(last.session),
            source="csv",
            kind=PriceKind.EOD,
        )

    def get_quotes(
        self, instruments: Iterable[Instrument], *, kind: PriceKind | None = None
    ) -> dict[Instrument, PriceQuote | None]:
        return {inst: self.get_quote(inst, kind=kind) for inst in instruments}

    def get_close(self, instrument: Instrument, on: datetime.date) -> PriceQuote | None:
        bound = self._bound(instrument)
        currency = instrument.price_currency
        if bound is None or currency is None or on not in bound:
            return None
        for candle in self._candles[instrument]:
            if candle.session == on:
                return PriceQuote(
                    price=candle.close,
                    currency=currency,
                    as_of=self._session_as_of(on),
                    source="csv",
                    kind=PriceKind.EOD,
                )
        return None  # non-session (holiday/weekend) — never interpolated

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
        bound = self._bound(instrument)
        currency = instrument.price_currency
        if bound is None or currency is None:
            return None
        daily = [
            candle
            for candle in self._candles[instrument]
            if start <= candle.session <= end and candle.session in bound
        ]
        return OHLCVSeries(
            instrument=instrument,
            currency=currency,
            resolution=resolution,
            source="csv",
            candles=aggregate_candles(daily, resolution),
        )


class CachedPriceSource:
    """TTL cache over any PriceSource (ADR-0039 §6): kind-aware quote
    keys; `ttl` governs quotes, `history_ttl` (default `ttl`) governs
    closes/OHLCV/capabilities — lifetimes that differ by orders of
    magnitude. None results are cached too. Caching is source-internal;
    no storage anywhere (ADR-0034 §3)."""

    def __init__(self, inner: PriceSource, ttl: int, history_ttl: int | None = None) -> None:
        self.inner = inner
        self.ttl = ttl
        self.history_ttl = history_ttl if history_ttl is not None else ttl
        self._quotes: dict[
            tuple[int, PriceKind | None], tuple[PriceQuote | None, datetime.datetime]
        ] = {}
        self._capabilities: dict[int, tuple[PriceCapabilities | None, datetime.datetime]] = {}
        self._closes: dict[
            tuple[int, datetime.date], tuple[PriceQuote | None, datetime.datetime]
        ] = {}
        self._series: dict[
            tuple[int, datetime.date, datetime.date, Resolution],
            tuple[OHLCVSeries | None, datetime.datetime],
        ] = {}

    @staticmethod
    def _fresh(cached_at: datetime.datetime, ttl: int, now: datetime.datetime) -> bool:
        return (now - cached_at).total_seconds() <= ttl

    def capabilities(self, instrument: Instrument) -> PriceCapabilities | None:
        now = timezone.now()
        hit = self._capabilities.get(instrument.pk)
        if hit is not None and self._fresh(hit[1], self.history_ttl, now):
            return hit[0]
        caps = self.inner.capabilities(instrument)
        self._capabilities[instrument.pk] = (caps, now)
        return caps

    def get_quote(
        self, instrument: Instrument, *, kind: PriceKind | None = None
    ) -> PriceQuote | None:
        now = timezone.now()
        key = (instrument.pk, kind)
        hit = self._quotes.get(key)
        if hit is not None and self._fresh(hit[1], self.ttl, now):
            return hit[0]
        quote = self.inner.get_quote(instrument, kind=kind)
        self._quotes[key] = (quote, now)
        return quote

    def get_quotes(
        self, instruments: Iterable[Instrument], *, kind: PriceKind | None = None
    ) -> dict[Instrument, PriceQuote | None]:
        now = timezone.now()
        result: dict[Instrument, PriceQuote | None] = {}
        misses: list[Instrument] = []
        for inst in instruments:
            hit = self._quotes.get((inst.pk, kind))
            if hit is not None and self._fresh(hit[1], self.ttl, now):
                result[inst] = hit[0]
            else:
                misses.append(inst)
        if misses:
            fetched = self.inner.get_quotes(misses, kind=kind)
            for inst, quote in fetched.items():
                self._quotes[(inst.pk, kind)] = (quote, now)
                result[inst] = quote
        return result

    def get_close(self, instrument: Instrument, on: datetime.date) -> PriceQuote | None:
        now = timezone.now()
        key = (instrument.pk, on)
        hit = self._closes.get(key)
        if hit is not None and self._fresh(hit[1], self.history_ttl, now):
            return hit[0]
        quote = self.inner.get_close(instrument, on)
        self._closes[key] = (quote, now)
        return quote

    def get_ohlcv(
        self,
        instrument: Instrument,
        *,
        start: datetime.date,
        end: datetime.date,
        resolution: Resolution = Resolution.DAY,
    ) -> OHLCVSeries | None:
        now = timezone.now()
        key = (instrument.pk, start, end, resolution)
        hit = self._series.get(key)
        if hit is not None and self._fresh(hit[1], self.history_ttl, now):
            return hit[0]
        series = self.inner.get_ohlcv(instrument, start=start, end=end, resolution=resolution)
        self._series[key] = (series, now)
        return series
