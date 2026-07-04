"""TransactionBuilder: the one supported write path (core spec §4.1).

Builds one balanced Transaction atomically. Amounts pass the runtime
intake guard (PADR-0006 Rule 3), are quantized strictly per instrument
[D-5], and every leg account must share the transaction account's owner
[D-3]. With DJANGO_ASSETS_USE_DB_TRIGGERS=False the builder is the
integrity gate and raises UnbalancedTransactionError before COMMIT; with
triggers on, an unbalanced build surfaces as IntegrityError at COMMIT.
"""

import datetime
import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import islice
from types import TracebackType
from typing import Any, Literal, Self

from django.db import transaction as db_transaction

from django_assets import conf
from django_assets.core.exceptions import MixedOwnershipError, UnbalancedTransactionError
from django_assets.core.intake import to_decimal
from django_assets.core.models import Account, Instrument, Transaction, TransactionLeg

logger = logging.getLogger("django_assets.core.builder")


@dataclass(frozen=True)
class BulkImportError:
    """Per-row error record inside BulkImportResult (spec §11) — a value,
    not an exception; the raising path re-raises the original error."""

    index: int
    message: str


@dataclass(frozen=True)
class BulkImportResult:
    inserted: int = 0
    failed: int = 0
    errors: list[BulkImportError] = field(default_factory=list)


def _invalidate_cachalot(using: str) -> None:
    """One cachalot invalidation per batch (ADR-0019); no-op when absent."""
    try:
        from cachalot.api import invalidate  # type: ignore[import-not-found]
    except ImportError:
        return
    invalidate(Transaction, TransactionLeg, db_alias=using)


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

    # -- Bulk APIs (spec §4.2/4.3, ADR-0019) --------------------------------

    @classmethod
    def _row_to_objects(cls, row: dict[str, Any]) -> tuple[Transaction, list[TransactionLeg]]:
        """Validate one TransactionDict: intake guard, strict quantization,
        same-owner invariant, and the per-instrument zero-sum — always in
        Python here so errors attribute to a row index; the deferred trigger
        still backstops every batch at COMMIT."""
        account: Account = row["account"]
        tx = Transaction(
            account=account,
            timestamp=row["timestamp"],
            trade_timestamp=row.get("trade_timestamp"),
            description=row.get("description", ""),
            metadata=row.get("metadata") or {},
            origin=row.get("origin", "manual"),
        )
        legs: list[TransactionLeg] = []
        sums: defaultdict[Instrument, Decimal] = defaultdict(Decimal)
        for leg_row in row["legs"]:
            leg_account: Account = leg_row["account"]
            instrument: Instrument = leg_row["instrument"]
            amount = instrument.quantize(to_decimal(leg_row["amount"]), strict=True)
            if leg_account.owner_id != account.owner_id:
                raise MixedOwnershipError(
                    f"leg account {leg_account.name!r} belongs to a different owner "
                    f"than transaction account {account.name!r} (D-3)"
                )
            sums[instrument] += amount
            legs.append(
                TransactionLeg(
                    account=leg_account,
                    instrument=instrument,
                    amount=amount,
                    description=leg_row.get("description", ""),
                    metadata=leg_row.get("metadata") or {},
                )
            )
        off = {inst.code: total for inst, total in sums.items() if total != 0}
        if off:
            raise UnbalancedTransactionError(
                f"transaction legs are not balanced per instrument: {off}"
            )
        return tx, legs

    @classmethod
    def bulk_import(
        cls,
        rows: Iterable[dict[str, Any]],
        *,
        batch_size: int = 1000,
        on_error: Literal["raise", "skip", "collect"] = "raise",
        using: str = "default",
    ) -> BulkImportResult:
        """Batched insertion of TransactionDicts (spec §4.2).

        One DB transaction per batch; bulk_create for both tables; one
        cachalot invalidation per batch. 'raise' stops at the first bad row
        (the in-flight batch rolls back, earlier batches persist); 'skip'
        logs and continues; 'collect' is 'skip' without the logging. Batch-
        agnostic: ImportBatch coupling is brokerage's wrapper, not core's.
        """
        inserted = 0
        errors: list[BulkImportError] = []
        rows_iter = iter(rows)
        index = 0
        while True:
            batch = list(islice(rows_iter, batch_size))
            if not batch:
                break
            prepared: list[tuple[Transaction, list[TransactionLeg]]] = []
            for row in batch:
                try:
                    prepared.append(cls._row_to_objects(row))
                except Exception as exc:
                    if on_error == "raise":
                        raise
                    if on_error == "skip":
                        logger.warning("bulk_import row %d skipped: %s", index, exc)
                    errors.append(BulkImportError(index=index, message=str(exc)))
                index += 1
            if prepared:
                with db_transaction.atomic(using=using):
                    Transaction.objects.using(using).bulk_create([tx for tx, _ in prepared])
                    all_legs = []
                    for tx, legs in prepared:
                        for leg in legs:
                            leg.transaction = tx
                        all_legs.extend(legs)
                    TransactionLeg.objects.using(using).bulk_create(all_legs)
                _invalidate_cachalot(using)
                inserted += len(prepared)
        return BulkImportResult(inserted=inserted, failed=len(errors), errors=errors)

    @classmethod
    def delete_range(
        cls,
        account: Account,
        from_: datetime.datetime,
        to_: datetime.datetime,
        *,
        confirm: bool = False,
        using: str = "default",
    ) -> int:
        """Delete `account`'s Transactions with timestamp in [from_, to_).

        Refuses without confirm=True (spec §4.3 brake). Whole-transaction
        deletion keeps the balance trigger satisfied.
        """
        if not confirm:
            raise ValueError(
                "delete_range removes ledger history irreversibly; pass confirm=True to proceed"
            )
        qs = Transaction.objects.using(using).filter(
            account=account, timestamp__gte=from_, timestamp__lt=to_
        )
        with db_transaction.atomic(using=using):
            _, per_model = qs.delete()
        _invalidate_cachalot(using)
        return per_model.get("django_assets.Transaction", 0)
