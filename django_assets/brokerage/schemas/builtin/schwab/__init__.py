"""Schwab built-in schemas (append-only; new formats = new versions)."""

from django_assets.brokerage.schemas.builtin.schwab.trades_csv_2026 import (
    SchwabTradesCsv2026,
)

__all__ = ["SchwabTradesCsv2026"]
