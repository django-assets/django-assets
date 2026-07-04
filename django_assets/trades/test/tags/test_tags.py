"""T3: tagging + query API (trades spec §2.5/§5, ADR-0030 §5)."""

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.db import transaction as db_tx

from django_assets.trades.models import Tag, TagCategory, Trade

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="trader", password="x")


@pytest.fixture
def trade(user):
    return Trade.objects.create(user=user, name="swing")


def test_uniqueness(user):
    strategy = TagCategory.objects.create(user=user, code="strategy", name="Strategy")
    Tag.objects.create(category=strategy, name="covered-call")
    with pytest.raises(IntegrityError), db_tx.atomic():
        TagCategory.objects.create(user=user, code="strategy", name="Again")
    with pytest.raises(IntegrityError), db_tx.atomic():
        Tag.objects.create(category=strategy, name="covered-call")


def test_add_tag_get_or_create(user, trade):
    tag = trade.add_tag("strategy", "swing-trade")
    assert tag.category.user == user
    same = trade.add_tag("strategy", "swing-trade")
    assert same.pk == tag.pk
    assert trade.tags.count() == 1
    trade.remove_tag("strategy", "swing-trade")
    assert trade.tags.count() == 0


def test_cross_user_tag_attachment_rejected(user, trade):
    other = get_user_model().objects.create_user(username="other", password="x")
    foreign_category = TagCategory.objects.create(user=other, code="x", name="X")
    foreign_tag = Tag.objects.create(category=foreign_category, name="theirs")
    with pytest.raises(ValueError, match="same user"):
        trade.tags.add(foreign_tag)


def test_queryset_tag_semantics(user, django_assert_num_queries):
    a = Trade.objects.create(user=user, name="a")
    b = Trade.objects.create(user=user, name="b")
    c = Trade.objects.create(user=user, name="c")
    a.add_tag("strategy", "swing")
    a.add_tag("conviction", "high")
    b.add_tag("strategy", "swing")
    c.add_tag("strategy", "income")

    assert set(Trade.objects.with_tag("strategy", "swing")) == {a, b}
    assert set(Trade.objects.with_category("conviction")) == {a}
    # AND across categories, OR within.
    assert set(Trade.objects.with_tags(strategy=["swing", "income"], conviction="high")) == {a}
    assert set(Trade.objects.with_tags_any("strategy", ["swing", "income"])) == {a, b, c}
    a.add_tag("strategy", "income")
    assert set(Trade.objects.with_tags_all("strategy", ["swing", "income"])) == {a}

    with django_assert_num_queries(1):
        list(Trade.objects.with_tags(strategy="swing").select_related())


def test_tag_filter_composition(user):
    from django_assets.trades.queries import TagFilter

    a = Trade.objects.create(user=user, name="a")
    b = Trade.objects.create(user=user, name="b")
    a.add_tag("strategy", "swing")
    b.add_tag("risk", "low")
    q = TagFilter("strategy", "swing") | TagFilter("risk", "low")
    assert set(Trade.objects.filter(q.q).distinct()) == {a, b}


def test_for_user_and_hierarchy_queries(user):
    other = get_user_model().objects.create_user(username="other2", password="x")
    mine = Trade.objects.create(user=user, name="mine")
    child = Trade.objects.create(user=user, name="child", parent=mine)
    grand = Trade.objects.create(user=user, name="grand", parent=child)
    Trade.objects.create(user=other, name="theirs")

    assert set(Trade.objects.for_user(user)) == {mine, child, grand}
    assert set(Trade.objects.for_user(user).root_trades()) == {mine}
    assert set(Trade.objects.children_of(mine)) == {child}
    assert set(Trade.objects.descendants_of(mine)) == {child, grand}
    assert set(Trade.objects.ancestors_of(grand)) == {mine, child}


def test_unallocated_legs_and_transaction_helpers(user):
    import datetime

    from django_assets.core.builder import TransactionBuilder
    from django_assets.core.models import Account, Instrument
    from django_assets.trades.queries import transactions_for, unallocated_legs

    usd = Instrument.objects.create(code="USD", quantity_decimals=2)
    cash = Account.objects.create(owner=user, name="cash")
    external = Account.objects.create(owner=user, name="external")
    with TransactionBuilder(
        account=cash, timestamp=datetime.datetime(2026, 3, 13, tzinfo=datetime.UTC)
    ) as b:
        b.add_leg(account=cash, instrument=usd, amount="10.00")
        b.add_leg(account=external, instrument=usd, amount="-10.00")
    tx = b.transaction

    assert unallocated_legs(account=cash).count() == 1
    trade = Trade.objects.create(user=user, name="organizer")
    trade.assign_leg(tx.legs.get(account=cash), "10.00")
    assert unallocated_legs(account=cash).count() == 0
    assert list(transactions_for(trade)) == [tx]
