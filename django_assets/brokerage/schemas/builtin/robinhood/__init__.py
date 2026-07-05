"""Robinhood built-in schemas (append-only; new formats = new versions)."""

from django_assets.brokerage.schemas.builtin.robinhood.activity_csv_2020 import (
    RobinhoodActivityCsv2020,
)

__all__ = ["RobinhoodActivityCsv2020"]
