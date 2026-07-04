"""TransactionBuilder: the one supported write path (core spec §4.1).

Builds one balanced Transaction atomically. Amounts pass the runtime
intake guard (PADR-0006 Rule 3), are quantized strictly per instrument
[D-5], and every leg account must share the transaction account's owner
[D-3]. With DJANGO_ASSETS_USE_DB_TRIGGERS=False the builder is the
integrity gate and raises UnbalancedTransactionError before COMMIT; with
triggers on, an unbalanced build surfaces as IntegrityError at COMMIT.
"""

import datetime
from collections import defaultdict
from decimal import Decimal
from types import TracebackType
from typing import Any, Self

from django.db import transaction as db_transaction

from django_assets import conf
from django_assets.core.exceptions import MixedOwnershipError, UnbalancedTransactionError
from django_assets.core.intake import to_decimal
from django_assets.core.models import Account, Instrument, Transaction, TransactionLeg


class TransactionBuilder:
    """Context manager; the Transaction persists when the block exits cleanly.

    with TransactionBuilder(account=acct, timestamp=ts) as b:
        b.add_leg(account=cash, instrument=usd, amount="-17550.56")
        b.add_leg(account=holdings, instrument=aapl, amount=100)
    tx = b.transaction
    """

    def __init__(
        self,
        *,
        account: Account,
        timestamp: datetime.datetime,
        trade_timestamp: datetime.datetime | None = None,
        description: str = "",
        metadata: dict[str, Any] | None = None,
        origin: str = "manual",
        using: str = "default",
    ) -> None:
        self.account = account
        self.timestamp = timestamp
        self.trade_timestamp = trade_timestamp
        self.description = description
        self.metadata = metadata if metadata is not None else {}
        self.origin = origin
        self.using = using
        self.transaction: Transaction | None = None
        self._legs: list[TransactionLeg] = []

    def add_leg(
        self,
        *,
        account: Account,
        instrument: Instrument,
        amount: Decimal | int | str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        quantized = instrument.quantize(to_decimal(amount), strict=True)
        if account.owner_id != self.account.owner_id:
            raise MixedOwnershipError(
                f"leg account {account.name!r} belongs to a different owner than "
                f"transaction account {self.account.name!r} — every leg of a "
                f"transaction must stay within one owner's books (D-3)"
            )
        self._legs.append(
            TransactionLeg(
                account=account,
                instrument=instrument,
                amount=quantized,
                description=description,
                metadata=metadata if metadata is not None else {},
            )
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            return  # propagate; nothing was written
        if not conf.use_db_triggers():
            self._assert_balanced()
        with db_transaction.atomic(using=self.using):
            tx = Transaction.objects.using(self.using).create(
                account=self.account,
                timestamp=self.timestamp,
                trade_timestamp=self.trade_timestamp,
                description=self.description,
                metadata=self.metadata,
                origin=self.origin,
            )
            for leg in self._legs:
                leg.transaction = tx
            TransactionLeg.objects.using(self.using).bulk_create(self._legs)
        self.transaction = tx

    def _assert_balanced(self) -> None:
        sums: defaultdict[Instrument, Decimal] = defaultdict(Decimal)
        for leg in self._legs:
            sums[leg.instrument] += leg.amount
        off = {inst.code: total for inst, total in sums.items() if total != 0}
        if off:
            raise UnbalancedTransactionError(
                f"transaction legs are not balanced per instrument: {off} "
                f"(DJANGO_ASSETS_USE_DB_TRIGGERS=False, Python check)"
            )
