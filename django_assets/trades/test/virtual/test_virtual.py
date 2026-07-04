"""T5: virtual transfers (trades spec §2.3, ADR-0031).

Balanced events that move position and P&L BETWEEN trades with no
ledger involvement. Balance is DB-enforced at COMMIT; crossing warns,
never blocks; the sum of all books always equals the real allocations.
"""

import datetime
from decimal import Decimal

import pytest
from django.db import IntegrityError
from django.db import transaction as db_tx
from django.test import override_settings

from django_assets.trades.exceptions import UnbalancedVirtualTransferError
from django_assets.trades.models import Trade, VirtualEntry, VirtualTransfer
from django_assets.trades.virtual import record_virtual_transfer, transfer_position

from ..harness import inviolable
from ..trades.conftest import TS

pytestmark = pytest.mark.ledger

D = Decimal
LATER = TS + datetime.timedelta(days=2)


@pytest.fixture
def two_trades(user):
    return (
        Trade.objects.create(user=user, name="A"),
        Trade.objects.create(user=user, name="B"),
    )


def test_precheck_raises_unbalanced(user, two_trades, aapl):
    a, b = two_trades
    with pytest.raises(UnbalancedVirtualTransferError, match="AAPL"):
        record_virtual_transfer(
            user,
            TS,
            entries=[
                {"trade": a, "instrument": aapl, "amount": "-100"},
                {"trade": b, "instrument": aapl, "amount": "99"},
            ],
        )
    assert VirtualTransfer.objects.count() == 0


def test_trigger_backstops_raw_orm(user, two_trades, aapl):
    a, _b = two_trades
    with pytest.raises(IntegrityError, match="alanc"), db_tx.atomic():
        transfer = VirtualTransfer.objects.create(user=user, timestamp=TS)
        VirtualEntry.objects.create(
            transfer=transfer, trade=a, instrument=aapl, amount=D("-100")
        )


def test_trigger_catches_one_sided_delete(user, two_trades, aapl):
    a, b = two_trades
    transfer = record_virtual_transfer(
        user,
        TS,
        entries=[
            {"trade": a, "instrument": aapl, "amount": "-100"},
            {"trade": b, "instrument": aapl, "amount": "100"},
        ],
    )
    with pytest.raises(IntegrityError), db_tx.atomic():
        transfer.entries.first().delete()


def test_deferred_until_commit(user, two_trades, aapl):
    """Entries insert sequentially and only balance at COMMIT."""
    a, b = two_trades
    with db_tx.atomic():
        transfer = VirtualTransfer.objects.create(user=user, timestamp=TS)
        VirtualEntry.objects.create(
            transfer=transfer, trade=a, instrument=aapl, amount=D("-100")
        )
        VirtualEntry.objects.create(
            transfer=transfer, trade=b, instrument=aapl, amount=D("100")
        )
    assert VirtualEntry.objects.count() == 2


@override_settings(DJANGO_ASSETS_USE_DB_TRIGGERS=False)
def test_precheck_only_without_triggers(user, two_trades, aapl):
    a, b = two_trades
    with pytest.raises(UnbalancedVirtualTransferError):
        record_virtual_transfer(
            user, TS, entries=[{"trade": a, "instrument": aapl, "amount": "-1"}]
        )


def test_same_user_guard(user, two_trades, aapl):
    from django.contrib.auth import get_user_model

    a, _b = two_trades
    stranger = get_user_model().objects.create_user(username="stranger", password="x")
    foreign = Trade.objects.create(user=stranger, name="foreign")
    with pytest.raises(ValueError, match="user"):
        record_virtual_transfer(
            user,
            TS,
            entries=[
                {"trade": a, "instrument": aapl, "amount": "-1"},
                {"trade": foreign, "instrument": aapl, "amount": "1"},
            ],
        )


def test_adr_0031_golden_scenario(user, accounts, usd):
    """Put premium $50 → assignment (+100 sh, −$1,000) in A → transfer at
    $9 → covered-call premium $120 + real $9.50 sale in B.
    A: −$50. B: +$170. Aggregate +$120 == ledger net cash. One unified
    number per trade — no separate virtual field."""
    from django_assets.core.models import Account, Instrument
    from django_assets.instruments.options import templates as opt
    from django_assets.instruments.options.models import Deliverable, OptionMeta

    xyz = Instrument.objects.create(
        code="XYZ", quantity_decimals=0, price_decimals=4, price_currency=usd
    )
    put = Instrument.objects.create(
        code="XYZ P10", quantity_decimals=0, price_decimals=4,
        multiplier=D("100"), price_currency=usd,
    )
    call = Instrument.objects.create(
        code="XYZ C11", quantity_decimals=0, price_decimals=4,
        multiplier=D("100"), price_currency=usd,
    )
    put_meta = OptionMeta.objects.create(
        instrument=put, underlying=xyz, expiry=datetime.date(2026, 6, 18),
        strike=D("10"), right="P",
    )
    OptionMeta.objects.create(
        instrument=call, underlying=xyz, expiry=datetime.date(2026, 7, 17),
        strike=D("11"), right="C",
    )
    Deliverable.objects.create(
        option_meta=put_meta, instrument=xyz, quantity=D("100"),
        effective_from=datetime.date(2026, 1, 2),
    )
    routing = {**accounts}

    t0 = TS
    sell_put = opt.sell_option(
        accounts=routing, instrument=put, contracts="1", price="0.50", timestamp=t0
    )
    assignment = opt.assign_option(
        accounts=routing, instrument=put, contracts="1",
        timestamp=t0 + datetime.timedelta(days=10),
    )
    a = Trade.objects.create(user=user, name="put trade")
    a.assign(sell_put, quantity="1", instrument=put)
    a.assign(assignment, fraction="1")

    transfer_ts = t0 + datetime.timedelta(days=11)
    b = Trade.objects.create(user=user, name="covered call")
    with inviolable():
        event = transfer_position(
            a, b, instrument=xyz, quantity="100", price="9.00", timestamp=transfer_ts
        )
    assert event.warnings == []

    sell_call = opt.sell_option(
        accounts=routing, instrument=call, contracts="1", price="1.20",
        timestamp=t0 + datetime.timedelta(days=12),
    )
    expire = opt.expire_option(
        accounts=routing, instrument=call, contracts="-1",
        timestamp=t0 + datetime.timedelta(days=40),
    )
    from django_assets.instruments.equities import templates as eq

    sale = eq.sell_shares(
        accounts=routing, instrument=xyz, quantity="100", price="9.50",
        timestamp=t0 + datetime.timedelta(days=41),
    )
    b.assign(sell_call, quantity="1", instrument=call)
    b.assign(expire, fraction="1")
    b.assign(sale, quantity="100", instrument=xyz)

    pnl_a = a.calculate_pnl()
    pnl_b = b.calculate_pnl()
    assert pnl_a["realized_pnl"] == D("-50.00")
    assert "virtual_pnl" not in pnl_a  # one unified number
    assert pnl_b["realized_pnl"] == D("170.00")
    assert pnl_a["realized_pnl"] + pnl_b["realized_pnl"] == D("120.00")

    assert a.status == "closed"
    assert a.closed_date == transfer_ts  # closed purely virtually
    assert b.status == "closed"


def test_crossing_warns_never_blocks(user, two_trades, sale_tx, aapl):
    """A holds −1000 (short); moving −150 THROUGH what B holds (0→+150 is
    fine) vs pulling 150 out of a 100-share book warns but persists."""
    a, b = two_trades
    a.assign(sale_tx, quantity="100", instrument=aapl)  # book: −100
    event = transfer_position(
        a, b, instrument=aapl, quantity="150", price="200.00", timestamp=LATER
    )
    assert any(w.kind == "position" for w in event.warnings)
    assert VirtualEntry.objects.filter(trade=b, instrument=aapl).get().amount == D("-150")
    # Touch-zero close: no warning.
    c = Trade.objects.create(user=user, name="C")
    event2 = transfer_position(
        b, c, instrument=aapl, quantity="150", price="200.00", timestamp=LATER
    )
    assert event2.warnings == []


def test_multi_destination_event(user, aapl):
    a = Trade.objects.create(user=user, name="src")
    b = Trade.objects.create(user=user, name="dst1")
    c = Trade.objects.create(user=user, name="dst2")
    transfer = record_virtual_transfer(
        user,
        TS,
        entries=[
            {"trade": a, "instrument": aapl, "amount": "-100"},
            {"trade": b, "instrument": aapl, "amount": "60"},
            {"trade": c, "instrument": aapl, "amount": "40"},
        ],
    )
    assert transfer.entries.count() == 3
    assert b.net_position(aapl) == D("60")
    assert c.net_position(aapl) == D("40")


def test_conservation_property(user, two_trades, sale_tx, aapl):
    """Balanced transfers never change Σ positions or Σ TOTAL P&L at a
    common mark — virtual events reallocate P&L between trades (realized
    may crystallize in one and offset in the other's basis) but can
    never create or destroy it."""
    from django_assets.core.prices import StaticPriceSource

    source = StaticPriceSource({aapl: "195.00"})
    a, b = two_trades
    a.assign(sale_tx, quantity="1000", instrument=aapl)
    total_position = a.net_position(aapl) + b.net_position(aapl)
    total_before = (
        a.calculate_pnl(price_source=source)["total_pnl"]
        + b.calculate_pnl(price_source=source)["total_pnl"]
    )
    for index, qty in enumerate(("100", "37.5", "250")):
        transfer_position(
            a, b, instrument=aapl, quantity=qty, price="199.00",
            timestamp=LATER + datetime.timedelta(hours=index),
        )
    assert a.net_position(aapl) + b.net_position(aapl) == total_position
    total_after = (
        a.calculate_pnl(price_source=source)["total_pnl"]
        + b.calculate_pnl(price_source=source)["total_pnl"]
    )
    assert total_after == total_before


def test_check_consistency_retroactive_crossing(user, two_trades, sale_tx, aapl):
    a, b = two_trades
    a.assign(sale_tx, quantity="200", instrument=aapl)
    transfer_position(
        a, b, instrument=aapl, quantity="150", price="200.00", timestamp=LATER
    )
    assert a.check_consistency() == {"errors": [], "warnings": []}
    a.unassign(sale_tx)  # retroactively the transfer over-draws
    report = a.check_consistency()
    assert report["errors"] == []
    assert any("cross" in w.lower() for w in report["warnings"])
