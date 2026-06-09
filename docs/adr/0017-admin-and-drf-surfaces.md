# ADR-0017: Admin and DRF surfaces in core

## Status

Accepted — 2026-06-02

## Context

The `django_assets.core` sub-package needs to decide what user-facing surfaces it ships. Two related questions:

1. **Django admin** — does core ship `ModelAdmin` registrations for its models? If so, how editable should they be? Hosts use core's models during development, debugging, and ops, and `django.contrib.admin` is the standard introspection surface in the Django ecosystem.
2. **DRF views and viewsets** — does core ship REST API endpoints? If yes, with what auth and what URL shape?

The two surfaces have very different audiences. The admin is for the host's *internal* operators (developers, support staff, ops). The DRF surface is for the host's *external* API consumers (users, third-party integrations, sheets/scripts).

The internal-vs-external split aligns cleanly with the package's scope from ADR-0011 (core is the ledger primitive). Internal introspection is universally needed; external API exposure is policy-driven and host-specific.

On the admin side, an additional question came up during design: should `Transaction` and `TransactionLeg` rows be read-only in admin to prevent accidental ledger corruption? The temptation is yes — direct edits feel dangerous. But the deferred balance trigger from ADR-0004 already enforces integrity at COMMIT regardless of how the rows are touched. An admin who edits a leg's amount in a way that breaks per-instrument zero-sum will see the save rejected by the trigger. Read-only admin treats the trigger as if it didn't exist.

The principled accounting argument for read-only is that historical corrections should go through reversal transactions (post an offsetting transaction rather than mutate the original), preserving audit trail. This is correct for regulated contexts but is a *policy* layered on top of the ledger, not an integrity requirement. The package can document it as best practice while leaving the choice to the host.

For DRF, ADR-0011 already commits core to "ledger primitive, not policy." Auth, permissions, URL mounting, and API shape are host concerns. Different hosts want different shapes — REST, GraphQL, custom RPC, internal-only — and the package should not constrain them. Shipping serializers is useful (so the host doesn't reinvent JSON shapes for Instrument, Transaction, etc.); shipping viewsets is presumptuous.

The host environment (per the compatibility doc) sets `REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES'] = ()` and wires authentication per-view. Core shipping a viewset with assumed auth would be wrong for this host and probably wrong for many others. Sibling apps (brokerage, trades) may ship their own viewsets if they make sense for their use cases; core does not.

## Decision

### Admin: auto-registered, fully editable

The `django_assets.core` sub-package ships `admin.py` with `ModelAdmin` classes registered for every model. Registration happens automatically when the app is in `INSTALLED_APPS` (the conventional Django package pattern).

All fields are editable. There are no `readonly_fields = "__all__"` blanket protections. The deferred balance trigger from ADR-0004 is the single source of integrity enforcement; admin edits that would break per-instrument zero-sum are rejected by the trigger at COMMIT with a clear error.

Concrete shipped admin classes:

Core's admin registers the narrow set of core models (per ADR-0020):

- `InstrumentAdmin` — list display of code/precision/is_active, search by code and identifier value
- `IdentifierAdmin` — list display of type/value/exchange/instrument, filters by type and exchange
- `AccountAdmin` — list display of name/owner, filters by owner
- `TransactionAdmin` — list display of timestamp/account/description, with `TransactionLegInline` so legs are edited in the context of the parent transaction by default
- `TransactionLegAdmin` — standalone admin for ad-hoc filtering and search by instrument or account
- `ExchangeAdmin` — basic reference data

Brokerage, trades, and lots register admin for their own models (`AccountProfile`, `OptionMeta`, `Deliverable`, `CorporateAction`, `CurrencyMeta`, `CryptoMeta`, `ImportBatch`, `TransactionImport`, `Trade`, `Lot`, `LotMatch`, etc.) per their own ADRs.

The `TransactionAdmin` uses an inline for `TransactionLeg` rather than separate edits as the default UX, so admins see the whole transaction when changing legs and are less likely to leave a transaction unbalanced. The standalone `TransactionLegAdmin` is also registered for cases that need it.

### Admin is overridable by the host

Hosts that want different admin classes use the standard Django pattern:

```python
# host's admin.py
from django.contrib import admin
from django_assets.core.models import Transaction
from django_assets.core.admin import TransactionAdmin

admin.site.unregister(Transaction)

class CustomTransactionAdmin(TransactionAdmin):
    # ... custom overrides ...
    pass

admin.site.register(Transaction, CustomTransactionAdmin)
```

The package documents this pattern. Common host customizations (using `rangefilter`, `admin_auto_filters`, or other third-party admin enhancements) layer on top via subclassing without core needing to know about them.

### Reversal pattern is documented, not enforced

The package's documentation explains the accounting-purist position: production data corrections should go through **reversal transactions** that offset the original rather than mutating it.

Example:

```
Original transaction (recorded 100 AAPL by mistake):
  +100 AAPL / -$15,000 USD (plus counterparty legs)

Correction transaction (offset the over-recording by 90):
  -90 AAPL / +$13,500 USD (plus counterparty legs)
```

The original transaction stays as a historical record; the correction is a separate dated event. This preserves audit trail and is the standard accounting practice in regulated contexts. The package does not enforce it. Hosts that need enforcement (regulated brokerage, institutional custody) can wire `pre_save` signal handlers that reject mutation of existing rows, or use a future opt-in mode (OQ-15 is the related open question).

### Audit logging is a host concern

The package does not ship an audit log of who edited what when. Hosts that need this wire up `django-simple-history`, `django-auditlog`, or their own pre-save handlers. The package's models are standard Django models and integrate cleanly with the conventional audit-log libraries.

### DRF: no viewsets, no URL conf; serializers and custom fields only

The `django_assets.core` sub-package does not ship DRF viewsets or `urls.py`. The host owns:

- Authentication (`REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES']`)
- Permissions (per-view, host-defined)
- URL mounting (path prefix, version, naming convention)
- API shape (read-only? CRUD? custom actions?)
- Pagination, filtering, throttling, throttling backends

What core ships in `serializers.py` (narrow set, matching the core schema per ADR-0020):

- `InstrumentSerializer`, `IdentifierSerializer`, `AccountSerializer`, `ExchangeSerializer` — for reference data.
- `TransactionSerializer`, `TransactionLegSerializer` — for ledger data. `TransactionSerializer` includes nested `TransactionLegSerializer` for whole-transaction read or write.
- `HoldingSerializer` and `PortfolioSerializer` — for query results (computed values, not model rows).

Sibling apps ship their own serializers for their own models (`OptionMetaSerializer`, `CurrencyMetaSerializer`, `AccountProfileSerializer`, `ImportBatchSerializer`, `LotSerializer`, `TradeSerializer`, etc.).

What core ships as a custom DRF field:

- `MeasureField` — the README's `{"amount": "12.3456", "unit": "USD"}` shape. Annotated with `@extend_schema_field` so `drf-spectacular` (in the host's environment per the compatibility doc) generates clean OpenAPI without intervention.

Hosts use the serializers directly or subclass them. They build their own `ViewSet` classes with their own auth and permission classes and mount them in their own `urls.py` per the convention established in the compatibility doc (`router.registry.extend(...)` against an app-local `router` symbol).

### Sibling apps may ship viewsets

`django_assets.brokerage` and `django_assets.trades` MAY ship viewsets for their own models (admin-facing or user-facing) if their requirements docs commit to that. Those decisions are separate from this ADR. Core does not.

## Consequences

**Easier:**

- Admin "just works" when `django_assets` is added to `INSTALLED_APPS` — no host wiring needed for the common dev/debugging case.
- Admin edits are integrity-safe by virtue of the balance trigger. No need for the package to write defensive `readonly_fields` declarations.
- Hosts have full control over their API shape, auth, and URLs. Core never gets in the way.
- Documentation can show host-side DRF examples without choosing for the host.
- Serializers and the `MeasureField` are reusable across hosts that may want very different viewset shapes.

**Harder:**

- Admins who don't understand the reversal pattern can still mutate historical transactions. Documentation explains the practice but does not enforce it. For regulated contexts, this is something the host needs to handle.
- Hosts that DO want a built-in DRF API have to write viewsets themselves. The package documents the pattern; the work is theirs.
- Two admin registration entry points (`TransactionAdmin` with inline, `TransactionLegAdmin` standalone) is slightly more surface area to document.
- A host with strong admin opinions has to unregister and re-register, which is one extra step on top of just adding the app.

**Deferred:**

- Opt-in append-only enforcement (override `delete` and `save` to refuse mutation of existing rows) — tracked as OQ-15. May be added if regulated adopters need it.
- A "balance check" admin action that highlights transactions where the per-instrument sum would not be zero — useful diagnostic but not v0.1 critical.
- Audit log integration (`django-simple-history` adapter) — host concern, may be packaged as a sibling later.

## Related

- ADR-0004 establishes the deferred balance trigger, which is what makes editable admin safe.
- ADR-0011 commits core to "ledger primitive, not policy" — this ADR carries that forward to the admin and DRF surfaces.
- OQ-11 (admin UI scope) and OQ-12 (DRF views in core) in `open-questions.md` are resolved by this ADR.
- OQ-15 (soft delete / append-only enforcement) remains open and is the natural follow-up if regulated adopters need stricter enforcement.
