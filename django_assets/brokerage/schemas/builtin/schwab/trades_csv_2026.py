"""Schwab trades CSV, 2026 export format.

Columns: Date, Action, Symbol, Description, Quantity, Price,
Fees & Comm, Amount. Buy/Sell rows are matchable (`broker_trade`);
anything else (Journal, MoneyLink, …) is kept as informational evidence.

Source-shape fidelity (ADR-0021): the broker's own net Amount drives the
cash legs — principal is derived from it, never recomputed from
quantity × price.
"""

import csv
import datetime
import io
from decimal import Decimal
from typing import Any

from django_assets.brokerage.accounts import ensure_standard_accounts
from django_assets.brokerage.schemas import ImportSchema, parse_us_date, register_schema
from django_assets.core.models import Instrument, Transaction
from django_assets.instruments.equities import templates

COLUMNS = ["Date", "Action", "Symbol", "Description", "Quantity", "Price", "Fees & Comm", "Amount"]


def _money(value: str) -> Decimal:
    return Decimal(value.replace("$", "").replace(",", "") or "0")


@register_schema(
    broker="schwab",
    document_kind="trades",
    format_kind="csv",
    version="2026.1",
    name="Schwab trades CSV (2026 format)",
)
class SchwabTradesCsv2026(ImportSchema):
    definition = {"layout": "tabular", "columns": COLUMNS}

    def parse_batch(self, batch: Any, source: Any) -> Any:
        from django_assets.brokerage.models import ImportLine

        text = source if isinstance(source, str) else source.read()
        reader = csv.reader(io.StringIO(text))
        header = next(reader, None)
        if header != COLUMNS:
            raise ValueError(f"unexpected Schwab CSV header: {header!r}")
        for number, row in enumerate(reader, start=1):
            if not any(row):
                continue
            action = row[1]
            kind = "broker_trade" if action in ("Buy", "Sell") else "balance_note"
            yield ImportLine(
                batch=batch,
                line_number=number,
                raw_data=row,
                kind=kind,
                source_reference=f"{batch.file_name}:{number}",
            )

    def materialize_line(self, line: Any) -> list[Transaction]:
        if not line.is_matchable:
            return []
        date, action, symbol, _description, quantity, price, fees, amount = line.raw_data
        batch = line.batch
        accounts = ensure_standard_accounts(batch.account.owner) | {"cash": batch.account}
        instrument = Instrument.resolve(symbol)
        settle = datetime.datetime.combine(
            parse_us_date(date), datetime.time(21, 0), tzinfo=datetime.UTC
        )
        fee = _money(fees)
        net = _money(amount)
        # Broker fidelity: net = ∓(principal ± fees) → recover principal.
        principal = -net - fee if action == "Buy" else net + fee
        template = templates.buy_shares if action == "Buy" else templates.sell_shares
        tx = template(
            accounts=accounts,
            instrument=instrument,
            quantity=quantity,
            price=price,
            commission=fee,
            principal=principal,
            timestamp=settle,
            origin="import",
            description=f"{action} {quantity} {symbol} (Schwab)",
        )
        return [tx]

    def match_criteria(self, line: Any) -> Any:
        """Cash-side criteria: the broker's net Amount in the trade's
        currency, on the batch (cash) account (ADR-0029)."""
        from django_assets.brokerage.matching import MatchCriteria

        date, _action, symbol, *_rest, amount = line.raw_data
        instrument = Instrument.resolve(symbol)
        currency = instrument.price_currency
        if currency is None:
            raise NotImplementedError(f"{symbol} has no price_currency")
        return MatchCriteria(date=parse_us_date(date), instrument=currency, amount=_money(amount))
