"""Holding and Portfolio: live leg aggregation (spec §6, ADR-0007/0016).

Plain query classes, not models. Positions are always computed from
TransactionLeg sums — nothing is materialized, so the ledger can never
disagree with the balances (ADR-0016). `as_of` filters by SETTLEMENT
timestamp (transaction.timestamp, ADR-0012); cash and asset positions are
the same query shape (ADR-0013).
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Sum

from django_assets.core.intake import to_decimal
from django_assets.core.measure import Measure
from django_assets.core.measure import value as measure_value
from django_assets.core.models import Account, Instrument
from django_assets.core.prices import PriceQuote, PriceSource


class Holding:
    """Position of one instrument in one account, by live aggregation."""

    @staticmethod
    def current(account: Account, instrument: Instrument) -> Decimal:
        return Holding.historical(account, instrument, as_of=None)

    @staticmethod
    def historical(
        account: Account, instrument: Instrument, as_of: datetime.datetime | None
    ) -> Decimal:
        legs = account.legs.filter(instrument=instrument)
        if as_of is not None:
            legs = legs.filter(transaction__timestamp__lte=as_of)
        total: Decimal | None = legs.aggregate(total=Sum("amount"))["total"]
        return instrument.quantize(total) if total is not None else Decimal(0)


@dataclass(frozen=True)
class PortfolioValue:
    """Per-currency totals plus the honestly-surfaced unpriced positions."""

    totals: dict[Instrument, Measure] = field(default_factory=dict)
    unpriced: list[Instrument] = field(default_factory=list)


class Portfolio:
    """All non-zero positions of an account (spec §6)."""

    @staticmethod
    def at(account: Account, as_of: datetime.datetime | None = None) -> dict[Instrument, Decimal]:
        """One GROUP BY over legs; zero positions excluded."""
        # One filter() call = ONE join over the multivalued relation, so the
        # Sum aggregates exactly the legs that passed both conditions.
        conditions: dict[str, object] = {"transactionleg__account": account}
        if as_of is not None:
            conditions["transactionleg__transaction__timestamp__lte"] = as_of
        positions = (
            Instrument.objects.filter(**conditions)
            .annotate(total=Sum("transactionleg__amount"))
            .exclude(total=Decimal(0))
        )
        return {inst: inst.quantize(inst.total) for inst in positions}

    @staticmethod
    def value(
        account: Account,
        price_source: PriceSource,
        *,
        as_of: datetime.datetime | None = None,
    ) -> PortfolioValue:
        """Positions from Portfolio.at, priced per the ADR-0039 protocol.

        Current valuations (as_of=None) mark at the source's best
        available quote (`get_quotes(kind=None)` — one batch call, the
        downgrade visible on each quote's kind). Dated valuations mark at
        the official close of as_of's calendar date via `get_close`; a
        non-session date leaves positions unpriced — never interpolated.

        Currency positions (price_currency is NULL) are their own value —
        no quote is consulted (ADR-0013). quote.currency must equal
        instrument.price_currency: there is no implicit FX. None from the
        source lands in `unpriced`, never guessed. Computed on demand,
        never stored (ADR-0016).
        """
        totals: dict[Instrument, Measure] = {}
        unpriced: list[Instrument] = []

        def add(currency: Instrument, measure: Measure) -> None:
            totals[currency] = totals.get(currency, Measure(Decimal(0), currency)) + measure

        positions = Portfolio.at(account, as_of=as_of)
        priceable = [inst for inst in positions if inst.price_currency is not None]
        quotes: dict[Instrument, PriceQuote | None]
        if as_of is None:
            quotes = price_source.get_quotes(priceable)
        else:
            quotes = {inst: price_source.get_close(inst, as_of.date()) for inst in priceable}

        for instrument, qty in positions.items():
            if instrument.price_currency is None:
                add(instrument, Measure(qty, instrument))
                continue
            quote = quotes.get(instrument)
            if quote is None:
                unpriced.append(instrument)
                continue
            price = to_decimal(quote.price, param="quote.price")
            if quote.currency != instrument.price_currency:
                raise ValueError(
                    f"quote for {instrument.code} is in {quote.currency} but the "
                    f"instrument's price_currency is {instrument.price_currency} — "
                    f"no implicit FX (ADR-0013)"
                )
            add(instrument.price_currency, measure_value(qty, price, instrument))
        return PortfolioValue(totals=totals, unpriced=unpriced)
