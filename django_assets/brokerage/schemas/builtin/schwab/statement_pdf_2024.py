"""Charles Schwab brokerage statement PDF (2024–2025 layout).

pdfplumber collapses much of Schwab's kerning ("JournaledFunds",
"CALLMICROSTRATEGYINC"), so parsing is anchored on what survives
intact: the leading MM/DD, the category word, and the numeric tail.

Transaction Details rows read
    [MM/DD] <Category>[<Action>] [SYMBOL] <DESC…> [qty] [price] [charges] [amount] [realized,(LT|ST)]
— the date carries forward to undated continuation rows, and a bare
"Activity" line is the wrapped second half of the "Other Activity"
category label. Option identity is split across the row ("MSTR
CALLMICROSTRATEGYINC $300") and its continuation lines ("01/16/2026
EXP01/16/26", "300.00C Commission$0.65;…").

Cash truth: rows in cash categories (Purchase, Sale, Dividend,
Interest, Expense, Deposit, Withdrawal) sum exactly to the
Transactions-Summary EndingCash − BeginningCash pair, which lands in
batch.metadata for harness acceptance. "Other" rows never move cash —
the statement prints market values there, so only quantities import.

Numeric-tail discipline: real columns always carry decimals
(quantities .0000, money .00), so integer "$142" strike tokens never
pollute the pop. Purchase amounts are NET (amount includes charges);
principal recovers as −amount − charges / amount + charges.
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

CATEGORIES = (
    "Purchase",
    "Sale",
    "Dividend",
    "Interest",
    "Expense",
    "Deposit",
    "Withdrawal",
    "Other",
)
CASH_CATEGORIES = set(CATEGORIES) - {"Other"}
ROW = re.compile(
    r"^(?:(?P<date>\d{2}/\d{2}) )?(?P<category>" + "|".join(CATEGORIES) + r")\b ?(?P<rest>.*)$"
)
#: Column values always carry decimals; "$142" strikes never match.
NUMBER = re.compile(r"^\(?-?\$?[\d,]+\.\d{2,5}\)?$")
REALIZED = re.compile(r"^\(?[\d,.]+\)?,\((?:LT|ST)\)$")
SYMBOL = re.compile(r"^[A-Z][A-Z.]{0,5}$")
OPTION_ROW = re.compile(
    r"(?P<underlying>[A-Z][A-Z.]*?)(?P<expiry>\d{2}/\d{2}/\d{4})? ?"
    r"(?P<right>CALL|PUT)(?P<name>[A-Z&.\- ]*?) ?\$(?P<strike>\d+(?:\.\d+)?)"
)
EXP_CONT = re.compile(r"(?:^|\s)(?P<expiry>\d{2}/\d{2}/\d{4})|EXP(?P<short>\d{2}/\d{2}/\d{2})")
SUMMARY_HEADER = re.compile(r"^BeginningCash\*asof")
PERIOD_YEAR = re.compile(r"_(\d{4})-(\d{2})-\d{2}_")
ACTIONS = (
    "ShortSale",
    "CoverShort",
    "CashDividend",
    "Qual.Dividend",
    "Non-QualifiedDiv",
    "PrYrCashDiv",
    "SpecQualDiv",
    "BankInterest",
    "CreditInterest",
    "SchwabInt",
    "MarginInterest",
    "ADRPassThru",
    "ForeignTaxPaid",
    "JournaledFunds",
    "JournaledShares",
    "AccountTransfer",
    "InternalTransfer",
    "MoneyLinkTxn",
    "MoneyLink",
    "ReinvestedShares",
    "ReinvestedDividend",
    "Cash-In-Lieu",
    "ExpiredLong",
    "ExpiredShort",
    "ExerciseShort",
    "ExerciseLong",
    "Assigned",
    "MergerAdj",
    "Merger",
    "ReverseSplit",
    "StockSplit",
    "AdjustPosition",
    "Option",
    "FundsReceived",
    "WireFunds",
    "SecurityTransfer",
)
REMOVALS = ("ExpiredLong", "ExpiredShort", "ExerciseShort", "ExerciseLong", "Assigned", "Option")


def _split_action(rest: str) -> "tuple[str, str]":
    """Strip a known action from the head of `rest`; the kerning may glue
    it to the symbol ("ReinvestedSharesSPY") or a footnote ("…X,Z")."""
    for action in ACTIONS:
        if rest.startswith(action):
            tail = rest[len(action) :]
            tail = re.sub(r"^(?:X,Z|X|Y|Z)\b ?", "", tail.lstrip(",")).lstrip()
            return action, tail
    return "", rest


def _pop_tail(tokens: "list[str]") -> "list[str]":
    numbers: list[str] = []
    while tokens and REALIZED.fullmatch(tokens[-1]):
        tokens.pop()  # realized gain/(loss) — informational
    while tokens and len(numbers) < 4 and NUMBER.fullmatch(tokens[-1]):
        numbers.insert(0, tokens.pop())
    return numbers


@register_schema(
    broker="schwab",
    document_kind="statement",
    format_kind="pdf",
    version="2024.1",
    name="Schwab brokerage statement PDF",
)
class SchwabStatementPdf2024(ImportSchema):
    definition = {"layout": "nested", "carrier": "schwab-statement-text"}

    def parse_batch(self, batch: Any, source: Any) -> Any:
        from django_assets.brokerage.models import ImportLine

        text = source if isinstance(source, str) else extract_text(source)
        lines = text.splitlines()

        balances: dict[str, str] = {}
        for index, raw in enumerate(lines):
            if SUMMARY_HEADER.match(raw.strip()):
                for candidate in lines[index + 1 : index + 3]:
                    values = [
                        token for token in candidate.strip().split() if NUMBER.fullmatch(token)
                    ]
                    if len(values) >= 8:
                        balances = {
                            "opening": str(parse_money(values[0])),
                            "closing": str(parse_money(values[-1])),
                        }
                        break
                break
        batch.metadata["balances"] = balances
        batch.metadata["recognized"] = bool(balances)
        if batch.pk:
            batch.save(update_fields=["metadata"])

        year = 0
        name_match = PERIOD_YEAR.search(batch.file_name or "")
        if name_match:
            year = int(name_match.group(1))
        end_month = int(name_match.group(2)) if name_match else 12

        number = 0
        record: dict[str, Any] | None = None
        current_date = ""
        in_details = False

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
            if line.startswith("Transaction Detail"):
                in_details = True
                continue
            if not in_details:
                continue
            if line.replace(" ", "").startswith("TotalTransactions"):
                done = finish()
                if done:
                    yield done
                in_details = False
                continue
            match = ROW.match(line)
            has_numbers = bool(re.search(r"\d\.\d{2}", line))
            if match and (match["date"] or has_numbers):
                done = finish()
                if done:
                    yield done
                if match["date"]:
                    current_date = match["date"]
                action, rest = _split_action(match["rest"])
                tokens = rest.split()
                numbers = _pop_tail(tokens)
                symbol = ""
                if tokens and SYMBOL.fullmatch(tokens[0]) and len(tokens) > 1:
                    symbol = tokens[0]
                    tokens = tokens[1:]
                month, day = (int(part) for part in current_date.split("/"))
                row_year = year - 1 if (month == 12 and end_month == 1) else year
                record = {
                    "date": f"{month:02d}/{day:02d}/{row_year}",
                    "category": match["category"],
                    "action": action,
                    "symbol": symbol,
                    "description": " ".join(tokens),
                    "numbers": numbers,
                    "detail": [],
                }
                continue
            if record is not None and line and line != "Activity":
                expiry = EXP_CONT.search(line)
                right = re.search(r"\b[\d,.]+\.\d{2}(C|P)\b", line)
                if expiry and "expiry" not in record:
                    value = expiry["expiry"] or _expand_short_date(expiry["short"])
                    record["expiry"] = value
                if right:
                    record["right"] = right.group(1)
                if not expiry and not right and len(record["detail"]) < 6:
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
            "origin": "import",
        }
        numbers = [parse_money(token) for token in data["numbers"]]
        category = data["category"]
        action = data["action"]
        description = data["description"]
        label = f"{category} {action} {data['symbol']} {description[:40]} (Schwab)".replace(
            "  ", " "
        )

        from django_assets.brokerage import templates as plumbing

        if category in ("Purchase", "Sale"):
            amount = numbers[-1] if numbers else Decimal(0)
            if action == "Cash-In-Lieu" or len(numbers) < 3:
                return [
                    eq.dividend_received(
                        instrument=_resolve_instrument(data, usd),
                        amount=amount,
                        currency=usd,
                        description=label,
                        **common,
                    )
                ]
            quantity, price = numbers[0], numbers[1]
            charges = numbers[2] if len(numbers) == 4 else Decimal(0)
            buying = category == "Purchase"
            principal = -amount - charges if buying else amount + charges
            instrument = _resolve_instrument(data, usd)
            if _is_option(data):
                option_template: Callable[..., Transaction] = (
                    opt.buy_option if buying else opt.sell_option
                )
                return [
                    option_template(
                        instrument=instrument,
                        contracts=abs(quantity),
                        price=price,
                        commission=charges,
                        principal=principal,
                        description=label,
                        **common,
                    )
                ]
            trade = eq.buy_shares if buying else eq.sell_shares
            return [
                trade(
                    instrument=instrument,
                    quantity=abs(quantity),
                    price=price,
                    commission=charges,
                    principal=principal,
                    description=label,
                    **common,
                )
            ]

        if category == "Dividend":
            return [
                eq.dividend_received(
                    instrument=_resolve_instrument(data, usd),
                    amount=numbers[-1],
                    currency=usd,
                    description=label,
                    **common,
                )
            ]

        if category == "Interest":
            amount = numbers[-1]
            interest: Callable[..., Transaction] = (
                plumbing.interest_earned if amount >= 0 else plumbing.interest_charged
            )
            return [interest(currency=usd, amount=abs(amount), description=label, **common)]

        if category == "Expense":
            amount = numbers[-1]
            if action == "MarginInterest":
                return [
                    plumbing.interest_charged(
                        currency=usd, amount=abs(amount), description=label, **common
                    )
                ]
            if action == "ADRPassThru":
                return [
                    plumbing.adr_fee_deducted(
                        currency=usd, amount=abs(amount), description=label, **common
                    )
                ]
            if action == "ForeignTaxPaid":
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

        if category in ("Deposit", "Withdrawal"):
            amount = numbers[-1]
            move: Callable[..., Transaction] = (
                plumbing.deposit_currency if amount > 0 else plumbing.withdraw_currency
            )
            return [move(currency=usd, amount=abs(amount), description=label, **common)]

        if category == "Other":
            cash = _other_cash(data)
            if cash is not None:
                transfer: Callable[..., Transaction] = (
                    plumbing.deposit_currency if cash > 0 else plumbing.withdraw_currency
                )
                return [transfer(currency=usd, amount=abs(cash), description=label, **common)]
            instrument = _resolve_instrument(data, usd)
            quantity = _other_quantity(numbers)
            if action in REMOVALS and _is_option(data):
                position = Holding.current(accounts["holdings"], instrument)
                if position != 0:
                    contracts = abs(quantity) if position > 0 else -abs(quantity)
                else:
                    # Row sign IS the position sign here: ExpiredLong
                    # prints 1.0000, ExpiredShort prints (1.0000).
                    contracts = quantity
                return [
                    opt.expire_option(
                        instrument=instrument,
                        contracts=contracts,
                        description=label,
                        **common,
                    )
                ]
            return [
                plumbing.quantity_adjustment(
                    instrument=instrument,
                    quantity=quantity,
                    description=label,
                    metadata={"schwab_action": action},
                    **common,
                )
            ]

        raise ValueError(f"unhandled Schwab statement category {category!r}")

    def match_criteria(self, line: Any) -> Any:
        from django_assets.brokerage.matching import MatchCriteria

        data = line.raw_data
        if data["category"] in CASH_CATEGORIES and data["numbers"]:
            amount = parse_money(data["numbers"][-1])
        elif data["category"] == "Other" and _other_cash(data) is not None:
            amount = _other_cash(data) or Decimal(0)
        else:
            raise NotImplementedError("no cash side")
        if not amount:
            raise NotImplementedError("no cash side")
        return MatchCriteria(
            date=_at(data["date"]).date(),
            instrument=ensure_currency("USD"),
            amount=amount,
        )


def _line_kind(payload: dict[str, Any]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", f"{payload['category']}_{payload['action']}".lower()).strip(
        "_"
    )[:32]
    if payload["category"] == "Other" and not payload["numbers"]:
        return f"note_{slug}"
    if payload["category"] in CASH_CATEGORIES and not payload["numbers"]:
        return f"note_{slug}"
    return f"broker_{slug}"


def _other_quantity(numbers: "list[Decimal]") -> Decimal:
    """Other rows: 3 numerics = qty/price/market-value, else qty last."""
    if not numbers:
        return Decimal(0)
    return numbers[0] if len(numbers) >= 3 else numbers[-1]


def _other_cash(data: dict[str, Any]) -> "Decimal | None":
    """An Other row whose single value prints with TWO decimals is a
    cash movement (an ACAT of cash); quantities always print four
    ("(31.0000)" reverse split, "125.0000" merger rights)."""
    numbers = data["numbers"]
    if len(numbers) == 1 and re.fullmatch(r"\(?-?\$?[\d,]+\.\d{2}\)?", numbers[0]):
        return parse_money(numbers[0])
    return None


def _is_option(data: dict[str, Any]) -> bool:
    return bool(OPTION_ROW.search(f"{data['symbol']} {data['description']}"))


def _resolve_instrument(data: dict[str, Any], usd: Instrument) -> Instrument:
    text = f"{data['symbol']} {data['description']}"
    match = OPTION_ROW.search(text)
    if match:
        expiry_text = match["expiry"] or data.get("expiry", "")
        if not expiry_text:
            raise ValueError(f"option row without expiry: {text[:60]!r}")
        right = data.get("right") or ("C" if match["right"] == "CALL" else "P")
        underlying = data["symbol"] or match["underlying"]
        return ensure_option(
            underlying_symbol=underlying,
            expiry=_us_date(expiry_text),
            strike=Decimal(match["strike"]),
            right=right,
            currency=usd,
        )
    symbol = data["symbol"]
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


def _expand_short_date(value: str) -> str:
    month, day, year = value.split("/")
    return f"{month}/{day}/20{year}"


def _us_date(value: str) -> datetime.date:
    month, day, year = value.split("/")
    return datetime.date(int(year), int(month), int(day))


def _at(value: str) -> datetime.datetime:
    month, day, year = value.split("/")
    return datetime.datetime(int(year), int(month), int(day), 21, 0, tzinfo=datetime.UTC)
