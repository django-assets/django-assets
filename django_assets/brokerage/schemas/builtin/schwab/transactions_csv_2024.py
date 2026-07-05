"""Schwab full-activity transactions CSV (real export format, validated
against 2023–2025 statements across four account types).

Columns: Date, Action, Symbol, Description, Quantity, Price,
Fees & Comm, Amount. Dates may read "MM/DD/YYYY as of MM/DD/YYYY"
(posted vs effective). Option symbols read "MSTR 01/16/2026 800.00 C".
Files list newest-first; parsing reverses into chronological order so
position-dependent dispatch (expiries, splits) sees prior state.

Assignment/exercise rows are pure option-position removals — Schwab
posts the resulting share trades as separate Buy/Sell rows. Same-day
Stock Merger / Stock Merger Adj / Reverse Split rows with opposite
signs pair into ONE conversion line so lots carries basis over
(ADR-0032 §5); unpaired rows fall back to quantity adjustments.
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
    option_from_schwab_symbol,
    parse_money,
    parse_us_date,
)
from django_assets.core.models import Transaction
from django_assets.core.queries import Holding
from django_assets.instruments.equities import templates as eq
from django_assets.instruments.options import templates as opt

COLUMNS = ["Date", "Action", "Symbol", "Description", "Quantity", "Price", "Fees & Comm", "Amount"]
DATE_ROW = re.compile(r"^\d{2}/\d{2}/\d{4}")

PAIRABLE = {"Stock Merger", "Stock Merger Adj", "Reverse Split"}
DIVIDEND_ACTIONS = {
    "Cash Dividend",
    "Qualified Dividend",
    "Non-Qualified Div",
    "Non-Qualified Div Adj",
    "Special Qual Div",
    "Pr Yr Cash Div",
    "Cash In Lieu",
}
CASH_TRANSFER_ACTIONS = {
    "MoneyLink Transfer",
    "MoneyLink Adj",
    "Journal",
    "Internal Transfer",
}
INTEREST_ACTIONS = {"Bank Interest", "Credit Interest"}
REMOVAL_ACTIONS = {"Expired", "Assigned", "Exchange or Exercise"}


def _kind(action: str) -> str:
    slug = action.lower().replace(" ", "_").replace("-", "_")
    return f"broker_{slug}"


@register_schema(
    broker="schwab",
    document_kind="transactions",
    format_kind="csv",
    version="2024.1",
    name="Schwab transactions CSV (full activity export)",
)
class SchwabTransactionsCsv2024(ImportSchema):
    definition = {"layout": "tabular", "columns": COLUMNS}

    def parse_batch(self, batch: Any, source: Any) -> Any:
        from django_assets.brokerage.models import ImportLine

        text = source if isinstance(source, str) else source.read()
        rows = [
            row
            for row in csv.reader(io.StringIO(text))
            if len(row) >= 8 and DATE_ROW.match(row[0] or "")
        ]
        rows.reverse()  # newest-first file → chronological processing

        number = 0
        pending_pairs: dict[str, list[list[str]]] = {}
        for row in rows:
            action = row[1]
            if action in PAIRABLE:
                date = row[0]
                bucket = pending_pairs.setdefault(date, [])
                partner = next(
                    (
                        other
                        for other in bucket
                        if _qty(other) and _qty(row) and (_qty(other) > 0) != (_qty(row) > 0)
                    ),
                    None,
                )
                if partner is not None:
                    bucket.remove(partner)
                    number += 1
                    yield ImportLine(
                        batch=batch,
                        line_number=number,
                        raw_data=[partner, row],
                        kind="broker_conversion",
                        source_reference=f"{batch.file_name}#{number}",
                    )
                else:
                    bucket.append(row)
                continue
            number += 1
            yield ImportLine(
                batch=batch,
                line_number=number,
                raw_data=row,
                kind=_kind(action),
                source_reference=f"{batch.file_name}#{number}",
            )
        for bucket in pending_pairs.values():
            for row in bucket:  # unpaired corporate-action rows
                number += 1
                yield ImportLine(
                    batch=batch,
                    line_number=number,
                    raw_data=row,
                    kind=_kind(row[1]),
                    source_reference=f"{batch.file_name}#{number}",
                )

    # -- materialization ---------------------------------------------------

    def materialize_line(self, line: Any) -> list[Transaction]:
        if line.kind == "broker_conversion":
            return self._conversion(line)
        row = line.raw_data
        (
            date,
            action,
            symbol,
            description,
            quantity,
            price,
            fees,
            amount,
        ) = row[:8]
        accounts = self._accounts(line.batch)
        usd = ensure_currency("USD")
        timestamp, trade_timestamp = _timestamps(date)
        common: dict[str, Any] = {
            "accounts": accounts,
            "timestamp": timestamp,
            "trade_timestamp": trade_timestamp,
            "origin": "import",
        }
        net = parse_money(amount)
        fee = parse_money(fees)
        qty = Decimal(quantity.replace(",", "")) if quantity else Decimal(0)

        if action in ("Buy", "Sell", "Sell Short"):
            option = option_from_schwab_symbol(symbol, currency=usd)
            if option is not None:
                template: Callable[..., Transaction] = (
                    opt.buy_option if action == "Buy" else opt.sell_option
                )
                principal = -net - fee if action == "Buy" else net + fee
                return [
                    template(
                        instrument=option,
                        contracts=abs(qty),
                        price=parse_money(price),
                        commission=fee,
                        principal=principal,
                        description=f"{action} {quantity} {symbol} (Schwab)",
                        **common,
                    )
                ]
            instrument = ensure_equity(symbol, currency=usd)
            buying = action == "Buy"
            principal = -net - fee if buying else net + fee
            equity_template: Callable[..., Transaction] = (
                eq.buy_shares if buying else eq.sell_shares
            )
            return [
                equity_template(
                    instrument=instrument,
                    quantity=abs(qty),
                    price=parse_money(price),
                    commission=fee,
                    principal=principal,
                    description=f"{action} {quantity} {symbol} (Schwab)",
                    **common,
                )
            ]

        if action in ("Sell to Open", "Sell to Close", "Buy to Open", "Buy to Close"):
            option = option_from_schwab_symbol(symbol, currency=usd)
            if option is None:
                raise ValueError(f"unparseable option symbol {symbol!r} for {action}")
            selling = action.startswith("Sell")
            principal = net + fee if selling else -net - fee
            option_template: Callable[..., Transaction] = (
                opt.sell_option if selling else opt.buy_option
            )
            return [
                option_template(
                    instrument=option,
                    contracts=abs(qty),
                    price=parse_money(price),
                    commission=fee,
                    principal=principal,
                    description=f"{action} {quantity} {symbol} (Schwab)",
                    **common,
                )
            ]

        if action in REMOVAL_ACTIONS:
            option = option_from_schwab_symbol(symbol, currency=usd)
            if option is None:
                raise ValueError(f"{action} on non-option symbol {symbol!r}")
            position = Holding.current(accounts["holdings"], option)
            if position > 0:
                contracts = abs(qty)
            elif position < 0:
                contracts = -abs(qty)
            else:
                contracts = -qty  # row-sign fallback (see module docstring)
            return [
                opt.expire_option(
                    instrument=option,
                    contracts=contracts,
                    description=f"{action} {quantity} {symbol} (Schwab)",
                    **common,
                )
            ]

        if action in DIVIDEND_ACTIONS:
            instrument = ensure_equity(symbol, currency=usd) if symbol else usd
            return [
                eq.dividend_received(
                    instrument=instrument,
                    amount=net,
                    currency=usd,
                    description=f"{action} {symbol or 'cash'}: {description[:80]} (Schwab)",
                    **common,
                )
            ]

        if action in INTEREST_ACTIONS:
            from django_assets.brokerage import templates as plumbing

            return [
                plumbing.interest_earned(
                    currency=usd, amount=net, description=description[:100], **common
                )
            ]

        if action == "Margin Interest":
            from django_assets.brokerage import templates as plumbing

            return [
                plumbing.interest_charged(
                    currency=usd, amount=abs(net), description=description[:100], **common
                )
            ]

        if action == "ADR Mgmt Fee":
            from django_assets.brokerage import templates as plumbing

            return [
                plumbing.adr_fee_deducted(
                    currency=usd,
                    amount=abs(net),
                    description=f"ADR fee {symbol}: {description[:80]}",
                    **common,
                )
            ]

        if action == "Foreign Tax Paid":
            from django_assets.brokerage import templates as plumbing

            return [
                plumbing.tax_withholding(
                    currency=usd,
                    amount=abs(net),
                    tracker_key="foreign_tax",
                    description=f"Foreign tax {symbol}: {description[:80]}",
                    **common,
                )
            ]

        if action in CASH_TRANSFER_ACTIONS:
            if symbol and qty:
                return self._share_adjustment(line, row, common, accounts, usd)
            from django_assets.brokerage import templates as plumbing

            cash_template: Callable[..., Transaction] = (
                plumbing.deposit_currency if net >= 0 else plumbing.withdraw_currency
            )
            return [
                cash_template(
                    currency=usd,
                    amount=abs(net),
                    description=f"{action}: {description[:90]}",
                    **common,
                )
            ]

        if action in ("Journaled Shares", "Security Transfer"):
            if not symbol and net:
                from django_assets.brokerage import templates as plumbing

                template = plumbing.deposit_currency if net >= 0 else plumbing.withdraw_currency
                return [
                    template(
                        currency=usd,
                        amount=abs(net),
                        description=f"{action}: {description[:90]}",
                        **common,
                    )
                ]
            return self._share_adjustment(line, row, common, accounts, usd)

        if action == "Stock Split":
            instrument = ensure_equity(symbol, currency=usd)
            prior = Holding.current(accounts["holdings"], instrument)
            if prior > 0:
                ratio = (prior + qty) / prior
                if ratio * prior == prior + qty:  # exact — safe to tag
                    return [
                        eq.stock_split(
                            instrument=instrument,
                            additional_quantity=qty,
                            ratio=ratio,
                            **common,
                        )
                    ]
            return self._share_adjustment(line, row, common, accounts, usd)

        if action in PAIRABLE:  # unpaired merger/reverse-split leftovers
            return self._share_adjustment(line, row, common, accounts, usd)

        raise ValueError(f"unhandled Schwab action {action!r} (line {line.line_number})")

    def _conversion(self, line: Any) -> list[Transaction]:
        """A paired corporate-action swap: old security out, new in —
        the ADR-0009 four-leg shape with the §5 conversion tag, so lots
        carries basis and dates across."""
        row_a, row_b = line.raw_data
        out_row = row_a if _qty(row_a) < 0 else row_b
        in_row = row_b if out_row is row_a else row_a
        accounts = self._accounts(line.batch)
        usd = ensure_currency("USD")
        timestamp, trade_timestamp = _timestamps(out_row[0])
        return [
            eq.convert_instrument(
                accounts=accounts,
                from_instrument=ensure_equity(out_row[2], currency=usd),
                to_instrument=ensure_equity(in_row[2], currency=usd),
                from_quantity=abs(_qty(out_row)),
                to_quantity=abs(_qty(in_row)),
                timestamp=timestamp,
                trade_timestamp=trade_timestamp,
                origin="import",
            )
        ]

    def _share_adjustment(
        self,
        line: Any,
        row: list[str],
        common: dict[str, Any],
        accounts: dict[str, Any],
        usd: Any,
    ) -> list[Transaction]:
        from django_assets.brokerage import templates as plumbing

        instrument = ensure_equity(row[2], currency=usd)
        return [
            plumbing.quantity_adjustment(
                instrument=instrument,
                quantity=_qty(row),
                description=f"{row[1]} {row[4]} {row[2]}: {row[3][:70]} (Schwab)",
                metadata={"csv_action": row[1]},
                **common,
            )
        ]

    def _accounts(self, batch: Any) -> dict[str, Any]:
        return ensure_standard_accounts(batch.account.owner) | {"cash": batch.account}

    def match_criteria(self, line: Any) -> Any:
        from django_assets.brokerage.matching import MatchCriteria

        row = line.raw_data if line.kind != "broker_conversion" else line.raw_data[0]
        net = parse_money(row[7]) if len(row) >= 8 else Decimal(0)
        if not net:
            raise NotImplementedError("no cash side to dedup on")
        return MatchCriteria(
            date=parse_us_date(row[0].split(" as of ")[0]),
            instrument=ensure_currency("USD"),
            amount=net,
        )


def _qty(row: list[str]) -> Decimal:
    return Decimal(row[4].replace(",", "")) if row[4] else Decimal(0)


def _timestamps(
    value: str,
) -> "tuple[datetime.datetime, datetime.datetime | None]":
    """'01/16/2024 as of 01/15/2024' → (posted/settlement, effective)."""
    parts = value.split(" as of ")
    posted = datetime.datetime.combine(
        parse_us_date(parts[0]), datetime.time(21, 0), tzinfo=datetime.UTC
    )
    effective = None
    if len(parts) > 1:
        effective = datetime.datetime.combine(
            parse_us_date(parts[1]), datetime.time(21, 0), tzinfo=datetime.UTC
        )
    return posted, effective
