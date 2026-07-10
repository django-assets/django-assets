"""Seed the option-tracker demo portfolio with REAL contracts.

Discovers live option contracts from MarketData (metered: ~60–120
credits per run — run once, not in a loop), then books a
reference-shaped portfolio into the ledger: cash, wheel campaigns
(shares + covered calls), open credit/debit spreads, iron condors, long
calls, cash-secured puts, a couple of rolled positions, and a few months
of closed history with fees. Strategy tags come from the library's own
classifier (ADR-0037). Prices at render time are always live via the
connector; the seed only fixes QUANTITIES and PREMIUMS (premiums are
plausibly derived from marks at seed time).

Usage: uv run python manage.py seed_tracker [--reset]
"""

import datetime
import random
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction as db_transaction
from django.utils import timezone
from django_assets_prices_marketdata.calendar import EASTERN
from django_assets_prices_marketdata.client import MarketDataClient, NoData

from django_assets.core.builder import TransactionBuilder
from django_assets.core.models import Account, Identifier, Instrument
from django_assets.instruments.options.models import OptionMeta
from django_assets.trades.detection import classify_structure
from django_assets.trades.models import Trade

D = Decimal
DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo"


class Command(BaseCommand):
    help = "Seed the option-tracker demo portfolio (live contract discovery; metered)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--reset", action="store_true", help="wipe the demo user first")

    def handle(self, *args: Any, **options: Any) -> None:
        random.seed(20260710)  # deterministic quantities/dates per seed day
        self.client = MarketDataClient()
        User = get_user_model()
        if options["reset"]:
            User.objects.filter(username=DEMO_USERNAME).delete()
        if User.objects.filter(username=DEMO_USERNAME).exists():
            self.stdout.write("demo user already seeded; use --reset to rebuild")
            return
        with db_transaction.atomic():
            self._seed()
        self.stdout.write(
            self.style.SUCCESS(f"seeded (vendor credits consumed: {self.client.credits_consumed})")
        )

    # -- vendor helpers -----------------------------------------------------

    def _chain_pick(
        self, symbol: str, *, side: str, dte: int, delta: str | None = None, strikes: int = 1
    ) -> "list[dict[str, Any]]":
        params: dict[str, str] = {"side": side, "dte": str(dte)}
        if delta:
            params["delta"] = delta  # strikeLimit would override the delta pick
        else:
            params["strikeLimit"] = str(strikes)
        payload = self.client.get(f"/v1/options/chain/{symbol}/", params)
        if isinstance(payload, NoData):
            return []
        count = len(payload.get("optionSymbol", []))
        return [
            {key: values[index] for key, values in payload.items() if isinstance(values, list)}
            for index in range(count)
        ]

    def _mark(self, row: "dict[str, Any]") -> Decimal:
        for key in ("mid", "last", "ask", "bid"):
            value = row.get(key)
            if isinstance(value, Decimal | int) and value:
                return Decimal(value)
        return D("0.50")

    # -- ledger helpers ------------------------------------------------------

    def _instrument(self, code: str, **kwargs: Any) -> Instrument:
        instrument, _ = Instrument.objects.get_or_create(code=code, defaults=kwargs)
        return instrument

    def _ticker(self, code: str) -> Instrument:
        instrument = self._instrument(
            code, quantity_decimals=4, price_decimals=2, price_currency=self.usd
        )
        Identifier.objects.get_or_create(
            instrument=instrument, type="ticker", value=code, is_active=True
        )
        return instrument

    def _option(self, row: "dict[str, Any]", underlying: Instrument) -> Instrument:
        symbol = row["optionSymbol"]
        expiry = datetime.datetime.fromtimestamp(int(row["expiration"]), tz=EASTERN).date()
        instrument = self._instrument(
            symbol,
            quantity_decimals=0,
            price_decimals=4,
            multiplier=D("100"),
            price_currency=self.usd,
        )
        OptionMeta.objects.get_or_create(
            instrument=instrument,
            defaults={
                "underlying": underlying,
                "expiry": expiry,
                "strike": Decimal(row["strike"]),
                "right": "C" if row["side"] == "call" else "P",
            },
        )
        Identifier.objects.get_or_create(
            instrument=instrument, type="opra", value=symbol, is_active=True
        )
        return instrument

    def _book(
        self,
        *,
        ts: datetime.datetime,
        position_legs: "list[tuple[Instrument, Decimal | int]]",
        cash: Decimal | None = None,
        fee: Decimal | None = None,
        description: str = "",
    ) -> Any:
        a = self.accounts
        with TransactionBuilder(account=a["cash"], timestamp=ts, description=description) as b:
            for instrument, amount in position_legs:
                b.add_leg(account=a["holdings"], instrument=instrument, amount=str(amount))
                b.add_leg(account=a["market"], instrument=instrument, amount=str(-D(amount)))
            if cash:
                b.add_leg(account=a["cash"], instrument=self.usd, amount=str(cash))
                b.add_leg(account=a["market"], instrument=self.usd, amount=str(-cash))
            if fee:
                b.add_leg(account=a["cash"], instrument=self.usd, amount=str(-fee))
                b.add_leg(account=a["market"], instrument=self.usd, amount=str(fee))
        return b.transaction

    def _trade(self, name: str, transactions: "list[Any]") -> Trade:
        trade = Trade.objects.create(user=self.user, name=name)
        for tx in transactions:
            trade.assign(tx, fraction=1)
        # Live trades classify over ALL legs (closed cohorts net away, so
        # the live structure remains — shares + short calls read as a
        # covered call). Fully-closed trades net to nothing, so they
        # classify over their OPENING transaction's structure instead.
        allocations = list(
            trade.allocations.filter(category="").select_related(
                "leg__instrument", "leg__transaction"
            )
        )
        legs = [allocation.leg for allocation in allocations]
        if legs and trade.status == "closed":
            by_tx: dict[Any, list[Any]] = {}
            for allocation in allocations:
                by_tx.setdefault(allocation.leg.transaction.timestamp, []).append(allocation.leg)
            legs = by_tx[sorted(by_tx)[0]]
        if legs:
            trade.add_tag("strategy", classify_structure(legs))
        return trade

    def _fee(self, contracts: int) -> Decimal:
        return (D("1.30") * contracts).quantize(D("0.01"))

    # -- the portfolio ----------------------------------------------------------

    def _seed(self) -> None:
        User = get_user_model()
        self.user = User.objects.create_user(username=DEMO_USERNAME, password=DEMO_PASSWORD)
        self.usd = self._instrument("USD", quantity_decimals=2, price_decimals=2)
        self.accounts = {
            name: Account.objects.create(owner=self.user, name=name)
            for name in ("cash", "holdings", "market")
        }
        now = timezone.now()
        self.stdout.write("depositing cash…")
        with TransactionBuilder(
            account=self.accounts["cash"],
            timestamp=now - datetime.timedelta(days=200),
            description="opening deposit",
        ) as b:
            b.add_leg(account=self.accounts["cash"], instrument=self.usd, amount="250000.00")
            b.add_leg(account=self.accounts["market"], instrument=self.usd, amount="-250000.00")

        # -- open strategies (live contracts) --------------------------------
        spreads = [  # (symbol, side, contracts, dte, short_delta)
            ("AAPL", "put", 5, 40, ".30"),
            ("NVDA", "put", 5, 40, ".25"),
            ("GOOGL", "put", 4, 40, ".20"),
            ("HIMS", "put", 3, 12, ".35"),
            ("TSLA", "put", 3, 40, ".30"),
            ("GLD", "call", 10, 40, ".20"),
            ("LULU", "put", 5, 40, ".22"),
        ]
        for symbol, side, contracts, dte, delta in spreads:
            self._credit_spread(symbol, side=side, contracts=contracts, dte=dte, delta=delta)

        self._iron_condor("SPY", contracts=10, dte=40)
        self._iron_condor("QQQ", contracts=5, dte=22)
        self._long_option("UNH", side="call", contracts=1, dte=340)
        self._long_option("GIS", side="call", contracts=2, dte=430)
        self._short_put("MSFT", contracts=2, dte=40, rolls=0)
        self._short_put("OSCR", contracts=5, dte=8, rolls=0)
        self._short_put("CRCL", contracts=2, dte=40, rolls=2)  # rolled twice
        self._credit_spread("IBIT", side="put", contracts=8, dte=40, delta=".30", rolls=1)

        # -- wheel campaigns ---------------------------------------------------
        for symbol, shares, basis_drift in (
            ("ETHA", 800, D("1.15")),
            ("HIMS", 300, D("1.08")),
            ("IBIT", 200, D("1.10")),
            ("PYPL", 100, D("1.03")),
            ("OSCR", 500, D("0.95")),
        ):
            self._wheel(symbol, shares=shares, basis_drift=basis_drift)

        # -- closed history ------------------------------------------------------
        self.stdout.write("booking closed history…")
        history = [
            ("AAPL", "put", 10, "bull_put_spread"),
            ("NVDA", "put", 10, "bull_put_spread"),
            ("HIMS", "put", 10, "bull_put_spread"),
            ("GLD", "call", 5, "bear_call_spread"),
            ("MSFT", "put", 5, "short_put"),
            ("OSCR", "put", 5, "short_put"),
            ("TSLA", "put", 3, "bull_put_spread"),
            ("PYPL", "put", 10, "short_put"),
            ("ETHA", "call", 3, "covered-ish"),
            ("QQQ", "put", 10, "bull_put_spread"),
            ("SPY", "put", 10, "bull_put_spread"),
            ("IBIT", "put", 8, "short_put"),
            ("LULU", "put", 5, "bull_put_spread"),
            ("GOOGL", "put", 4, "bull_put_spread"),
            ("UNH", "put", 2, "short_put"),
        ]
        day = 15
        for symbol, side, contracts, kind in history:
            self._closed_history(symbol, side=side, contracts=contracts, days_ago=day, kind=kind)
            day += random.randint(4, 11)

    # -- strategy builders --------------------------------------------------------

    def _credit_spread(
        self, symbol: str, *, side: str, contracts: int, dte: int, delta: str, rolls: int = 0
    ) -> None:
        self.stdout.write(f"open {symbol} {side} credit spread…")
        underlying = self._ticker(symbol)
        short_target = int(delta.strip("."))
        wing_target = max(short_target - 13, 5)
        rows = self._chain_pick(symbol, side=side, dte=dte, delta=delta)
        rows += self._chain_pick(symbol, side=side, dte=dte, delta=f".{wing_target:02d}")
        seen: set[str] = set()
        rows = [r for r in rows if not (r["optionSymbol"] in seen or seen.add(r["optionSymbol"]))]
        if len(rows) < 2:
            rows += self._chain_pick(symbol, side=side, dte=dte, delta=".08")
            rows = [
                r for r in rows if not (r["optionSymbol"] in seen or seen.add(r["optionSymbol"]))
            ]
        if len(rows) < 2:
            self.stdout.write(self.style.WARNING(f"  no chain for {symbol}; skipped"))
            return
        rows.sort(key=lambda row: Decimal(row["strike"]), reverse=(side == "put"))
        short_row, long_row = rows[0], rows[1]
        short = self._option(short_row, underlying)
        long_ = self._option(long_row, underlying)
        credit = ((self._mark(short_row) - self._mark(long_row)) * contracts * 100).quantize(
            D("0.01")
        )
        credit = max(credit, D("25.00"))
        now = timezone.now()
        opened = now - datetime.timedelta(days=random.randint(5, 20))
        transactions = []
        if rolls:
            # earlier segments: same short strike, previous expiry — book
            # as open + close pairs before the live cohort.
            for index in range(rolls):
                seg_open = opened - datetime.timedelta(days=14 * (rolls - index))
                seg_close = seg_open + datetime.timedelta(days=13)
                seg_credit = (credit * D("0.9")).quantize(D("0.01"))
                seg_debit = (seg_credit * D("0.4")).quantize(D("0.01"))
                transactions.append(
                    self._book(
                        ts=seg_open,
                        position_legs=[(short, -contracts), (long_, contracts)],
                        cash=seg_credit,
                        fee=self._fee(contracts),
                        description=f"{symbol} spread segment {index}",
                    )
                )
                transactions.append(
                    self._book(
                        ts=seg_close,
                        position_legs=[(short, contracts), (long_, -contracts)],
                        cash=-seg_debit,
                        fee=self._fee(contracts),
                        description=f"{symbol} spread segment {index} close",
                    )
                )
        transactions.append(
            self._book(
                ts=opened,
                position_legs=[(short, -contracts), (long_, contracts)],
                cash=credit,
                fee=self._fee(contracts * 2),
                description=f"open {symbol} {side} credit spread",
            )
        )
        self._trade(f"{symbol} {side} credit spread", transactions)

    def _iron_condor(self, symbol: str, *, contracts: int, dte: int) -> None:
        self.stdout.write(f"open {symbol} iron condor…")
        underlying = self._ticker(symbol)

        def two(side: str) -> "list[dict[str, Any]]":
            rows = self._chain_pick(symbol, side=side, dte=dte, delta=".10")
            rows += self._chain_pick(symbol, side=side, dte=dte, delta=".04")
            seen: set[str] = set()
            return [
                r for r in rows if not (r["optionSymbol"] in seen or seen.add(r["optionSymbol"]))
            ]

        puts = two("put")
        calls = two("call")
        if len(puts) < 2 or len(calls) < 2:
            self.stdout.write(self.style.WARNING(f"  thin chain for {symbol}; skipped"))
            return
        puts.sort(key=lambda row: Decimal(row["strike"]), reverse=True)
        calls.sort(key=lambda row: Decimal(row["strike"]))
        legs = [
            (self._option(puts[0], underlying), -contracts, self._mark(puts[0])),
            (self._option(puts[1], underlying), contracts, self._mark(puts[1])),
            (self._option(calls[0], underlying), -contracts, self._mark(calls[0])),
            (self._option(calls[1], underlying), contracts, self._mark(calls[1])),
        ]
        credit = (
            sum((mark * (1 if qty < 0 else -1) for _inst, qty, mark in legs), D(0))
            * contracts
            * 100
        )
        credit = max(credit.quantize(D("0.01")), D("50.00"))
        tx = self._book(
            ts=timezone.now() - datetime.timedelta(days=random.randint(4, 15)),
            position_legs=[(inst, qty) for inst, qty, _ in legs],
            cash=credit,
            fee=self._fee(contracts * 4),
            description=f"open {symbol} iron condor",
        )
        self._trade(f"{symbol} iron condor", [tx])

    def _long_option(self, symbol: str, *, side: str, contracts: int, dte: int) -> None:
        self.stdout.write(f"open {symbol} long {side}…")
        underlying = self._ticker(symbol)
        rows = self._chain_pick(symbol, side=side, dte=dte, delta=".40", strikes=2)
        if not rows:
            self.stdout.write(self.style.WARNING(f"  no chain for {symbol}; skipped"))
            return
        row = rows[0]
        option = self._option(row, underlying)
        debit = (self._mark(row) * contracts * 100).quantize(D("0.01"))
        tx = self._book(
            ts=timezone.now() - datetime.timedelta(days=random.randint(10, 60)),
            position_legs=[(option, contracts)],
            cash=-debit,
            fee=self._fee(contracts),
            description=f"open {symbol} long {side}",
        )
        self._trade(f"{symbol} long {side}", [tx])

    def _short_put(self, symbol: str, *, contracts: int, dte: int, rolls: int) -> None:
        self.stdout.write(f"open {symbol} short put…")
        underlying = self._ticker(symbol)
        rows = self._chain_pick(symbol, side="put", dte=dte, delta=".30", strikes=1)
        if not rows:
            self.stdout.write(self.style.WARNING(f"  no chain for {symbol}; skipped"))
            return
        row = rows[0]
        option = self._option(row, underlying)
        credit = (self._mark(row) * contracts * 100).quantize(D("0.01"))
        now = timezone.now()
        opened = now - datetime.timedelta(days=random.randint(3, 12))
        transactions = []
        for index in range(rolls):
            seg_open = opened - datetime.timedelta(days=12 * (rolls - index))
            seg_close = seg_open + datetime.timedelta(days=11)
            seg_credit = (credit * D("0.85")).quantize(D("0.01"))
            seg_debit = (seg_credit * D("0.35")).quantize(D("0.01"))
            transactions.append(
                self._book(
                    ts=seg_open,
                    position_legs=[(option, -contracts)],
                    cash=seg_credit,
                    fee=self._fee(contracts),
                    description=f"{symbol} short put segment {index}",
                )
            )
            transactions.append(
                self._book(
                    ts=seg_close,
                    position_legs=[(option, contracts)],
                    cash=-seg_debit,
                    fee=self._fee(contracts),
                    description=f"{symbol} short put segment {index} close",
                )
            )
        transactions.append(
            self._book(
                ts=opened,
                position_legs=[(option, -contracts)],
                cash=credit,
                fee=self._fee(contracts),
                description=f"open {symbol} short put",
            )
        )
        self._trade(f"{symbol} short put", transactions)

    def _wheel(self, symbol: str, *, shares: int, basis_drift: Decimal) -> None:
        self.stdout.write(f"wheel campaign {symbol}…")
        underlying = self._ticker(symbol)
        quote = self.client.get(f"/v1/stocks/quotes/{symbol}/")
        if isinstance(quote, NoData):
            self.stdout.write(self.style.WARNING(f"  no quote for {symbol}; skipped"))
            return
        mark = Decimal(
            quote["mid"][0] if quote.get("mid") and quote["mid"][0] else quote["last"][0]
        )
        basis = (mark * basis_drift).quantize(D("0.01"))
        cost = (basis * shares).quantize(D("0.01"))
        now = timezone.now()
        acquired = now - datetime.timedelta(days=random.randint(45, 120))
        buy = self._book(
            ts=acquired,
            position_legs=[(underlying, shares)],
            cash=-cost,
            description=f"assigned {shares} {symbol}",
        )
        transactions = [buy]
        contracts = shares // 100
        rows = self._chain_pick(symbol, side="call", dte=30, delta=".20", strikes=1)
        if rows and contracts:
            option = self._option(rows[0], underlying)
            credit = (self._mark(rows[0]) * contracts * 100).quantize(D("0.01"))
            transactions.append(
                self._book(
                    ts=now - datetime.timedelta(days=random.randint(2, 12)),
                    position_legs=[(option, -contracts)],
                    cash=credit,
                    fee=self._fee(contracts),
                    description=f"covered call on {symbol}",
                )
            )
        self._trade(f"{symbol} wheel", transactions)

    def _closed_history(
        self, symbol: str, *, side: str, contracts: int, days_ago: int, kind: str = ""
    ) -> None:
        underlying = self._ticker(symbol)
        expiry = (timezone.now() - datetime.timedelta(days=days_ago - 10)).date()
        strike = D(random.randrange(20, 400))
        code = f"{symbol}{expiry:%y%m%d}{'P' if side == 'put' else 'C'}{int(strike * 1000):08d}"
        option = self._instrument(
            code,
            quantity_decimals=0,
            price_decimals=4,
            multiplier=D("100"),
            price_currency=self.usd,
        )
        OptionMeta.objects.get_or_create(
            instrument=option,
            defaults={
                "underlying": underlying,
                "expiry": expiry,
                "strike": strike,
                "right": "P" if side == "put" else "C",
            },
        )
        legs: list[tuple[Instrument, int]] = [(option, -contracts)]
        if "spread" in kind:  # two-leg vertical for reference-shaped history
            wing_strike = strike - 5 if side == "put" else strike + 5
            wing_code = (
                f"{symbol}{expiry:%y%m%d}{'P' if side == 'put' else 'C'}"
                f"{int(wing_strike * 1000):08d}"
            )
            wing = self._instrument(
                wing_code,
                quantity_decimals=0,
                price_decimals=4,
                multiplier=D("100"),
                price_currency=self.usd,
            )
            OptionMeta.objects.get_or_create(
                instrument=wing,
                defaults={
                    "underlying": underlying,
                    "expiry": expiry,
                    "strike": wing_strike,
                    "right": "P" if side == "put" else "C",
                },
            )
            legs.append((wing, contracts))
        open_ts = timezone.now() - datetime.timedelta(days=days_ago)
        close_ts = open_ts + datetime.timedelta(days=random.randint(4, 9))
        premium = D(random.randrange(150, 1700)) + D("0.75")
        win = random.random() > 0.25
        debit = (premium * (D("0.35") if win else D("1.55"))).quantize(D("0.01"))
        open_tx = self._book(
            ts=open_ts,
            position_legs=legs,
            cash=premium,
            fee=self._fee(contracts * len(legs)),
            description=f"open {symbol} history",
        )
        close_tx = self._book(
            ts=close_ts,
            position_legs=[(inst, -qty) for inst, qty in legs],
            cash=-debit,
            fee=self._fee(contracts * len(legs)),
            description=f"close {symbol} history",
        )
        self._trade(f"{symbol} history {days_ago}", [open_tx, close_tx])
