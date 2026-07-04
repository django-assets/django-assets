"""C5: Holding — spec §6, ADR-0007/0012/0016.

Plain value class over live leg aggregation. `historical` filters by
settlement timestamp (transaction.timestamp <= as_of): the ADR-0012 T+1
worked example is the canonical test.
"""

import datetime
from decimal import Decimal

import pytest

from django_assets.core.queries import Holding

from .conftest import BTC_SELL_TS, DEPOSIT_TS, SELL_TS, SETTLE_TS, TRADE_TS

pytestmark = pytest.mark.ledger

D = Decimal
UTC = datetime.UTC


def test_current_position(history, aapl):
    assert Holding.current(history["holdings"], aapl) == D("60")


def test_cash_is_a_holding(history, usd):
    """ADR-0013: the USD balance uses the same API as the AAPL position."""
    assert Holding.current(history["cash"], usd) == D("6100.00")


def test_zero_for_untouched_instrument(history, usd, aapl):
    """An instrument with no legs in this account is an exact Decimal zero."""
    balance = Holding.current(history["holdings"], usd)
    assert balance == D("0")
    assert isinstance(balance, Decimal)


def test_historical_before_any_activity(history, usd):
    before = DEPOSIT_TS - datetime.timedelta(days=1)
    assert Holding.historical(history["cash"], usd, as_of=before) == D("0")


def test_t_plus_one_settlement_semantics(history, aapl):
    """ADR-0012: bought Tuesday, settles Wednesday. Tuesday-evening query
    excludes the position; Wednesday-after-settlement query includes it."""
    tuesday_evening = TRADE_TS.replace(hour=23, minute=59)
    wednesday_after = SETTLE_TS + datetime.timedelta(hours=1)
    assert Holding.historical(history["holdings"], aapl, as_of=tuesday_evening) == D("0")
    assert Holding.historical(history["holdings"], aapl, as_of=wednesday_after) == D("100")


def test_historical_boundary_is_inclusive(history, aapl):
    """as_of exactly at settlement includes the transaction (<=)."""
    assert Holding.historical(history["holdings"], aapl, as_of=SETTLE_TS) == D("100")


def test_historical_after_partial_sale(history, aapl):
    after_sale = SELL_TS + datetime.timedelta(hours=1)
    assert Holding.historical(history["holdings"], aapl, as_of=after_sale) == D("60")


def test_current_equals_historical_now(history, btc):
    after_everything = BTC_SELL_TS + datetime.timedelta(days=1)
    assert Holding.current(history["holdings"], btc) == D("0")
    assert Holding.historical(history["holdings"], btc, as_of=after_everything) == D("0")
