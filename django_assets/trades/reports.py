"""Trades reporting surface — the option-tracker vertical's data layer.

Every number an option dashboard renders (position rows, greeks panels,
roll history, account summary, calendar, performance analytics, wheel
campaigns) is computed HERE, against ledger facts and an ADR-0039
PriceSource — never in an app layer. Prices come from the supplied
source at read time; nothing is stored (ADR-0034). Unpriced positions
surface honestly as None fields plus an `unpriced` list.

Conventions:

- Ratios are Decimal fractions (0.19 = 19%); money is Decimal, quantized
  through the instrument's own precision rules where a price is implied.
- A trade's option events partition into COHORTS — sets of contracts
  opened together; a cohort fully closed while the trade stays open is a
  ROLL SEGMENT (opened/closed dates, its own opening premium, realized
  cash over its life). A transaction mixing two cohorts merges them (a
  combo roll booked as one fill is one continuing segment — per-segment
  attribution would need per-leg prices the ledger doesn't store).
- The PRIMARY leg of an open strategy is the leg with the largest
  absolute delta (the risk driver); moneyness measures the underlying's
  distance to the primary strike.
- Margin is an ESTIMATE (defined-risk width for verticals/condors, cash
  securing for naked short puts, zero for covered structures, premium at
  risk for long/debit structures) — display-grade, not broker truth.
- AROI: initial = opening premium / margin, annualized over open→expiry;
  now = total P&L / margin, annualized over days open so far.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.utils import timezone

from django_assets.core.models import Instrument
from django_assets.core.prices import (
    OptionChainSource,
    OptionContract,
    OptionQuote,
    PriceQuote,
    PriceSource,
)
from django_assets.instruments.options.models import OptionMeta
from django_assets.trades.models import Trade, TradeAllocation

STRATEGY_CATEGORY = "strategy"


def classify_trade(trade: Trade) -> str:
    """Structural strategy classification for a whole trade (ADR-0037's
    classify_structure, applied trade-wide): LIVE trades classify over
    all position legs (closed cohorts net away, leaving the live
    structure); fully-CLOSED trades classify over their opening
    event's structure (everything nets to zero otherwise)."""
    from django_assets.trades.detection import classify_structure

    allocations = list(
        trade.allocations.filter(category="").select_related("leg__instrument", "leg__transaction")
    )
    legs = [allocation.leg for allocation in allocations]
    if not legs:
        return "stock"
    if trade.status == "closed":
        by_ts: dict[datetime.datetime, list[Any]] = {}
        for allocation in allocations:
            by_ts.setdefault(allocation.leg.transaction.timestamp, []).append(allocation.leg)
        legs = by_ts[sorted(by_ts)[0]]
    return classify_structure(legs)


_DAYS_PER_YEAR = Decimal(365)


# -- shapes ---------------------------------------------------------------------


@dataclass(frozen=True)
class OpenLeg:
    instrument: Instrument
    right: str  # "C" | "P"
    side: str  # "short" | "long"
    strike: Decimal
    expiry: datetime.date
    contracts: Decimal  # absolute
    quote: PriceQuote | None


@dataclass(frozen=True)
class RollSegment:
    opened_on: datetime.date
    closed_on: datetime.date
    initial_premium: Decimal
    realized_pnl: Decimal


@dataclass(frozen=True)
class OpenStrategy:
    trade: Trade
    strategy: str | None
    underlying: Instrument | None
    contracts: int
    expiration: datetime.date | None
    opened_on: datetime.date | None
    legs: "list[OpenLeg]"
    rolls: "list[RollSegment]"
    initial_premium: Decimal
    premium_incl_rolls: Decimal
    market_value: Decimal | None
    net_value: Decimal | None  # signed liquidation value of the option legs
    unrealized_pnl: Decimal | None
    pnl_pct: Decimal | None
    pnl_pct_incl_rolls: Decimal | None  # (unrealized + realized rolls) / premium incl. rolls
    pnl_incl_rolls: Decimal | None  # absolute: unrealized + realized rolls
    delta_pct: Decimal | None
    extrinsic_value: Decimal | None  # Σ signed qty × leg extrinsic × multiplier
    moneyness: str | None  # "ITM" | "OTM"
    moneyness_pct: Decimal | None
    margin_estimate: Decimal
    aroi_initial: Decimal | None
    aroi_now: Decimal | None
    unpriced: "list[Instrument]"


@dataclass(frozen=True)
class ClosedLeg:
    instrument: Instrument
    right: str
    side: str  # initial side
    strike: Decimal
    contracts: Decimal
    open_price: Decimal | None  # derivable only from single-instrument fills
    close_price: Decimal | None
    closed_on: datetime.date | None
    status: str = "closed"  # "closed" | "expired" | "assigned"
    fees: Decimal = Decimal(0)  # transaction fees pro-rated across touched legs


@dataclass(frozen=True)
class ClosedStrategy:
    trade: Trade
    strategy: str | None
    underlying: Instrument | None
    contracts: int
    expiration: datetime.date | None
    opened_on: datetime.date | None
    closed_on: datetime.date | None
    initial_premium: Decimal
    realized_pnl: Decimal  # option cash flows, before fees
    fees: Decimal
    assigned: bool  # shares moved inside this trade (exercise/assignment)
    legs: "list[ClosedLeg]"

    @property
    def net_profit(self) -> Decimal:
        return self.realized_pnl - self.fees


@dataclass(frozen=True)
class AccountSummary:
    total_value: Decimal
    options_value: Decimal
    equity_value: Decimal
    cash: Decimal
    options_pnl: Decimal | None
    equity_pnl: Decimal | None
    margin_estimate: Decimal
    contributions: Decimal  # net external cash (pure cash transactions)
    total_return_pct: Decimal | None  # (total − contributions) / contributions
    unpriced: "list[Instrument]"


@dataclass(frozen=True)
class CalendarDay:
    net_premium: Decimal
    events: int
    wins: int
    losses: int


@dataclass(frozen=True)
class PerformanceStats:
    total_profit: Decimal
    fees: Decimal
    wins: int
    losses: int
    win_ratio: Decimal | None
    average_win: Decimal | None
    average_loss: Decimal | None
    largest_win: Decimal | None
    largest_loss: Decimal | None
    strategy_counts: "dict[str, int]"
    monthly_profit: "dict[datetime.date, Decimal]"
    weekly_profit: "dict[datetime.date, Decimal]"  # keyed by ISO week Monday
    daily_cumulative: "list[tuple[datetime.date, Decimal]]"


@dataclass(frozen=True)
class FlowRow:
    underlying: Instrument
    right: str  # "C" | "P" | "mixed"
    outcome: str  # "gain" | "loss"
    realized_pnl: Decimal  # net of fees


@dataclass(frozen=True)
class WheelHistoryRow:
    """One option contract's life inside a wheel campaign."""

    instrument: Instrument
    right: str
    strike: Decimal
    contracts: Decimal
    opened_on: datetime.date
    closed_on: datetime.date | None
    initial_premium: Decimal
    realized_pnl: Decimal | None  # None while still open
    status: str  # "open" | "closed" | "expired" | "assigned"


@dataclass(frozen=True)
class WheelCampaign:
    trade: Trade
    underlying: Instrument
    shares: Decimal
    cost_basis: Decimal  # per share
    adjusted_cost: Decimal  # per share, net of option premiums
    adjusted_cost_pct: Decimal | None  # discount vs raw basis (negative = cheaper)
    market_value: Decimal | None
    pnl: Decimal | None  # absolute, vs adjusted basis
    pnl_pct: Decimal | None  # vs adjusted cost
    total_premium: Decimal
    history: "list[WheelHistoryRow]"
    unpriced: "list[Instrument]"


# -- event stream -----------------------------------------------------------------


@dataclass
class _Event:
    when: datetime.datetime
    positions: "dict[int, Decimal]" = field(default_factory=dict)
    cash: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)


def _events(trade: Trade) -> "list[_Event]":
    """The trade's allocation stream grouped by transaction, oldest
    first: per-instrument position deltas + settlement cash + fees."""
    allocations = (
        TradeAllocation.objects.filter(trade_id__in=trade._tree_pks())
        .select_related("leg", "leg__transaction", "leg__instrument")
        .order_by("leg__transaction__timestamp", "leg__transaction_id", "id")
    )
    # Fills sharing an exact timestamp are ONE market event (a combo
    # order booked per leg with per-leg prices); grouping by timestamp
    # keeps the cohort/premium math on the combo, while per-leg prices
    # remain derivable from the underlying single-instrument fills.
    grouped: dict[datetime.datetime, _Event] = {}
    for allocation in allocations:
        tx = allocation.leg.transaction
        event = grouped.setdefault(tx.timestamp, _Event(when=tx.timestamp))
        if allocation.category == "":
            iid = allocation.leg.instrument_id
            event.positions[iid] = event.positions.get(iid, Decimal(0)) + allocation.amount
        elif allocation.category in ("revenue", "cost"):
            event.cash += allocation.amount
        elif allocation.category == "fee":
            event.fees += -allocation.amount  # fees stored as negative cash
    return sorted(grouped.values(), key=lambda event: event.when)


def _option_event_cash(event: _Event, metas: "dict[int, OptionMeta]") -> Decimal:
    """The event's cash attributable to its OPTION legs. Assignment /
    exercise policy (same as Trade.calculate_pnl): when an event both
    closes options and opens a non-option position, the cash is the new
    position's basis (the strike payment), not option P&L."""
    opens_shares = any(iid not in metas and delta != 0 for iid, delta in event.positions.items())
    touches_options = any(iid in metas for iid in event.positions)
    if opens_shares and touches_options:
        return Decimal(0)
    return event.cash


def _transaction_events(trade: Trade) -> "list[_Event]":
    """Like _events, but at raw transaction granularity (no timestamp
    merge) — the source for per-leg price derivation."""
    allocations = (
        TradeAllocation.objects.filter(trade_id__in=trade._tree_pks())
        .select_related("leg", "leg__transaction", "leg__instrument")
        .order_by("leg__transaction__timestamp", "leg__transaction_id", "id")
    )
    grouped: dict[int, _Event] = {}
    for allocation in allocations:
        tx = allocation.leg.transaction
        event = grouped.setdefault(tx.pk, _Event(when=tx.timestamp))
        if allocation.category == "":
            iid = allocation.leg.instrument_id
            event.positions[iid] = event.positions.get(iid, Decimal(0)) + allocation.amount
        elif allocation.category in ("revenue", "cost"):
            event.cash += allocation.amount
        elif allocation.category == "fee":
            event.fees += -allocation.amount
    return sorted(grouped.values(), key=lambda event: event.when)


def _option_metas(instrument_ids: "list[int]") -> "dict[int, OptionMeta]":
    return (
        OptionMeta.objects.filter(instrument_id__in=instrument_ids)
        .select_related("underlying", "instrument")
        .in_bulk(field_name="instrument_id")
    )


@dataclass
class _Cohort:
    opened_on: datetime.date
    open_cash: Decimal
    total_cash: Decimal
    instruments: "set[int]"
    closed_on: datetime.date | None = None


def _cohorts(trade: Trade, option_ids: "set[int]") -> "tuple[list[_Cohort], _Cohort | None]":
    """Partition the trade's option lifecycle into cohorts. Returns
    (closed cohorts in close order, the live cohort or None)."""
    positions: dict[int, Decimal] = defaultdict(Decimal)
    membership: dict[int, _Cohort] = {}
    closed: list[_Cohort] = []
    for event in _events(trade):
        touched = [iid for iid in event.positions if iid in option_ids]
        if not touched:
            continue
        cohorts = {id(membership[iid]): membership[iid] for iid in touched if iid in membership}
        opening = [iid for iid in touched if iid not in membership]
        cohort: _Cohort
        if not cohorts:
            cohort = _Cohort(
                opened_on=event.when.date(),
                open_cash=event.cash + event.fees * 0,
                total_cash=Decimal(0),
                instruments=set(),
            )
        elif len(cohorts) == 1:
            cohort = next(iter(cohorts.values()))
        else:  # a fill spanning cohorts merges them: one continuing segment
            merged = sorted(cohorts.values(), key=lambda c: c.opened_on)
            cohort = merged[0]
            for other in merged[1:]:
                cohort.instruments |= other.instruments
                cohort.open_cash += other.open_cash
                cohort.total_cash += other.total_cash
                for iid in other.instruments:
                    membership[iid] = cohort
        for iid in opening:
            cohort.instruments.add(iid)
            membership[iid] = cohort
        cohort.total_cash += event.cash
        for iid in touched:
            positions[iid] += event.positions[iid]
        if all(positions[iid] == 0 for iid in cohort.instruments):
            closed.append(
                _Cohort(
                    opened_on=cohort.opened_on,
                    open_cash=cohort.open_cash,
                    total_cash=cohort.total_cash,
                    instruments=set(cohort.instruments),
                    closed_on=event.when.date(),
                )
            )
            for iid in cohort.instruments:
                membership.pop(iid, None)
    live = next(iter({id(c): c for c in membership.values()}.values()), None)
    return closed, live


def roll_segments(trade: Trade) -> "list[RollSegment]":
    """Fully-closed cohorts of an option trade, as roll history rows:
    each closed cohort's opening premium and the realized cash over its
    life. The currently-open cohort is not a segment."""
    option_ids = {
        meta.instrument_id
        for meta in _option_metas([inst.pk for inst in trade.tracked_instruments()]).values()
    }
    closed, _live = _cohorts(trade, option_ids)
    return [
        RollSegment(
            opened_on=cohort.opened_on,
            closed_on=cohort.closed_on or cohort.opened_on,
            initial_premium=cohort.open_cash,
            realized_pnl=cohort.total_cash,
        )
        for cohort in closed
    ]


# -- margin ------------------------------------------------------------------------


def _margin_estimate(strategy: str | None, legs: "list[OpenLeg]", premium: Decimal) -> Decimal:
    """Display-grade margin estimate (documented in the module header)."""
    shorts = [leg for leg in legs if leg.side == "short"]
    longs = [leg for leg in legs if leg.side == "long"]
    if strategy in ("covered_call", "covered_put", "collar"):
        return Decimal(0)
    if not shorts:  # long/debit structures risk the premium paid
        return abs(premium)
    total = Decimal(0)
    for short in shorts:
        multiplier = short.instrument.multiplier
        protection = [
            leg for leg in longs if leg.right == short.right and leg.contracts >= short.contracts
        ]
        if protection:
            width = min(abs(short.strike - leg.strike) for leg in protection)
            total += width * short.contracts * multiplier
        elif short.right == "P":  # cash-secured put
            total += short.strike * short.contracts * multiplier
        else:  # naked call estimate: 20% of strike notional
            total += short.strike * short.contracts * multiplier * Decimal("0.2")
    if strategy == "iron_condor" and len(shorts) == 2:
        # one side's width collateralizes both wings
        sides = []
        for short in shorts:
            protection = [leg for leg in longs if leg.right == short.right]
            if protection:
                width = min(abs(short.strike - leg.strike) for leg in protection)
                sides.append(width * short.contracts * short.instrument.multiplier)
        if len(sides) == 2:
            return max(sides)
    return total


# -- open strategies ------------------------------------------------------------------


def _classify_option_opening(trade: Trade, metas: "dict[int, OptionMeta]") -> str | None:
    """The strategy of the trade's OPTION structure at its option
    opening event — the history label for trades whose whole-trade
    classification is dominated by later share legs (assignments)."""
    from django_assets.trades.detection import classify_structure

    allocations = list(
        trade.allocations.filter(category="", leg__instrument_id__in=list(metas)).select_related(
            "leg__instrument", "leg__transaction"
        )
    )
    if not allocations:
        return None
    by_ts: dict[datetime.datetime, list[Any]] = {}
    for allocation in allocations:
        by_ts.setdefault(allocation.leg.transaction.timestamp, []).append(allocation.leg)
    return classify_structure(by_ts[sorted(by_ts)[0]])


def _strategy_tag(trade: Trade) -> str | None:
    tags = trade.get_tags_by_category().get(STRATEGY_CATEGORY)
    return tags[0] if tags else None


def _trades_with_options(
    user: Any,
) -> "list[tuple[Trade, dict[int, Decimal], dict[int, OptionMeta]]]":
    out = []
    for trade in Trade.objects.filter(user=user).order_by("id"):
        instruments = trade.tracked_instruments()
        metas = _option_metas([inst.pk for inst in instruments])
        if not metas:
            continue
        net = {inst.pk: trade.net_position(inst) for inst in instruments}
        out.append((trade, net, metas))
    return out


def open_option_strategies(user: Any, price_source: PriceSource) -> "list[OpenStrategy]":
    """One row per confirmed trade with a live option position — the
    dashboard's positions table, greeks panel, and roll history."""
    candidates = [
        (trade, net, metas)
        for trade, net, metas in _trades_with_options(user)
        if any(net.get(iid, Decimal(0)) != 0 for iid in metas)
    ]
    all_instruments: dict[int, Instrument] = {}
    for _trade, net, metas in candidates:
        for iid, meta in metas.items():
            if net.get(iid, Decimal(0)) != 0:
                all_instruments[iid] = meta.instrument
                all_instruments[meta.underlying_id] = meta.underlying
    quotes = price_source.get_quotes(list(all_instruments.values()))
    by_id = {inst.pk: quotes.get(inst) for inst in all_instruments.values()}

    now = timezone.now()
    rows: list[OpenStrategy] = []
    for trade, net, metas in candidates:
        legs: list[OpenLeg] = []
        unpriced: list[Instrument] = []
        for iid, meta in sorted(metas.items(), key=lambda kv: (kv[1].expiry, kv[1].strike)):
            qty = net.get(iid, Decimal(0))
            if qty == 0:
                continue
            quote = by_id.get(iid)
            if quote is None:
                unpriced.append(meta.instrument)
            legs.append(
                OpenLeg(
                    instrument=meta.instrument,
                    right=meta.right,
                    side="short" if qty < 0 else "long",
                    strike=Decimal(meta.strike),
                    expiry=meta.expiry,
                    contracts=abs(qty),
                    quote=quote,
                )
            )
        option_ids = set(metas.keys())
        closed_cohorts, live = _cohorts(trade, option_ids)
        rolls = [
            RollSegment(
                opened_on=cohort.opened_on,
                closed_on=cohort.closed_on or cohort.opened_on,
                initial_premium=cohort.open_cash,
                realized_pnl=cohort.total_cash,
            )
            for cohort in closed_cohorts
        ]
        initial_premium = live.open_cash if live else Decimal(0)
        premium_incl_rolls = initial_premium + sum(
            (segment.realized_pnl for segment in rolls), Decimal(0)
        )
        opened_on = live.opened_on if live else trade.open_date and trade.open_date.date()

        priced = not unpriced
        net_value: Decimal | None = None
        if priced:
            net_value = Decimal(0)
            for leg in legs:
                assert leg.quote is not None  # priced ⇒ every leg has a quote
                qty = net[leg.instrument.pk]
                net_value += leg.instrument.quantize_price(
                    qty * leg.quote.price * leg.instrument.multiplier
                )
        market_value = abs(net_value) if net_value is not None else None

        # Option-side unrealized: the live cohort's premium flows plus the
        # (signed) cost of closing it now. Share legs riding in the same
        # trade (covered structures) deliberately stay out — the dashboard's
        # PnL%% column is an option-side number.
        live_cash = live.total_cash if live else Decimal(0)
        unrealized = live_cash + net_value if net_value is not None else None
        pnl_pct = (
            unrealized / abs(initial_premium)
            if unrealized is not None and initial_premium
            else None
        )
        realized_rolls_total = sum((segment.realized_pnl for segment in rolls), Decimal(0))
        pnl_incl_rolls = unrealized + realized_rolls_total if unrealized is not None else None
        pnl_pct_incl_rolls = (
            (unrealized + realized_rolls_total) / abs(premium_incl_rolls)
            if unrealized is not None and premium_incl_rolls
            else None
        )

        primary: OpenLeg | None = None

        def leg_delta(leg: OpenLeg) -> Decimal | None:
            return leg.quote.delta if isinstance(leg.quote, OptionQuote) else None

        deltas = [leg for leg in legs if leg_delta(leg) is not None]
        if deltas:
            primary = max(deltas, key=lambda leg: abs(leg_delta(leg) or Decimal(0)))
        elif legs:
            primary = next((leg for leg in legs if leg.side == "short"), legs[0])
        delta_pct = (
            abs(primary.quote.delta)
            if primary is not None
            and isinstance(primary.quote, OptionQuote)
            and primary.quote.delta is not None
            else None
        )

        extrinsic_acc = Decimal(0)
        extrinsic_missing = not legs
        for leg in legs:
            quote = leg.quote
            if not isinstance(quote, OptionQuote) or quote.extrinsic_value is None:
                extrinsic_missing = True
                break
            qty = net[leg.instrument.pk]
            extrinsic_acc = extrinsic_acc + leg.instrument.quantize_price(
                qty * quote.extrinsic_value * leg.instrument.multiplier
            )
        extrinsic_total = None if extrinsic_missing else extrinsic_acc

        moneyness = moneyness_pct = None
        if primary is not None:
            underlying_price = None
            if isinstance(primary.quote, OptionQuote):
                underlying_price = primary.quote.underlying_price
            if underlying_price is None:
                meta = metas[primary.instrument.pk]
                underlying_quote = by_id.get(meta.underlying_id)
                underlying_price = underlying_quote.price if underlying_quote else None
            if underlying_price:
                moneyness_pct = abs(primary.strike - underlying_price) / underlying_price
                in_the_money = (
                    underlying_price < primary.strike
                    if primary.right == "P"
                    else underlying_price > primary.strike
                )
                moneyness = "ITM" if in_the_money else "OTM"

        strategy = _strategy_tag(trade)
        margin = _margin_estimate(strategy, legs, initial_premium)
        expiration = min((leg.expiry for leg in legs), default=None)
        aroi_initial = aroi_now = None
        if margin > 0 and opened_on and expiration:
            horizon = (expiration - opened_on).days
            if horizon > 0:
                aroi_initial = initial_premium / margin * _DAYS_PER_YEAR / horizon
            elapsed = max((now.date() - opened_on).days, 1)
            realized_rolls = sum((segment.realized_pnl for segment in rolls), Decimal(0))
            if unrealized is not None:
                aroi_now = (unrealized + realized_rolls) / margin * _DAYS_PER_YEAR / elapsed

        meta_of_primary = metas[primary.instrument.pk] if primary else next(iter(metas.values()))
        rows.append(
            OpenStrategy(
                trade=trade,
                strategy=strategy,
                underlying=meta_of_primary.underlying,
                contracts=int(max((leg.contracts for leg in legs), default=0)),
                expiration=expiration,
                opened_on=opened_on,
                legs=legs,
                rolls=rolls,
                initial_premium=initial_premium,
                premium_incl_rolls=premium_incl_rolls,
                market_value=market_value,
                net_value=net_value,
                unrealized_pnl=unrealized,
                pnl_pct=pnl_pct,
                pnl_pct_incl_rolls=pnl_pct_incl_rolls,
                pnl_incl_rolls=pnl_incl_rolls,
                delta_pct=delta_pct,
                extrinsic_value=extrinsic_total,
                moneyness=moneyness,
                moneyness_pct=moneyness_pct,
                margin_estimate=margin,
                aroi_initial=aroi_initial,
                aroi_now=aroi_now,
                unpriced=unpriced,
            )
        )
    return rows


# -- closed strategies -------------------------------------------------------------------


def closed_option_strategies(user: Any) -> "list[ClosedStrategy]":
    """History rows: option trades whose contracts have all gone to
    zero. Per-leg open/close prices derive only from fills that touched
    a single option instrument (the ledger stores net cash, not per-leg
    prices) — otherwise honestly None."""
    rows: list[ClosedStrategy] = []
    for trade, net, metas in _trades_with_options(user):
        if any(net.get(iid, Decimal(0)) != 0 for iid in metas):
            continue
        events = _events(trade)
        positions: dict[int, Decimal] = defaultdict(Decimal)
        first_side: dict[int, str] = {}
        peak: dict[int, Decimal] = defaultdict(Decimal)
        open_price: dict[int, Decimal | None] = {}
        close_price: dict[int, Decimal | None] = {}
        leg_fees: dict[int, Decimal] = defaultdict(Decimal)
        # Per-transaction (not merged-event) single-instrument fills give
        # per-leg prices even when a combo is booked as per-leg fills.
        tx_prices: dict[tuple[int, bool], Decimal] = {}
        leg_status: dict[int, str] = {}
        tx_walk: dict[int, Decimal] = defaultdict(Decimal)
        for tx_event in _transaction_events(trade):
            touched_tx = [iid for iid in tx_event.positions if iid in metas]
            moves_shares = any(
                iid not in metas and delta != 0 for iid, delta in tx_event.positions.items()
            )
            if len(touched_tx) == 1 and tx_event.positions[touched_tx[0]] != 0 and not moves_shares:
                iid = touched_tx[0]
                meta = metas[iid]
                per = meta.instrument.quantize_price(
                    abs(tx_event.cash) / abs(tx_event.positions[iid]) / meta.instrument.multiplier
                )
                before_walk = tx_walk[iid]
                delta = tx_event.positions[iid]
                opening_fill = before_walk == 0 or (delta > 0) == (before_walk > 0)
                key = (iid, opening_fill)
                tx_prices.setdefault(key, per)
                if before_walk + delta == 0:
                    tx_prices[(iid, False)] = per
            for iid_w, amt in tx_event.positions.items():
                if iid_w in metas:
                    before_w = tx_walk[iid_w]
                    tx_walk[iid_w] += amt
                    if before_w != 0 and tx_walk[iid_w] == 0:
                        # How did this leg end? Shares moved → assigned/
                        # exercised; zero-cash close on/after expiry →
                        # expired; else an ordinary closing fill.
                        if moves_shares:
                            leg_status[iid_w] = "assigned"
                        elif tx_event.cash == 0 and tx_event.when.date() >= metas[iid_w].expiry:
                            leg_status[iid_w] = "expired"
                        else:
                            leg_status[iid_w] = "closed"
        closed_on_by_iid: dict[int, datetime.date] = {}
        opened_on: datetime.date | None = None
        closed_on: datetime.date | None = None
        initial_premium = Decimal(0)
        realized = Decimal(0)
        seen_option_event = False
        for event in events:
            touched = [iid for iid in event.positions if iid in metas]
            if not touched:
                continue
            # Per-leg prices come from _transaction_events/tx_prices above;
            # the event walk only tracks positions, cash, fees, and status.
            option_cash = _option_event_cash(event, metas)
            if not seen_option_event:
                opened_on = event.when.date()
                initial_premium = option_cash
                seen_option_event = True
            realized += option_cash
            for iid in touched:
                leg_fees[iid] += event.fees / len(touched)
            for iid in touched:
                before = positions[iid]
                positions[iid] += event.positions[iid]
                if before == 0 and positions[iid] != 0:
                    first_side.setdefault(iid, "short" if positions[iid] < 0 else "long")
                    open_price.setdefault(iid, tx_prices.get((iid, True)))
                peak[iid] = max(peak[iid], abs(positions[iid]))
                if positions[iid] == 0 and before != 0:
                    closed_on_by_iid[iid] = event.when.date()
                    close_price[iid] = tx_prices.get((iid, False))
            if all(positions[iid] == 0 for iid in metas):
                closed_on = event.when.date()
        if not seen_option_event:
            continue

        legs = [
            ClosedLeg(
                instrument=meta.instrument,
                right=meta.right,
                side=first_side.get(iid, "long"),
                strike=Decimal(meta.strike),
                contracts=peak[iid],
                open_price=open_price.get(iid),
                close_price=close_price.get(iid),
                closed_on=closed_on_by_iid.get(iid),
                status=leg_status.get(iid, "closed"),
                fees=leg_fees[iid].quantize(Decimal("0.01")),
            )
            for iid, meta in sorted(metas.items(), key=lambda kv: (kv[1].expiry, kv[1].strike))
            if peak[iid] > 0
        ]
        raw_fees = trade.get_summary()["fees"]
        summary_fees = raw_fees if isinstance(raw_fees, Decimal) else Decimal(0)
        share_moved = any(
            iid not in metas and delta != 0
            for event in events
            for iid, delta in event.positions.items()
        )
        tag = _strategy_tag(trade)
        if tag in (None, "stock"):
            tag = _classify_option_opening(trade, metas) or tag
        rows.append(
            ClosedStrategy(
                trade=trade,
                strategy=tag,
                underlying=next(iter(metas.values())).underlying,
                contracts=int(max((leg.contracts for leg in legs), default=0)),
                expiration=max((meta.expiry for meta in metas.values()), default=None),
                opened_on=opened_on,
                closed_on=closed_on,
                initial_premium=initial_premium,
                realized_pnl=realized,
                fees=-summary_fees if summary_fees < 0 else summary_fees,
                assigned=share_moved,
                legs=legs,
            )
        )
    rows.sort(key=lambda row: row.closed_on or datetime.date.min, reverse=True)
    return rows


# -- account summary --------------------------------------------------------------------


def _equity_unrealized(
    positions: "dict[Instrument, Decimal]",
    basis: "dict[Instrument, Decimal]",
    quotes: "dict[Instrument, PriceQuote | None]",
) -> Decimal | None:
    total = Decimal(0)
    for instrument, qty in positions.items():
        quote = quotes.get(instrument)
        if quote is None:
            return None
        market = instrument.quantize_price(qty * quote.price * instrument.multiplier)
        total += market - basis.get(instrument, Decimal(0))
    return total


def account_summary(
    user: Any, price_source: PriceSource, *, accounts: "list[Any] | None" = None
) -> AccountSummary:
    """The always-visible summary card: cash / equity / options values,
    open-position P&L, estimated margin. `accounts` are the USER-side
    accounts (counterparty purpose accounts are the host's naming
    convention — ADR-0035 — so the caller says which side is theirs);
    default: every account owned by the user."""
    from django_assets.core.models import Account, TransactionLeg

    if accounts is None:
        accounts = list(Account.objects.filter(owner=user))
    account_ids = [account.pk for account in accounts]

    legs = (
        TransactionLeg.objects.filter(account_id__in=account_ids)
        .select_related("instrument", "transaction")
        .order_by("transaction__timestamp", "id")
    )
    positions: dict[Instrument, Decimal] = defaultdict(Decimal)
    cash = Decimal(0)

    tx_cash: dict[int, Decimal] = defaultdict(Decimal)
    tx_equity: dict[int, list[tuple[Instrument, Decimal]]] = defaultdict(list)
    ordered_txs: list[int] = []
    for leg in legs:
        instrument = leg.instrument
        if instrument.price_currency_id is None:
            cash += leg.amount
            tx_cash[leg.transaction_id] += leg.amount
        else:
            positions[instrument] += leg.amount
            if leg.transaction_id not in ordered_txs:
                ordered_txs.append(leg.transaction_id)
            tx_equity[leg.transaction_id].append((instrument, leg.amount))
    # Pure cash transactions (no position legs on the user side) are
    # external contributions: deposits and withdrawals.
    contributions = sum(
        (amount for tx_id, amount in tx_cash.items() if not tx_equity.get(tx_id)),
        Decimal(0),
    )

    option_flags = _option_metas([inst.pk for inst in positions])
    option_positions = {
        inst: qty for inst, qty in positions.items() if qty != 0 and inst.pk in option_flags
    }
    equity_positions = {
        inst: qty for inst, qty in positions.items() if qty != 0 and inst.pk not in option_flags
    }

    # Average-cost basis walk for equities (per transaction, cash pro-rated
    # across that transaction's equity deltas by absolute notional share).
    walk_positions: dict[Instrument, Decimal] = defaultdict(Decimal)
    walk_basis: dict[Instrument, Decimal] = defaultdict(Decimal)
    for tx_id in ordered_txs:
        deltas = [(inst, amt) for inst, amt in tx_equity[tx_id] if inst.pk not in option_flags]
        if not deltas:
            continue
        share = tx_cash[tx_id] / len(deltas)
        for inst, amount in deltas:
            before = walk_positions[inst]
            if before == 0 or (amount > 0) == (before > 0):  # opening
                walk_positions[inst] = before + amount
                walk_basis[inst] += -share
            else:  # closing releases proportional basis
                closing = min(abs(amount), abs(before))
                if before != 0:
                    walk_basis[inst] -= walk_basis[inst] * closing / abs(before)
                walk_positions[inst] = before + amount

    instruments = list(option_positions) + list(equity_positions)
    quotes = price_source.get_quotes(instruments) if instruments else {}
    unpriced = [inst for inst in instruments if quotes.get(inst) is None]

    def value_of(book: "dict[Instrument, Decimal]") -> Decimal:
        total = Decimal(0)
        for inst, qty in book.items():
            quote = quotes.get(inst)
            if quote is not None:
                total += inst.quantize_price(qty * quote.price * inst.multiplier)
        return total

    options_value = value_of(option_positions)
    equity_value = value_of(equity_positions)

    open_rows = open_option_strategies(user, price_source)
    options_pnl: Decimal | None = Decimal(0)
    for row in open_rows:
        if row.unrealized_pnl is None or options_pnl is None:
            options_pnl = None
            break
        options_pnl = options_pnl + row.unrealized_pnl
    margin = sum((row.margin_estimate for row in open_rows), Decimal(0))

    equity_pnl = (
        _equity_unrealized(equity_positions, dict(walk_basis), quotes)
        if equity_positions
        else Decimal(0)
    )

    total_value = cash + equity_value + options_value
    return AccountSummary(
        total_value=total_value,
        options_value=options_value,
        equity_value=equity_value,
        cash=cash,
        options_pnl=options_pnl,
        equity_pnl=equity_pnl,
        margin_estimate=margin,
        contributions=contributions,
        total_return_pct=((total_value - contributions) / contributions if contributions else None),
        unpriced=unpriced,
    )


# -- calendar / performance / flow ----------------------------------------------------------


def premium_calendar(
    user: Any, year: int, month: int, *, underlyings: "list[str] | None" = None
) -> "dict[datetime.date, CalendarDay]":
    """Per-day option cash flow (premium received minus paid, net of
    fees) with event counts, plus win/loss counts of trades that CLOSED
    that day. `underlyings` filters by underlying ticker code."""
    wanted = {code.upper() for code in underlyings} if underlyings else None

    def keep(metas: "dict[int, OptionMeta]") -> bool:
        if wanted is None:
            return True
        return any(meta.underlying.code.upper() in wanted for meta in metas.values())

    days: dict[datetime.date, dict[str, Any]] = defaultdict(
        lambda: {"net": Decimal(0), "events": 0, "wins": 0, "losses": 0}
    )
    for trade, _net, metas in _trades_with_options(user):
        if not keep(metas):
            continue
        for event in _transaction_events(trade):
            if not any(iid in metas for iid in event.positions):
                continue
            when = event.when.date()
            if (when.year, when.month) != (year, month):
                continue
            days[when]["net"] += _option_event_cash(event, metas) - event.fees
            days[when]["events"] += 1
    for row in closed_option_strategies(user):
        if wanted is not None and (
            row.underlying is None or row.underlying.code.upper() not in wanted
        ):
            continue
        closed_when = row.closed_on
        if closed_when is None or (closed_when.year, closed_when.month) != (year, month):
            continue
        key = "wins" if row.net_profit > 0 else "losses"
        days[closed_when][key] += 1
    return {
        day: CalendarDay(
            net_premium=data["net"], events=data["events"], wins=data["wins"], losses=data["losses"]
        )
        for day, data in days.items()
    }


def strategy_performance(
    user: Any,
    *,
    strategies: "list[str] | None" = None,
    underlyings: "list[str] | None" = None,
    start: datetime.date | None = None,
    end: datetime.date | None = None,
) -> PerformanceStats:
    """Aggregates over CLOSED option strategies (finalized trades only)."""
    rows = closed_option_strategies(user)
    if strategies:
        rows = [row for row in rows if row.strategy in strategies]
    if underlyings:
        wanted = {code.upper() for code in underlyings}
        rows = [row for row in rows if row.underlying and row.underlying.code.upper() in wanted]
    if start:
        rows = [row for row in rows if row.closed_on and row.closed_on >= start]
    if end:
        rows = [row for row in rows if row.closed_on and row.closed_on <= end]

    profits = [row.net_profit for row in rows]
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p <= 0]
    monthly: dict[datetime.date, Decimal] = defaultdict(Decimal)
    weekly: dict[datetime.date, Decimal] = defaultdict(Decimal)
    daily: dict[datetime.date, Decimal] = defaultdict(Decimal)
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        if row.closed_on:
            monthly[row.closed_on.replace(day=1)] += row.net_profit
            week_monday = row.closed_on - datetime.timedelta(days=row.closed_on.weekday())
            weekly[week_monday] += row.net_profit
            daily[row.closed_on] += row.net_profit
        counts[row.strategy or "mixed"] += 1

    cumulative: list[tuple[datetime.date, Decimal]] = []
    running = Decimal(0)
    for day in sorted(daily):
        running += daily[day]
        cumulative.append((day, running))

    def avg(values: "list[Decimal]") -> Decimal | None:
        return sum(values, Decimal(0)) / len(values) if values else None

    total = sum(profits, Decimal(0))
    return PerformanceStats(
        total_profit=total,
        fees=sum((row.fees for row in rows), Decimal(0)),
        wins=len(wins),
        losses=len(losses),
        win_ratio=Decimal(len(wins)) / len(profits) if profits else None,
        average_win=avg(wins),
        average_loss=avg(losses),
        largest_win=max(wins) if wins else None,
        largest_loss=min(losses) if losses else None,
        strategy_counts=dict(counts),
        monthly_profit=dict(monthly),
        weekly_profit=dict(weekly),
        daily_cumulative=cumulative,
    )


def pnl_flow(
    user: Any,
    *,
    strategies: "list[str] | None" = None,
    underlyings_filter: "list[str] | None" = None,
    start: datetime.date | None = None,
    end: datetime.date | None = None,
) -> "list[FlowRow]":
    """Realized P&L flow rows aggregated by (underlying, right, outcome)
    over finalized trades — the sankey's edges."""
    wanted = {code.upper() for code in underlyings_filter} if underlyings_filter else None
    buckets: dict[tuple[int, str, str], Decimal] = defaultdict(Decimal)
    underlyings: dict[int, Instrument] = {}
    for row in closed_option_strategies(user):
        if row.underlying is None:
            continue
        if strategies and row.strategy not in strategies:
            continue
        if wanted is not None and row.underlying.code.upper() not in wanted:
            continue
        if start and (row.closed_on is None or row.closed_on < start):
            continue
        if end and (row.closed_on is None or row.closed_on > end):
            continue
        rights = {leg.right for leg in row.legs}
        right = rights.pop() if len(rights) == 1 else "mixed"
        outcome = "gain" if row.net_profit > 0 else "loss"
        buckets[(row.underlying.pk, right, outcome)] += row.net_profit
        underlyings[row.underlying.pk] = row.underlying
    return [
        FlowRow(underlying=underlyings[uid], right=right, outcome=outcome, realized_pnl=amount)
        for (uid, right, outcome), amount in sorted(buckets.items(), key=lambda kv: kv[0])
    ]


@dataclass(frozen=True)
class Assignment:
    """One exercise/assignment event: shares delivered against a strike."""

    trade: Trade
    underlying: Instrument
    shares: Decimal
    strike: Decimal
    right: str
    assigned_on: datetime.date


def assignments(user: Any) -> "list[Assignment]":
    """Share deliveries from option exercise/assignment, newest first."""
    rows: list[Assignment] = []
    for trade, _net, metas in _trades_with_options(user):
        for event in _transaction_events(trade):
            option_deltas = {iid: delta for iid, delta in event.positions.items() if iid in metas}
            share_deltas = {
                iid: delta
                for iid, delta in event.positions.items()
                if iid not in metas and delta != 0
            }
            if not option_deltas or not share_deltas:
                continue
            for iid, _delta in option_deltas.items():
                meta = metas[iid]
                for share_iid, share_delta in share_deltas.items():
                    if share_iid != meta.underlying_id:
                        continue
                    rows.append(
                        Assignment(
                            trade=trade,
                            underlying=meta.underlying,
                            shares=abs(share_delta),
                            strike=Decimal(meta.strike),
                            right=meta.right,
                            assigned_on=event.when.date(),
                        )
                    )
    rows.sort(key=lambda row: row.assigned_on, reverse=True)
    return rows


# -- wheel ------------------------------------------------------------------------------------


def wheel_campaigns(user: Any, price_source: PriceSource) -> "list[WheelCampaign]":
    """Per-trade share campaigns: trades holding shares whose option
    premiums adjust the effective ('true') cost basis."""
    campaigns: list[WheelCampaign] = []
    for trade in Trade.objects.filter(user=user).order_by("id"):
        instruments = trade.tracked_instruments()
        metas = _option_metas([inst.pk for inst in instruments])
        shares_by_inst = {
            inst: trade.net_position(inst)
            for inst in instruments
            if inst.pk not in metas and inst.price_currency_id is not None
        }
        shares_by_inst = {inst: qty for inst, qty in shares_by_inst.items() if qty > 0}
        if len(shares_by_inst) != 1:
            continue
        underlying, shares = next(iter(shares_by_inst.items()))

        share_cost = Decimal(0)
        premiums = Decimal(0)
        # Per-transaction granularity: distinct fills (buy-write pairs)
        # keep their own cash even when they share a timestamp.
        for event in _transaction_events(trade):
            option_touched = any(iid in metas for iid in event.positions)
            equity_touched = underlying.pk in event.positions
            option_cash = _option_event_cash(event, metas) if option_touched else Decimal(0)
            if equity_touched:
                # assignment policy: the non-option share of the event's
                # cash (the strike payment) is share basis.
                share_cost += -(event.cash - option_cash)
            if option_touched:
                premiums += option_cash
        if shares == 0:
            continue
        history = _wheel_history(trade, metas)
        cost_basis = underlying.quantize_price(share_cost / shares)
        adjusted = underlying.quantize_price((share_cost - premiums) / shares)

        quote = price_source.get_quote(underlying)
        market_value = (
            underlying.quantize_price(shares * quote.price * underlying.multiplier)
            if quote
            else None
        )
        pnl_pct = (quote.price - adjusted) / adjusted if quote and adjusted else None
        adjusted_basis_total = underlying.quantize_price(adjusted * shares)
        campaigns.append(
            WheelCampaign(
                trade=trade,
                underlying=underlying,
                shares=shares,
                cost_basis=cost_basis,
                adjusted_cost=adjusted,
                adjusted_cost_pct=(adjusted - cost_basis) / cost_basis if cost_basis else None,
                market_value=market_value,
                pnl=market_value - adjusted_basis_total if market_value is not None else None,
                pnl_pct=pnl_pct,
                total_premium=premiums,
                history=history,
                unpriced=[] if quote else [underlying],
            )
        )
    campaigns.sort(key=lambda campaign: campaign.underlying.code)
    return campaigns


def _wheel_history(trade: Trade, metas: "dict[int, OptionMeta]") -> "list[WheelHistoryRow]":
    """Per-contract lifecycle rows for a wheel campaign's options."""
    rows: list[WheelHistoryRow] = []
    walk: dict[int, Decimal] = defaultdict(Decimal)
    peak: dict[int, Decimal] = defaultdict(Decimal)
    opened: dict[int, datetime.date] = {}
    closed: dict[int, datetime.date] = {}
    premium: dict[int, Decimal] = defaultdict(Decimal)
    cashflow: dict[int, Decimal] = defaultdict(Decimal)
    status: dict[int, str] = {}
    for event in _transaction_events(trade):
        touched = [iid for iid in event.positions if iid in metas]
        if not touched:
            continue
        moves_shares = any(
            iid not in metas and delta != 0 for iid, delta in event.positions.items()
        )
        option_cash = _option_event_cash(event, metas)
        share = option_cash / len(touched)
        for iid in touched:
            before = walk[iid]
            walk[iid] += event.positions[iid]
            peak[iid] = max(peak[iid], abs(walk[iid]))
            cashflow[iid] += share
            if before == 0 and walk[iid] != 0:
                opened.setdefault(iid, event.when.date())
                premium[iid] += share
                status[iid] = "open"
            elif before != 0 and walk[iid] == 0:
                closed[iid] = event.when.date()
                if moves_shares:
                    status[iid] = "assigned"
                elif event.cash == 0 and event.when.date() >= metas[iid].expiry:
                    status[iid] = "expired"
                else:
                    status[iid] = "closed"
    for iid, meta in sorted(metas.items(), key=lambda kv: (kv[1].expiry, kv[1].strike)):
        if iid not in opened:
            continue
        is_open = status.get(iid) == "open"
        rows.append(
            WheelHistoryRow(
                instrument=meta.instrument,
                right=meta.right,
                strike=Decimal(meta.strike),
                contracts=peak[iid],
                opened_on=opened[iid],
                closed_on=closed.get(iid),
                initial_premium=premium[iid],
                realized_pnl=None if is_open else cashflow[iid],
                status=status.get(iid, "open"),
            )
        )
    return rows


def wheel_total_pnl(campaigns: "list[WheelCampaign]") -> Decimal | None:
    """Σ campaign pnl; None when any campaign is unpriced (honest, not 0)."""
    total = Decimal(0)
    for campaign in campaigns:
        if campaign.pnl is None:
            return None
        total += campaign.pnl
    return total


@dataclass(frozen=True)
class FlowSummary:
    """Aggregated P&L flow: per-node totals for the sankey's columns."""

    rows: "list[FlowRow]"
    total: Decimal
    by_symbol: "dict[Instrument, Decimal]"
    by_right: "dict[str, Decimal]"
    by_outcome: "dict[str, Decimal]"

    def share_of_total(self, amount: Decimal) -> Decimal | None:
        """|amount| as a fraction of Σ|node| within the outcome axis."""
        denominator = sum((abs(value) for value in self.by_outcome.values()), Decimal(0))
        return abs(amount) / denominator if denominator else None


def pnl_flow_summary(
    user: Any,
    *,
    strategies: "list[str] | None" = None,
    underlyings: "list[str] | None" = None,
    start: datetime.date | None = None,
    end: datetime.date | None = None,
) -> FlowSummary:
    rows = pnl_flow(
        user, strategies=strategies, underlyings_filter=underlyings, start=start, end=end
    )
    by_symbol: dict[Instrument, Decimal] = defaultdict(Decimal)
    by_right: dict[str, Decimal] = defaultdict(Decimal)
    by_outcome: dict[str, Decimal] = defaultdict(Decimal)
    total = Decimal(0)
    for row in rows:
        by_symbol[row.underlying] += row.realized_pnl
        by_right[row.right] += row.realized_pnl
        by_outcome[row.outcome] += row.realized_pnl
        total += row.realized_pnl
    return FlowSummary(
        rows=rows,
        total=total,
        by_symbol=dict(by_symbol),
        by_right=dict(by_right),
        by_outcome=dict(by_outcome),
    )


def account_value_series(
    user: Any,
    price_source: PriceSource,
    *,
    accounts: "list[Any]",
    start: datetime.date,
    end: datetime.date,
) -> "list[tuple[datetime.date, Decimal]]":
    """Daily account value (cash + positions at each session's close)
    over [start, end]. Valuation policy, documented: each day marks at
    the most recent close ON OR BEFORE it (marks carry forward across
    days a particular instrument has no close); days before any market
    data exist take positions at their flow value. Days emitted = union
    of the instruments' close sessions plus ledger event days."""
    from django_assets.core.models import TransactionLeg

    account_ids = [account.pk for account in accounts]
    legs = (
        TransactionLeg.objects.filter(account_id__in=account_ids)
        .select_related("instrument", "transaction")
        .order_by("transaction__timestamp", "id")
    )
    cash_delta: dict[datetime.date, Decimal] = defaultdict(Decimal)
    position_delta: dict[datetime.date, dict[Instrument, Decimal]] = defaultdict(
        lambda: defaultdict(Decimal)
    )
    instruments: set[Instrument] = set()
    for leg in legs:
        day = leg.transaction.timestamp.date()
        if day > end:
            continue
        if leg.instrument.price_currency_id is None:
            cash_delta[day] += leg.amount
        else:
            position_delta[day][leg.instrument] += leg.amount
            instruments.add(leg.instrument)

    closes: dict[Instrument, dict[datetime.date, Decimal]] = {}
    for instrument in instruments:
        by_day: dict[datetime.date, Decimal] = {}
        series = price_source.get_ohlcv(instrument, start=start, end=end)
        if series is not None and series.candles:
            for candle in series.candles:
                by_day[candle.session] = candle.close
        else:  # no bar archive (options): dated closes, one ask per day
            caps = price_source.capabilities(instrument)
            bound = caps.closes if caps else None
            if bound is not None:
                day = max(start, bound.min)
                last = min(end, bound.max)
                while day <= last:
                    quote = price_source.get_close(instrument, day)
                    if quote is not None:
                        by_day[day] = quote.price
                    day += datetime.timedelta(days=1)
        closes[instrument] = by_day

    session_days = sorted(
        {day for by_day in closes.values() for day in by_day}
        | {day for day in cash_delta if start <= day <= end}
        | {day for day in position_delta if start <= day <= end}
    )
    # Roll state forward from the beginning of the ledger to `start`.
    cash = sum((amount for day, amount in cash_delta.items() if day < start), Decimal(0))
    positions: dict[Instrument, Decimal] = defaultdict(Decimal)
    for day in sorted(d for d in position_delta if d < start):
        for instrument, amount in position_delta[day].items():
            positions[instrument] += amount
    last_mark: dict[Instrument, Decimal] = {}

    out: list[tuple[datetime.date, Decimal]] = []
    for day in session_days:
        if day < start:
            continue
        cash += cash_delta.get(day, Decimal(0))
        for instrument, amount in position_delta.get(day, {}).items():
            positions[instrument] += amount
        total = cash
        for instrument, qty in positions.items():
            if qty == 0:
                continue
            mark = closes[instrument].get(day)
            if mark is not None:
                last_mark[instrument] = mark
            carried = last_mark.get(instrument)
            if carried is not None:
                total += instrument.quantize_price(qty * carried * instrument.multiplier)
        out.append((day, total))
    return out


@dataclass(frozen=True)
class EquityHolding:
    instrument: Instrument
    shares: Decimal
    cost_basis: Decimal | None  # per share, average cost
    market_value: Decimal | None
    pnl: Decimal | None  # absolute, vs total basis
    pnl_pct: Decimal | None  # vs cost basis


def equity_holdings(
    user: Any, price_source: PriceSource, *, accounts: "list[Any]"
) -> "list[EquityHolding]":
    """Every stock/ETF position across the user-side accounts, with an
    average-cost basis walk (assignment strike cash counts as basis)."""
    from django_assets.core.models import TransactionLeg

    account_ids = [account.pk for account in accounts]
    legs = (
        TransactionLeg.objects.filter(account_id__in=account_ids)
        .select_related("instrument", "transaction")
        .order_by("transaction__timestamp", "id")
    )
    tx_cash: dict[int, Decimal] = defaultdict(Decimal)
    tx_positions: dict[int, list[tuple[Instrument, Decimal]]] = defaultdict(list)
    order: list[int] = []
    for leg in legs:
        if leg.instrument.price_currency_id is None:
            tx_cash[leg.transaction_id] += leg.amount
        else:
            if leg.transaction_id not in order:
                order.append(leg.transaction_id)
            tx_positions[leg.transaction_id].append((leg.instrument, leg.amount))

    option_flags = _option_metas(
        [inst.pk for deltas in tx_positions.values() for inst, _amt in deltas]
    )
    positions: dict[Instrument, Decimal] = defaultdict(Decimal)
    basis: dict[Instrument, Decimal] = defaultdict(Decimal)
    for tx_id in order:
        equity_deltas = [
            (inst, amt) for inst, amt in tx_positions[tx_id] if inst.pk not in option_flags
        ]
        if not equity_deltas:
            continue
        share = tx_cash[tx_id] / len(equity_deltas)
        for inst, amount in equity_deltas:
            before = positions[inst]
            if before == 0 or (amount > 0) == (before > 0):
                positions[inst] = before + amount
                basis[inst] += -share
            else:
                closing = min(abs(amount), abs(before))
                if before != 0:
                    basis[inst] -= basis[inst] * closing / abs(before)
                positions[inst] = before + amount

    held = {inst: qty for inst, qty in positions.items() if qty != 0}
    quotes = price_source.get_quotes(list(held)) if held else {}
    rows: list[EquityHolding] = []
    for inst, qty in held.items():
        per_share = inst.quantize_price(basis[inst] / qty) if qty else None
        quote = quotes.get(inst)
        market_value = inst.quantize_price(qty * quote.price * inst.multiplier) if quote else None
        pnl_pct = (quote.price - per_share) / per_share if quote is not None and per_share else None
        basis_total = inst.quantize_price(per_share * qty) if per_share is not None else None
        rows.append(
            EquityHolding(
                instrument=inst,
                shares=qty,
                cost_basis=per_share,
                market_value=market_value,
                pnl=(
                    market_value - basis_total
                    if market_value is not None and basis_total is not None
                    else None
                ),
                pnl_pct=pnl_pct,
            )
        )
    rows.sort(key=lambda row: row.instrument.code)
    return rows


@dataclass(frozen=True)
class MonthDetail:
    """The calendar's month-detail dialog: realized-PnL breakdown for one
    month with the prior month for comparison, over CLOSED strategies."""

    year: int
    month: int
    total_pnl: Decimal
    previous_pnl: Decimal
    wins: int
    losses: int
    win_ratio: Decimal | None
    overall_win_ratio: Decimal | None
    avg_daily_pnl: Decimal | None
    previous_avg_daily_pnl: Decimal | None
    trading_days: int
    daily: "list[tuple[datetime.date, Decimal]]"  # ascending
    worst_day: "tuple[datetime.date, Decimal] | None"
    best_day: "tuple[datetime.date, Decimal] | None"
    transactions: "list[ClosedStrategy]"


def month_detail(user: Any, year: int, month: int) -> MonthDetail:
    """Aggregate CLOSED strategies for one month (and the prior month for
    the comparison figures) — every value the month dialog renders."""
    closed = closed_option_strategies(user)

    def month_of(target_year: int, target_month: int) -> "list[ClosedStrategy]":
        return [
            row
            for row in closed
            if row.closed_on
            and (row.closed_on.year, row.closed_on.month) == (target_year, target_month)
        ]

    prev_year, prev_month = (year, month - 1) if month > 1 else (year - 1, 12)
    rows = sorted(month_of(year, month), key=lambda r: (r.closed_on, r.trade.pk))
    prev_rows = month_of(prev_year, prev_month)

    daily_map: dict[datetime.date, Decimal] = defaultdict(Decimal)
    for row in rows:
        if row.closed_on:
            daily_map[row.closed_on] += row.net_profit
    daily = sorted(daily_map.items())
    total = sum((row.net_profit for row in rows), Decimal(0))
    prev_total = sum((row.net_profit for row in prev_rows), Decimal(0))
    wins = sum(1 for row in rows if row.net_profit > 0)
    losses = sum(1 for row in rows if row.net_profit <= 0)
    prev_days = len({row.closed_on for row in prev_rows if row.closed_on})
    all_wins = sum(1 for row in closed if row.net_profit > 0)

    return MonthDetail(
        year=year,
        month=month,
        total_pnl=total,
        previous_pnl=prev_total,
        wins=wins,
        losses=losses,
        win_ratio=Decimal(wins) / len(rows) if rows else None,
        overall_win_ratio=Decimal(all_wins) / len(closed) if closed else None,
        avg_daily_pnl=total / len(daily) if daily else None,
        previous_avg_daily_pnl=prev_total / prev_days if prev_days else None,
        trading_days=len(daily),
        daily=daily,
        worst_day=min(daily, key=lambda kv: kv[1]) if daily else None,
        best_day=max(daily, key=lambda kv: kv[1]) if daily else None,
        transactions=rows,
    )


@dataclass(frozen=True)
class RealizedPeriod:
    """Realized-PnL aggregate over CLOSED strategies for a calendar
    period — the Week/Month calendar cells (which show PnL, unlike the
    premium 'paycheck' Day cells)."""

    pnl: Decimal
    trades: int
    wins: int
    losses: int


def _realized_buckets(
    user: Any, key: "Any", *, underlyings: "list[str] | None" = None
) -> "dict[Any, RealizedPeriod]":
    wanted = {code.upper() for code in underlyings} if underlyings else None
    acc: dict[Any, dict[str, Any]] = defaultdict(
        lambda: {"pnl": Decimal(0), "trades": 0, "wins": 0, "losses": 0}
    )
    for row in closed_option_strategies(user):
        if row.closed_on is None:
            continue
        if wanted is not None and (
            row.underlying is None or row.underlying.code.upper() not in wanted
        ):
            continue
        bucket = key(row.closed_on)
        if bucket is None:
            continue
        cell = acc[bucket]
        cell["pnl"] += row.net_profit
        cell["trades"] += 1
        cell["wins" if row.net_profit > 0 else "losses"] += 1
    return {
        bucket: RealizedPeriod(
            pnl=cell["pnl"], trades=cell["trades"], wins=cell["wins"], losses=cell["losses"]
        )
        for bucket, cell in acc.items()
    }


def realized_months(
    user: Any, year: int, *, underlyings: "list[str] | None" = None
) -> "dict[int, RealizedPeriod]":
    """Per-month realized PnL for `year` (Month-view cells; agrees with
    month_detail's total exactly)."""
    return _realized_buckets(
        user,
        lambda on: on.month if on.year == year else None,
        underlyings=underlyings,
    )


def realized_weeks(
    user: Any, year: int, *, underlyings: "list[str] | None" = None
) -> "dict[datetime.date, RealizedPeriod]":
    """Per-ISO-week realized PnL (Week-view cells), keyed by week Monday."""
    return _realized_buckets(
        user,
        lambda on: (
            (on - datetime.timedelta(days=(on.weekday() + 1) % 7)) if on.year == year else None
        ),
        underlyings=underlyings,
    )


def premium_months(
    user: Any, year: int, *, underlyings: "list[str] | None" = None
) -> "dict[int, CalendarDay]":
    """Monthly aggregates of the premium calendar for one year — the
    calendar's Month view (Jan–Dec grid). Single pass over the trades
    (not 12 per-month derivations)."""
    wanted = {code.upper() for code in underlyings} if underlyings else None

    def keep(metas: "dict[int, OptionMeta]") -> bool:
        if wanted is None:
            return True
        return any(meta.underlying.code.upper() in wanted for meta in metas.values())

    months: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"net": Decimal(0), "events": 0, "wins": 0, "losses": 0}
    )
    for trade, _net, metas in _trades_with_options(user):
        if not keep(metas):
            continue
        for event in _transaction_events(trade):
            if not any(iid in metas for iid in event.positions):
                continue
            when = event.when.date()
            if when.year != year:
                continue
            months[when.month]["net"] += _option_event_cash(event, metas) - event.fees
            months[when.month]["events"] += 1
    for row in closed_option_strategies(user):
        if wanted is not None and (
            row.underlying is None or row.underlying.code.upper() not in wanted
        ):
            continue
        if row.closed_on is None or row.closed_on.year != year:
            continue
        months[row.closed_on.month]["wins" if row.net_profit > 0 else "losses"] += 1
    return {
        month: CalendarDay(
            net_premium=data["net"], events=data["events"], wins=data["wins"], losses=data["losses"]
        )
        for month, data in months.items()
    }


@dataclass(frozen=True)
class RollCandidate:
    contract: OptionContract
    expiry: datetime.date
    strike: Decimal
    right: str
    price: Decimal  # per-contract mark of the candidate (sell-to-open)
    delta: Decimal | None
    net_credit: Decimal  # (candidate − current) × contracts × multiplier


@dataclass(frozen=True)
class RollFinder:
    trade: Trade
    underlying: Instrument
    right: str
    current_strike: Decimal
    current_expiry: datetime.date
    current_price: Decimal  # buy-to-close cost, per contract
    contracts: Decimal
    candidates: "list[RollCandidate]"


def roll_candidates(
    trade: Trade,
    leg_instrument: Instrument,
    price_source: PriceSource,
    chain_source: OptionChainSource,
    *,
    count: int = 5,
    max_expirations: int = 3,
) -> "RollFinder | None":
    """Roll targets for an open short-option leg: later-dated contracts of
    the same right, ranked nearest-expiry then closest-strike. Needs an
    OptionChainSource (chain read); returns None when the source can't
    enumerate the underlying's chain — never a guess (ADR-0041). The
    current leg's own quote gives the buy-to-close cost; each candidate's
    net_credit is what rolling into it collects."""
    meta = OptionMeta.objects.filter(instrument=leg_instrument).select_related("underlying").first()
    if meta is None:
        return None
    position = trade.net_position(leg_instrument)
    if position == 0:
        return None
    current_quote = price_source.get_quote(leg_instrument)
    current_price = current_quote.price if current_quote is not None else Decimal(0)

    expirations = chain_source.get_expirations(meta.underlying)
    if expirations is None:
        return None
    later = [exp for exp in expirations if exp > meta.expiry][:max_expirations]
    strike = Decimal(meta.strike)
    contracts = abs(position)

    candidates: list[RollCandidate] = []
    for expiry in later:
        chain = chain_source.get_option_chain(meta.underlying, expiration=expiry, right=meta.right)
        if not chain:
            continue
        for contract in chain:
            if contract.right != meta.right or contract.quote is None:
                continue
            candidates.append(
                RollCandidate(
                    contract=contract,
                    expiry=contract.expiry,
                    strike=contract.strike,
                    right=contract.right,
                    price=contract.quote.price,
                    delta=contract.quote.delta,
                    net_credit=leg_instrument.quantize_price(
                        (contract.quote.price - current_price)
                        * contracts
                        * leg_instrument.multiplier
                    ),
                )
            )
    candidates.sort(key=lambda c: (c.expiry, abs(c.strike - strike)))
    return RollFinder(
        trade=trade,
        underlying=meta.underlying,
        right=meta.right,
        current_strike=strike,
        current_expiry=meta.expiry,
        current_price=current_price,
        contracts=contracts,
        candidates=candidates[:count],
    )


@dataclass(frozen=True)
class RollLink:
    """The reference roll finder: link an open option position to PRIOR
    closed trades on the same underlying (a roll's predecessors). All
    library-backed — the candidates are closed strategies, the current
    summary is the open row."""

    trade: Trade
    underlying: Instrument
    strategy: str | None
    contracts: int
    strike: Decimal | None
    opened_on: datetime.date | None
    expiration: datetime.date | None
    initial_premium: Decimal
    current_pnl: Decimal | None
    lookback_days: int
    candidates: "list[ClosedStrategy]"  # newest close first


def roll_link_candidates(
    user: Any,
    trade: Trade,
    price_source: PriceSource,
    *,
    lookback_days: int = 60,
) -> "RollLink | None":
    """Prior closed trades on the same underlying that an open option
    position could be linked to as a roll — within `lookback_days` before
    it opened, newest close first. None when the trade holds no live
    option position."""
    current = next(
        (row for row in open_option_strategies(user, price_source) if row.trade == trade), None
    )
    if current is None or current.underlying is None:
        return None
    opened = current.opened_on
    floor = opened - datetime.timedelta(days=lookback_days) if opened else None
    candidates = [
        row
        for row in closed_option_strategies(user)
        if row.underlying == current.underlying
        and row.closed_on is not None
        and (opened is None or row.closed_on <= opened)
        and (floor is None or row.closed_on >= floor)
    ]
    candidates.sort(key=lambda row: row.closed_on or datetime.date.min, reverse=True)
    primary_strike = current.legs[0].strike if current.legs else None
    return RollLink(
        trade=trade,
        underlying=current.underlying,
        strategy=current.strategy,
        contracts=current.contracts,
        strike=primary_strike,
        opened_on=opened,
        expiration=current.expiration,
        initial_premium=current.initial_premium,
        current_pnl=current.unrealized_pnl,
        lookback_days=lookback_days,
        candidates=candidates,
    )
