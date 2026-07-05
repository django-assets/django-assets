"""Robinhood monthly statement PDF (2020–2025 layout).

Account Activity rows read
    <Description…> [SYMBOL] Margin|Cash <CODE> MM/DD/YYYY [qty] [$price] [$amount]
with CUSIP continuations and long descriptions wrapped onto the
PRECEDING line. Codes match the activity-CSV vocabulary (Buy/Sell,
BTO/STO/BTC/STC, CDIV, SLIP, GOLD, …) plus statement-only arrivals:
ACATI (transfer in), MTCH (IRA match), OASGN (assignment), SOFF
(contra-CUSIP spin-off), COIN/RTP (money movement).

Debits and credits are separate COLUMNS that flatten identically in
text, so bytes sources are re-extracted word-by-word and each trailing
amount gets a <DR>/<CR> marker from its x-position against the
Debit/Credit header midpoint. Plain-text sources (synthetic tests)
fall back to per-code direction defaults.

"Executed Trades Pending Settlement" is skipped by design: those rows
are excluded from the statement's own summaries and reappear in the
next month's Account Activity once settled.

Acceptance anchor: the Account Summary's "Net Account Balance"
opening/closing pair (opening prints N/A on the account's first
statement) lands in batch.metadata like the other statement schemas.
"""

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
    option_from_robinhood_description,
    parse_money,
)
from django_assets.core.models import Identifier, Instrument, Transaction
from django_assets.core.queries import Holding
from django_assets.instruments.equities import templates as eq
from django_assets.instruments.equities.models import EquityMeta
from django_assets.instruments.options import templates as opt

ROW = re.compile(
    r"^(?:(?P<desc>.*?) )?(?P<acct>Margin|Cash|Sweep) (?P<code>[A-Z]{2,7}|Buy|Sell) "
    r"(?P<date>\d{2}/\d{2}/\d{4})(?P<rest>( .*)?)$"
)
CUSIP_LINE = re.compile(r"CUSIP: ?(?P<cusip>[0-9A-Z]{9})")
#: Pre-2025 statements print one "Net Account Balance" pair; the 2025
#: layout splits cash into Brokerage Cash + Deposit Sweep pair rows.
BALANCE = re.compile(
    r"^(?P<label>Net Account Balance|Brokerage Cash Balance|Deposit Sweep Balance) "
    r"(?P<opening>N/A|\(?\$[\d,.]+\)?) (?P<closing>\(?\$[\d,.]+\)?)(?= |$)"
)
MONEY = re.compile(r"^\(?\$[\d,]+\.\d{2}\)?$")
BARE_NUMBER = re.compile(r"^[\d,]+(?:\.\d+)?$")
SYMBOL = re.compile(r"^[A-Z][A-Z.]{0,5}$")
MARKER = re.compile(r" <(DR|CR)>$")

TRADE_CODES = {"Buy", "Sell", "BTO", "STO", "BTC", "STC"}
CREDIT_CODES = {
    "Sell",
    "STO",
    "STC",
    "CDIV",
    "MDIV",
    "SLIP",
    "MISC",
    "GMPC",
    "MTCH",
    "CIL",
    "ACATI",
    "INT",
}
DEBIT_CODES = {"Buy", "BTO", "BTC", "GOLD", "MINT", "DFEE", "DTAX", "AFEE"}
QUANTITY_CODES = {"REC", "SPL", "SOFF", "ACATI"}
REMOVAL_CODES = {"OEXP", "OASGN"}


def _statement_lines(source: Any) -> "list[str]":
    """bytes/file-like → per-word extraction with <DR>/<CR> column
    markers; str → verbatim lines (tests rely on code defaults)."""
    if isinstance(source, str):
        return source.splitlines()
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "PDF import schemas require pdfplumber. "
            'Install the extra: pip install "django-assets[pdf]".'
        ) from exc
    handle: Any = io.BytesIO(source) if isinstance(source, bytes) else source
    lines: list[str] = []
    with pdfplumber.open(handle) as pdf:
        boundary: float | None = None  # float-ok (pdf geometry, not money)
        for page in pdf.pages:
            words = sorted(page.extract_words(), key=lambda w: (w["top"], w["x0"]))
            grouped: list[list[dict[str, Any]]] = []
            row_top: float | None = None  # float-ok (pdf geometry, not money)
            for word in words:
                if row_top is None or word["top"] - row_top > 2.5:
                    grouped.append([word])
                    row_top = word["top"]
                else:
                    grouped[-1].append(word)
            for row in grouped:
                row.sort(key=lambda w: w["x0"])
                text = " ".join(w["text"] for w in row)
                labels = {w["text"]: w for w in row}
                if "Debit" in labels and "Credit" in labels:
                    boundary = (labels["Debit"]["x1"] + labels["Credit"]["x0"]) / 2
                last = row[-1]
                if boundary is not None and MONEY.fullmatch(last["text"]):
                    side = "CR" if last["x0"] >= boundary else "DR"
                    text += f" <{side}>"
                lines.append(text)
    return lines


@register_schema(
    broker="robinhood",
    document_kind="statement",
    format_kind="pdf",
    version="2020.1",
    name="Robinhood monthly statement PDF",
)
class RobinhoodStatementPdf2020(ImportSchema):
    definition = {"layout": "nested", "carrier": "robinhood-statement-text"}

    @classmethod
    def sniff(cls, sample: str) -> bool:
        """Robinhood monthly statements brand every page."""
        return "Robinhood" in sample and (
            "Account Activity" in sample or "Portfolio Value" in sample
        )

    def parse_batch(self, batch: Any, source: Any) -> Any:
        from django_assets.brokerage.models import ImportLine

        lines = _statement_lines(source)

        pairs: dict[str, tuple[str, str]] = {}
        for raw in lines:
            match = BALANCE.match(MARKER.sub("", raw.strip()))
            if match and match["label"] not in pairs:
                pairs[match["label"]] = (match["opening"], match["closing"])
        balances = _combine_balances(pairs)
        batch.metadata["balances"] = balances
        batch.metadata["recognized"] = bool(balances)
        if batch.pk:
            batch.save(update_fields=["metadata"])

        number = 0
        record: dict[str, Any] | None = None
        pending_desc = ""
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
                kind=f"broker_{payload['code'].lower()}",
                source_reference=f"{batch.file_name}#{number}",
            )

        for raw in lines:
            line = raw.strip()
            if line.startswith("Account Activity"):
                in_activity = True
                pending_desc = ""
                continue
            if line.startswith(
                ("Total Funds Paid", "Executed Trades Pending", "Important Information")
            ):
                done = finish()
                if done:
                    yield done
                in_activity = False
                continue
            if not in_activity:
                continue
            if line.startswith(("Description Symbol", "Page ")):
                continue
            marker_match = MARKER.search(line)
            column = marker_match.group(1) if marker_match else ""
            bare = MARKER.sub("", line)
            match = ROW.match(bare)
            if match:
                done = finish()
                if done:
                    yield done
                desc = (match["desc"] or "").strip()
                if pending_desc and (not desc or len(desc) < 3):
                    desc = pending_desc
                elif pending_desc and not SYMBOL.search(desc.split()[-1] if desc else ""):
                    desc = f"{pending_desc} {desc}".strip()
                pending_desc = ""
                tokens = (match["rest"] or "").split()
                monies = [t for t in tokens if MONEY.fullmatch(t)]
                bares = [t for t in tokens if BARE_NUMBER.fullmatch(t)]
                record = {
                    "code": match["code"],
                    "date": match["date"],
                    "description": desc,
                    "quantity": bares[0] if bares else "",
                    "price": monies[0] if len(monies) >= 2 else "",
                    "amount": monies[-1] if monies else "",
                    "column": column,
                    "detail": [],
                }
                continue
            cusip = CUSIP_LINE.search(bare)
            if cusip and record is not None:
                record["cusip"] = cusip["cusip"]
                continue
            if record is not None and len(record["detail"]) < 4 and bare:
                record["detail"].append(bare[:90])
            if bare and not cusip:
                pending_desc = bare[:90]
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
        code = data["code"]
        description = data["description"]
        quantity = Decimal(data["quantity"].replace(",", "")) if data["quantity"] else Decimal(0)
        price = parse_money(data["price"]) if data["price"] else Decimal(0)
        amount = parse_money(data["amount"]) if data["amount"] else Decimal(0)
        signed = _signed_amount(data, amount)
        label = f"{code} {description[:70]} (Robinhood)"

        from django_assets.brokerage import templates as plumbing

        if code in TRADE_CODES:
            option = option_from_robinhood_description(description, currency=usd)
            if code in ("BTO", "STO", "BTC", "STC") or (
                option is not None and code not in ("Buy", "Sell")
            ):
                if option is None:
                    raise ValueError(f"option code {code} without descriptor: {description!r}")
                buying = code in ("BTO", "BTC")
                option_template: Callable[..., Transaction] = (
                    opt.buy_option if buying else opt.sell_option
                )
                return [
                    option_template(
                        instrument=option,
                        contracts=abs(quantity),
                        price=price,
                        principal=amount,
                        description=label,
                        **common,
                    )
                ]
            buying = code == "Buy"
            trade = eq.buy_shares if buying else eq.sell_shares
            return [
                trade(
                    instrument=_ensure_security(data, usd),
                    quantity=abs(quantity),
                    price=price,
                    principal=amount,
                    description=label,
                    **common,
                )
            ]

        if code in REMOVAL_CODES:
            option = option_from_robinhood_description(description, currency=usd)
            if option is None:
                raise ValueError(f"{code} without option descriptor: {description!r}")
            position = Holding.current(accounts["holdings"], option)
            if position != 0:
                contracts = abs(quantity) if position > 0 else -abs(quantity)
            else:
                contracts = -quantity
            return [
                opt.expire_option(
                    instrument=option, contracts=contracts, description=label, **common
                )
            ]

        if code in ("CDIV", "MDIV", "CIL"):
            return [
                eq.dividend_received(
                    instrument=_ensure_security(data, usd),
                    amount=abs(amount),
                    currency=usd,
                    description=label,
                    **common,
                )
            ]
        if code in ("SLIP", "MISC", "GMPC", "MTCH", "INT"):
            return [
                plumbing.interest_earned(
                    currency=usd, amount=abs(amount), description=label, **common
                )
            ]
        if code == "GOLD":
            return [
                plumbing.account_fee(currency=usd, amount=abs(amount), description=label, **common)
            ]
        if code == "MINT":
            return [
                plumbing.interest_charged(
                    currency=usd, amount=abs(amount), description=label, **common
                )
            ]
        if code in ("DFEE", "AFEE"):
            return [
                plumbing.adr_fee_deducted(
                    currency=usd, amount=abs(amount), description=label, **common
                )
            ]
        if code == "DTAX":
            return [
                plumbing.tax_withholding(
                    currency=usd,
                    amount=abs(amount),
                    tracker_key="foreign_tax",
                    description=label,
                    **common,
                )
            ]

        if code in ("ACH", "RTP", "COIN"):
            move: Callable[..., Transaction] = (
                plumbing.deposit_currency if signed > 0 else plumbing.withdraw_currency
            )
            return [move(currency=usd, amount=abs(amount), description=label, **common)]

        if code in QUANTITY_CODES:
            if code == "ACATI" and amount and not quantity:
                return [
                    plumbing.deposit_currency(
                        currency=usd, amount=abs(amount), description=label, **common
                    )
                ]
            return [
                plumbing.quantity_adjustment(
                    instrument=_ensure_security(data, usd),
                    quantity=quantity,
                    description=label,
                    metadata={"robinhood_code": code},
                    **common,
                )
            ]

        raise ValueError(f"unhandled Robinhood statement code {code!r}")

    def match_criteria(self, line: Any) -> Any:
        from django_assets.brokerage.matching import MatchCriteria

        data = line.raw_data
        amount = parse_money(data["amount"]) if data["amount"] else Decimal(0)
        if not amount:
            raise NotImplementedError("no cash side")
        return MatchCriteria(
            date=_at(data["date"]).date(),
            instrument=ensure_currency("USD"),
            amount=_signed_amount(data, amount),
        )


def _combine_balances(pairs: "dict[str, tuple[str, str]]") -> dict[str, str]:
    """Net Account Balance when the statement prints it; otherwise the
    sum of the Brokerage Cash and Deposit Sweep pairs (2025 layout).
    An N/A opening (the account's first statement) leaves the key out."""
    if "Net Account Balance" in pairs:
        selected = [pairs["Net Account Balance"]]
    else:
        selected = [
            pairs[label]
            for label in ("Brokerage Cash Balance", "Deposit Sweep Balance")
            if label in pairs
        ]
    if not selected:
        return {}
    balances = {"closing": str(sum(parse_money(closing) for _open, closing in selected))}
    if all(opening != "N/A" for opening, _close in selected):
        balances["opening"] = str(sum(parse_money(opening) for opening, _close in selected))
    return balances


def _signed_amount(data: dict[str, Any], amount: Decimal) -> Decimal:
    """Column marker first; code semantics as the plain-text fallback;
    ACH/RTP/COIN descriptions break their own tie."""
    if data.get("column") == "CR":
        return abs(amount)
    if data.get("column") == "DR":
        return -abs(amount)
    code = data["code"]
    if code in CREDIT_CODES:
        return abs(amount)
    if code in DEBIT_CODES:
        return -abs(amount)
    if "WITHDRAW" in data["description"].upper():
        return -abs(amount)
    return abs(amount)


def _ensure_security(data: dict[str, Any], usd: Instrument) -> Instrument:
    cusip = data.get("cusip", "")
    description = data["description"]
    tokens = description.split()
    symbol = ""
    if tokens and SYMBOL.fullmatch(tokens[-1]) and len(tokens) > 1:
        symbol = tokens[-1]
    elif tokens and SYMBOL.fullmatch(tokens[0]):
        symbol = tokens[0]
    if cusip:
        existing = Identifier.objects.filter(type="cusip", value=cusip, is_active=True).first()
        if existing is not None:
            return existing.instrument
    if symbol:
        existing = Identifier.objects.filter(type="ticker", value=symbol, is_active=True).first()
        if existing is not None:
            return existing.instrument
    code = symbol or cusip or description[:16].strip() or "UNKNOWN"
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
    return datetime.datetime(int(year), int(month), int(day), 21, 0, tzinfo=datetime.UTC)
