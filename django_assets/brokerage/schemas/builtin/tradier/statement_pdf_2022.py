"""Tradier monthly statement PDF (Apex Clearing layout, validated
against 2022–2025 statements).

Record lines inside the activity sections read
    <TYPE> MM/DD/YY <acct> <free text…> [qty] [price] [amount]
with continuation lines (security name wrap, CUSIP:, REC/PAY dates,
UNSOLICITED, dividend class) until the next record, a Total, or a page
break. Security identity comes from the CUSIP continuation line; the
ticker is recovered from the holdings table ("<NAME> <SYMBOL> <TYPE>
<QTY>…") when the file carries one. The statement's NET ACCOUNT
BALANCE opening/closing pair is exposed in every line's metadata so
harnesses can assert cash to the cent.
"""

import datetime
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from django_assets.brokerage.accounts import ensure_standard_accounts
from django_assets.brokerage.schemas import ImportSchema, register_schema
from django_assets.brokerage.schemas.instruments import ensure_currency, parse_money
from django_assets.brokerage.schemas.pdf import extract_text
from django_assets.core.models import Identifier, Instrument, Transaction
from django_assets.instruments.equities import templates as eq
from django_assets.instruments.equities.models import EquityMeta

RECORD = re.compile(
    r"^(?P<type>BOUGHT|SOLD|DIVIDEND|INTEREST|ACH|JOURNAL|WIRE|CANCEL"
    r"|REINVEST|SWEEP|REDEEMED|TRANSFER)"
    r" (?P<date>\d{2}/\d{2}/\d{2}) (?P<account_type>[A-Z]) (?P<rest>.*)$"
)
#: Internal pool moves: money-market sweeps and cash↔margin journals.
#: The harness tracks combined cash, so these are evidence only.
INTERNAL_TYPES = {"SWEEP", "REDEEMED", "TRANSFER"}
MM_BALANCE = re.compile(r"^Money Market funds (?P<opening>[\d,.()$-]+) (?P<closing>[\d,.()$-]+)")
SECTIONS = {
    "BUY / SELL TRANSACTIONS": "trade",
    "DIVIDENDS AND INTEREST": "income",
    "FUNDS PAID AND RECEIVED": "funds",
    "MISCELLANEOUS TRANSACTIONS": "misc",
}
CUSIP_LINE = re.compile(r"^CUSIP: (?P<cusip>[0-9A-Z]{9})$")
BALANCE_LINE = re.compile(r"^NET ACCOUNT BALANCE (?P<opening>[\d,.()$-]+) (?P<closing>[\d,.()$-]+)")
HOLDING_LINE = re.compile(
    r"^(?P<name>.+?) (?P<symbol>[A-Z]{1,6}) (?P<account_type>[A-Z]) "
    r"(?P<quantity>[\d,.]+) \$(?P<price>[\d,.]+) "
)
MONEY = re.compile(r"\(?-?\$?[\d,]+\.\d{2,6}\)?$")


def _tail_numbers(rest: str) -> "tuple[str, list[str]]":
    """Split trailing numeric tokens (qty/price/amount) off the text."""
    tokens = rest.split(" ")
    numbers: list[str] = []
    while tokens and re.fullmatch(r"\(?-?\$?[\d,]+(?:\.\d+)?\)?", tokens[-1]):
        numbers.insert(0, tokens.pop())
        if len(numbers) == 3:
            break
    return " ".join(tokens), numbers


@register_schema(
    broker="tradier",
    document_kind="statement",
    format_kind="pdf",
    version="2022.1",
    name="Tradier monthly statement PDF (Apex layout)",
)
class TradierStatementPdf2022(ImportSchema):
    definition = {"layout": "nested", "carrier": "apex-statement-text"}

    @classmethod
    def sniff(cls, sample: str) -> bool:
        """Apex statements print an upper-case NET ACCOUNT BALANCE pair."""
        return "NET ACCOUNT BALANCE" in sample

    def parse_positions(self, source: Any) -> "list[Any]":
        """ADR-0036: closing holdings from the EQUITIES / OPTIONS table
        (the same rows the ticker-recovery pass reads)."""
        from django_assets.brokerage.schemas.positions import StatementPosition

        text = source if isinstance(source, str) else extract_text(source)
        positions: list[Any] = []
        for raw in text.splitlines():
            holding = HOLDING_LINE.match(raw.strip())
            if holding and holding["symbol"] not in ("M", "C"):
                positions.append(
                    StatementPosition(
                        quantity=Decimal(holding["quantity"].replace(",", "")),
                        ticker=holding["symbol"],
                        description=holding["name"].strip(),
                    )
                )
        return positions

    def parse_batch(self, batch: Any, source: Any) -> Any:
        """source: PDF bytes / file-like, or already-extracted statement
        text (str) — the latter serves tests and text-side tooling."""
        from django_assets.brokerage.models import ImportLine

        text = source if isinstance(source, str) else extract_text(source)
        lines = text.splitlines()

        balances: dict[str, str] = {}
        symbols: dict[str, str] = {}  # security-name first words -> ticker
        for line in lines:
            match = BALANCE_LINE.match(line.strip())
            if match:
                balances = {
                    "opening": str(parse_money(match["opening"])),
                    "closing": str(parse_money(match["closing"])),
                    **balances,
                }
            mm = MM_BALANCE.match(line.strip())
            if mm:
                balances["mm_opening"] = str(parse_money(mm["opening"]))
                balances["mm_closing"] = str(parse_money(mm["closing"]))
            holding = HOLDING_LINE.match(line.strip())
            if holding and holding["symbol"] not in ("M", "C"):
                symbols[holding["name"].strip()] = holding["symbol"]

        # Zero-record months still need their balances for acceptance.
        batch.metadata["balances"] = balances
        batch.metadata["recognized"] = bool(balances)
        if batch.pk:
            batch.save(update_fields=["metadata"])

        number = 0
        section = None
        record: dict[str, Any] | None = None

        def finish() -> "Any":
            nonlocal record, number
            if record is None:
                return None
            number += 1
            payload, record = record, None
            payload["balances"] = balances
            name = payload["text"].strip()
            payload["symbol"] = symbols.get(name, "")
            prefix = "note" if payload["type"] in INTERNAL_TYPES else "broker"
            return ImportLine(
                batch=batch,
                line_number=number,
                raw_data=payload,
                kind=f"{prefix}_{payload['type'].lower()}",
                source_reference=f"{batch.file_name}#{number}",
            )

        for raw in lines:
            line = raw.strip()
            for header, section_name in SECTIONS.items():
                if line.startswith(header):
                    section = section_name
            if section is None:
                continue
            if line.startswith("Total "):
                done = finish()
                if done:
                    yield done
                section = None
                continue
            match = RECORD.match(line)
            if match:
                done = finish()
                if done:
                    yield done
                text_part, numbers = _tail_numbers(match["rest"])
                record = {
                    "type": match["type"],
                    "date": match["date"],
                    "text": text_part,
                    "numbers": numbers,
                    "detail": [],
                    "section": section,
                }
                continue
            if record is not None:
                cusip = CUSIP_LINE.match(line)
                if cusip:
                    record["cusip"] = cusip["cusip"]
                elif (
                    line
                    and not line.startswith(("PAGE ", "ACCOUNT NUMBER"))
                    and len(line) > 2
                    and not re.fullmatch(r"[A-Z]", line)
                ):
                    record["detail"].append(line[:80])
        done = finish()
        if done:
            yield done

    def materialize_line(self, line: Any) -> list[Transaction]:
        data = line.raw_data
        accounts = ensure_standard_accounts(line.batch.account.owner) | {"cash": line.batch.account}
        usd = ensure_currency("USD")
        timestamp = _at(data["date"])
        common: dict[str, Any] = {
            "accounts": accounts,
            "timestamp": timestamp,
            "origin": "import",
        }
        numbers = [parse_money(token) for token in data["numbers"]]
        kind, text = data["type"], data["text"]

        if kind in ("BOUGHT", "SOLD", "REINVEST"):
            quantity, price, amount = (
                numbers if len(numbers) == 3 else (numbers[0], None, numbers[-1])
            )
            instrument = _ensure_security(data, usd)
            template: Callable[..., Transaction] = (
                eq.sell_shares if kind == "SOLD" else eq.buy_shares
            )
            return [
                template(
                    instrument=instrument,
                    quantity=abs(quantity),
                    price=price
                    if price is not None
                    else (abs(amount) / abs(quantity)).quantize(Decimal("0.0001")),
                    principal=abs(amount),
                    description=f"{kind} {quantity} {instrument.code} (Apex)",
                    **common,
                )
            ]
        if kind == "DIVIDEND":
            amount = numbers[-1]
            instrument = _ensure_security(data, usd)
            return [
                eq.dividend_received(
                    instrument=instrument,
                    amount=amount,
                    currency=usd,
                    description=f"Dividend {instrument.code}: {text[:70]} (Tradier)",
                    **common,
                )
            ]
        if kind == "INTEREST":
            from django_assets.brokerage import templates as plumbing

            return [
                plumbing.interest_earned(
                    currency=usd, amount=numbers[-1], description=text[:90], **common
                )
            ]
        if kind == "ACH" or kind == "WIRE":
            from django_assets.brokerage import templates as plumbing

            amount = numbers[-1]
            outgoing = "DISBURSEMENT" in text or "WITHDRAW" in text
            move: Callable[..., Transaction] = (
                plumbing.withdraw_currency if outgoing else plumbing.deposit_currency
            )
            return [
                move(
                    currency=usd,
                    amount=abs(amount),
                    description=f"{kind} {text[:80]} (Tradier)",
                    **common,
                )
            ]
        if kind == "JOURNAL":
            from django_assets.brokerage import templates as plumbing

            amount = numbers[-1]
            if "FEE" in text:
                return [
                    plumbing.account_fee(
                        currency=usd, amount=abs(amount), description=text[:90], **common
                    )
                ]
            template = plumbing.deposit_currency if amount >= 0 else plumbing.withdraw_currency
            return [template(currency=usd, amount=abs(amount), description=text[:90], **common)]
        raise ValueError(f"unhandled Tradier record type {kind!r}")

    def match_criteria(self, line: Any) -> Any:
        from django_assets.brokerage.matching import MatchCriteria

        data = line.raw_data
        numbers = [parse_money(token) for token in data["numbers"]]
        if not numbers:
            raise NotImplementedError("no cash side")
        amount = numbers[-1]
        if data["type"] in ("BOUGHT", "REINVEST") or (
            data["type"] in ("ACH", "WIRE", "JOURNAL")
            and ("DISBURSEMENT" in data["text"] or "FEE" in data["text"])
        ):
            amount = -abs(amount)
        return MatchCriteria(
            date=_at(data["date"]).date(), instrument=ensure_currency("USD"), amount=amount
        )


def _ensure_security(data: dict[str, Any], usd: Instrument) -> Instrument:
    """Identity by CUSIP (always present on Apex records); code from the
    holdings-table ticker when the statement provides one."""
    cusip = data.get("cusip", "")
    symbol = data.get("symbol") or ""
    if cusip:
        existing = Identifier.objects.filter(type="cusip", value=cusip, is_active=True).first()
        if existing is not None:
            return existing.instrument
    code = symbol or cusip or data["text"][:16]
    instrument = Instrument.objects.create(
        code=code, quantity_decimals=8, price_decimals=4, price_currency=usd
    )
    if cusip:
        Identifier.objects.create(type="cusip", value=cusip, instrument=instrument)
    if (
        symbol
        and not Identifier.objects.filter(type="ticker", value=symbol, is_active=True).exists()
    ):
        Identifier.objects.create(type="ticker", value=symbol, instrument=instrument)
    EquityMeta.objects.create(instrument=instrument)
    return instrument


def _at(value: str) -> datetime.datetime:
    month, day, year = value.split("/")
    return datetime.datetime(2000 + int(year), int(month), int(day), 21, 0, tzinfo=datetime.UTC)
