"""ADR-0029 matching primitives: MatchCriteria, MatchScore, hard
filters, and soft scoring. Lower scores are better; 0.0 is a perfect
match across all soft dimensions."""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from django_assets.core.models import Instrument, Transaction, TransactionLeg

if TYPE_CHECKING:
    from django_assets.brokerage.models import ImportLine
    from django_assets.brokerage.schemas import ImportSchema


@dataclass(frozen=True)
class MatchCriteria:
    date: datetime.date
    instrument: Instrument
    amount: Decimal
    compound_hint: str = "combine"  # "combine" | "split" — UI labeling only


@dataclass(frozen=True)
class MatchScore:
    total: float  # float-ok: ranking heuristic, never money
    amount_drift: float  # float-ok
    date_drift_days: float  # float-ok
    breakdown: dict[str, float] = field(default_factory=dict)  # float-ok


def find_asset_leg(
    candidate: Transaction, criteria: MatchCriteria, line: "ImportLine"
) -> TransactionLeg | None:
    """The candidate's reconcilable asset leg per the hard filters:
    same account as the batch, same instrument, same sign, not already
    matched anywhere. None = the candidate is (no longer) eligible."""
    from django_assets.brokerage.accounts import account_allows_reconciliation
    from django_assets.brokerage.models import ImportLine as ImportLineModel

    for leg in candidate.legs.filter(account=line.batch.account, instrument=criteria.instrument):
        if (criteria.amount >= 0) != (leg.amount >= 0):
            continue
        if not account_allows_reconciliation(leg.account):
            continue
        if ImportLineModel.objects.filter(matched_legs=leg).exists():
            continue
        return leg
    return None


def hard_filter_candidates(
    line: "ImportLine", criteria: MatchCriteria, schema: "ImportSchema"
) -> list[tuple[Transaction, TransactionLeg]]:
    window = datetime.timedelta(days=schema.date_window_days)
    candidates = (
        Transaction.objects.filter(
            origin="manual",
            legs__account=line.batch.account,
            legs__instrument=criteria.instrument,
            timestamp__date__gte=criteria.date - window,
            timestamp__date__lte=criteria.date + window,
        )
        .distinct()
        .order_by("timestamp")
    )
    survivors = []
    for candidate in candidates:
        leg = find_asset_leg(candidate, criteria, line)
        if leg is not None:
            survivors.append((candidate, leg))
    return survivors


def score_candidate(
    criteria: MatchCriteria,
    candidate: Transaction,
    leg: TransactionLeg,
    schema: "ImportSchema",
) -> MatchScore:
    if criteria.amount:
        drift_ratio = abs(criteria.amount - leg.amount) / abs(criteria.amount)
        amount_drift = min(float(drift_ratio), 1.0)  # float-ok
    else:
        amount_drift = 0.0 if leg.amount == 0 else 1.0  # float-ok
    days_off = abs((criteria.date - candidate.timestamp.date()).days)
    over = max(0, days_off - schema.settlement_tolerance_days)
    date_drift = float(over)  # float-ok
    total = amount_drift + date_drift  # float-ok
    return MatchScore(
        total=total,
        amount_drift=amount_drift,
        date_drift_days=date_drift,
        breakdown={"amount": amount_drift, "date": date_drift},
    )
