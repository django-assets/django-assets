"""Home Broker «Resumen de Cuenta» PDF — the CNV-731/2018 account
report produced by the Home Broker platform many Argentine ALyCs run
(validated against Invertir en Bolsa statements).

Three cash pools move independently and reconcile separately:
pesos (ARS), main dollars («DOLARES MERC. VALORES», USD) and the
foreign-custody dollar subaccount («DOLARUSA DOLARES USA ESP 7000»,
its own instrument) — CEDEAR dividends land there and EGAJ journals
move them into main USD, mirrored by NCCD credits in the cash ledger.

The money columns (Importe en Pesos / Importe USD / Importe DOLAR
EXT.) are indistinguishable in flat text, so bytes sources get a
word-position pass: column labels anchor x-positions and every numeric
token is tagged `¦key=value` by its right edge (values are
right-aligned to the header). Plain-text sources (tests) carry the
same tags.

Sections used:
- DETALLE DE MOVIMIENTOS — the ARS/USD cash ledger (SALDO INICIAL
  opens it; «Saldo al» lines re-state running balances).
- MOVIMIENTOS POR ESPECIE — per-security quantity history; imports
  ONLY what the cash ledger cannot see: share/bond dividends and CANJE
  swaps (quantity, no cash) and every DOLARUSA row (the EXT ledger).
- DETALLE DE COMISIONES — joined by Numero: Total Gastos becomes the
  commission on the matching trade (the ledger's importe is all-in,
  so principal = cash ∓ commission keeps both cash and basis exact).
- POSICION AL — opening/closing balances for all three pools.
- INCREMENTOS/DECREMENTOS — skipped: it restates COBR/PAGO rows the
  ledger already carries.

Comprobante semantics follow the platform codes (CPRA/VTAS compra y
venta en pesos, CPU$/VTU$ paridad en dólares, COPR/VTPR primas,
COBR/COBW/COME/CU$S cobros, PAGO/PAGW/PAME/PAU$/PAUW pagos, DIV,
RTA renta y amortización, DECC mantenimiento, NCCD nota de crédito,
EGAJ asiento entre subcuentas, NOCR SIRCREB, CLCC/CCTE cauciones).
A negative DIV in the cash ledger is the dividend-processing gasto —
the dividend itself arrives in the DOLARUSA subaccount.
"""

import datetime
import io
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from django_assets.brokerage.accounts import ensure_standard_accounts
from django_assets.brokerage.schemas import ImportSchema, register_schema
from django_assets.brokerage.schemas.instruments import ensure_currency
from django_assets.core.models import Identifier, Instrument, Transaction
from django_assets.instruments.equities import templates as eq
from django_assets.instruments.equities.models import EquityMeta

ROW = re.compile(
    r"^(?P<settle>\d{2}/\d{2}/\d{2}) (?P<trade>\d{2}/\d{2}/\d{2}) "
    r"(?P<comp>[A-Z$]{2,5}) (?P<numero>\d+)(?P<rest>.*)$"
)
COMISION_ROW = re.compile(
    r"^(?P<date>\d{2}/\d{2}/\d{2}) (?P<comp>[A-Z$]{2,5}) (?P<numero>\d+) "
    r"(?P<especie>\S+)(?P<rest>.*)$"
)
NUMBER = re.compile(r"^[\d,]+(?:\.\d+)?-?$")
TAG = re.compile(r"¦(?P<key>[a-z]+)=(?P<value>[\d,.]+-?)")
TICKER = re.compile(r"^[A-Z][A-Z0-9$]{1,7}$")
#: Column labels → tag keys; anchors rebuild at every header line.
ANCHOR_LABELS = {
    "Cantidad": "qty",
    "Precio": "px",
    "Pesos": "ars",
    "USD": "usd",
    "EXT.": "ext",
    "Saldo": "saldo",
    "Gastos": "total",
}
SECTION_MARKS = (
    ("DETALLE DE MOVIMIENTOS", "detalle"),
    ("MOVIMIENTOS POR ESPECIE", "especie"),
    ("DETALLE DE COMISIONES", "comisiones"),
    ("POSICION AL", "posicion"),
    ("INCREMENTOS/DECREMENTOS", "incrementos"),
)

BUYS = {"CPRA", "CRPN", "CPU$", "COPR"}
SELLS = {"VTAS", "VRCN", "VTU$", "VTPR"}
RECEIPTS = {"COBR", "COBW", "COME", "CU$S", "NOCR", "NCCD", "COUW"}
PAYMENTS = {"PAGO", "PAGW", "PAME", "PAU$", "PAUW"}
FEES = {"DECC"}
#: Everything else with a cash amount moves cash by its own sign —
#: cauciones, custody transfers, MEP conversion legs, débito/crédito
#: notes. The comprobante is preserved in kind and description.
DIVIDEND = "DIV"
RENT = "RTA"
EXT_CODE = "DOLARUSA"
PERIOD_LINE = re.compile(r"Desde Fecha: (\d{2}/\d{2}/\d{2}) Hasta Fecha: (\d{2}/\d{2}/\d{2})")


def _money(token: str) -> Decimal:
    text = token.strip().replace(",", "")
    if not text:
        return Decimal(0)
    if text.endswith("-"):
        return -Decimal(text[:-1])
    return Decimal(text)


def _tagged_lines(source: Any) -> "list[str]":
    """bytes/file-like → visual lines with ¦key=value column tags from
    word x-positions; str → verbatim (tests pre-tag)."""
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
    anchors: dict[str, float] = {}
    with pdfplumber.open(handle) as pdf:
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
                labels = [w["text"] for w in row]
                if "Cantidad" in labels or "Gastos" in labels:
                    anchors = {
                        ANCHOR_LABELS[w["text"]]: w["x1"] for w in row if w["text"] in ANCHOR_LABELS
                    }
                elif anchors:
                    tags = ""
                    for w in row:
                        if not NUMBER.fullmatch(w["text"]):
                            continue
                        key = min(
                            anchors,
                            key=lambda k: abs(anchors[k] - w["x1"]),
                            default="",
                        )
                        if key and abs(anchors[key] - w["x1"]) <= 6:
                            tags += f"¦{key}={w['text']}"
                    text += tags
                lines.append(text)
    return lines


@register_schema(
    broker="homebroker",
    document_kind="resumen",
    format_kind="pdf",
    version="2018.1",
    name="Home Broker resumen de cuenta PDF (CNV 731/2018)",
)
class HomeBrokerResumenPdf2018(ImportSchema):
    definition = {"layout": "nested", "carrier": "homebroker-resumen-text"}

    @classmethod
    def sniff(cls, sample: str) -> bool:
        """The CNV resumen names its comitente and movement ledger."""
        return "Comitente:" in sample and "DETALLE DE MOVIMIENTOS" in sample

    def parse_positions(self, source: Any) -> "list[Any]":
        """ADR-0036: the CLOSING «POSICION AL <hasta>» security rows
        (cash rows excluded — the triple-currency acceptance owns
        them). Quantities come from the positional qty tag."""
        from django_assets.brokerage.schemas.positions import StatementPosition

        lines = _tagged_lines(source)
        period = next((m for line in lines if (m := PERIOD_LINE.search(TAG.sub("", line)))), None)
        desde = period.group(1) if period else ""
        hasta = period.group(2) if period else ""
        positions: list[Any] = []
        in_closing = False
        for raw in lines:
            line = raw.strip()
            plain = TAG.sub("", line).strip()
            if plain.startswith("POSICION AL "):
                date = plain.split("POSICION AL ", 1)[1][:8]
                in_closing = date == hasta or desde == hasta
                continue
            if plain.startswith(("TOTAL POSICION", "INCREMENTOS", "DETALLE")):
                in_closing = False
                continue
            if not in_closing:
                continue
            if plain.startswith(
                (
                    "PESOS ",
                    "DOLARES ",
                    "DOLARUSA",
                    "DOLAR EXTERIOR",
                    "U$ ",
                    "U$S ",
                    "CASH",
                    "Especie ",
                )
            ):
                continue
            tags = {m["key"]: m["value"] for m in TAG.finditer(line)}
            head = plain.split()
            if tags.get("qty") and head and TICKER.fullmatch(head[0]):
                positions.append(
                    StatementPosition(
                        quantity=_money(tags["qty"]),
                        ticker=head[0],
                        description=plain[:80],
                    )
                )
        return positions

    def parse_batch(self, batch: Any, source: Any) -> Any:
        from django_assets.brokerage.models import ImportLine

        lines = _tagged_lines(source)

        commissions: dict[str, dict[str, str]] = {}
        balances: dict[str, str] = {}
        booked_ars: set[str] = set()  # numeros with a pesos ledger row
        section = ""
        stage = ""
        period = next((m for line in lines if (m := PERIOD_LINE.search(TAG.sub("", line)))), None)
        desde = period.group(1) if period else ""
        hasta = period.group(2) if period else ""

        def tags_of(line: str) -> dict[str, str]:
            return {m["key"]: m["value"] for m in TAG.finditer(line)}

        for raw in lines:
            line = raw.strip()
            plain = TAG.sub("", line).strip()
            for mark, name in SECTION_MARKS:
                if plain.startswith(mark):
                    section = name
                    if name == "posicion":
                        date = plain.split("POSICION AL ", 1)[1][:8]
                        stage = "close" if date == hasta and date != desde else "open"
                        if desde == hasta:  # closing-only snapshot files
                            stage = "close"
                    break
            else:
                if section == "comisiones":
                    match = COMISION_ROW.match(plain)
                    if match and "total" in tags_of(line):
                        commissions[match["numero"]] = {
                            "date": match["date"],
                            "comp": match["comp"],
                            "especie": match["especie"],
                            "total": tags_of(line)["total"],
                        }
                elif section == "detalle":
                    if plain.startswith("SALDO INICIAL"):
                        row_tags = tags_of(line)
                        balances.setdefault("ars_open", str(_money(row_tags.get("ars", "0"))))
                        balances.setdefault("usd_open", str(_money(row_tags.get("usd", "0"))))
                    else:
                        row = ROW.match(plain)
                        if row and tags_of(line).get("ars"):
                            booked_ars.add(row["numero"])
                elif section == "posicion" and stage:
                    row_tags = tags_of(line)
                    qty = row_tags.get("qty", "")
                    # Cash-row labels drifted across platform vintages.
                    if plain.startswith("PESOS Cuenta"):
                        balances[f"ars_{stage}"] = str(_money(qty)) if qty else "0"
                    elif plain.startswith(("DOLARES MERC. VALORES", "U$ Cuenta", "U$S Cuenta")):
                        balances[f"usd_{stage}"] = str(_money(qty)) if qty else "0"
                    elif plain.startswith(("DOLARUSA", "DOLARES USA ESP", "DOLAR EXTERIOR")):
                        balances[f"ext_{stage}"] = str(_money(qty)) if qty else "0"

        for key in ("ars", "usd", "ext"):
            balances.setdefault(f"{key}_open", "0")
            balances.setdefault(f"{key}_close", "0")
        batch.metadata["balances"] = balances
        batch.metadata["recognized"] = any(
            "DETALLE DE MOVIMIENTOS" in TAG.sub("", line) for line in lines
        )
        if batch.pk:
            batch.save(update_fields=["metadata"])

        number = 0
        section = ""
        especie_group = ""
        pending_desc = ""
        record: dict[str, Any] | None = None
        running = {"ars": _money(balances["ars_open"]), "usd": _money(balances["usd_open"])}
        adjustments: list[dict[str, Any]] = []

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
            plain = TAG.sub("", line).strip()
            for mark, name in SECTION_MARKS:
                if plain.startswith(mark):
                    done = finish()
                    if done:
                        yield done
                    if name != "especie":
                        especie_group = ""  # groups persist across page breaks
                    section = name
                    pending_desc = ""
                    break
            else:
                if section not in ("detalle", "especie"):
                    continue
                if plain.startswith(
                    (
                        "Fecha Liq",
                        "Saldo al",
                        "Resumen de Cuenta",
                        "Comitente:",
                        "Desde Fecha",
                        "Le informamos",
                        "SALDO INICIAL",
                    )
                ):
                    if plain.startswith(("Saldo al", "SALDO INICIAL")):
                        done = finish()
                        if done:
                            yield done
                    if section == "detalle" and plain.startswith("Saldo al"):
                        # The platform's own running balance occasionally
                        # drifts a cent from its rows; the Saldo line is
                        # the printed truth, so book the difference.
                        saldo_tags = tags_of(line)
                        date = plain.split("Saldo al ", 1)[1][:8]
                        for key in ("ars", "usd"):
                            if key not in saldo_tags:
                                continue
                            stated = _money(saldo_tags[key])
                            delta = stated - running[key]
                            if delta and abs(delta) <= Decimal("0.05"):
                                adjustments.append({"date": date, "currency": key, "delta": delta})
                            running[key] = stated
                    continue
                match = ROW.match(plain)
                if match:
                    done = finish()
                    if done:
                        yield done
                    row_tags = tags_of(line)
                    if section == "detalle":
                        for key in ("ars", "usd"):
                            if row_tags.get(key):
                                running[key] += _money(row_tags[key])
                    rest = TAG.sub("", match["rest"]).strip()
                    rest_tokens = [t for t in rest.split() if not NUMBER.fullmatch(t)]
                    symbol = ""
                    if rest_tokens and TICKER.fullmatch(rest_tokens[0]):
                        symbol = rest_tokens[0]
                    elif pending_desc:
                        head = pending_desc.split()
                        if head and TICKER.fullmatch(head[0]):
                            symbol = head[0]
                    numero = match["numero"]
                    if not symbol and numero in commissions:
                        symbol = commissions[numero]["especie"]
                    record = {
                        "settle": match["settle"],
                        "trade": match["trade"],
                        "comp": match["comp"],
                        "numero": numero,
                        "section": section,
                        "especie_group": especie_group,
                        "symbol": symbol,
                        "description": (pending_desc + " " + rest).strip()[:120],
                        "commission": commissions.get(numero, {}).get("total", ""),
                        "detail": [],
                        **{
                            k: row_tags.get(k, "")
                            for k in ("qty", "px", "ars", "usd", "ext", "saldo")
                        },
                    }
                    pending_desc = ""
                    continue
                if section == "especie" and plain and not TAG.search(line):
                    head = plain.split()
                    if head and TICKER.fullmatch(head[0]) and not plain.startswith("EFECT"):
                        especie_group = head[0]
                        pending_desc = ""
                        continue
                if record is not None and plain and len(record["detail"]) < 4:
                    record["detail"].append(plain[:90])
                if plain and not TAG.search(line):
                    pending_desc = plain[:90]
        done = finish()
        if done:
            yield done

        for adjustment in adjustments:
            number += 1
            delta = adjustment["delta"]
            token = f"{abs(delta)}" + ("-" if delta < 0 else "")
            yield ImportLine(
                batch=batch,
                line_number=number,
                raw_data={
                    "settle": adjustment["date"],
                    "trade": adjustment["date"],
                    "comp": "AJUS",
                    "numero": "",
                    "section": "ajuste",
                    "especie_group": "",
                    "symbol": "",
                    "description": f"redondeo según Saldo al {adjustment['date']}",
                    "commission": "",
                    "detail": [],
                    "qty": "",
                    "px": "",
                    "ars": token if adjustment["currency"] == "ars" else "",
                    "usd": token if adjustment["currency"] == "usd" else "",
                    "ext": "",
                    "saldo": "",
                    "balances": balances,
                },
                kind="broker_ajuste",
                source_reference=f"{batch.file_name}#{number}",
            )

        # Gastos the cash ledger never booked (e.g. the pesos charge on a
        # dollar-side renta): the commissions table is the only place the
        # statement records them, and the closing POSICION includes them.
        for numero, info in commissions.items():
            if numero in booked_ars:
                continue
            total = _money(info["total"])
            if total == 0:
                continue
            number += 1
            yield ImportLine(
                batch=batch,
                line_number=number,
                raw_data={
                    "settle": info["date"],
                    "trade": info["date"],
                    "comp": info["comp"],
                    "numero": numero,
                    "section": "gasto",
                    "especie_group": "",
                    "symbol": info["especie"],
                    "description": f"gastos {info['comp']} {info['especie']}",
                    "commission": "",
                    "detail": [],
                    "qty": "",
                    "px": "",
                    "ars": f"{info['total']}-",
                    "usd": "",
                    "ext": "",
                    "saldo": "",
                    "balances": balances,
                },
                kind="broker_gasto",
                source_reference=f"{batch.file_name}#{number}",
            )

    def materialize_line(self, line: Any) -> list[Transaction]:
        data = line.raw_data
        accounts = ensure_standard_accounts(line.batch.account.owner) | {"cash": line.batch.account}
        ars = ensure_currency("ARS")
        usd = ensure_currency("USD")
        ext = ensure_currency(EXT_CODE)
        common: dict[str, Any] = {
            "accounts": accounts,
            "timestamp": _at(data["settle"]),
            "trade_timestamp": _at(data["trade"]),
            "origin": "import",
        }
        comp = data["comp"]
        quantity = _money(data["qty"]) if data["qty"] else Decimal(0)
        commission = _money(data["commission"]) if data["commission"] else Decimal(0)
        label = f"{comp} {data['numero']} {data['description'][:50]} (HomeBroker)"

        from django_assets.brokerage import templates as plumbing

        if data["section"] == "especie":
            if data["especie_group"] == EXT_CODE:
                amount = quantity  # the EXT ledger counts dollars as Cantidad
                if comp == "DIV" and amount > 0:
                    return [
                        eq.dividend_received(
                            instrument=_ensure_security(data, usd),
                            amount=amount,
                            currency=ext,
                            description=label,
                            **common,
                        )
                    ]
                move: Callable[..., Transaction] = (
                    plumbing.deposit_currency if amount > 0 else plumbing.withdraw_currency
                )
                return [
                    move(
                        currency=ext,
                        amount=abs(amount),
                        description=label,
                        via="conversions",  # subaccount leg of an FX journal
                        **common,
                    )
                ]
            # Share/bond dividends and CANJE swaps: quantity, no cash.
            instrument = _ensure_especie(data["especie_group"] or data["symbol"], ars)
            return [
                plumbing.quantity_adjustment(
                    instrument=instrument,
                    quantity=quantity,
                    description=label,
                    metadata={"homebroker_comp": comp, "numero": data["numero"]},
                    **common,
                )
            ]

        amount, currency = _cash_of(data, ars, usd, ext)

        if data["section"] == "gasto":
            return [
                plumbing.account_fee(
                    currency=currency, amount=abs(amount), description=label, **common
                )
            ]

        if comp in BUYS or comp in SELLS:
            buying = comp in BUYS
            principal = (-amount - commission) if buying else (amount + commission)
            trade = eq.buy_shares if buying else eq.sell_shares
            instrument = _ensure_especie(data["symbol"], currency)
            return [
                trade(
                    instrument=instrument,
                    quantity=abs(quantity),
                    price=_money(data["px"]) if data["px"] else Decimal(0),
                    commission=commission,
                    principal=principal,
                    currency=currency,
                    description=label,
                    metadata={"numero": data["numero"]},
                    **common,
                )
            ]

        if comp in RECEIPTS:
            via = "conversions" if comp == "NCCD" else "funding"
            return [
                plumbing.deposit_currency(
                    currency=currency, amount=abs(amount), description=label, via=via, **common
                )
            ]
        if comp in PAYMENTS:
            return [
                plumbing.withdraw_currency(
                    currency=currency, amount=abs(amount), description=label, **common
                )
            ]
        if comp in FEES:
            return [
                plumbing.account_fee(
                    currency=currency, amount=abs(amount), description=label, **common
                )
            ]
        if comp == DIVIDEND:
            if amount > 0:
                return [
                    eq.dividend_received(
                        instrument=_ensure_security(data, currency),
                        amount=amount,
                        currency=currency,
                        description=label,
                        **common,
                    )
                ]
            return [  # negative DIV = the dividend-processing gasto
                plumbing.account_fee(
                    currency=currency, amount=abs(amount), description=label, **common
                )
            ]
        if comp == RENT and amount > 0:
            # ADR-0038 §5 / R-12: renta y amortización blends coupon with
            # principal return; without amortization schedules the split
            # is unknowable — visibly unclassified, never confidently
            # "interest".
            return [
                plumbing.interest_earned(
                    currency=currency,
                    amount=amount,
                    description=label,
                    character="unclassified",
                    character_label="RTA renta y amortización",
                    **common,
                )
            ]
        # Cauciones, custody transfers, MEP legs, débito/crédito notes and
        # anything the platform invents next: cash moves by its own sign.
        cash_move: Callable[..., Transaction] = (
            plumbing.deposit_currency if amount > 0 else plumbing.withdraw_currency
        )
        return [cash_move(currency=currency, amount=abs(amount), description=label, **common)]

    def match_criteria(self, line: Any) -> Any:
        from django_assets.brokerage.matching import MatchCriteria

        data = line.raw_data
        ars = ensure_currency("ARS")
        usd = ensure_currency("USD")
        ext = ensure_currency(EXT_CODE)
        if data["section"] == "especie":
            if data["especie_group"] != EXT_CODE:
                raise NotImplementedError("no cash side")
            amount, currency = _money(data["qty"]), ext
        else:
            amount, currency = _cash_of(data, ars, usd, ext)
        if not amount:
            raise NotImplementedError("no cash side")
        return MatchCriteria(date=_at(data["settle"]).date(), instrument=currency, amount=amount)


def _cash_of(
    data: dict[str, Any], ars: Instrument, usd: Instrument, ext: Instrument
) -> "tuple[Decimal, Instrument]":
    if data.get("ars"):
        return _money(data["ars"]), ars
    if data.get("usd"):
        return _money(data["usd"]), usd
    if data.get("ext"):
        return _money(data["ext"]), ext
    return Decimal(0), ars


def _line_kind(payload: dict[str, Any]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", payload["comp"].lower()).strip("_")[:20]
    if payload["section"] == "especie":
        group = payload["especie_group"]
        if group == EXT_CODE:
            return f"broker_ext_{slug}"[:40]
        if payload["comp"] in ("DIV",) and payload.get("qty"):
            return f"broker_esp_{slug}"[:40]
        return f"note_esp_{slug}"[:40]
    has_cash = any(payload.get(k) for k in ("ars", "usd", "ext"))
    return f"broker_{slug}" if has_cash else f"note_{slug}"


def _ensure_especie(symbol: str, currency: Instrument) -> Instrument:
    value = symbol or "UNKNOWN"
    existing = Identifier.objects.filter(type="ticker", value=value, is_active=True).first()
    if existing is not None:
        return existing.instrument
    instrument = Instrument.objects.create(
        code=value, quantity_decimals=8, price_decimals=4, price_currency=currency
    )
    Identifier.objects.create(type="ticker", value=value, instrument=instrument)
    EquityMeta.objects.create(instrument=instrument)
    return instrument


def _ensure_security(data: dict[str, Any], currency: Instrument) -> Instrument:
    return _ensure_especie(data.get("symbol", ""), currency)


def _at(value: str) -> datetime.datetime:
    day, month, year = value.split("/")  # DD/MM/YY — Argentine dates
    return datetime.datetime(2000 + int(year), int(month), int(day), 21, 0, tzinfo=datetime.UTC)
