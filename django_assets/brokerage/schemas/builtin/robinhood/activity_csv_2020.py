"""Robinhood account-activity CSV (real export format, validated
against 2020–2024 statements).

Columns: Activity Date, Process Date, Settle Date, Instrument,
Description, Trans Code, Quantity, Price, Amount. Money reads
"$1,234.56" / "($5.00)"; descriptions may span lines (quoted);
each file ends with a disclaimer footer (no leading date — skipped).
Option details live in the Description ("PSTH 4/23/2021 Call $30.00");
amounts are all-in (no separate fee column). Files list newest-first;
parsing reverses so position-dependent dispatch sees prior state.
"""

import csv
import datetime
import io
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from django_assets.brokerage.accounts import ensure_standard_accounts
from django_assets.brokerage.schemas import ImportSchema, register_schema
from django_assets.brokerage.schemas.instruments import (
    ensure_currency,
    ensure_equity,
    option_from_robinhood_description,
    parse_money,
)
from django_assets.core.models import Transaction
from django_assets.core.queries import Holding
from django_assets.instruments.equities import templates as eq
from django_assets.instruments.options import templates as opt

COLUMNS = [
    "Activity Date",
    "Process Date",
    "Settle Date",
    "Instrument",
    "Description",
    "Trans Code",
    "Quantity",
    "Price",
    "Amount",
]
DATE_ROW = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")

OPTION_TRADE_CODES = {"BTO", "BTC", "STO", "STC"}
DIVIDEND_CODES = {"CDIV", "MDIV", "CIL"}
CASH_MOVE_CODES = {"ACH", "RTP"}


@register_schema(
    broker="robinhood",
    document_kind="activity",
    format_kind="csv",
    version="2020.1",
    name="Robinhood account activity CSV",
)
class RobinhoodActivityCsv2020(ImportSchema):
    definition = {"layout": "tabular", "columns": COLUMNS}

    @classmethod
    def sniff(cls, sample: str) -> bool:
        """The activity-CSV header row is the fingerprint."""
        return "Activity Date" in sample[:200] and "Trans Code" in sample[:200]

    def parse_batch(self, batch: Any, source: Any) -> Any:
        from django_assets.brokerage.models import ImportLine

        text = source if isinstance(source, str) else source.read()
        rows = [
            row
            for row in csv.reader(io.StringIO(text))
            if len(row) >= 9 and DATE_ROW.match(row[0] or "") and row[5]
        ]
        rows.reverse()
        for number, row in enumerate(rows, start=1):
            yield ImportLine(
                batch=batch,
                line_number=number,
                raw_data=row,
                kind=f"broker_{row[5].lower()}",
                source_reference=f"{batch.file_name}#{number}",
            )

    def materialize_line(self, line: Any) -> list[Transaction]:
        row = line.raw_data
        activity, _process, settle, symbol, description, code, quantity, price, amount = row[:9]
        accounts = self._accounts(line.batch)
        usd = ensure_currency("USD")
        common: dict[str, Any] = {
            "accounts": accounts,
            "timestamp": _at(settle),
            "trade_timestamp": _at(activity),
            "origin": "import",
        }
        net = parse_money(amount)
        qty = Decimal(quantity.replace(",", "")) if quantity else Decimal(0)
        first_line = description.splitlines()[0][:90] if description else ""

        if code in ("Buy", "Sell"):
            instrument = ensure_equity(symbol, currency=usd)
            buying = code == "Buy"
            if net == 0 and price:
                net = (-1 if buying else 1) * qty * parse_money(price)
            template: Callable[..., Transaction] = eq.buy_shares if buying else eq.sell_shares
            return [
                template(
                    instrument=instrument,
                    quantity=abs(qty),
                    price=parse_money(price) if price else "0",
                    principal=abs(net),
                    description=f"{code} {quantity} {symbol} (Robinhood)",
                    **common,
                )
            ]

        if code in OPTION_TRADE_CODES:
            option = option_from_robinhood_description(description, currency=usd)
            if option is None:
                raise ValueError(f"unparseable option description {description!r}")
            selling = code.startswith("S")
            option_template: Callable[..., Transaction] = (
                opt.sell_option if selling else opt.buy_option
            )
            return [
                option_template(
                    instrument=option,
                    contracts=abs(qty),
                    price=parse_money(price) if price else "0",
                    principal=abs(net),
                    description=f"{code} {quantity} {option.code} (Robinhood)",
                    **common,
                )
            ]

        if code == "OEXP":
            option = option_from_robinhood_description(description, currency=usd)
            if option is None:
                raise ValueError(f"unparseable option description {description!r}")
            position = Holding.current(accounts["holdings"], option)
            contracts = abs(qty) if position > 0 else -abs(qty) if position < 0 else abs(qty)
            return [
                opt.expire_option(
                    instrument=option,
                    contracts=contracts,
                    description=f"OEXP {quantity} {option.code} (Robinhood)",
                    **common,
                )
            ]

        if code in DIVIDEND_CODES:
            instrument = ensure_equity(symbol, currency=usd) if symbol else usd
            return [
                eq.dividend_received(
                    instrument=instrument,
                    amount=net,
                    currency=usd,
                    description=f"{code} {symbol or 'cash'}: {first_line} (Robinhood)",
                    **common,
                )
            ]

        from django_assets.brokerage import templates as plumbing

        if code == "SLIP":
            return [
                plumbing.interest_earned(
                    currency=usd, amount=net, description=f"Stock lending {symbol}", **common
                )
            ]
        if code == "GOLD":
            return [
                plumbing.account_fee(
                    currency=usd, amount=abs(net), description="Robinhood Gold fee", **common
                )
            ]
        if code == "MINT":
            return [
                plumbing.interest_charged(
                    currency=usd, amount=abs(net), description=first_line, **common
                )
            ]
        if code == "DFEE":
            return [
                plumbing.adr_fee_deducted(
                    currency=usd,
                    amount=abs(net),
                    description=f"ADR fee {symbol}: {first_line}",
                    **common,
                )
            ]
        if code == "DTAX":
            return [
                plumbing.tax_withholding(
                    currency=usd,
                    amount=abs(net),
                    tracker_key="foreign_tax",
                    description=f"Foreign tax {symbol}: {first_line}",
                    **common,
                )
            ]
        if code in CASH_MOVE_CODES:
            cash_template: Callable[..., Transaction] = (
                plumbing.deposit_currency if net >= 0 else plumbing.withdraw_currency
            )
            return [
                cash_template(
                    currency=usd, amount=abs(net), description=f"{code}: {first_line}", **common
                )
            ]
        if code == "SPL":
            instrument = ensure_equity(symbol, currency=usd)
            prior = Holding.current(accounts["holdings"], instrument)
            if prior > 0:
                ratio = (prior + qty) / prior
                if ratio * prior == prior + qty:
                    return [
                        eq.stock_split(
                            instrument=instrument,
                            additional_quantity=qty,
                            ratio=ratio,
                            **common,
                        )
                    ]
            return [
                plumbing.quantity_adjustment(
                    instrument=instrument,
                    quantity=qty,
                    description=f"SPL {quantity} {symbol}: {first_line}",
                    metadata={"csv_code": "SPL"},
                    **common,
                )
            ]
        if code == "REC":
            instrument = ensure_equity(symbol, currency=usd)
            return [
                plumbing.quantity_adjustment(
                    instrument=instrument,
                    quantity=qty,
                    description=f"REC {quantity} {symbol}: {first_line}",
                    metadata={"csv_code": "REC"},
                    **common,
                )
            ]
        raise ValueError(f"unhandled Robinhood code {code!r} (line {line.line_number})")

    def _accounts(self, batch: Any) -> dict[str, Any]:
        return ensure_standard_accounts(batch.account.owner) | {"cash": batch.account}

    def match_criteria(self, line: Any) -> Any:
        from django_assets.brokerage.matching import MatchCriteria
        from django_assets.brokerage.schemas.instruments import parse_us_date

        row = line.raw_data
        net = parse_money(row[8]) if len(row) >= 9 else Decimal(0)
        if not net:
            raise NotImplementedError("no cash side to dedup on")
        return MatchCriteria(
            date=parse_us_date(row[2]), instrument=ensure_currency("USD"), amount=net
        )


def _at(value: str) -> datetime.datetime:
    from django_assets.brokerage.schemas.instruments import parse_us_date

    return datetime.datetime.combine(
        parse_us_date(value), datetime.time(21, 0), tzinfo=datetime.UTC
    )
