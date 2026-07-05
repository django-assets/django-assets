"""TD Ameritrade monthly statement PDF (2012–2023 layouts).

Account Activity rows read
    TT/DD/YY SS/DD/YY <Acct> <Type?> - <Cash Activity?> <DESC…> <SYM|-> <qty|-> <price> <amount> <balance>
where either side of the dash may be blank ("Received - CENTRAL PUERTO
SA…", "Cash  - Funds Deposited ELECTRONIC FUNDING"). Money may render
"$ 1,234.56" or bare, negatives in parens, dashes for empty cells;
quantities carry a trailing minus for outflows ("100-"). Continuation
lines carry wrapped names, Commission/Fee + Regulatory Fee (summed —
row amounts are NET of them, so principal recovers exactly) and the
option identity ("CCJ Feb 17 23 28.0 C TO OPEN").

Dispatch keys off the CASH-ACTIVITY phrase (the type prefix lies:
"Div/Int - Securities Sold" exists). FDIC-sweep journals move cash in
the statement's own running balance, so they import as cash moves and
the closing balance still reconciles to the cent.

Balance capture prefers the Account Activity section's own
Opening/Closing pair; statements for inactive months omit the section
entirely, so the page-one Cash Activity Summary (first money token on
its merged multi-column lines) is the fallback. The Insured Deposit
Account section's balances are never consulted.
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
from django_assets.instruments.equities import templates as eq
from django_assets.instruments.equities.models import EquityMeta
from django_assets.instruments.options import templates as opt

ROW = re.compile(
    r"^(?P<trade_date>\d{2}/\d{2}/\d{2}) (?P<settle_date>\d{2}/\d{2}/\d{2}) "
    r"(?:Margin|Cash|Short) "
    r"(?P<txn_type>[A-Za-z/#]+(?: [A-Za-z/#]+){0,3}?)? ?- ?"
    r"(?P<activity>(?:[A-Z][a-z]+)(?: [A-Za-z][a-z]+)*)?"
    r"(?P<rest>.*)$"
)
BALANCE = re.compile(r"^(Opening|Closing) Balance (-|\(?\$? ?[\d,.]+\)?)(?= |$)")
NUMBER = re.compile(r"^\(?-?\$?[\d,]+(?:\.\d+)?\)?-?$|^-$")
SYMBOLISH = re.compile(r"^[A-Z]{1,5}$|^[0-9A-Z]{9}$")
FEE_LINE = re.compile(r"^(?:Commission/Fee|Regulatory Fee|Fee) \$? ?([\d,.]+)$")
#: "CCJ Feb 17 23 28.0 C TO OPEN" — option identity rides a continuation line.
OPTION_DESC = re.compile(
    r"^(?P<underlying>[A-Z][A-Z0-9.]*) (?P<month>[A-Z][a-z]{2}) (?P<day>\d{1,2}) "
    r"(?P<year>\d{2}) \(?(?P<strike>[\d.]+)\)? (?P<right>[CP]) TO (?P<action>OPEN|CLOSE)\b"
)
MONTHS = {
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
#: Page furniture that interleaves records across page breaks.
PAGE_NOISE = re.compile(
    r"^(page \d+ of \d+|Statement for Account|Trade Settle Acct|"
    r"Date Date Type Cash Activity|Account Activity$|\*For Cash Activity totals)"
)


def _split_row_tail(tokens: "list[str]") -> "dict[str, str]":
    """Resolve the fixed tail  <SYM|-> <qty|-> <price> <amount> <balance>.

    The symbol column always exists (dash placeholder), but its dash —
    and a numeric CUSIP standing in for a ticker — both look like
    numbers, so pop up to five numeric-ish tokens (skipping loose $
    glyphs) and disambiguate by count."""
    tail: list[str] = []
    while tokens and len(tail) < 5:
        token = tokens[-1]
        if token == "$":
            tokens.pop()
            continue
        if NUMBER.fullmatch(token):
            tail.insert(0, tokens.pop())
            continue
        break
    symbol = ""
    if len(tail) == 5:
        slot, tail = tail[0], tail[1:]
        symbol = "" if slot == "-" else slot
    elif len(tail) == 4 and tokens and SYMBOLISH.fullmatch(tokens[-1]):
        symbol = tokens.pop()
    while len(tail) < 4:
        tail.insert(0, "-")
    return {
        "symbol": symbol,
        "quantity": tail[0],
        "price": tail[1],
        "amount": tail[2],
        "balance": tail[3],
    }


def _find_balances(lines: "list[str]") -> dict[str, str]:
    """Activity-section pair first; page-one Cash Activity Summary as
    the inactive-month fallback. Exact-match headers only — Terms &
    Conditions prose and 'Insured Deposit Account Activity' both embed
    the phrase."""
    activity: dict[str, str] = {}
    summary: dict[str, str] = {}
    in_activity = False
    for raw in lines:
        line = raw.strip()
        if line == "Account Activity":
            in_activity = True
            continue
        match = BALANCE.match(line)
        if not match:
            continue
        key = match.group(1).lower()
        value = str(parse_money(match.group(2)))
        if in_activity:
            activity.setdefault(key, value)
            if key == "closing":
                in_activity = False
        else:
            summary.setdefault(key, value)
    return activity if len(activity) == 2 else summary


@register_schema(
    broker="tdameritrade",
    document_kind="statement",
    format_kind="pdf",
    version="2012.1",
    name="TD Ameritrade monthly statement PDF",
)
class TdAmeritradeStatementPdf2012(ImportSchema):
    definition = {"layout": "nested", "carrier": "tda-statement-text"}

    @classmethod
    def sniff(cls, sample: str) -> bool:
        """Retail TDA statements carry a Cash Activity Summary page."""
        return "Cash Activity Summary" in sample and "Independent Advisor" not in sample

    def parse_positions(self, source: Any) -> "list[Any]":
        """ADR-0036: closing holdings from the "Account Positions"
        section. Rows read NAME… <SYM|-> <qty> <price> …; shorts carry
        the trailing-minus quantity and option identity rides a
        continuation line ("CCJ Feb 17 23 28.0 C")."""
        from django_assets.brokerage.schemas.positions import (
            StatementPosition,
            option_canonical_code,
        )

        text = source if isinstance(source, str) else extract_text(source)
        positions: list[Any] = []
        in_section = False
        current: Any = None
        row_re = re.compile(
            r"^(?P<name>.+?) (?P<sym>[A-Z][A-Z0-9]{0,5}|-) "
            r"(?P<qty>[\d,]+(?:\.\d+)?-?) \$? ?(?P<price>[\d,]+\.\d+)"
        )
        option_re = re.compile(
            r"^(?P<u>[A-Z][A-Z0-9.]*) (?P<mon>[A-Z][a-z]{2}) (?P<d>\d{1,2}) "
            r"(?P<yy>\d{2}) (?P<strike>[\d.]+) (?P<r>[CP])$"
        )

        def finish() -> None:
            nonlocal current
            if current is None:
                return
            record, current = current, None
            positions.append(record)

        for raw in text.splitlines():
            line = raw.strip()
            if line == "Account Positions":
                in_section = True
                continue
            if line == "Account Activity" or line.startswith("Total Account Positions"):
                finish()
                in_section = False
                continue
            if not in_section:
                continue
            match = row_re.match(line)
            if match and not line.startswith(("Total", "Symbol", "Investment")):
                finish()
                quantity = _qty(match["qty"])
                current = StatementPosition(
                    quantity=quantity,
                    ticker="" if match["sym"] == "-" else match["sym"],
                    description=match["name"].strip(),
                )
                continue
            if current is not None and line:
                opt = option_re.match(line)
                if opt:
                    current.option_code = option_canonical_code(
                        opt["u"],
                        datetime.date(2000 + int(opt["yy"]), MONTHS[opt["mon"]], int(opt["d"])),
                        Decimal(opt["strike"]),
                        opt["r"],
                    )
        finish()
        return positions

    def parse_batch(self, batch: Any, source: Any) -> Any:
        from django_assets.brokerage.models import ImportLine

        text = source if isinstance(source, str) else extract_text(source)
        lines = text.splitlines()
        balances = _find_balances(lines)
        # Inactive months have no Account Activity section (zero lines to
        # carry metadata), so balances also land on the batch itself.
        # "recognized" is False for image-only scans with no text layer.
        batch.metadata["balances"] = balances
        batch.metadata["recognized"] = "Cash Activity Summary" in text
        if batch.pk:
            batch.save(update_fields=["metadata"])

        number = 0
        record: dict[str, Any] | None = None
        in_activity = False

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
            if line == "Account Activity":
                in_activity = True
                continue
            if not in_activity:
                continue
            if line.startswith("Closing Balance"):
                done = finish()
                if done:
                    yield done
                in_activity = False
                continue
            match = ROW.match(line)
            if match:
                done = finish()
                if done:
                    yield done
                tokens = match["rest"].split()
                tail = _split_row_tail(tokens)
                txn_type = (match["txn_type"] or "").strip()
                activity = (match["activity"] or "").strip()
                record = {
                    "trade_date": match["trade_date"],
                    "settle_date": match["settle_date"],
                    "kind": f"{txn_type} - {activity}".strip(),
                    "txn_type": txn_type,
                    "activity": activity,
                    "description": " ".join(tokens),
                    "detail": [],
                    **tail,
                }
                continue
            if record is not None and line and not PAGE_NOISE.match(line):
                fee = FEE_LINE.match(line)
                option = OPTION_DESC.match(line)
                if fee:
                    total = parse_money(record.get("fee", "0")) + parse_money(fee.group(1))
                    record["fee"] = str(total)
                elif option:
                    record["option"] = option.groupdict()
                elif len(record["detail"]) < 6:
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
            "timestamp": _at(data["settle_date"]),
            "trade_timestamp": _at(data["trade_date"]),
            "origin": "import",
        }
        amount = parse_money(data["amount"])
        price = parse_money(data["price"])
        quantity = _qty(data["quantity"])
        fee = parse_money(data.get("fee", "0"))
        txn_type = data["txn_type"]
        activity = data["activity"]
        description = data["description"]
        label = f"{data['kind']} {description[:60]} (TDA)"

        from django_assets.brokerage import templates as plumbing

        if txn_type in ("Received", "Delivered") and quantity != 0:
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
                    metadata={"tda_kind": data["kind"]},
                    **common,
                )
            ]

        if activity.startswith(("Securities Purchased", "Securities Sold")):
            if quantity == 0:
                # e.g. gold-trust expense sales: cash arrives, no shares move.
                return [
                    plumbing.interest_earned(
                        currency=usd, amount=amount, description=label, **common
                    )
                ]
            buying = activity.startswith("Securities Purchased")
            principal = -amount - fee if buying else amount + fee
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
                        commission=fee,
                        principal=principal,
                        description=label,
                        **common,
                    )
                ]
            instrument = _ensure_security(data, usd)
            trade = eq.buy_shares if buying else eq.sell_shares
            return [
                trade(
                    instrument=instrument,
                    quantity=abs(quantity),
                    price=price,
                    commission=fee,
                    principal=principal,
                    description=label,
                    **common,
                )
            ]

        if activity.startswith("Expense"):
            upper = description.upper()
            if "MARGIN INTEREST" in upper:
                return [
                    plumbing.interest_charged(
                        currency=usd, amount=abs(amount), description=label, **common
                    )
                ]
            if "ADR" in upper:
                return [
                    plumbing.adr_fee_deducted(
                        currency=usd, amount=abs(amount), description=label, **common
                    )
                ]
            if "FOREIGN" in upper or "WITHHOLD" in upper:
                return [
                    plumbing.tax_withholding(
                        currency=usd,
                        amount=abs(amount),
                        tracker_key="foreign_tax",
                        description=label,
                        **common,
                    )
                ]
            return [
                plumbing.account_fee(currency=usd, amount=abs(amount), description=label, **common)
            ]

        if activity.startswith("Income") or txn_type == "Div/Int":
            upper = description.upper()
            if "INTEREST" in upper or "MMDA" in upper or not data["symbol"]:
                template: Callable[..., Transaction] = (
                    plumbing.interest_earned if amount >= 0 else plumbing.interest_charged
                )
                return [template(currency=usd, amount=abs(amount), description=label, **common)]
            character, class_label = _dividend_class(data.get("detail", []))
            return [
                eq.dividend_received(
                    instrument=_ensure_security(data, usd),
                    amount=amount,
                    currency=usd,
                    description=label,
                    character=character,
                    character_label=class_label,
                    **common,
                )
            ]

        if activity.startswith(
            ("Funds Deposited", "Funds Disbursed", "Contributions", "Distributions")
        ):
            # The signed amount is the ground truth, not the label:
            # "ACH OUT - CANCELLED" is a Funds Disbursed row with a
            # POSITIVE amount (the reversal).
            move: Callable[..., Transaction] = (
                plumbing.deposit_currency if amount > 0 else plumbing.withdraw_currency
            )
            return [move(currency=usd, amount=abs(amount), description=label, **common)]

        if (
            activity.startswith("Other")
            or activity == ""
            or txn_type
            in (
                "Received",
                "Delivered",
            )
        ):
            upper = description.upper()
            if "WITHHOLDING" in upper:
                tracker = "foreign_tax" if "FOREIGN" in upper else "tax_withheld"
                if amount > 0:  # withholding refund/modification
                    return [
                        plumbing.interest_earned(
                            currency=usd, amount=amount, description=label, **common
                        )
                    ]
                return [
                    plumbing.tax_withholding(
                        currency=usd,
                        amount=abs(amount),
                        tracker_key=tracker,
                        description=label,
                        **common,
                    )
                ]
            if amount == 0 and quantity != 0 and data["symbol"]:
                return [
                    plumbing.quantity_adjustment(
                        instrument=_ensure_security(data, usd),
                        quantity=quantity,
                        description=label,
                        metadata={"tda_kind": data["kind"]},
                        **common,
                    )
                ]
            journal_move: Callable[..., Transaction] = (
                plumbing.deposit_currency if amount > 0 else plumbing.withdraw_currency
            )
            return [journal_move(currency=usd, amount=abs(amount), description=label, **common)]

        raise ValueError(f"unhandled TDA activity {data['kind']!r}")

    def match_criteria(self, line: Any) -> Any:
        from django_assets.brokerage.matching import MatchCriteria

        data = line.raw_data
        amount = parse_money(data["amount"])
        if not amount:
            raise NotImplementedError("no cash side")
        return MatchCriteria(
            date=_at(data["settle_date"]).date(),
            instrument=ensure_currency("USD"),
            amount=amount,
        )


def _dividend_class(detail: "list[str]") -> "tuple[str, str]":
    """ADR-0038 §2: TDA prints the dividend class as a continuation line
    under the row ("Ordinary Dividends 288.23")."""
    for line in detail:
        lowered = line.lower()
        if "qualified dividend" in lowered and not lowered.startswith("non"):
            return "qualified", line.split("  ")[0][:40]
        if (
            "ordinary dividend" in lowered
            or "nonqualified" in lowered
            or "non-qualified" in lowered
        ):
            return "ordinary", line.split("  ")[0][:40]
    return "unclassified", ""


def _line_kind(payload: dict[str, Any]) -> str:
    """MARK TO MARKET ADJ and friends move neither cash nor shares:
    record them as evidence but keep them out of materialization."""
    # ImportLine.kind is varchar(40); keep room for the 7-char prefix.
    slug = (re.sub(r"[^a-z0-9]+", "_", payload["kind"].lower()).strip("_") or "row")[:32]
    no_op = parse_money(payload["amount"]) == 0 and _qty(payload["quantity"]) == 0
    return f"note_{slug}" if no_op else f"broker_{slug}"


def _qty(token: str) -> Decimal:
    text = token.strip()
    if text in ("-", ""):
        return Decimal(0)
    if text.endswith("-"):
        return -Decimal(text[:-1].replace(",", ""))
    return Decimal(text.replace(",", ""))


def _ensure_option(desc: dict[str, str], usd: Instrument) -> Instrument:
    return ensure_option(
        underlying_symbol=desc["underlying"],
        expiry=datetime.date(2000 + int(desc["year"]), MONTHS[desc["month"]], int(desc["day"])),
        strike=Decimal(desc["strike"]),
        right=desc["right"],
        currency=usd,
    )


def _ensure_security(data: dict[str, Any], usd: Instrument) -> Instrument:
    symbol = data.get("symbol", "")
    is_cusip = bool(re.fullmatch(r"[0-9A-Z]{9}", symbol or "")) and any(
        ch.isdigit() for ch in symbol
    )
    identifier_type = "cusip" if is_cusip else "ticker"
    value = symbol or data["description"][:16].strip()
    existing = Identifier.objects.filter(type=identifier_type, value=value, is_active=True).first()
    if existing is not None:
        return existing.instrument
    instrument = Instrument.objects.create(
        code=value,
        quantity_decimals=8,
        price_decimals=4,
        price_currency=usd,
    )
    Identifier.objects.create(type=identifier_type, value=value, instrument=instrument)
    EquityMeta.objects.create(instrument=instrument)
    return instrument


def _at(value: str) -> datetime.datetime:
    month, day, year = value.split("/")
    return datetime.datetime(2000 + int(year), int(month), int(day), 21, 0, tzinfo=datetime.UTC)
