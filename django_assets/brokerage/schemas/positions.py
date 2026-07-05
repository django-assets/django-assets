"""Statement position records — the ADR-0036 detection substrate.

`ImportSchema.parse_positions` returns the document's closing holdings
as a list of these records. A record carries every identity the
document offers (ticker, CUSIP, option descriptor) so the checkpoint
can match ledger instruments by ANY known identifier before flagging
anything — an identifier-blind diff would flag the very renames it
exists to find.

Quantities are signed (shorts negative) and serialized as strings.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class StatementPosition:
    quantity: Decimal
    ticker: str = ""
    cusip: str = ""
    description: str = ""
    #: canonical option code ("CCJ 02/17/2023 28 C") when derivable
    option_code: str = ""

    def identities(self) -> "list[tuple[str, str]]":
        """(identifier_type, value) pairs, strongest first."""
        out: list[tuple[str, str]] = []
        if self.option_code:
            out.append(("ticker", self.option_code))
        if self.cusip:
            out.append(("cusip", self.cusip))
        if self.ticker:
            out.append(("ticker", self.ticker))
        return out

    def label(self) -> str:
        return self.option_code or self.ticker or self.cusip or self.description[:24]


@dataclass
class Checkpoint:
    """One statement's closing holdings, ready to diff."""

    as_of: datetime.date
    source_reference: str
    positions: "list[StatementPosition]" = field(default_factory=list)


def option_canonical_code(
    underlying: str, expiry: datetime.date, strike: Decimal, right: str
) -> str:
    """Must match ensure_option's code format exactly (schemas/instruments)."""
    return f"{underlying} {expiry:%m/%d/%Y} {strike.normalize():f} {right}"
