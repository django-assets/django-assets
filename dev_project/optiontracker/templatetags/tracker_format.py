"""Presentation-only formatting filters (ALLOWED arithmetic zone).

This module is the app's single sanctioned place for display transforms:
ratio->percent (x100), money strings, sign classes, day counts, slug->
label mapping. Nothing here COMPUTES a domain number — every value in
is a finished figure from django_assets.trades.reports; out comes a
string or a CSS class. scripts/check_app_thinness.py allowlists this
file for exactly that reason.
"""

import datetime
from decimal import Decimal

from django import template

register = template.Library()

EM_DASH = "—"

#: Strategy slug -> reference display label (pure presentation mapping).
STRATEGY_LABELS = {
    "bull_put_spread": "Put Credit Spread",
    "bear_call_spread": "Call Credit Spread",
    "short_put": "Cash Secured Put",
    "covered_call": "Covered Call",
    "iron_condor": "Iron Condor",
    "long_call": "Long Call",
    "long_put": "Long Put",
    "bear_put_spread": "Put Debit Spread",
    "bull_call_spread": "Call Debit Spread",
    "short_strangle": "Short strangle",
    "long_straddle": "Long Straddle",
    "short_straddle": "Short Straddle",
    "short_call": "Covered Call",
    "stock": "Stock",
    "mixed": "Mixed",
}

RIGHT_LABELS = {"C": "Call", "P": "Put"}

#: Wheel-campaign option right -> reference Type label: inside a wheel a put is
#: a cash-secured put and a call is a covered call (pure label mapping).
WHEEL_TYPE_LABELS = {"C": "Covered Call", "P": "Cash Secured Put"}

#: Leg side -> reference transaction ACTION label (pure label mapping):
#: a short leg was sold to open, a long leg was bought.
ACTION_LABELS = {"short": "Sell", "long": "Buy"}

#: Debit structures label their opening cash "Cost Basis" (reference behavior).
DEBIT_STRATEGIES = {"long_call", "long_put", "bear_put_spread", "bull_call_spread"}


@register.filter
def money(value: object) -> str:
    """$1,234.56 / -$125.50; em-dash for None."""
    if value is None:
        return EM_DASH
    amount = Decimal(str(value))
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}"


@register.filter
def money_signed(value: object) -> str:
    """Like money but positive values carry an explicit +."""
    if value is None:
        return EM_DASH
    amount = Decimal(str(value))
    if amount > 0:
        return f"+${amount:,.2f}"
    return money(amount)


@register.filter
def money0(value: object) -> str:
    """Whole-dollar money for chart node labels: $6,500; em-dash for None."""
    if value is None:
        return EM_DASH
    amount = Decimal(str(value))
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.0f}"


@register.filter
def money0_abs(value: object) -> str:
    """Unsigned whole-dollar magnitude for flow node labels: $5,288."""
    if value is None:
        return EM_DASH
    return f"${abs(Decimal(str(value))):,.0f}"


@register.filter
def money_abs(value: object) -> str:
    """Magnitude as money — for debit 'Cost Basis' display."""
    if value is None:
        return EM_DASH
    return f"${abs(Decimal(str(value))):,.2f}"


@register.filter
def strike(value: object) -> str:
    """Strike display ONLY: $6200.00 — currency with NO thousand
    separators (reference renders strikes ungrouped)."""
    if value is None:
        return EM_DASH
    return f"${Decimal(str(value)):.2f}"


@register.filter
def strike_plain(value: object) -> str:
    """Bare strike for the share-card terms line: no $ sign, trailing
    zeros stripped — 305.00 -> '305', 292.50 -> '292.5' (reference
    renders '190/185')."""
    if value is None:
        return EM_DASH
    text = f"{Decimal(str(value)):f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


@register.filter
def plain_number(value: object) -> str:
    """1,234.56 without a currency sign (history strike column)."""
    if value is None:
        return EM_DASH
    return f"{Decimal(str(value)):,.2f}"


@register.filter
def pct0(value: object) -> str:
    """Fraction -> whole percent: Decimal('0.19') -> '19%'."""
    if value is None:
        return EM_DASH
    scaled = (Decimal(str(value)) * 100).quantize(Decimal("1"))
    return f"{scaled}%"


@register.filter
def pct1(value: object) -> str:
    """Fraction -> one-decimal percent: 0.342 -> '34.2%'."""
    if value is None:
        return EM_DASH
    scaled = (Decimal(str(value)) * 100).quantize(Decimal("0.1"))
    return f"{scaled}%"


@register.filter
def pct1_abs(value: object) -> str:
    """Unsigned one-decimal percent (summary arrow renders the sign)."""
    if value is None:
        return EM_DASH
    scaled = (abs(Decimal(str(value))) * 100).quantize(Decimal("0.1"))
    return f"{scaled}%"


@register.filter
def pct2(value: object) -> str:
    """Fraction -> two-decimal percent (IV column): 0.5234 -> '52.34%'."""
    if value is None:
        return EM_DASH
    scaled = (Decimal(str(value)) * 100).quantize(Decimal("0.01"))
    return f"{scaled}%"


@register.filter
def pct2_signed(value: object) -> str:
    """Two-decimal percent with explicit + (wheel PnL column)."""
    if value is None:
        return EM_DASH
    scaled = (Decimal(str(value)) * 100).quantize(Decimal("0.01"))
    return f"+{scaled}%" if scaled > 0 else f"{scaled}%"


@register.filter
def greek(value: object) -> str:
    """Four-decimal greek display: -0.3422; em-dash for None."""
    if value is None:
        return EM_DASH
    return f"{Decimal(str(value)):.4f}"


@register.filter
def sign_class(value: object) -> str:
    """CSS class for a signed figure: pos / neg / '' (None or zero)."""
    if value is None:
        return ""
    amount = Decimal(str(value))
    if amount > 0:
        return "pos"
    if amount < 0:
        return "neg"
    return ""


@register.filter
def inverse_sign_class(value: object) -> str:
    """CSS class for a figure where NEGATIVE is good (wheel adjusted-cost
    discount: a cheaper basis is green): neg -> pos, pos -> neg."""
    if value is None:
        return ""
    amount = Decimal(str(value))
    if amount < 0:
        return "pos"
    if amount > 0:
        return "neg"
    return ""


@register.filter
def days_until(value: datetime.date | None) -> int | str:
    """Calendar days from today to a date (expiration '(15d)' badges)."""
    if value is None:
        return EM_DASH
    return (value - datetime.date.today()).days


@register.filter
def strategy_label(slug: str | None) -> str:
    if slug is None:
        return "Mixed"
    return STRATEGY_LABELS.get(slug, slug.replace("_", " ").title())


@register.filter
def right_label(right: str | None) -> str:
    if right is None:
        return EM_DASH
    return RIGHT_LABELS.get(right, right)


@register.filter
def wheel_type_label(right: str | None) -> str:
    if right is None:
        return EM_DASH
    return WHEEL_TYPE_LABELS.get(right, right)


@register.filter
def side_label(side: str | None) -> str:
    if side is None:
        return EM_DASH
    return side.capitalize()


@register.filter
def action_label(side: str | None) -> str:
    if side is None:
        return EM_DASH
    return ACTION_LABELS.get(side, side.capitalize())


@register.filter
def is_debit(slug: str | None) -> bool:
    return slug in DEBIT_STRATEGIES


@register.filter
def dash_if_none(value: object) -> object:
    return EM_DASH if value is None else value
