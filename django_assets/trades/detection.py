"""Trade detection & strategy engine (ADR-0037).

The default bucket is derived, never stored: a leg is in the bucket
precisely when it carries no TradeAllocation. The engine walks the
bucket chronologically, clusters fills by (trade date, underlying),
and proposes — `open` for new structures, `close`/`adjust` against
CONFIRMED open trades only (the cascade; there is no unmatched-close
concept), and `event` for income/corporate-action transactions that
belong inside the trade holding the instrument. Nothing books itself;
resolution is confirm / reject / MODIFY, and rejection is a no-op on
bucket membership because the legs never left.

Classification is structural only — instruments, quantities, timing,
coverage ratios (ADR-0033 lets trades read instruments). Every
proposal must be explainable in one evidence sentence.
"""

import datetime
import hashlib
from collections import defaultdict
from decimal import Decimal
from typing import Any

from django.utils import timezone

from django_assets.core.models import Instrument, Transaction, TransactionLeg
from django_assets.instruments.options.models import OptionMeta
from django_assets.trades.models import Trade, TradeProposal

STRATEGY_CATEGORY = "strategy"
HORIZON_CATEGORY = "horizon"
SWING_DAYS = 30
POSITION_DAYS = 365


def default_bucket(user: Any) -> "list[TransactionLeg]":
    """Position-side security legs still unextracted — the bucket.

    Mirrored pairs (the ADR-0035 counterparty side of every template)
    resolve to their user-side leg: templates insert the user-side leg
    of a pair first, so the lower id wins (the same convention
    Trade.assign relies on)."""
    legs = list(
        TransactionLeg.objects.filter(
            account__owner=user,
            instrument__price_currency__isnull=False,
            trade_allocations__isnull=True,
        )
        .select_related("transaction", "instrument", "account")
        .order_by("transaction__trade_timestamp", "transaction__timestamp", "id")
    )
    # Pair-detect against the transactions' FULL security leg sets —
    # a mirror must stay excluded even after its user-side twin has
    # been extracted (allocated) out of the bucket.
    all_security_legs = TransactionLeg.objects.filter(
        transaction_id__in={leg.transaction_id for leg in legs},
        instrument__price_currency__isnull=False,
    ).order_by("id")
    pair_min: dict[tuple[int, int], TransactionLeg] = {}
    pair_mixed: set[tuple[int, int]] = set()
    for leg in all_security_legs:
        key = (leg.transaction_id, leg.instrument_id)
        first = pair_min.get(key)
        if first is None:
            pair_min[key] = leg
        elif (first.amount > 0) != (leg.amount > 0):
            pair_mixed.add(key)  # a mirrored pair exists for this key
    kept = [
        leg
        for leg in legs
        if (leg.transaction_id, leg.instrument_id) not in pair_mixed
        or pair_min[(leg.transaction_id, leg.instrument_id)].pk == leg.pk
    ]
    return kept


def _leg_date(leg: TransactionLeg) -> datetime.date:
    ts = leg.transaction.trade_timestamp or leg.transaction.timestamp
    return ts.date()


def _underlying_id(leg: TransactionLeg, option_meta: "dict[int, OptionMeta]") -> int:
    meta = option_meta.get(leg.instrument_id)
    return meta.underlying_id if meta else leg.instrument_id


def _fingerprint(user: Any, kind: str, parts: "list[str]") -> str:
    raw = "|".join([str(user.pk), kind, *parts])
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def _open_trades_holding(user: Any, instrument: Instrument) -> "list[tuple[Trade, Decimal]]":
    """Confirmed trades with a live position in `instrument`, newest
    first — proposals never count (the cascade)."""
    out: list[tuple[Trade, Decimal]] = []
    trades = Trade.objects.filter(user=user, allocations__leg__instrument=instrument).distinct()
    for trade in trades:
        position = trade.net_position(instrument)
        if position != 0:
            out.append((trade, position))
    out.sort(key=lambda pair: pair[0].open_date or timezone.now(), reverse=True)
    return out


def classify_structure(legs: "list[TransactionLeg]") -> str:
    """Full options-strategy taxonomy (ADR-0037 §3 as extended for the
    options-focused platform). Purely structural: rights, expiries,
    strikes, NET contract counts per instrument, share coverage. Every
    label is explainable in one evidence sentence; anything that
    doesn't match a canonical shape is `mixed` — never a dressed-up
    guess."""
    metas = OptionMeta.objects.filter(
        instrument_id__in=[leg.instrument_id for leg in legs]
    ).in_bulk(field_name="instrument_id")

    # Net per instrument: multiple fills of one contract are one
    # position; a cluster netting a contract to zero drops it.
    net: dict[int, Decimal] = defaultdict(Decimal)
    for leg in legs:
        net[leg.instrument_id] += leg.amount
    share_qty = sum((qty for iid, qty in net.items() if iid not in metas), Decimal(0))
    contracts = [
        _Contract(
            right=metas[iid].right,
            expiry=metas[iid].expiry,
            strike=Decimal(metas[iid].strike),
            count=qty,
        )
        for iid, qty in net.items()
        if iid in metas and qty != 0
    ]
    contracts.sort(key=lambda c: (c.expiry, c.strike, c.right))

    if not contracts:
        return "stock"
    if share_qty > 0:
        return _with_long_shares(share_qty, contracts)
    if share_qty < 0:
        return _with_short_shares(contracts)
    return _options_only(contracts)


class _Contract:
    __slots__ = ("right", "expiry", "strike", "count")

    def __init__(self, right: str, expiry: Any, strike: Decimal, count: Decimal) -> None:
        self.right = right
        self.expiry = expiry
        self.strike = strike
        self.count = count

    @property
    def is_long(self) -> bool:
        return self.count > 0


def _with_long_shares(share_qty: Decimal, contracts: "list[_Contract]") -> str:
    short_calls = [c for c in contracts if c.right == "C" and not c.is_long]
    long_puts = [c for c in contracts if c.right == "P" and c.is_long]
    short_puts = [c for c in contracts if c.right == "P" and not c.is_long]
    others = [c for c in contracts if c not in short_calls + long_puts + short_puts]
    covered = sum((abs(c.count) for c in short_calls), Decimal(0)) * 100 <= share_qty
    if others:
        return "mixed"
    if short_calls and long_puts and not short_puts and covered:
        return "collar"
    if short_calls and short_puts and not long_puts and covered:
        same_strike = (
            len(short_calls) == 1
            and len(short_puts) == 1
            and (short_calls[0].strike == short_puts[0].strike)
        )
        return "covered_short_straddle" if same_strike else "covered_short_strangle"
    if short_calls and not long_puts and not short_puts and covered:
        return "covered_call"
    if long_puts and not short_calls and not short_puts:
        return "protective_put"
    return "mixed"


def _with_short_shares(contracts: "list[_Contract]") -> str:
    if len(contracts) == 1:
        contract = contracts[0]
        if contract.right == "P" and not contract.is_long:
            return "covered_put"
        if contract.right == "C" and contract.is_long:
            return "synthetic_put"  # short stock + long call
    return "mixed"


def _options_only(contracts: "list[_Contract]") -> str:
    if len(contracts) == 1:
        contract = contracts[0]
        side = "long" if contract.is_long else "short"
        if side == "short" and contract.right == "P":
            return "short_put"
        return f"{side}_{'call' if contract.right == 'C' else 'put'}"
    if len(contracts) == 2:
        return _two_legs(*contracts)
    if len(contracts) == 3:
        return _three_legs(contracts)
    if len(contracts) == 4:
        return _four_legs(contracts)
    return "mixed"


def _two_legs(a: "_Contract", b: "_Contract") -> str:
    same_right = a.right == b.right
    same_expiry = a.expiry == b.expiry
    same_strike = a.strike == b.strike
    opposite = a.is_long != b.is_long
    equal_size = abs(a.count) == abs(b.count)

    if same_right and same_expiry and not same_strike and opposite:
        low, high = (a, b) if a.strike < b.strike else (b, a)
        if not equal_size:
            shorter = a if abs(a.count) < abs(b.count) else b
            return (
                f"{'call' if a.right == 'C' else 'put'}_backspread"
                if not shorter.is_long
                else f"ratio_{'call' if a.right == 'C' else 'put'}_spread"
            )
        if a.right == "C":
            return "bull_call_spread" if low.is_long else "bear_call_spread"
        return "bear_put_spread" if high.is_long else "bull_put_spread"

    if same_right and not same_expiry and opposite and equal_size:
        flavor = "call" if a.right == "C" else "put"
        return f"calendar_{flavor}_spread" if same_strike else f"diagonal_{flavor}_spread"

    if not same_right and same_expiry and not opposite:
        call = a if a.right == "C" else b
        put = b if a.right == "C" else a
        if equal_size:
            side = "long" if a.is_long else "short"
            if same_strike:
                return f"{side}_straddle"
            if call.strike > put.strike:
                return f"{side}_strangle"
            # in-the-money pair: call struck BELOW the put
            return "guts" if side == "long" else "short_guts"
        if same_strike and a.is_long and b.is_long:
            # 1:2 same-strike volatility tilts
            if abs(put.count) == 2 * abs(call.count):
                return "strip"
            if abs(call.count) == 2 * abs(put.count):
                return "strap"

    if not same_right and same_expiry and opposite and equal_size:
        call = a if a.right == "C" else b
        if same_strike:
            return "long_synthetic_future" if call.is_long else "short_synthetic_future"
        return "long_combo" if call.is_long else "short_combo"

    return "mixed"


def _three_legs(contracts: "list[_Contract]") -> str:
    rights = {c.right for c in contracts}
    expiries = {c.expiry for c in contracts}

    # Butterfly family: one right, one expiry, three strikes, 1:2:1,
    # body opposite the wings.
    if len(rights) == 1 and len(expiries) == 1 and len({c.strike for c in contracts}) == 3:
        low, mid, high = sorted(contracts, key=lambda c: c.strike)
        counts = [abs(low.count), abs(mid.count), abs(high.count)]
        ratio_ok = counts[1] == counts[0] + counts[2] and counts[0] == counts[2]
        wings_same = low.is_long == high.is_long
        body_opposite = mid.is_long != low.is_long
        flavor = "call" if low.right == "C" else "put"
        if ratio_ok and wings_same and body_opposite:
            if (mid.strike - low.strike) != (high.strike - mid.strike):
                prefix = "" if low.is_long else "inverse_"
                return f"{prefix}{flavor}_broken_wing"
            side = "long" if low.is_long else "short"
            return f"{side}_{flavor}_butterfly"
        # Ladders: three strikes, 1:1:1, a vertical plus one extra leg.
        if counts[0] == counts[1] == counts[2]:
            pattern = (low.is_long, mid.is_long, high.is_long)
            ladder = {
                ("C", (True, False, False)): "bull_call_ladder",
                ("C", (False, True, True)): "bear_call_ladder",
                ("P", (True, True, False)): "bull_put_ladder",
                ("P", (False, False, True)): "bear_put_ladder",
            }.get((low.right, pattern))
            if ladder:
                return ladder

    # Jade lizard: short put + short call vertical, one expiry, no
    # upside risk beyond the call spread.
    if rights == {"C", "P"} and len(expiries) == 1:
        puts = [c for c in contracts if c.right == "P"]
        calls = sorted((c for c in contracts if c.right == "C"), key=lambda c: c.strike)
        if (
            len(puts) == 1
            and not puts[0].is_long
            and len(calls) == 2
            and not calls[0].is_long
            and calls[1].is_long
            and abs(calls[0].count) == abs(calls[1].count)
        ):
            return "jade_lizard"
        # Reverse jade lizard: short call + bull put credit spread.
        puts_sorted = sorted((c for c in contracts if c.right == "P"), key=lambda c: c.strike)
        lone_calls = [c for c in contracts if c.right == "C"]
        if (
            len(lone_calls) == 1
            and not lone_calls[0].is_long
            and len(puts_sorted) == 2
            and puts_sorted[0].is_long
            and not puts_sorted[1].is_long
            and abs(puts_sorted[0].count) == abs(puts_sorted[1].count)
        ):
            return "reverse_jade_lizard"

    return "mixed"


def _four_legs(contracts: "list[_Contract]") -> str:
    rights = {c.right for c in contracts}
    expiries = sorted({c.expiry for c in contracts})
    calls = sorted((c for c in contracts if c.right == "C"), key=lambda c: c.strike)
    puts = sorted((c for c in contracts if c.right == "P"), key=lambda c: c.strike)
    equal_size = len({abs(c.count) for c in contracts}) == 1

    if rights == {"C", "P"} and len(calls) == 2 and len(puts) == 2 and equal_size:
        if len(expiries) == 1:
            call_pair = calls[0].is_long != calls[1].is_long
            put_pair = puts[0].is_long != puts[1].is_long
            if call_pair and put_pair:
                # Box: synthetic long at one strike + synthetic short at
                # another (call and put AGREEING per strike-pair).
                if (
                    {calls[0].strike, calls[1].strike} == {puts[0].strike, puts[1].strike}
                    and calls[0].is_long != puts[0].is_long
                    and calls[1].is_long != puts[1].is_long
                ):
                    return "box_spread"
                body_short = not calls[0].is_long and not puts[1].is_long
                body_long = calls[0].is_long and puts[1].is_long
                iron = "iron_butterfly" if calls[0].strike == puts[1].strike else "iron_condor"
                if body_short:
                    return iron
                if body_long:
                    return f"reverse_{iron}"
        elif len(expiries) == 2:
            near = [c for c in contracts if c.expiry == expiries[0]]
            far = [c for c in contracts if c.expiry == expiries[1]]
            all_calls = [c for c in contracts if c.right == "C"]
            all_puts = [c for c in contracts if c.right == "P"]
            if (
                len(near) == 2
                and len(far) == 2
                and {c.right for c in near} == {"C", "P"}
                and {c.right for c in far} == {"C", "P"}
                and all(
                    c.is_long != n.is_long
                    for c, n in zip(
                        sorted(far, key=lambda x: x.right),
                        sorted(near, key=lambda x: x.right),
                        strict=True,
                    )
                )
            ):
                same_strikes = (
                    all_calls[0].strike == all_calls[1].strike
                    and all_puts[0].strike == all_puts[1].strike
                )
                return "double_calendar" if same_strikes else "double_diagonal"

    if len(rights) == 1 and len(expiries) == 1 and equal_size:
        ordered = sorted(contracts, key=lambda c: c.strike)
        strikes = [c.strike for c in ordered]
        if len(set(strikes)) == 4:
            pattern = [c.is_long for c in ordered]
            if pattern in ([True, False, False, True], [False, True, True, False]):
                side = "long" if pattern[0] else "short"
                return f"{side}_{'call' if ordered[0].right == 'C' else 'put'}_condor"

    return "mixed"


def classify_horizon(opened: datetime.date, closed: "datetime.date | None") -> str:
    if closed is None:
        age = (timezone.now().date() - opened).days
        return "long_term" if age >= POSITION_DAYS else ""
    span = (closed - opened).days
    if span == 0:
        return "intraday"
    if span <= SWING_DAYS:
        return "swing"
    if span <= POSITION_DAYS:
        return "position"
    return "long_term"


def detect(user: Any) -> "list[TradeProposal]":
    """One pass over the bucket. Idempotent per fingerprint; re-run
    after every confirmation — the cascade only sees one step past the
    confirmation frontier."""
    legs = default_bucket(user)
    option_meta = OptionMeta.objects.filter(
        instrument_id__in={leg.instrument_id for leg in legs}
    ).in_bulk(field_name="instrument_id")

    clusters: dict[tuple[datetime.date, int], list[TransactionLeg]] = defaultdict(list)
    for leg in legs:
        clusters[(_leg_date(leg), _underlying_id(leg, option_meta))].append(leg)

    # Running position per instrument across the chronological walk:
    # confirmed allocations plus bucket legs already walked. A reducing
    # fill with no confirmed target is a close-in-waiting — it stays in
    # the bucket silently (no unmatched-close concept).
    from django.db.models import Sum

    running: dict[int, Decimal] = defaultdict(Decimal)
    for row in (
        TransactionLeg.objects.filter(
            account__owner=user,
            instrument__price_currency__isnull=False,
            trade_allocations__isnull=False,
        )
        .values("instrument")
        .annotate(total=Sum("amount"))
    ):
        running[row["instrument"]] = row["total"]

    proposals: list[TradeProposal] = []

    def propose(kind: str, *, date: datetime.date, parts: "list[str]", **kwargs: Any) -> None:
        fingerprint = _fingerprint(user, kind, parts)
        proposal, created = TradeProposal.objects.get_or_create(
            user=user,
            fingerprint=fingerprint,
            defaults={"kind": kind, "proposed_at_date": date, **kwargs},
        )
        if created:
            proposals.append(proposal)

    for (date, underlying_id), cluster in sorted(clusters.items(), key=lambda kv: kv[0]):
        remaining: list[TransactionLeg] = []
        closes: dict[int, list[TransactionLeg]] = defaultdict(list)
        close_targets: dict[int, Trade] = {}

        for leg in cluster:
            before = running[leg.instrument_id]
            running[leg.instrument_id] = before + leg.amount
            is_reducing = before != 0 and (leg.amount > 0) != (before > 0)
            if not is_reducing:
                remaining.append(leg)
                continue
            candidates = _open_trades_holding(user, leg.instrument)
            target = next((t for t, p in candidates if (leg.amount > 0) != (p > 0)), None)
            if target is None:
                continue  # close-in-waiting: stays in the bucket, silently
            closes[target.pk].append(leg)
            close_targets[target.pk] = target

        for trade_pk, close_legs in closes.items():
            trade = close_targets[trade_pk]
            opened = (trade.open_date or timezone.now()).date()
            propose(
                "close",
                date=date,
                parts=[str(trade_pk), *sorted(str(leg.pk) for leg in close_legs)],
                legs=[{"leg_id": leg.pk, "amount": str(leg.amount)} for leg in close_legs],
                target_trade=trade,
                proposed_name=trade.name,
                horizon=classify_horizon(opened, date),
                evidence={
                    "reason": "fills reduce this trade's open position",
                    "candidates_ranked_by": "recency",
                    "underlying_id": underlying_id,
                },
            )

        if not remaining:
            continue

        underlying = Instrument.objects.get(pk=underlying_id)
        adjust_target = next(
            (trade for trade, _p in _open_trades_holding_underlying(user, underlying_id)),
            None,
        )
        if adjust_target is not None:
            propose(
                "adjust",
                date=date,
                parts=[str(adjust_target.pk), *sorted(str(leg.pk) for leg in remaining)],
                legs=[{"leg_id": leg.pk, "amount": str(leg.amount)} for leg in remaining],
                target_trade=adjust_target,
                proposed_name=adjust_target.name,
                evidence={
                    "reason": "same-underlying fills while this trade is open",
                    "underlying": underlying.code,
                },
            )
            continue

        structure = classify_structure(remaining)
        propose(
            "open",
            date=date,
            parts=sorted(str(leg.pk) for leg in remaining),
            legs=[{"leg_id": leg.pk, "amount": str(leg.amount)} for leg in remaining],
            proposed_name=f"{structure.replace('_', ' ')} {underlying.code} {date:%Y-%m-%d}",
            structure=structure,
            horizon=classify_horizon(date, None),
            evidence={
                "reason": "unextracted fills form a new structure",
                "underlying": underlying.code,
                "leg_count": len(remaining),
            },
        )

    _detect_events(user, propose)
    return proposals


def _open_trades_holding_underlying(user: Any, underlying_id: int) -> "list[tuple[Trade, Decimal]]":
    instrument_ids = {underlying_id} | set(
        OptionMeta.objects.filter(underlying_id=underlying_id).values_list(
            "instrument_id", flat=True
        )
    )
    out: list[tuple[Trade, Decimal]] = []
    for instrument in Instrument.objects.filter(pk__in=instrument_ids):
        out.extend(_open_trades_holding(user, instrument))
    seen: set[int] = set()
    unique = []
    for trade, position in sorted(
        out, key=lambda pair: pair[0].open_date or timezone.now(), reverse=True
    ):
        if trade.pk not in seen:
            seen.add(trade.pk)
            unique.append((trade, position))
    return unique


def _detect_events(user: Any, propose: Any) -> None:
    """Dividends / interest / ROC / approved corporate actions belong
    inside the trade that held the instrument at the event date."""
    from django.db.models import Q

    events = (
        Transaction.objects.filter(legs__account__owner=user)
        .filter(
            Q(metadata__has_key="income_instrument_id")
            | Q(metadata__has_key="return_of_capital")
            | Q(metadata__has_key="corporate_action_proposal")
        )
        .distinct()
    )
    for transaction in events:
        instrument_id = transaction.metadata.get("income_instrument_id") or (
            transaction.metadata.get("return_of_capital") or {}
        ).get("instrument_id")
        category = "income"
        if instrument_id is None and transaction.metadata.get("corporate_action_proposal"):
            category = "corporate_action"
            conversion = transaction.metadata.get("conversion")
            if isinstance(conversion, dict):
                instrument_id = conversion.get("from_instrument_id")
        if instrument_id is None:
            continue
        if transaction.legs.filter(trade_allocations__isnull=False).exists():
            continue  # already extracted
        try:
            instrument = Instrument.objects.get(pk=instrument_id)
        except Instrument.DoesNotExist:
            continue
        date = (transaction.trade_timestamp or transaction.timestamp).date()
        holder = next(
            (
                trade
                for trade, position in _open_trades_holding(user, instrument)
                if position > 0 and (trade.open_date or timezone.now()).date() <= date
            ),
            None,
        )
        if holder is None:
            continue  # stays in the bucket — never guessed
        propose(
            "event",
            date=date,
            parts=[str(transaction.pk), str(holder.pk)],
            event_transaction=transaction,
            target_trade=holder,
            proposed_name=holder.name,
            evidence={
                "reason": f"{category} on {instrument.code} while this trade held it",
                "category": category,
                "income_character": transaction.metadata.get("income_character"),
            },
        )


def confirm_proposal(
    proposal: TradeProposal,
    *,
    name: "str | None" = None,
    structure: "str | None" = None,
    horizon: "str | None" = None,
    target_trade: "Trade | None" = None,
    leg_ids: "list[int] | None" = None,
    note: str = "",
) -> Trade:
    """Confirm — or MODIFY-and-confirm (ADR-0037 §4): every parameter
    overrides the proposal, and the deltas are recorded."""
    if proposal.resolution:
        raise ValueError(f"proposal already {proposal.resolution}")

    modifications: dict[str, Any] = {}
    chosen_name = name if name is not None else proposal.proposed_name
    chosen_structure = structure if structure is not None else proposal.structure
    chosen_horizon = horizon if horizon is not None else proposal.horizon
    trade = target_trade if target_trade is not None else proposal.target_trade
    if name is not None:
        modifications["name"] = name
    if structure is not None:
        modifications["structure"] = structure
    if horizon is not None:
        modifications["horizon"] = horizon
    if target_trade is not None:
        modifications["target_trade"] = target_trade.pk

    proposal_legs = proposal.legs
    if leg_ids is not None:
        proposal_legs = [entry for entry in proposal.legs if entry["leg_id"] in leg_ids]
        modifications["leg_ids"] = leg_ids

    if proposal.kind == "open":
        trade = Trade.objects.create(
            user=proposal.user,
            name=chosen_name or f"trade {proposal.proposed_at_date}",
            metadata={"trade_proposal": proposal.pk},
        )
        category = "open"
    elif proposal.kind in ("close", "adjust"):
        if trade is None:
            raise ValueError(f"{proposal.kind} confirmation needs a target trade")
        category = proposal.kind
    elif proposal.kind == "event":
        if trade is None or proposal.event_transaction is None:
            raise ValueError("event confirmation needs its trade and transaction")
        event_category = proposal.evidence.get("category", "income")
        trade_accounts = set(trade.allocations.values_list("leg__account_id", flat=True))
        cash_legs = [
            leg
            for leg in proposal.event_transaction.legs.select_related("instrument")
            if leg.instrument.price_currency_id is None
        ]
        preferred = [leg for leg in cash_legs if leg.account_id in trade_accounts]
        for leg in preferred or sorted(cash_legs, key=lambda cl: -abs(cl.amount))[:1]:
            trade.assign_leg(leg, leg.amount, category=event_category)
        _finish(proposal, trade, note, modifications)
        return trade
    else:
        raise ValueError(f"unknown proposal kind {proposal.kind!r}")

    del category  # roles come from Trade.assign's double-entry analysis
    legs = TransactionLeg.objects.in_bulk([entry["leg_id"] for entry in proposal_legs])
    for entry in proposal_legs:
        leg = legs[entry["leg_id"]]
        trade.assign(
            leg.transaction,
            quantity=abs(Decimal(entry["amount"])),
            instrument=leg.instrument,
        )

    if proposal.kind == "open":
        if chosen_structure:
            trade.add_tag(STRATEGY_CATEGORY, chosen_structure)
        if chosen_horizon:
            trade.add_tag(HORIZON_CATEGORY, chosen_horizon)
    elif proposal.kind == "close" and chosen_horizon:
        trade.remove_tag(HORIZON_CATEGORY, "")  # no-op guard
        for existing in trade.get_tags_by_category().get(HORIZON_CATEGORY, []):
            trade.remove_tag(HORIZON_CATEGORY, existing)
        trade.add_tag(HORIZON_CATEGORY, chosen_horizon)

    _finish(proposal, trade, note, modifications)
    return trade


def _finish(
    proposal: TradeProposal, trade: Trade, note: str, modifications: "dict[str, Any]"
) -> None:
    proposal.resolution = "confirmed"
    proposal.resolved_at = timezone.now()
    proposal.note = note
    proposal.booked_trade = trade
    if modifications:
        proposal.evidence = {**proposal.evidence, "modifications": modifications}
    proposal.save(update_fields=["resolution", "resolved_at", "note", "booked_trade", "evidence"])


def reject_proposal(proposal: TradeProposal, *, note: str = "") -> None:
    """Rejection of a grouping is not rejection of the legs — they
    never left the bucket."""
    if proposal.resolution:
        raise ValueError(f"proposal already {proposal.resolution}")
    proposal.resolution = "rejected"
    proposal.resolved_at = timezone.now()
    proposal.note = note
    proposal.save(update_fields=["resolution", "resolved_at", "note"])
