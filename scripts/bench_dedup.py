#!/usr/bin/env python
"""ADR-0029 O(N×M) profiling harness — not a CI gate.

Processes a 500-row batch against a ledger with 500 manual candidates
and prints the wall time: uv run python scripts/bench_dedup.py
"""

import datetime
import os
import sys
import time

import django


def main() -> int:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dev_project.settings.dev")
    django.setup()

    from django.contrib.auth import get_user_model

    from django_assets.brokerage.accounts import ensure_standard_accounts
    from django_assets.brokerage.imports import process_batch
    from django_assets.brokerage.models import AccountProfile, ImportBatch
    from django_assets.core.builder import TransactionBuilder
    from django_assets.core.models import Identifier, Instrument

    tag = f"dedupbench-{os.getpid()}"
    user = get_user_model().objects.create_user(username=tag, password="x")
    accounts = ensure_standard_accounts(user)
    AccountProfile.objects.create(account=accounts["cash"], allows_reconciliation=True)
    AccountProfile.objects.create(account=accounts["holdings"], allows_reconciliation=True)
    usd = Instrument.objects.create(code=f"USD-{tag}", quantity_decimals=2)
    aapl = Instrument.objects.create(code=f"AAPL-{tag}", quantity_decimals=0, price_currency=usd)
    Identifier.objects.create(instrument=aapl, type="ticker", value=f"AAPL{os.getpid()}")

    base = datetime.datetime(2026, 3, 2, 20, 0, tzinfo=datetime.UTC)
    rows = [(base + datetime.timedelta(days=i % 20), 100 + i) for i in range(500)]
    for ts, amount in rows:
        with TransactionBuilder(account=accounts["cash"], timestamp=ts) as b:
            b.add_leg(account=accounts["cash"], instrument=usd, amount=f"-{amount}.00")
            b.add_leg(account=accounts["external"], instrument=usd, amount=f"{amount}.00")

    header = '"Date","Action","Symbol","Description","Quantity","Price","Fees & Comm","Amount"'
    lines = [
        f'"03/{(i % 20) + 2:02d}/2026","Buy","AAPL{os.getpid()}","X","1","{100 + i}.00","0.00","-{100 + i}.00"'
        for i in range(500)
    ]
    batch = ImportBatch.objects.create(
        account=accounts["cash"],
        schema_broker="schwab",
        schema_document_kind="trades",
        schema_format_kind="csv",
        schema_version="2026.1",
    )
    try:
        start = time.perf_counter()
        process_batch(batch, "\n".join([header, *lines, ""]))
        elapsed = time.perf_counter() - start
        print(f"500 lines x 500 candidates: {elapsed:.2f}s")
    finally:
        from django_assets.brokerage.dedup import delete_import_batch

        delete_import_batch(batch)
        user.delete()
    return 0


if __name__ == "__main__":
    sys.exit(main())
