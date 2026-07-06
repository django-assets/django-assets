"""ADR-0037: default bucket, cascade, structure/horizon classification,
event insertion, confirm/reject/modify."""

import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from django_assets.core.models import Account, Instrument
from django_assets.instruments.equities import templates as eq
from django_assets.instruments.equities.models import EquityMeta
from django_assets.instruments.options import templates as opt
from django_assets.instruments.options.models import OptionMeta
from django_assets.trades.detection import (
    classify_structure,
    confirm_proposal,
    default_bucket,
    detect,
    reject_proposal,
)

pytestmark = pytest.mark.ledger

D = Decimal
TS = datetime.datetime(2026, 1, 5, 15, 0, tzinfo=datetime.UTC)


def at(days: int) -> datetime.datetime:
    return TS + datetime.timedelta(days=days)


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="strategist", password="x")


@pytest.fixture
def usd():
    return Instrument.objects.create(code="USD", quantity_decimals=2)


@pytest.fixture
def accounts(user):
    names = [
        "cash",
        "holdings",
        "market",
        "funding",
        "issuers",
        "conversions",
        "commissions",
        "regulatory_fees",
        "tax_withheld",
        "foreign_tax",
        "interest",
    ]
    return {n: Account.objects.create(owner=user, name=n) for n in names}


def make_equity(code, usd):
    inst = Instrument.objects.create(
        code=code, quantity_decimals=4, price_decimals=4, price_currency=usd
    )
    EquityMeta.objects.create(instrument=inst)
    return inst


def make_option(underlying, usd, strike, right, expiry=datetime.date(2026, 6, 19)):
    inst = Instrument.objects.create(
        code=f"{underlying.code} {expiry:%m/%d/%Y} {strike} {right}",
        quantity_decimals=0,
        price_decimals=4,
        multiplier=D("100"),
        price_currency=usd,
    )
    OptionMeta.objects.create(
        instrument=inst, underlying=underlying, expiry=expiry, strike=strike, right=right
    )
    return inst


def test_cascade_close_only_after_open_confirmed(accounts, usd, user):
    pm = make_equity("PM", usd)
    eq.buy_shares(
        accounts=accounts,
        instrument=pm,
        quantity=100,
        price="100",
        principal="10000",
        timestamp=at(0),
        origin="import",
    )
    eq.sell_shares(
        accounts=accounts,
        instrument=pm,
        quantity=100,
        price="120",
        principal="12000",
        timestamp=at(10),
        origin="import",
    )

    first = detect(user)
    # The sell has no confirmed open trade: NO close proposal, no flag —
    # it stays in the bucket (no unmatched-close concept).
    assert [p.kind for p in first] == ["open"]
    assert first[0].structure == "stock"

    trade = confirm_proposal(first[0])
    second = detect(user)
    assert [p.kind for p in second] == ["close"]
    assert second[0].target_trade == trade
    assert second[0].horizon == "swing"

    confirm_proposal(second[0])
    assert trade.net_position(pm) == 0
    assert default_bucket(user) == []
    assert "swing" in trade.get_tags_by_category()["horizon"]
    # re-run: silence
    assert detect(user) == []


def test_covered_call_structure_and_event_insertion(accounts, usd, user):
    pm = make_equity("PM", usd)
    call = make_option(pm, usd, D("130"), "C")
    eq.buy_shares(
        accounts=accounts,
        instrument=pm,
        quantity=100,
        price="100",
        principal="10000",
        timestamp=at(0),
        origin="import",
    )
    opt.sell_option(
        accounts=accounts,
        instrument=call,
        contracts=1,
        price="2.50",
        principal="250",
        timestamp=at(0),
        origin="import",
    )

    proposals = detect(user)
    assert [p.kind for p in proposals] == ["open"]
    assert proposals[0].structure == "covered_call"
    trade = confirm_proposal(proposals[0])
    assert "covered_call" in trade.get_tags_by_category()["strategy"]

    # A qualified dividend arrives while the trade holds the shares.
    eq.dividend_received(
        accounts=accounts,
        instrument=pm,
        amount="130",
        timestamp=at(20),
        character="qualified",
        character_label="Qualified Dividend",
        origin="import",
    )
    events = detect(user)
    assert [p.kind for p in events] == ["event"]
    assert events[0].target_trade == trade
    assert events[0].evidence["income_character"] == "qualified"
    confirm_proposal(events[0])
    categories = {a.category for a in trade.allocations.all()}
    assert "income" in categories


def test_structure_classifier_shapes(accounts, usd, user):
    spy = make_equity("SPY", usd)
    put_lo = make_option(spy, usd, D("400"), "P")
    put_hi = make_option(spy, usd, D("420"), "P")
    call_lo = make_option(spy, usd, D("500"), "C")
    call_hi = make_option(spy, usd, D("520"), "C")

    def legs_for(*fills):
        made = []
        for instrument, contracts in fills:
            tx = opt.buy_option if contracts > 0 else opt.sell_option
            transaction = tx(
                accounts=accounts,
                instrument=instrument,
                contracts=abs(contracts),
                price="1",
                principal=str(100 * abs(contracts)),
                timestamp=at(0),
            )
            made.extend(
                leg
                for leg in transaction.legs.all()
                if leg.instrument.price_currency_id is not None
                and leg.account_id == accounts["holdings"].pk
            )
        return made

    condor = legs_for((put_lo, 1), (put_hi, -1), (call_lo, -1), (call_hi, 1))
    assert classify_structure(condor) == "iron_condor"
    assert classify_structure([condor[0]]) == "long_put"

    csp = legs_for((put_hi, -1))
    assert classify_structure(csp[-1:]) == "short_put"


def test_modify_and_reject(accounts, usd, user):
    ko = make_equity("KO", usd)
    eq.buy_shares(
        accounts=accounts,
        instrument=ko,
        quantity=10,
        price="60",
        principal="600",
        timestamp=at(0),
        origin="import",
    )
    (proposal,) = detect(user)
    reject_proposal(proposal, note="not a trade, gift shares")
    # rejection: legs never left the bucket; fingerprint stays resolved
    assert len(default_bucket(user)) == 1
    assert detect(user) == []

    # a fresh but differently-shaped proposal would have a new fingerprint;
    # simulate by another fill (new cluster) and modify on confirm
    eq.buy_shares(
        accounts=accounts,
        instrument=ko,
        quantity=5,
        price="61",
        principal="305",
        timestamp=at(1),
        origin="import",
    )
    (proposal2,) = detect(user)
    trade = confirm_proposal(
        proposal2,
        name="KO long-term hold",
        structure="stock",
        horizon="long_term",
        note="user relabel",
    )
    assert trade.name == "KO long-term hold"
    proposal2.refresh_from_db()
    assert proposal2.evidence["modifications"]["name"] == "KO long-term hold"
    assert "long_term" in trade.get_tags_by_category()["horizon"]
