"""Synthetic coverage for the Home Broker resumen schema (fabricated,
pre-tagged text — the real corpus derives ¦key=value column tags from
word x-positions; the private files are git-excluded)."""

from decimal import Decimal

import pytest

from django_assets.brokerage.imports import process_batch
from django_assets.brokerage.models import ImportBatch
from django_assets.brokerage.schemas.instruments import ensure_currency
from django_assets.core.models import Instrument
from django_assets.core.queries import Holding

pytestmark = pytest.mark.ledger

D = Decimal

# Ledger walk: ARS −75.45 +122,340.47 −74.51 −29,665.17 = 92,525.34;
# +28.00 = 92,553.34, Saldo prints 92,553.35 (the platform's cent
# drift → one ajuste line); the unbooked RTA-37998 gasto −135.07 lands
# only in the commissions table → ARS close 92,418.28.
# USD 11,610.64 +135 +258.07 −123.00 = 11,880.71.
# EXT 135 −135 +49.65 +135.00 = 184.65.
RESUMEN_TEXT = """\
RESUMEN DE CUENTA
Comitente: 300718 SELDEN TAYLOR ALLEN Fecha: 06/09/24
Desde Fecha: 01/01/23 Hasta Fecha: 31/12/23 Hoja: 1
POSICION AL 01/01/23
Especie Detalle Custodia Cantidad Precio Importe en Pesos % de Cartera Importe en Dolares % de Cartera
PESOS Cuenta Corriente 75.45- 1.000 75.45- 0.41-¦qty=75.45-¦px=1.000
DOLARES USA ESP 7000 Titulos Publicos CAJA VALORES 135.00 183.250 24,738.75 0.10¦qty=135.00¦px=183.250
DOLARES MERC. VALORES Cuenta Corriente 11,610.64 183.250 2,127,649.78 8.37¦qty=11,610.64¦px=183.250
POSICION AL 31/12/23
Especie Detalle Custodia Cantidad Precio Importe en Pesos % de Cartera Importe en Dolares % de Cartera
PESOS Cuenta Corriente 92,418.28 1.000 92,418.28 0.28¦qty=92,418.28¦px=1.000
DOLARES USA ESP 7000 Titulos Publicos CAJA VALORES 184.65 808.450 149,280.29 0.05¦qty=184.65¦px=808.450
DOLARES MERC. VALORES Cuenta Corriente 11,880.71 808.450 9,605,469.99 0.85¦qty=11,880.71¦px=808.450
DETALLE DE MOVIMIENTOS
Fecha Liq. Fecha Conc. Comprobante Numero Especie Cantidad Precio Importe en Pesos Importe USD Importe DOLAR EXT.
SALDO INICIAL 75.45- 11,610.64¦ars=75.45-¦usd=11,610.64
03/01/23 03/01/23 COBR 257 122,340.47¦ars=122,340.47
03/01/23 03/01/23 DIV 1034 PBR CEDEAR PETROLEO BRASILEIRO S.A. 74.51-¦ars=74.51-
03/01/23 03/01/23 NCCD 814 135.00¦usd=135.00
Saldo al 03/01/23 122,190.51 11,745.64¦ars=122,190.51¦usd=11,745.64
06/01/23 04/01/23 CPRA 3639 BMA BANCO MACRO 53.00 559.720 29,665.17-¦qty=53.00¦px=559.720¦ars=29,665.17-
09/01/23 09/01/23 RTA 3690 AL30 BONO REP. ARGENTINA USD STEP UP 258.07¦usd=258.07
GD30 BONOS REP- ARG U$S STEP UP V
16/03/23 16/03/23 CPU$ 76155 39,864.00 29.149 123.00-¦qty=39,864.00¦px=29.149¦usd=123.00-
Saldo al 16/03/23 92,525.34 11,880.71¦ars=92,525.34¦usd=11,880.71
31/05/23 31/05/23 RTA 27532 BMA BANCO MACRO 28.00¦ars=28.00
Saldo al 31/05/23 92,553.35 11,880.71¦ars=92,553.35¦usd=11,880.71
MOVIMIENTOS POR ESPECIE
Fecha Liq. Fecha Conc. Comprobante Numero Detalle Custodia Corresponsal Cantidad Precio Saldo
BMA BANCO MACRO
06/01/23 04/01/23 CPRA 3639 CAJA VALORES 53.00 559.720 53.00¦qty=53.00¦px=559.720¦saldo=53.00
IRSA IRSA "B"
12/10/23 12/10/23 DIV 67768 EFECT 633 TENENCIA CAJA VALORES 74.00 74.00¦qty=74.00¦saldo=74.00
DOLARUSA DOLARES USA ESP 7000
03/01/23 03/01/23 EGAJ 251 CAJA VALORES 135.00- 184.750¦qty=135.00-¦px=184.750
03/01/23 03/01/23 DIV 1034 EFECT 8526 TENENCIA CAJA VALORES 49.65 49.65¦qty=49.65¦saldo=49.65
12/01/23 12/01/23 DIV 5972 EFECT 8431 TENENCIA CAJA VALORES 135.00 184.65¦qty=135.00¦saldo=184.65
DETALLE DE COMISIONES
Fecha Comprobante Numero Especie Moneda Importe Arancel Derechos Iva Perc.Iva Otros Total Gastos
03/01/23 DIV 1034 PBR 61.58 12.93 74.51¦total=74.51
04/01/23 CPRA 3639 BMA 29,282.50 292.83 23.43 66.41 382.67¦total=382.67
10/07/23 RTA 37998 AL30 104.10 104.10¦total=135.07
"""


def test_homebroker_resumen_parses_and_reconciles(accounts):
    batch = ImportBatch.objects.create(
        account=accounts["cash"],
        schema_broker="homebroker",
        schema_document_kind="resumen",
        schema_format_kind="pdf",
        schema_version="2018.1",
        file_name="2023 RESUMEN_300718.PDF",
    )
    process_batch(batch, RESUMEN_TEXT)
    batch.refresh_from_db()

    balances = batch.metadata["balances"]
    assert balances["ars_open"] == "-75.45"
    assert balances["ars_close"] == "92418.28"
    assert balances["usd_open"] == "11610.64"
    assert balances["usd_close"] == "11880.71"
    assert balances["ext_open"] == "135.00"
    assert balances["ext_close"] == "184.65"

    assert not batch.lines.filter(kind__startswith="broker_", matched_legs__isnull=True).exists()

    ars = ensure_currency("ARS")
    usd = ensure_currency("USD")
    ext = ensure_currency("DOLARUSA")
    # Every pool moves by exactly close − open, including the platform's
    # own cent drift (an ajuste line from the Saldo evidence) and the
    # unbooked RTA gasto emitted from the commissions table.
    assert Holding.current(accounts["cash"], ars) == D("92418.28") - D("-75.45")
    assert Holding.current(accounts["cash"], usd) == D("11880.71") - D("11610.64")
    assert Holding.current(accounts["cash"], ext) == D("184.65") - D("135.00")
    assert batch.lines.filter(kind="broker_ajuste").count() == 1
    assert batch.lines.filter(kind="broker_gasto", raw_data__numero="37998").exists()

    # The all-in importe splits into principal + commission by Numero.
    bma = Instrument.objects.get(code="BMA")
    assert Holding.current(accounts["holdings"], bma) == D("53")
    buy_line = batch.lines.get(raw_data__numero="3639", kind__startswith="broker_cpra")
    assert buy_line.raw_data["commission"] == "382.67"

    # Share dividend arrives from MOVIMIENTOS POR ESPECIE.
    irsa = Instrument.objects.get(code="IRSA")
    assert Holding.current(accounts["holdings"], irsa) == D("74")

    # Bond bought with dollars (paridad).
    gd30 = Instrument.objects.get(code="GD30")
    assert Holding.current(accounts["holdings"], gd30) == D("39864")
