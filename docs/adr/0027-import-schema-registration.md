# ADR-0027: Broker import schemas — code-only registry, four-part natural key

## Status

Proposed — 2026-06-07

## Context

ADR-0019 introduced `ImportBatch` (one upload event). ADR-0025 settled per-row storage on `ImportLine`. ADR-0026 settled the line-to-leg M2M. What none of those ADRs settled: **how do we describe a broker's parsing format, and where does that description live?**

Two failed approaches surfaced during ADR-0025 discussion and were rejected:

1. **Schema in every `ImportLine`.** Storing key/value dicts in `raw_data` duplicates the column-name schema across every row.
2. **Schema snapshot on `ImportBatch`.** Less duplication, but the schema is conceptually a property of the broker + document + format, not of a single upload event. Two batches imported from the same Fidelity Trades CSV format should not each carry their own copy of "what does this CSV look like."

The right shape is: schemas are **first-class durable definitions**, batches **reference** a schema by its natural key, and lines hold positional values that the schema interprets.

This ADR settles three sub-questions together because they're tightly coupled:

1. **Where do schema definitions live?** Code, DB, or both?
2. **How are schemas added without forking the package?**
3. **What is a schema's identity — what's the natural key?**

## Decision

**Schemas live in Python code, period.** There is no `ImportSchema` DB table, no admin-editable schema rows, no `post_migrate` sync handler. Adding a new schema means writing a Python class. Hosts can do this without forking the package by shipping their own Django app with a `schemas.py` module.

**`ImportBatch` references its schema by a four-part natural key stored as CharFields:** `(broker, document_kind, format_kind, version)`. At import or read time, the brokerage package looks up the matching `ImportSchema` Python class in an in-memory registry.

**Convention: schemas, once shipped, are immortal in code.** This is the same convention Django itself uses for app labels, migration names, and admin registrations. New broker format → new `version`. Definition changes that aren't version bumps are forbidden.

### Four-part natural key

| Field | Meaning | Examples |
| --- | --- | --- |
| `broker` | Who issued the data | `fidelity`, `schwab`, `vanguard`, `ibkr` |
| `document_kind` | What the document carries | `trades`, `dividends`, `balances`, `positions`, `transfers` |
| `format_kind` | How it's encoded | `csv`, `json`, `ofx`, `qfx`, `fixed_width` |
| `version` | Which iteration | `2026.01`, `2024.q3`, `v2` |

A single broker ships many schemas: Fidelity Trades CSV v2026.01, Fidelity Dividends CSV v2026.01, Fidelity Balances CSV v2024.q4, Fidelity Trades JSON v2026.01, and so on. Each combination is its own registered Python class with its own definition and parser.

### The four moving parts

#### 1. `ImportSchema` base class

A schema owns the full broker-specific pipeline: parsing the raw upload into `ImportLine` records, and turning each line into ledger `Transaction`s. It is the only place broker-specific logic lives.

```python
# django_assets/brokerage/schemas/base.py

class ImportSchema:
    """Base class for a broker import schema.

    Subclasses are decorated with @register_schema(...) and must implement:
      - `parse_batch(batch, source)`: bytes/stream → ImportLine instances
      - `materialize_line(line)`: ImportLine → ledger Transactions
      - `match_criteria(line)`: ImportLine → MatchCriteria, used by the
        orchestrator's dedup pass to find pre-existing manual entries
        (per ADR-0028). Pure-informational schemas may raise or return
        a sentinel; they are skipped by dedup either way.

    The class also carries a `definition` dict describing the row structure
    so consumers can interpret ImportLine.raw_data without calling Python.
    """

    broker: str          # set by decorator
    document_kind: str   # set by decorator
    format_kind: str     # set by decorator
    version: str         # set by decorator
    name: str            # display name

    definition: dict     # column structure, parse hints

    def parse_batch(self, batch, source) -> Iterator["ImportLine"]:
        """Parse the raw upload into ImportLine records.

        Each yielded ImportLine carries:
          - batch (the ImportBatch passed in)
          - line_number (1-indexed)
          - raw_data, structured per self.definition
            (positional list for tabular shapes, dict for nested shapes)
          - kind, indicating whether the row is transactional (e.g. "broker_csv")
            or informational (e.g. "balance_snapshot", "ytd_summary")

        Yielded ImportLines are not yet saved; the orchestrator bulk-creates them.
        """
        raise NotImplementedError

    def materialize_line(self, line) -> list["Transaction"]:
        """Turn one ImportLine into ledger Transactions.

        Returns:
          []                   for informational lines (balance snapshots, summaries)
          [Transaction]        for most transactional rows
          [Transaction, ...]   for rows that produce multiple events

        Implementations call brokerage templates (sell_option, dividend_received,
        adr_fee_deducted, etc.) per ADR-0021, and return the Transactions those
        templates produced. The orchestrator handles M2M wiring of asset legs to
        line.matched_legs (per ADR-0024/0025).

        Required override. Pure-informational schemas implement as `return []`.
        """
        raise NotImplementedError

    def match_criteria(self, line) -> "MatchCriteria":
        """Extract the fields used to find a matching pre-existing manual leg.

        Returns a MatchCriteria (date, instrument, amount, date_window_days)
        per ADR-0028 that the orchestrator's find_matching_legs() uses to
        scope the dedup search. Schemas override for broker-specific quirks
        (settlement-date offsets, amount unit conversion, longer match
        windows for slow-reporting brokers).

        Required for transactional schemas. Pure-informational schemas may
        leave this unimplemented — the orchestrator skips dedup for lines
        whose kind isn't matchable (per ADR-0025's broker_ prefix rule).
        """
        raise NotImplementedError
```

The two methods correspond to two persistence milestones: lines are saved before any transactions are materialized. A crash mid-batch leaves the raw evidence intact for re-processing; nothing is half-imported.

#### 2. `@register_schema` decorator + registry

```python
# django_assets/brokerage/schemas/registry.py

SchemaKey = tuple[str, str, str, str]  # (broker, document_kind, format_kind, version)


class SchemaRegistry:
    def __init__(self):
        self._schemas: dict[SchemaKey, type[ImportSchema]] = {}

    def register(self, schema_cls):
        key = (
            schema_cls.broker,
            schema_cls.document_kind,
            schema_cls.format_kind,
            schema_cls.version,
        )
        if key in self._schemas:
            existing = self._schemas[key]
            raise ImproperlyConfigured(
                f"Duplicate schema registration for {key}: "
                f"{existing.__module__}.{existing.__name__} vs "
                f"{schema_cls.__module__}.{schema_cls.__name__}"
            )
        self._schemas[key] = schema_cls
        return schema_cls

    def get(self, broker, document_kind, format_kind, version):
        return self._schemas.get((broker, document_kind, format_kind, version))

    def all(self):
        return dict(self._schemas)


registry = SchemaRegistry()


def register_schema(*, broker, document_kind, format_kind, version):
    def decorator(cls):
        cls.broker = broker
        cls.document_kind = document_kind
        cls.format_kind = format_kind
        cls.version = version
        registry.register(cls)
        return cls
    return decorator
```

#### 3. Auto-discovery on app startup

```python
# django_assets/brokerage/apps.py

from django.apps import AppConfig
from django.utils.module_loading import autodiscover_modules


class BrokerageConfig(AppConfig):
    name = "django_assets.brokerage"

    def ready(self):
        # Imports schemas.py from every installed Django app, the same
        # pattern Django uses for admin.autodiscover().
        autodiscover_modules("schemas")
```

Built-in schemas live at `django_assets/brokerage/schemas/builtin/*.py` and are imported by the brokerage app's own `schemas/__init__.py`.

#### 4. `ImportBatch` natural-key fields

```python
# django_assets/brokerage/models.py

class ImportBatch(models.Model):
    # ... existing fields per ADR-0019 ...
    schema_broker = models.SlugField(max_length=50)
    schema_document_kind = models.SlugField(max_length=50)
    schema_format_kind = models.CharField(max_length=20)
    schema_version = models.CharField(max_length=20)

    class Meta:
        indexes = [
            models.Index(
                fields=[
                    "schema_broker",
                    "schema_document_kind",
                    "schema_format_kind",
                    "schema_version",
                ],
                name="importbatch_schema_key_idx",
            ),
        ]

    def get_schema(self) -> type[ImportSchema]:
        schema_cls = registry.get(
            self.schema_broker,
            self.schema_document_kind,
            self.schema_format_kind,
            self.schema_version,
        )
        if schema_cls is None:
            raise SchemaNotRegistered(
                f"Batch #{self.pk} was imported under "
                f"{self.schema_broker}/{self.schema_document_kind}/"
                f"{self.schema_format_kind}/{self.schema_version}, "
                f"but no Python class is currently registered for that key. "
                f"Restore the class to read this batch's data."
            )
        return schema_cls
```

### How schemas are added in practice

| Path | Who uses it | Mechanism |
| --- | --- | --- |
| Built-in | Package maintainers | `django_assets/brokerage/schemas/builtin/<broker>/<document>_<format>.py` in this package, registered via decorator. |
| Host-defined | Host devs | `<host_app>/schemas.py` in the host's own Django app, registered via decorator. Host app appears in `INSTALLED_APPS`. |
| Third-party | Community packages | A pip-installable Django app (`django-assets-fidelity`, etc.) ships a `schemas.py`. Hosts pip-install and add to `INSTALLED_APPS`. |

All three paths use the same `@register_schema` decorator. No fork required.

Example host-defined schema:

```python
# myhost_app/schemas.py
import csv
from django_assets.brokerage import ImportSchema, ImportLine, register_schema, templates


@register_schema(
    broker="customcorp",
    document_kind="trades",
    format_kind="csv",
    version="2026.01",
)
class CustomCorpTradesCSV(ImportSchema):
    name = "CustomCorp Trades CSV"
    definition = {
        "shape": "tabular",
        "delimiter": ",",
        "header_row": 0,
        "columns": [
            {"name": "trade_date", "type": "date", "format": "%Y-%m-%d"},
            {"name": "action", "type": "string"},      # BUY / SELL / DIV / FEE
            {"name": "symbol", "type": "string"},
            {"name": "quantity", "type": "decimal"},
            {"name": "price", "type": "decimal"},
            {"name": "commission", "type": "decimal"},
            {"name": "fee", "type": "decimal"},
            {"name": "amount", "type": "decimal", "extract": r"^\$([0-9.,]+)$"},
        ],
    }

    def parse_batch(self, batch, source):
        reader = csv.reader(source)
        next(reader)  # skip header
        for n, row in enumerate(reader, start=1):
            yield ImportLine(
                batch=batch,
                line_number=n,
                raw_data=row,             # positional list aligned to definition["columns"]
                kind="broker_csv",
            )

    def materialize_line(self, line):
        row = dict(zip([c["name"] for c in self.definition["columns"]], line.raw_data))
        action = row["action"]
        if action == "BUY":
            return [templates.buy_stock(
                account=line.batch.account,
                symbol=row["symbol"],
                quantity=row["quantity"],
                price=row["price"],
                commission=row["commission"],
                industry_fee=row["fee"],
            )]
        if action == "SELL":
            return [templates.sell_stock(...)]
        if action == "DIV":
            return [templates.dividend_received(...)]
        if action == "FEE":
            return [templates.account_fee(...)]
        raise ValueError(f"Unknown action in CustomCorp row: {action}")
```

One class, the full broker-specific pipeline. Templates (`buy_stock`, `sell_stock`, `dividend_received`, `account_fee`) come from `django_assets.brokerage.templates` and are the atomic ledger constructors specified by ADR-0021 — schemas are their callers from the import path.

### Orchestration

Schemas only own broker semantics. Persistence order, dedup against pre-existing manual entries, and reconciliation linkage are handled by an orchestrator helper in brokerage:

```python
# django_assets/brokerage/import_runner.py

def process_batch(batch: ImportBatch, source) -> None:
    schema = batch.get_schema()

    # Step 1: parse → lines (persisted before any materialization, so a crash
    # mid-way leaves raw evidence intact for re-processing).
    lines = list(schema.parse_batch(batch, source))
    ImportLine.objects.bulk_create(lines)

    # Step 2: lines → transactions, with dedup against pre-existing manual entries.
    for line in lines:
        existing_legs = find_matching_legs(line)   # see "Dedup" below
        if existing_legs:
            line.matched_legs.add(*existing_legs)  # link, don't materialize
        else:
            for tx in schema.materialize_line(line):
                asset_legs = [
                    leg for leg in tx.legs
                    if leg.account.brokerage_profile.allows_reconciliation
                ]
                line.matched_legs.add(*asset_legs)
```

This split keeps schemas dumb about ledger state: they don't query for existing transactions, they don't make dedup decisions, they don't manage the M2M to `TransactionLeg`. They just produce.

### Dedup against pre-existing manual entries

The orchestrator must distinguish "manually entered, not yet reconciled" legs from "automatically imported" legs so it can match the former against newly-parsed lines. This requires a provenance marker on `Transaction` (or `TransactionLeg`) — something like `Transaction.origin = "manual" | "import"`.

That marker is **out of scope for this ADR** and will be settled by ADR-0028 (Transaction provenance). For the purposes of ADR-0027, the orchestrator's `find_matching_legs(line)` is treated as a placeholder; its implementation depends on what ADR-0028 settles. The schema-level API in this ADR (`parse_batch`, `materialize_line`) is unaffected by whichever direction ADR-0028 takes.

### Definition JSON

The `definition` dict is free-form. Convention documents a recommended shape; the registry does not enforce it.

Tabular shape:

```json
{
  "shape": "tabular",
  "delimiter": ",",
  "header_row": 0,
  "columns": [
    {"name": "trade_date", "type": "date", "format": "%Y-%m-%d"},
    {"name": "symbol", "type": "string"},
    {"name": "quantity", "type": "decimal"},
    {"name": "amount", "type": "decimal", "extract": "^\\$([0-9.,]+)$"}
  ]
}
```

`ImportLine.raw_data` for a tabular batch is a positional list aligned to `columns`:

```json
["2026-06-07", "AAPL", "100", "15000.00"]
```

Nested shape (OFX aggregates, broker JSON APIs):

```json
{
  "shape": "nested",
  "field_paths": {
    "trade_date": "$.STMTTRN.DTPOSTED",
    "symbol": "$.STMTTRN.SECID.UNIQUEID",
    "amount": "$.STMTTRN.TRNAMT"
  }
}
```

`raw_data` for a nested batch stays a dict matching the broker's hierarchical structure.

### Immortality convention

Once a `(broker, document_kind, format_kind, version)` tuple is shipped — by this package or by any host — its Python class **stays** in the codebase. Treat schemas like Django migration files: append-only.

If a broker changes their format:

- Right way: register a new class with `version="2026.02"`, leave the old class in place. Old batches continue to read through the old class; new imports use the new class.
- Wrong way: edit the existing class's `definition`. Old batches now read with the wrong column layout.

The registry's duplicate-key check catches the obvious mistake of two classes claiming the same key. It cannot catch the silent mutation of an existing class. That discipline is enforced by code review, not by the system.

### What happens if a schema class is accidentally removed?

- **Reading old batches**: `batch.get_schema()` raises `SchemaNotRegistered` with a clear pointer to the missing key. The fix is to restore the class. Data is not lost — `ImportLine.raw_data` is intact — only interpretation is blocked.
- **Importing new batches**: blocked at the same point. No silent data loss.

This is the same failure mode as deleting a Django app from `INSTALLED_APPS` while migrations still reference its models: loud, fixable, no corruption.

## Considered alternatives

### Alt A: `ImportSchema` DB model with `post_migrate` sync (considered; rejected)

The earlier draft of this ADR proposed snapshotting each registered schema into an `ImportSchema` table. `ImportBatch.schema` was a `PROTECT` FK to that table.

**Pros:** Real FK safety. Definition JSON survives code removal. Admin-browsable.
**Cons:**
- The definition snapshot turned out to be weak as durability insurance. The snapshot describes columns but not the `parse()` method. If the Python class is gone, the snapshot can't actually re-parse — it only tells you what the columns were named.
- A `post_migrate` sync handler with drift detection is real maintenance burden.
- Adds a model, a migration, and a sync execution path the package owner has to keep correct on every release.
- Duplicates the source of truth (code AND DB), creating an ongoing reconciliation problem.

The convention "schemas, once shipped, are immortal in code" gives most of the same durability benefits without any of these costs. Rejected.

### Alt B: DB-defined schemas (admin-editable)

`ImportSchema.definition` is the source of truth; no code component. Hosts edit definitions in Django admin.

**Pros:** No code required to onboard a new broker.
**Cons:** Custom parsing logic (multi-line CSV quirks, encoding peculiarities, broker-specific data fixups) can't be expressed in admin-edited JSON. Forces hosts who hit a non-trivial broker format to fork the package anyway, defeating the goal. Rejected per the "schemas are code, period" preference.

### Alt C: Schema snapshot embedded in `ImportBatch`

Each batch carries its own snapshot of the schema definition; no shared registry.

**Pros:** Total self-containment.
**Cons:** Schema duplication across thousands of batches. Cannot query "all batches using Fidelity Trades CSV v2026.01" without inspecting JSON in every row. A regression to "schema in every X." Rejected.

### Alt D: Three-part key (no `document_kind`)

The natural key is `(broker, format_kind, version)` only.

**Pros:** Smaller key.
**Cons:** A single broker ships multiple kinds of CSV: trades, dividends, balances, transfers, positions. Without `document_kind`, two different documents would collide on the same key. Rejected.

## Consequences

**Easier:**

- Schemas are plain Python in any installed app. No fork, no admin clicks, no DB editing. Type-safe, version-controlled, testable.
- Third-party schema packages "just work" — pip install, add to `INSTALLED_APPS`, done. Enables community schema packages without coordinating with this repo's release cycle.
- One source of truth: code. No `post_migrate` sync, no `ImportSchema` table, no drift-detection logic.
- The four-part natural key (broker, document_kind, format_kind, version) makes natural queries trivial: "all Fidelity imports across all formats," "all CSV imports," "everything imported at v2026.01."
- `ImportLine.raw_data` becomes positional values (for tabular shapes), cutting per-row storage by 5–10x for typical CSV imports.
- Auto-discovery via `autodiscover_modules("schemas")` mirrors Django's existing `admin.py` pattern — familiar to any Django developer.
- Adding a new broker format (Fidelity changes their trades CSV) is a single new class with a bumped `version`. Old batches keep working through the old class.

**Harder:**

- No `PROTECT`-style enforcement at the DB level. The "immortality" rule is a convention enforced by code review, not by the schema. A maintainer who deletes a shipped class breaks any host that has historical batches referencing it.
- No DB-side audit trail of "when was this schema first seen." Git history of the Python class is the only record.
- Slightly harder to introspect schemas from non-Python tools (psql, BI). The broker/document/format/version triple on `ImportBatch` is enough for most cases, but tooling that wants to render "what does column 3 mean" needs to call into Python.
- The convention for `definition` JSON shape is documented, not enforced. Drift between brokers (one uses `"format"`, another uses `"date_format"`) is a soft consistency problem.
- If a `version` change is forgotten when a definition is edited, the registry catches it only when a duplicate registration would occur — silent mutation of an existing class is not caught by the system.

## Impact on other ADRs

- **ADR-0019** (Bulk import primitives) — `ImportBatch` gains four schema-key CharFields (`schema_broker`, `schema_document_kind`, `schema_format_kind`, `schema_version`) and loses the original free-form `source` CharField, which is fully subsumed by the schema-key tuple. Dedup helpers (`is_period_imported`, `find_by_external_ids`) now key off `(schema_broker, schema_document_kind)` instead of `source`. High-level ADR is not invalidated; this is a refinement.
- **ADR-0021** (Brokerage templates follow source's transaction shape) — needs a small clarification noting that `ImportSchema.materialize_line` is the import-path caller of brokerage templates. Templates themselves are unchanged; the addition is documentation of who their callers are.
- **ADR-0025** (Broker download lines) — `raw_data` storage clarified to "structured per the batch's registered `ImportSchema.definition`" (positional list for tabular shapes, dict for nested shapes). The intent (canonical evidence, no denormalized columns) is preserved. Applied 2026-06-09.
- **ADR-0028** (Transaction provenance) — settles the `Transaction.origin` marker that the import orchestrator uses to find pre-existing manual entries during dedup, and adds a third required schema method (`match_criteria`), which the base class declared above includes.

## Related

- ADR-0019 (Bulk import primitives; import management) — introduces `ImportBatch`.
- ADR-0021 (Brokerage templates follow source's transaction shape) — templates run after parsing.
- ADR-0025 (Broker download lines — storage and matching workflow) — settles `ImportLine` storage and matching; this ADR specifies the schema layer that interprets `raw_data`.
- ADR-0026 (`ImportLine` → `TransactionLeg` relationship) — orthogonal but related.
