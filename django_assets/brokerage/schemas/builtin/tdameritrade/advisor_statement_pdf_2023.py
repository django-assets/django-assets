"""TD Ameritrade advisor-managed account statement PDF (the
"MONTHLY STATEMENT" layout produced for independent-advisor accounts,
validated against 2023 Invest In Vol statements).

TRANSACTIONS DETAIL rows read
    MM/DD MM/DD <Activity Type> <DESC…> <SYM|-> <qty|-> <price|-> <amount|->
with no running-balance column and no per-row fees (amounts are net).
Dates carry no year — it comes from the "Reporting Period:" line.
Option identity rides continuation lines ("UVXY APR 28 23 6.5 C TO
OPEN", or without the TO suffix on EXPIRATION removals).

Cash truth: all cash pools (TD Ameritrade Cash + the FDIC sweep)
consolidate to the holdings page's "TOTAL CASH & CASH ALTERNATIVES"
figure, so acceptance is ABSOLUTE — after importing a statement the
cash holding must equal that closing figure ($0 for the closing
statement, which prints "ENDING VALUE -"). The INSURED DEPOSIT ACCOUNT
ACTIVITY section is the sweep mirror: its Received/Delivered rows are
skipped, but its INTEREST: credit rows are real income and import.
"""

import datetime
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from django_assets.brokerage.accounts import ensure_standard_accounts
from django_assets.brokerage.schemas import ImportSchema, register_schema
from django_assets.brokerage.schemas.instruments import (
    ensure_currency,
    ensure_option,
    parse_money,
)
from django_assets.brokerage.schemas.pdf import extract_text
from django_assets.core.models import Identifier, Instrument, Transaction
from django_assets.core.queries import Holding
from django_assets.instruments.equities import templates as eq
from django_assets.instruments.equities.models import EquityMeta
from django_assets.instruments.options import templates as opt

ACTIVITY_TYPES = (
    "Dividends and Interest",
    "Deposits to Account",
    "Withdrawals from Account",
    "Other Income or Expense",
    "Buy",
    "Sell",
    "Delivered",
    "Received",
    "Deliver",
    "Receive",
    "Journal",
)
ROW = re.compile(
    r"^(?P<trade_date>\d{2}/\d{2}) (?P<settle_date>\d{2}/\d{2}) "
    r"(?P<type>" + "|".join(ACTIVITY_TYPES) + r")(?P<rest>( .*)?)$"
)
NUMBER = re.compile(r"^\$?\(?-?\$?[\d,]+(?:\.\d+)?\)?$|^-$")
SYMBOLISH = re.compile(r"^[A-Z]{1,5}$|^[0-9A-Z]{9}$")
PERIOD = re.compile(r"Reporting Period: ?([A-Z][a-z]+) ?(\d{1,2}) ?- ?(\d{1,2}), ?(\d{4})")
CLOSING_CASH = re.compile(r"^TOTAL CASH & CASH ALTERNATIVES \$([\d,.()-]+)")
OPTION_DESC = re.compile(
    r"^(?P<underlying>[A-Z][A-Z0-9.]*) (?P<month>[A-Za-z]{3}) (?P<day>\d{1,2}) "
    r"(?P<year>\d{2}) (?P<strike>[\d.]+) (?P<right>[CP])( TO (?:OPEN|CLOSE))?$"
)
MONTH_NAMES = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
SECTION_STARTS = ("TRANSACTIONS DETAIL", "INSURED DEPOSIT ACCOUNT ACTIVITY")
SECTION_ENDS = (
    "TRADES PENDING SETTLEMENT",
    "TD AMERITRADE CASH INTEREST",
    "IMPORTANT INFORMATION",
    "RETIREMENT ACCOUNT ACTIVITY",
)


def _split_tail(tokens: "list[str]") -> "dict[str, str]":
    """<SYM|-> <qty|-> <price|-> <amount|-> — like the retail layout but
    with no trailing balance column."""
    tail: list[str] = []
    while tokens and len(tail) < 4:
        token = tokens[-1]
        if token == "$":
            tokens.pop()
            continue
        if NUMBER.fullmatch(token):
            tail.insert(0, tokens.pop())
            continue
        break
    symbol = ""
    if len(tail) == 4:
        slot, tail = tail[0], tail[1:]
        symbol = "" if slot == "-" else slot
    elif len(tail) == 3 and tokens and SYMBOLISH.fullmatch(tokens[-1]):
        symbol = tokens.pop()
    while len(tail) < 3:
        tail.insert(0, "-")
    return {
        "symbol": symbol,
        "quantity": tail[0],
        "price": tail[1],
        "amount": tail[2],
    }


@register_schema(
    broker="tdameritrade",
    document_kind="advisor-statement",
    format_kind="pdf",
    version="2023.1",
    name="TD Ameritrade advisor-managed statement PDF",
)
class TdAmeritradeAdvisorStatementPdf2023(ImportSchema):
    definition = {"layout": "nested", "carrier": "tda-advisor-statement-text"}

    @classmethod
    def sniff(cls, sample: str) -> bool:
        """Advisor-managed statements name the Independent Advisor."""
        return "Independent Advisor" in sample and "TRANSACTIONS DETAIL" in sample

    def parse_positions(self, source: Any) -> "list[Any]":
        """ADR-0036: closing holdings from HOLDINGS DETAIL. Rows tail as
        <SYM|-> <qty> <price|NA> <value|NA>; option identity rides the
        continuation descriptor. Cash rows are excluded — the cash
        acceptance covers them."""
        from django_assets.brokerage.schemas.positions import (
            StatementPosition,
            option_canonical_code,
        )

        text = source if isinstance(source, str) else extract_text(source)
        positions: list[Any] = []
        in_section = False
        current: Any = None
        months = {
            "Jan": 1,
            "Feb": 2,
            "Mar": 3,
            "Apr": 4,
            "May": 5,
            "Jun": 6,
            "Jul": 7,
            "Aug": 8,
            "Sep": 9,
            "Oct": 10,
            "Nov": 11,
            "Dec": 12,
        }

        def finish() -> None:
            nonlocal current
            if current is None:
                return
            record, current = current, None
            positions.append(record)

        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("HOLDINGS DETAIL"):
                in_section = True
                continue
            if line.startswith(("TOTAL HOLDINGS", "TRANSACTIONS DETAIL", "ACCOUNT SUMMARY")):
                finish()
                in_section = False
                continue
            if not in_section or not line or "CASH" in line.upper()[:24]:
                continue
            tokens = line.split()
            tail: list[str] = []
            while (
                tokens
                and len(tail) < 4
                and (tokens[-1] in ("NA", "-") or NUMBER.fullmatch(tokens[-1]))
            ):
                tail.insert(0, tokens.pop())
            symbol = ""
            if len(tail) == 4:
                slot, tail = tail[0], tail[1:]
                symbol = "" if slot in ("-", "NA") else slot
            elif len(tail) == 3 and tokens and SYMBOLISH.fullmatch(tokens[-1]):
                symbol = tokens.pop()
            quantities = [t for t in tail[:1] if t not in ("NA", "-")]
            if quantities and not line.startswith(("Investment", "Symbol/", "Closing")):
                finish()
                current = StatementPosition(
                    quantity=_qty(quantities[0]),
                    ticker=symbol,
                    description=" ".join(tokens),
                )
                continue
            if current is not None and line:
                opt = OPTION_DESC.match(line)
                if opt:
                    current.option_code = option_canonical_code(
                        opt["underlying"],
                        datetime.date(
                            2000 + int(opt["year"]),
                            months[opt["month"].capitalize()],
                            int(opt["day"]),
                        ),
                        Decimal(opt["strike"]),
                        opt["right"],
                    )
        finish()
        return positions

    def parse_batch(self, batch: Any, source: Any) -> Any:
        from django_assets.brokerage.models import ImportLine

        text = source if isinstance(source, str) else extract_text(source)
        lines = text.splitlines()

        period = PERIOD.search(text)
        year = int(period.group(4)) if period else 0
        period_month = MONTH_NAMES.get(period.group(1).lower(), 12) if period else 12

        balances: dict[str, str] = {}
        for raw in lines:
            match = CLOSING_CASH.match(raw.strip())
            if match:
                balances["closing"] = str(parse_money(match.group(1)))
                break
        if "closing" not in balances and re.search(r"^ENDING VALUE - ", text, re.M):
            balances["closing"] = "0"  # closing statement: account emptied
        batch.metadata["balances"] = balances
        batch.metadata["recognized"] = bool(period)
        if batch.pk:
            batch.save(update_fields=["metadata"])

        number = 0
        record: dict[str, Any] | None = None
        section = ""

        def finish() -> Any:
            nonlocal record, number
            if record is None:
                return None
            payload, record = record, None
            number += 1
            payload["balances"] = balances
            return ImportLine(
                batch=batch,
                line_number=number,
                raw_data=payload,
                kind=_line_kind(payload),
                source_reference=f"{batch.file_name}#{number}",
            )

        for raw in lines:
            line = raw.strip()
            started = next((s for s in SECTION_STARTS if line.startswith(s)), None)
            if started:
                done = finish()
                if done:
                    yield done
                section = "ida" if started.startswith("INSURED") else "tx"
                continue
            if any(line.startswith(end) for end in SECTION_ENDS):
                done = finish()
                if done:
                    yield done
                section = ""
                continue
            if not section:
                continue
            match = ROW.match(line)
            if match:
                done = finish()
                if done:
                    yield done
                tokens = (match["rest"] or "").split()
                tail = _split_tail(tokens)
                month, day = (int(part) for part in match["settle_date"].split("/"))
                row_year = year - 1 if (month == 12 and period_month == 1) else year
                trade_month, trade_day = (int(part) for part in match["trade_date"].split("/"))
                trade_year = row_year - 1 if (trade_month == 12 and month == 1) else row_year
                record = {
                    "date": f"{month:02d}/{day:02d}/{row_year}",
                    "trade_date": f"{trade_month:02d}/{trade_day:02d}/{trade_year}",
                    "type": match["type"],
                    "section": section,
                    # Rows settling after month-end are next month's cash
                    # and reappear in the next statement's detail.
                    "in_period": month == period_month,
                    "description": " ".join(tokens),
                    "detail": [],
                    **tail,
                }
                continue
            if record is not None and line:
                option = OPTION_DESC.match(line)
                if option:
                    record["option"] = option.groupdict()
                elif line == "EXPIRATION":
                    record["expiration"] = True
                elif len(record["detail"]) < 4 and not line.startswith(
                    ("Questions?", "INVEST IN", "Account ", "Page ", "MONTHLY STATEMENT")
                ):
                    record["detail"].append(line[:90])
        done = finish()
        if done:
            yield done

    def materialize_line(self, line: Any) -> list[Transaction]:
        data = line.raw_data
        accounts = ensure_standard_accounts(line.batch.account.owner) | {"cash": line.batch.account}
        usd = ensure_currency("USD")
        common: dict[str, Any] = {
            "accounts": accounts,
            "timestamp": _at(data["date"]),
            "trade_timestamp": _at(data["trade_date"]),
            "origin": "import",
        }
        row_type = data["type"]
        quantity = _qty(data["quantity"])
        price = parse_money(data["price"])
        amount = parse_money(data["amount"])
        description = data["description"]
        label = f"{row_type} {description[:60]} (TDA advisor)"

        from django_assets.brokerage import templates as plumbing

        if data["section"] == "ida":
            # Only INTEREST: credits are real income; sweeps mirror cash.
            return [
                plumbing.interest_earned(
                    currency=usd,
                    amount=_ida_amount(data),
                    description=label,
                    **common,
                )
            ]

        if row_type in ("Buy", "Sell"):
            buying = row_type == "Buy"
            principal = -amount if buying else amount
            if data.get("option"):
                option = _ensure_option(data["option"], usd)
                option_template: Callable[..., Transaction] = (
                    opt.buy_option if buying else opt.sell_option
                )
                return [
                    option_template(
                        instrument=option,
                        contracts=abs(quantity),
                        price=price,
                        principal=principal,
                        description=label,
                        **common,
                    )
                ]
            trade = eq.buy_shares if buying else eq.sell_shares
            return [
                trade(
                    instrument=_ensure_security(data, usd),
                    quantity=abs(quantity),
                    price=price,
                    principal=principal,
                    description=label,
                    **common,
                )
            ]

        if row_type == "Dividends and Interest":
            if data["symbol"]:
                return [
                    eq.dividend_received(
                        instrument=_ensure_security(data, usd),
                        amount=amount,
                        currency=usd,
                        description=label,
                        **common,
                    )
                ]
            return [
                plumbing.interest_earned(currency=usd, amount=amount, description=label, **common)
            ]

        if row_type in ("Deliver", "Receive", "Delivered", "Received"):
            if data.get("option") and data.get("expiration"):
                option = _ensure_option(data["option"], usd)
                position = Holding.current(accounts["holdings"], option)
                if position > 0:
                    contracts = abs(quantity)
                elif position < 0:
                    contracts = -abs(quantity)
                else:
                    contracts = -quantity
                return [
                    opt.expire_option(
                        instrument=option,
                        contracts=contracts,
                        description=label,
                        **common,
                    )
                ]
            instrument = (
                _ensure_option(data["option"], usd)
                if data.get("option")
                else _ensure_security(data, usd)
            )
            return [
                plumbing.quantity_adjustment(
                    instrument=instrument,
                    quantity=quantity,
                    description=label,
                    metadata={"tda_advisor_type": row_type},
                    **common,
                )
            ]

        if row_type in (
            "Deposits to Account",
            "Withdrawals from Account",
            "Other Income or Expense",
            "Journal",
        ):
            if "FEE" in description.upper() and amount < 0:
                return [
                    plumbing.account_fee(
                        currency=usd, amount=abs(amount), description=label, **common
                    )
                ]
            move: Callable[..., Transaction] = (
                plumbing.deposit_currency if amount > 0 else plumbing.withdraw_currency
            )
            return [move(currency=usd, amount=abs(amount), description=label, **common)]

        raise ValueError(f"unhandled TDA advisor activity {row_type!r}")

    def match_criteria(self, line: Any) -> Any:
        from django_assets.brokerage.matching import MatchCriteria

        data = line.raw_data
        amount = _ida_amount(data) if data["section"] == "ida" else parse_money(data["amount"])
        if not amount:
            raise NotImplementedError("no cash side")
        return MatchCriteria(
            date=_at(data["date"]).date(),
            instrument=ensure_currency("USD"),
            amount=amount,
        )


def _line_kind(payload: dict[str, Any]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", payload["type"].lower()).strip("_")[:32]
    if payload["section"] == "ida":
        # The whole IDA section mirrors cash: sweeps net to zero and its
        # interest credits re-print in TRANSACTIONS DETAIL as MMDA rows.
        return f"note_ida_{slug}"[:40]
    if not payload.get("in_period", True):
        return f"note_pending_{slug}"[:40]
    no_op = parse_money(payload["amount"]) == 0 and _qty(payload["quantity"]) == 0
    return f"note_{slug}" if no_op else f"broker_{slug}"


def _ida_amount(data: dict[str, Any]) -> Decimal:
    """IDA rows tail as <amount> <running balance>; _split_tail mapped
    them into price/amount slots."""
    return parse_money(data["price"])


def _qty(token: str) -> Decimal:
    text = token.strip()
    if text in ("-", ""):
        return Decimal(0)
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    value = Decimal(text.replace(",", ""))
    return -value if negative else value


def _ensure_option(desc: dict[str, str], usd: Instrument) -> Instrument:
    month = desc["month"].capitalize()
    months = {
        "Jan": 1,
        "Feb": 2,
        "Mar": 3,
        "Apr": 4,
        "May": 5,
        "Jun": 6,
        "Jul": 7,
        "Aug": 8,
        "Sep": 9,
        "Oct": 10,
        "Nov": 11,
        "Dec": 12,
    }
    return ensure_option(
        underlying_symbol=desc["underlying"],
        expiry=datetime.date(2000 + int(desc["year"]), months[month], int(desc["day"])),
        strike=Decimal(desc["strike"]),
        right=desc["right"],
        currency=usd,
    )


def _ensure_security(data: dict[str, Any], usd: Instrument) -> Instrument:
    symbol = data.get("symbol", "")
    value = symbol or data["description"][:16].strip() or "UNKNOWN"
    existing = Identifier.objects.filter(type="ticker", value=value, is_active=True).first()
    if existing is not None:
        return existing.instrument
    instrument = Instrument.objects.create(
        code=value, quantity_decimals=8, price_decimals=4, price_currency=usd
    )
    Identifier.objects.create(type="ticker", value=value, instrument=instrument)
    EquityMeta.objects.create(instrument=instrument)
    return instrument


def _at(value: str) -> datetime.datetime:
    month, day, year = value.split("/")
    return datetime.datetime(int(year), int(month), int(day), 21, 0, tzinfo=datetime.UTC)
