#!/usr/bin/env python
"""Import every private brokerage CSV under ./artifacts and verify the
pipeline end-to-end (goal harness — artifacts are git-excluded and this
runner never copies their contents anywhere).

Per account folder: one user + the standard account set; files import
oldest-first; overlapping periods (e.g. a monthly export inside an
already-imported year) are skipped via the period-discipline helper.
Acceptance per file: every data row lands (parsed == materialized, no
review-queue stalls), and the cash account moves by EXACTLY the sum of
the file's Amount column — the broker's own net-cash ground truth.

Usage: uv run python scripts/import_artifacts.py [--artifacts DIR]
"""

import csv
import datetime
import io
import os
import re
import sys
from decimal import Decimal
from pathlib import Path


def bootstrap() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dev_project.settings.dev")
    import django

    django.setup()


BROKERS = {
    "Robinhood": {
        "schema": ("robinhood", "activity", "csv", "2020.1"),
        "amount_column": 8,
        "min_columns": 9,
    },
    "Schwab": {
        "schema": ("schwab", "transactions", "csv", "2024.1"),
        "amount_column": 7,
        "min_columns": 8,
    },
}
DATE_ROW = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}")
FILE_PERIOD = re.compile(r"^(?P<year>\d{4})(?:-(?P<month>\d{2}))?$")


def file_period(path: Path) -> "tuple[datetime.date, datetime.date] | None":
    match = FILE_PERIOD.match(path.stem)
    if match is None:
        return None
    year = int(match["year"])
    if match["month"]:
        month = int(match["month"])
        last = datetime.date(year + (month == 12), (month % 12) + 1, 1) - datetime.timedelta(days=1)
        return datetime.date(year, month, 1), last
    return datetime.date(year, 1, 1), datetime.date(year, 12, 31)


def expected_cash_delta(text: str, config: dict) -> Decimal:
    from django_assets.brokerage.schemas.instruments import parse_money

    total = Decimal(0)
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= config["min_columns"] and DATE_ROW.match(row[0] or ""):
            if config["min_columns"] == 9 and not row[5]:
                continue  # Robinhood rows without a trans code are noise
            total += parse_money(row[config["amount_column"]])
    return total


def import_account_folder(broker: str, folder: Path, config: dict) -> "list[dict]":
    from django.contrib.auth import get_user_model

    from django_assets.brokerage.accounts import ensure_standard_accounts
    from django_assets.brokerage.dedup import is_period_imported
    from django_assets.brokerage.imports import process_batch
    from django_assets.brokerage.models import AccountProfile, ImportBatch
    from django_assets.brokerage.schemas.instruments import ensure_currency
    from django_assets.core.queries import Holding

    slug = re.sub(r"[^a-z0-9]+", "-", f"{broker}-{folder.name}".lower()).strip("-")
    user, _ = get_user_model().objects.get_or_create(username=slug)
    accounts = ensure_standard_accounts(user)
    for key in ("cash", "holdings"):
        AccountProfile.objects.get_or_create(
            account=accounts[key], defaults={"allows_reconciliation": True}
        )
    usd = ensure_currency("USD")
    broker_key, document_kind, format_kind, version = config["schema"]

    results = []
    files = sorted(
        [p for p in folder.iterdir() if p.suffix.lower() == ".csv"],
        key=lambda p: p.stem,
    )
    for path in files:
        period = file_period(path)
        if period and is_period_imported(accounts["cash"], broker_key, document_kind, *period):
            results.append(
                {"file": f"{folder.name}/{path.name}", "status": "SKIP (period covered)"}
            )
            continue
        text = path.read_text(encoding="utf-8-sig")
        expected = expected_cash_delta(text, config)
        before = Holding.current(accounts["cash"], usd)
        batch = ImportBatch.objects.create(
            account=accounts["cash"],
            schema_broker=broker_key,
            schema_document_kind=document_kind,
            schema_format_kind=format_kind,
            schema_version=version,
            period_start=period[0] if period else None,
            period_end=period[1] if period else None,
            file_name=path.name,
        )
        process_batch(batch, text)
        after = Holding.current(accounts["cash"], usd)
        stalled = batch.lines.filter(kind__startswith="broker_", matched_legs__isnull=True).count()
        delta = after - before
        ok = delta == expected and stalled == 0
        results.append(
            {
                "file": f"{folder.name}/{path.name}",
                "status": "OK" if ok else "FAIL",
                "lines": batch.lines.count(),
                "transactions": batch.transaction_count,
                "cash_delta": str(delta),
                "expected": str(expected),
                "stalled_lines": stalled,
            }
        )
    return results, accounts


def verify_downstream(folder_name: str, accounts: dict) -> dict:
    """Exercise the entire flow over the imported history: the lots
    rebuild (conservation triggers fire at COMMIT), the 1099 report,
    and open-lot reconciliation against Portfolio.at."""
    from django_assets.core.queries import Portfolio
    from django_assets.lots.models import Lot, LotMatch
    from django_assets.lots.queries import open_lots
    from django_assets.lots.rebuild import rebuild_lots
    from django_assets.lots.reports import realized_gains

    holdings = accounts["holdings"]
    rebuild_lots(holdings)  # trigger-checked
    rows = realized_gains(holdings)
    positions = Portfolio.at(holdings)
    mismatches = []
    for instrument, quantity in positions.items():
        lot_total = sum(
            lot.quantity_remaining * (1 if lot.direction == "long" else -1)
            for lot in open_lots(holdings, instrument)
        )
        if lot_total != quantity:
            mismatches.append(f"{instrument.code}: lots {lot_total} vs ledger {quantity}")
    return {
        "account": folder_name,
        "lots": Lot.objects.filter(account=holdings).count(),
        "matches": LotMatch.objects.filter(lot__account=holdings).count(),
        "realized_rows": len(rows),
        "open_positions": len(positions),
        "lot_vs_ledger_mismatches": mismatches,
    }


def tradier_statement_files(root):
    """Every Tradier monthly statement, chronologically. 1099 tax forms
    are excluded by design (tax documents, not transactions)."""
    base = root / "Tradier"
    if not base.exists():
        return []
    stamped = []
    for path in base.rglob("*.pdf"):
        if "1099" in path.name.upper():
            continue
        stamp = _statement_month(path)
        if stamp:
            stamped.append((stamp, path))
    stamped.sort()
    return [path for _stamp, path in stamped]


def _statement_month(path):
    name = path.stem
    # "Doc_-991_STATEMENT_6YA22794_2024_02_29_…" — full date, most specific.
    match = re.search(r"_(\d{4})_(\d{2})_\d{2}_", name)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    # "2022-02" / "2022-04,05,06" — leading year-month.
    match = re.match(r"^(\d{4})-(\d{2})", name)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    # "STATEMENT-3_28_2024".
    match = re.search(r"STATEMENT[-_](\d{1,2})_\d{1,2}_(\d{4})", name)
    if match:
        return f"{match.group(2)}-{int(match.group(1)):02d}"
    return ""


def import_tradier(root):
    from django.contrib.auth import get_user_model

    from django_assets.brokerage.accounts import ensure_standard_accounts
    from django_assets.brokerage.imports import process_batch
    from django_assets.brokerage.models import AccountProfile, ImportBatch
    from django_assets.brokerage.schemas.instruments import ensure_currency
    from django_assets.core.queries import Holding

    files = tradier_statement_files(root)
    if not files:
        return [], None
    user, _ = get_user_model().objects.get_or_create(username="tradier-6ya22794")
    accounts = ensure_standard_accounts(user)
    for key in ("cash", "holdings"):
        AccountProfile.objects.get_or_create(
            account=accounts[key], defaults={"allows_reconciliation": True}
        )
    usd = ensure_currency("USD")

    results = []
    for path in files:
        batch = ImportBatch.objects.create(
            account=accounts["cash"],
            schema_broker="tradier",
            schema_document_kind="statement",
            schema_format_kind="pdf",
            schema_version="2022.1",
            file_name=path.name,
        )
        before = Holding.current(accounts["cash"], usd)
        process_batch(batch, path.read_bytes())
        after = Holding.current(accounts["cash"], usd)
        opening = closing = None
        line = batch.lines.first()
        if line is not None:
            balances = line.raw_data.get("balances", {})
            opening = Decimal(balances["opening"]) if balances.get("opening") else None
            closing = Decimal(balances["closing"]) if balances.get("closing") else None
        stalled = batch.lines.filter(kind__startswith="broker_", matched_legs__isnull=True).count()
        # The file-level truth: this statement's activity must move cash
        # by exactly closing − opening. Continuity breaks (a missing
        # month in the corpus) are surfaced as GAP, never papered over.
        if closing is not None and opening is not None:
            ok = stalled == 0 and (after - before) == (closing - opening)
        else:
            ok = stalled == 0
        gap = opening is not None and before != opening
        status = "FAIL" if not ok else ("GAP (missing prior stmt)" if gap else "OK")
        results.append(
            {
                "file": f"Tradier/{_statement_month(path)} {path.name[:34]}",
                "status": status,
                "lines": batch.lines.count(),
                "transactions": batch.transaction_count,
                "cash_delta": str(after - before),
                "expected": str(closing - opening)
                if closing is not None and opening is not None
                else "(no stmt balances)",
                "stalled_lines": stalled,
            }
        )
    return results, accounts


def reset() -> None:
    """Purge every artifact-runner user (cascades accounts, batches,
    transactions, lots — the whole graph) for a clean acceptance run."""
    from django.contrib.auth import get_user_model

    from django_assets.brokerage.models import ImportLine

    users = get_user_model().objects.filter(
        username__regex=r"^(robinhood|schwab|td-ameritrade|tradier)-"
    )
    for user in users:
        for line in ImportLine.objects.filter(batch__account__owner=user):
            line.matched_legs.clear()  # unflip: the leg lock guards deletes
        user.delete()
    print(f"reset: removed {users.count() or 'all prior'} artifact users")


def main() -> int:
    bootstrap()
    if "--reset" in sys.argv:
        reset()
    root = Path(
        sys.argv[sys.argv.index("--artifacts") + 1] if "--artifacts" in sys.argv else "artifacts"
    )
    if not root.exists():
        print("no artifacts directory; nothing to do")
        return 0

    all_results = []
    downstream = []
    tradier_results, tradier_accounts = import_tradier(root)
    all_results.extend(tradier_results)
    if tradier_accounts:
        downstream.append(verify_downstream("Tradier/6YA22794", tradier_accounts))
    for broker, config in BROKERS.items():
        base = root / broker / broker
        if not base.exists():
            continue
        for folder in sorted(p for p in base.iterdir() if p.is_dir()):
            if not any(p.suffix.lower() == ".csv" for p in folder.iterdir()):
                continue
            folder_results, accounts = import_account_folder(broker, folder, config)
            all_results.extend(folder_results)
            summary = verify_downstream(f"{broker}/{folder.name}", accounts)
            downstream.append(summary)

    failures = 0
    for result in all_results:
        line = f"{result['status']:22s} {result['file']}"
        if "lines" in result:
            line += (
                f"  lines={result['lines']} tx={result['transactions']} "
                f"cash={result['cash_delta']} expected={result['expected']}"
            )
        print(line)
        if result["status"] == "FAIL":
            failures += 1
    print("\n=== downstream (lots/portfolio) ===")
    for summary in downstream:
        state = "OK" if not summary["lot_vs_ledger_mismatches"] else "FAIL"
        print(
            f"{state:5s} {summary['account']}: lots={summary['lots']} "
            f"matches={summary['matches']} 1099-rows={summary['realized_rows']} "
            f"open={summary['open_positions']}"
        )
        for mismatch in summary["lot_vs_ledger_mismatches"]:
            print(f"      MISMATCH {mismatch}")
            failures += 1
    print(f"\n{len(all_results)} files, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
