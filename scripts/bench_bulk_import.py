#!/usr/bin/env python
"""bulk_import benchmark harness (core plan C6) — not a CI gate pre-v0.3.

Inserts N two-leg transactions through TransactionBuilder.bulk_import and
prints rows/sec for regression eyeballing:

    uv run python scripts/bench_bulk_import.py [N]
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

    from django_assets.core.builder import TransactionBuilder
    from django_assets.core.models import Account, Instrument, Transaction

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000
    tag = f"bench-{os.getpid()}"
    user = get_user_model().objects.create_user(username=tag, password="x")
    usd = Instrument.objects.create(code=f"USD-{tag}", quantity_decimals=2)
    cash = Account.objects.create(owner=user, name="cash")
    external = Account.objects.create(owner=user, name="external")
    ts = datetime.datetime(2026, 3, 13, 20, 0, tzinfo=datetime.UTC)

    rows = [
        {
            "timestamp": ts,
            "account": cash,
            "legs": [
                {"account": cash, "instrument": usd, "amount": "10.00"},
                {"account": external, "instrument": usd, "amount": "-10.00"},
            ],
        }
        for _ in range(n)
    ]
    try:
        start = time.perf_counter()
        result = TransactionBuilder.bulk_import(rows, batch_size=1000)
        elapsed = time.perf_counter() - start
        print(f"{result.inserted} transactions in {elapsed:.2f}s = {n / elapsed:,.0f} rows/sec")
    finally:
        Transaction.objects.filter(account=cash).delete()
        user.delete()
    return 0


if __name__ == "__main__":
    sys.exit(main())
