"""parse_positions snippet tests per statement format (ADR-0036) —
fabricated holdings blocks in each format's real shape."""

from decimal import Decimal

import pytest

from django_assets.brokerage.schemas import registry

pytestmark = pytest.mark.ledger

D = Decimal


def by_label(positions):
    return {p.label(): p.quantity for p in positions}


def test_schwab_positions_including_short_options():
    schema = registry.get("schwab", "statement", "pdf", "2024.1")
    text = (
        "Positions - Exchange Traded Funds\n"
        "Symbol Description Quantity Price($) Market Value($)\n"
        "ZVOL VOLATILITYPREMIUMPLUS, 200.0000 20.74000 4,148.00 4,141.00 7.00 28.06%\n"
        "TotalExchangeTradedFunds $4,148.00\n"
        "Positions - Options\n"
        "MSTR CALLMICROSTRATEGYINC, (1.0000)S 132.54920 (13,254.92)\n"
        "01/16/20 $800 EXP01/16/26\n"
        "26\n"
        "800.00C\n"
        "TotalOptions $0.00\n"
    )
    positions = by_label(schema.parse_positions(text))
    assert positions == {"ZVOL": D("200.0000"), "MSTR 01/16/2026 800 C": D("-1.0000")}


def test_tda_retail_positions_with_short_option():
    schema = registry.get("tdameritrade", "statement", "pdf", "2012.1")
    text = (
        "Account Positions\n"
        "Stocks - Margin\n"
        "CAMECO CORP CCJ 300 28.01 8,403.00 10/21/22 7,713.98 25.71 689.02 26.47 0.3%\n"
        "Short Options - Margin\n"
        "CAMECO CORP - 3- $ 1.2625 $(378.75) 01/10/23 $ (194.02)\n"
        "CCJ Feb 17 23 28.0 C\n"
        "Account Activity\n"
    )
    positions = by_label(schema.parse_positions(text))
    assert positions == {"CCJ": D("300"), "CCJ 02/17/2023 28 C": D("-3")}


def test_advisor_positions_with_uppercase_month_option():
    schema = registry.get("tdameritrade", "advisor-statement", "pdf", "2023.1")
    text = (
        "HOLDINGS DETAIL\n"
        "EXCHANGE TRADED FUNDS(ETFs)\n"
        "Investment Description CUSIP Quantity Price Market Value\n"
        "VS TRUST SVIX 500 17.8999 8,949.95\n"
        "OPTIONS\n"
        "PROSHARES TRUST II - 25 NA NA\n"
        "UVXY Apr 28 23 6.5 C\n"
        "TOTAL HOLDINGS $8,949.95\n"
    )
    positions = by_label(schema.parse_positions(text))
    assert positions == {"SVIX": D("500"), "UVXY 04/28/2023 6.5 C": D("25")}


def test_apex_positions():
    schema = registry.get("tradier", "statement", "pdf", "2022.1")
    text = (
        "EQUITIES / OPTIONS\n"
        "ISHARES TRUST SGOV M 49 $100.56 $4,927.44 N/A $120 98.419%\n"
        "Total Equities $4,927.44\n"
    )
    positions = by_label(schema.parse_positions(text))
    assert positions == {"SGOV": D("49")}


def test_robinhood_positions_with_short_option_and_cusip():
    schema = registry.get("robinhood", "statement", "pdf", "2020.1")
    text = (
        "Securities Held in Account Sym/Cusip Acct Type Qty Price Mkt Value\n"
        "Pershing Square Tontine Holdings, Ltd.\n"
        "PSTH Margin 200 $29.35 $5,870.00 $0.00 68.84%\n"
        "Estimated Yield: 0.00%\n"
        "PSTH 03/19/2021 Call $35.00\n"
        "PSTH Margin 1S $1.80 ($180.00) $0.00 2.11%\n"
        "Central Puerto Contra CUSIP\n"
        "155CNT017 Cash 378 $0.00 $0.00 0.00%\n"
        "Total Securities $5,690.00\n"
    )
    positions = by_label(schema.parse_positions(text))
    assert positions == {
        "PSTH": D("200"),
        "PSTH 03/19/2021 35 C": D("-1"),
        "155CNT017": D("378"),
    }


def test_homebroker_closing_posicion_only():
    schema = registry.get("homebroker", "resumen", "pdf", "2018.1")
    text = (
        "Desde Fecha: 01/01/23 Hasta Fecha: 31/12/23 Hoja: 1\n"
        "POSICION AL 01/01/23\n"
        "BMA BANCO MACRO CAJA VALORES 66.00 561.100 37,032.60¦qty=66.00¦px=561.100\n"
        "PESOS Cuenta Corriente 75.45- 1.000¦qty=75.45-\n"
        "POSICION AL 31/12/23\n"
        "BMA BANCO MACRO CAJA VALORES 121.00 920.320 111,358.72¦qty=121.00¦px=920.320\n"
        "AL30 BONO REP. ARGENTINA USD STEP UP 2030 Titulos Publicos CAJA VALORES "
        "152,516.00 10371.849¦qty=152,516.00¦px=10371.849\n"
        "DOLARES MERC. VALORES Cuenta Corriente 11,880.71¦qty=11,880.71\n"
        "TOTAL POSICION AL 31/12/23 126,291,709.99\n"
    )
    positions = by_label(schema.parse_positions(text))
    # only the CLOSING snapshot, securities only (cash rows excluded)
    assert positions == {"BMA": D("121.00"), "AL30": D("152516.00")}
