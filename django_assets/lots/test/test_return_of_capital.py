"""ADR-0038 §3 fixture ladder: partial ROC, ROC-to-zero,
ROC-beyond-basis, ROC interleaved with sells — all under the untouched
conservation trigger (ROC is zero-quantity basis recovery, the law's
own primitive)."""

from decimal import Decimal

import pytest

from django_assets.instruments.equities import templates as eq
from django_assets.lots.models import Lot, LotMatch
from django_assets.lots.rebuild import rebuild_lots
from django_assets.lots.reports import income_summary, realized_gains
from django_assets.lots.test.conftest import at

pytestmark = pytest.mark.ledger

D = Decimal


def roc(accounts, instrument, amount, when):
    return eq.return_of_capital(
        accounts=accounts, instrument=instrument, amount=amount, timestamp=when
    )


def test_partial_roc_reduces_basis_pro_rata(accounts, aapl, buy):
    buy(100, "10", at(0))  # basis 1000
    buy(300, "20", at(1))  # basis 6000
    roc(accounts, aapl, "400", at(10))  # per-share 1.00: 100 / 300 split
    rebuild_lots(accounts["holdings"])

    lots = list(Lot.objects.filter(account=accounts["holdings"]).order_by("acquired_at"))
    assert [lot.cost_basis_remaining for lot in lots] == [D("900"), D("5700")]
    assert [lot.quantity_remaining for lot in lots] == [D("100"), D("300")]
    matches = LotMatch.objects.filter(lot__account=accounts["holdings"])
    assert all(m.quantity == 0 and m.metadata.get("return_of_capital") for m in matches)
    assert sum((m.basis_recovered for m in matches), D(0)) == D("400")
    assert all(m.realized_gain == 0 for m in matches)
    # pure basis reductions stay off the 1099-B listing
    assert realized_gains(accounts["holdings"]) == []


def test_roc_to_zero_then_excess_is_gain(accounts, aapl, buy):
    buy(100, "10", at(0))  # basis 1000
    roc(accounts, aapl, "1000", at(5))  # drains basis exactly
    roc(accounts, aapl, "250", at(400))  # beyond basis → capital gain, long-term
    rebuild_lots(accounts["holdings"])

    lot = Lot.objects.get(account=accounts["holdings"])
    assert lot.cost_basis_remaining == 0
    assert lot.quantity_remaining == D("100")
    gains = realized_gains(accounts["holdings"])
    assert len(gains) == 1
    assert gains[0]["realized_gain"] == D("250")
    assert gains[0]["return_of_capital"] is True
    assert gains[0]["term"] == "long"


def test_roc_interleaved_with_sells(accounts, aapl, buy, sell):
    buy(200, "10", at(0))  # basis 2000
    roc(accounts, aapl, "200", at(3))  # basis → 1800 (9/share)
    sell(100, "15", at(6))  # closes half: basis consumed 900, gain 600
    roc(accounts, aapl, "100", at(9))  # applies to the remaining 100 only
    rebuild_lots(accounts["holdings"])

    lot = Lot.objects.get(account=accounts["holdings"])
    assert lot.quantity_remaining == D("100")
    # 2000 − 200(roc) − 900(sell slice) − 100(roc) = 800
    assert lot.cost_basis_remaining == D("800")
    sale_rows = [r for r in realized_gains(accounts["holdings"]) if not r["return_of_capital"]]
    assert len(sale_rows) == 1
    assert sale_rows[0]["realized_gain"] == D("600")


def test_income_summary_boxes(accounts, aapl, usd, buy):
    buy(100, "10", at(0))
    eq.dividend_received(
        accounts=accounts,
        instrument=aapl,
        amount="70",
        timestamp=at(2),
        character="qualified",
        character_label="Qualified Dividend",
    )
    eq.dividend_received(
        accounts=accounts,
        instrument=aapl,
        amount="30",
        timestamp=at(3),
        character="ordinary",
        character_label="Non-Qualified Div",
    )
    eq.dividend_received(
        accounts=accounts, instrument=aapl, amount="11", timestamp=at(4)
    )  # unclassified default
    roc(accounts, aapl, "40", at(5))

    summary = income_summary(accounts["cash"])
    assert summary["box_1a_total_ordinary"] == D("100")
    assert summary["box_1b_qualified"] == D("70")
    assert summary["box_3_nondividend_distributions"] == D("40")
    assert summary["unclassified"] == D("11")
    assert "Qualified Dividend" in summary["labels"]["qualified"]


def test_characterize_income_user_precedence(accounts, aapl, buy):
    from django_assets.core.income import characterize_income

    tx = eq.dividend_received(accounts=accounts, instrument=aapl, amount="11", timestamp=at(4))
    assert tx.metadata["income_character"] == "unclassified"
    characterize_income(tx, "qualified", label="per 1099-DIV box 1b")
    tx.refresh_from_db()
    assert tx.metadata["income_character"] == "qualified"
    assert tx.metadata["income_character_source"] == "user"
    assert tx.metadata["income_character_history"][0]["income_character"] == "unclassified"
