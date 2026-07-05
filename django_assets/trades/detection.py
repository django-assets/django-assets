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
    """Structural taxonomy (ADR-0037 §3): what do these fills form?"""
    metas = OptionMeta.objects.filter(
        instrument_id__in=[leg.instrument_id for leg in legs]
    ).in_bulk(field_name="instrument_id")
    options = [leg for leg in legs if leg.instrument_id in metas]
    shares = [leg for leg in legs if leg.instrument_id not in metas]
    share_qty = sum((leg.amount for leg in shares), Decimal(0))

    if not options:
        return "stock"

    def sig(leg: TransactionLeg) -> "tuple[str, Any, Decimal, Decimal]":
        meta = metas[leg.instrument_id]
        return (meta.right, meta.expiry, Decimal(meta.strike), leg.amount)

    signatures = [sig(leg) for leg in options]
    contracts = sum((abs(leg.amount) for leg in options), Decimal(0))

    if not shares and len(options) == 1:
        right, _, _, amount = signatures[0]
        side = "long" if amount > 0 else "short"
        if side == "short" and right == "P":
            return "cash_secured_put"
        return f"{side}_{'call' if right == 'C' else 'put'}"

    if shares and share_qty > 0:
        short_calls = [s for s in signatures if s[0] == "C" and s[3] < 0]
        long_puts = [s for s in signatures if s[0] == "P" and s[3] > 0]
        covered = sum((abs(s[3]) for s in short_calls), Decimal(0)) * 100 <= share_qty
        if short_calls and long_puts and covered:
            return "collar"
        if short_calls and not long_puts and covered and len(options) == len(short_calls):
            return "covered_call"
        if long_puts and not short_calls and len(options) == len(long_puts):
            return "protective_put"
        return "mixed"

    if not shares and len(options) == 2:
        (r1, e1, k1, a1), (r2, e2, k2, a2) = signatures
        if r1 == r2 and e1 == e2 and k1 != k2 and (a1 > 0) != (a2 > 0):
            return "vertical_spread"
        if r1 != r2 and e1 == e2 and (a1 > 0) == (a2 > 0):
            return "straddle" if k1 == k2 else "strangle"
        if r1 == r2 and e1 != e2 and (a1 > 0) != (a2 > 0):
            return "calendar_spread"

    if not shares and len(options) == 4 and contracts:
        calls = sorted(s for s in signatures if s[0] == "C")
        puts = sorted(s for s in signatures if s[0] == "P")
        expiries = {s[1] for s in signatures}
        if (
            len(calls) == 2
            and len(puts) == 2
            and len(expiries) == 1
            and (calls[0][3] > 0) != (calls[1][3] > 0)
            and (puts[0][3] > 0) != (puts[1][3] > 0)
        ):
            return "iron_condor"

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
