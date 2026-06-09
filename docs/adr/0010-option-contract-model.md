# ADR-0010: Option contract model in brokerage — OptionMeta, Deliverable, CorporateAction

## Status

Accepted — 2026-06-03

## Context

Listed options need structured representation: expiry date, strike, right (call/put), settlement type, exercise style, and (because OCC corporate-action adjustments are routine) a per-contract deliverable composition that can change over the option's lifetime. The PFE1 case from OCC memo #47935 is the canonical example: a Pfizer/Viatris spinoff adjusted all open PFE options on 2020-11-17 so that each contract now delivers 100 PFE shares + 12 VTRS shares + $6.47 cash.

The structural fields and the deliverable composition are useful for:

- Querying ("show me all my SPY calls expiring this Friday").
- Pricing the underlying for an adjusted option (sum the deliverable components).
- Driving exercise-template legs from the active deliverable at the moment of exercise.

These are opinionated structures: they encode a specific accounting model for options, the OCC's symbol conventions, the half-open temporal model for deliverable cutover, and the way adjusted-option pricing works.

Per ADR-0020 (Core ships only numeric integrity), opinionated structures do not live in core. The option model lives in the `django_assets.brokerage` sub-package. Core remains unaware of options as a category — to core, an option Instrument is just an Instrument like any other (a unit of value that can be held, transferred, and balanced).

This ADR's earlier version placed `OptionMeta` and `Deliverable` in core as "optional" schema. ADR-0020 supersedes that framing: optional in the install sense isn't enough; if it's opinionated, it doesn't go in core regardless of whether it's required at runtime.

## Decision

### `OptionMeta` lives in the `django_assets.brokerage` sub-package

```python
# django_assets/brokerage/models.py

class OptionMeta(models.Model):
    instrument = models.OneToOneField(
        "django_assets.Instrument",
        related_name="option_meta",
        on_delete=models.CASCADE,
    )
    underlying = models.ForeignKey(
        "django_assets.Instrument",
        related_name="option_meta_as_underlying",
        on_delete=models.PROTECT,
    )
    expiry = models.DateField(db_index=True)
    strike = models.DecimalField(max_digits=20, decimal_places=8, db_index=True)
    right = models.CharField(max_length=1, choices=[("C", "Call"), ("P", "Put")])
    settlement_type = models.CharField(
        max_length=20, default="physical",
        choices=[("physical", "Physical"), ("cash", "Cash"), ("basket", "Basket")],
    )
    exercise_style = models.CharField(
        max_length=20, default="american",
        choices=[("american", "American"), ("european", "European"), ("bermudan", "Bermudan")],
    )
```

`underlying` is a brokerage-side FK to a core Instrument. Core's Instrument has no `underlying` field (per ADR-0009 revisions); the derivative relationship lives here.

### `Deliverable` lives in the `django_assets.brokerage` sub-package

```python
class Deliverable(models.Model):
    """What one contract of this option delivers on exercise."""
    option_meta = models.ForeignKey(OptionMeta, related_name="deliverables", on_delete=models.CASCADE)
    sequence = models.PositiveSmallIntegerField(default=0)

    instrument = models.ForeignKey(
        "django_assets.Instrument", null=True, blank=True,
        related_name="deliverable_components", on_delete=models.PROTECT,
    )
    quantity = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    cash_currency = models.ForeignKey(
        "django_assets.Instrument", null=True, blank=True,
        related_name="cash_deliverables", on_delete=models.PROTECT,
    )
    cash_amount = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)

    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    corporate_action = models.ForeignKey(
        "CorporateAction", null=True, blank=True,
        related_name="deliverable_changes", on_delete=models.SET_NULL,
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(instrument__isnull=False, quantity__isnull=False, cash_currency__isnull=True, cash_amount__isnull=True)
                    | models.Q(instrument__isnull=True, quantity__isnull=True, cash_currency__isnull=False, cash_amount__isnull=False)
                ),
                name="deliverable_either_instrument_or_cash",
            ),
        ]
```

Half-open temporal interval `[effective_from, effective_to)`. A row is active at date D when `effective_from <= D < effective_to`, with `effective_to = NULL` treated as `+infinity`.

### `CorporateAction` lives in the `django_assets.brokerage` sub-package

```python
class CorporateAction(models.Model):
    """A discrete event that adjusts one or more instruments."""
    id = models.BigAutoField(primary_key=True)
    effective_date = models.DateField(db_index=True)
    action_type = models.CharField(max_length=40)
    # spinoff, split, reverse_split, merger, acquisition, special_dividend,
    # symbol_change, exchange_change, option_adjustment, delisting, ...
    source_reference = models.CharField(max_length=64, blank=True)  # "OCC #47935", etc.
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    primary_instrument = models.ForeignKey(
        "django_assets.Instrument", null=True, blank=True,
        related_name="corporate_actions", on_delete=models.SET_NULL,
    )
```

`CorporateAction` is referenced by `Deliverable` (above) and by `Identifier` change events (via `Identifier.corporate_action`, optional). When the host populates corporate actions, both identifier and deliverable changes can be traced back to the originating event.

Core's `Identifier` (per ADR-0009) gets an optional `corporate_action` FK that points at `django_assets.brokerage.CorporateAction`. This is a deliberate exception to "core knows nothing about brokerage internals": the FK is nullable, so core works fine without brokerage installed; if brokerage is installed and populates corporate actions, the link works.

### Exercise template stays in brokerage

```python
def exercise_option(
    account, option_instrument, contracts, *, as_of=None, override_deliverables=None,
):
    """Exercise a long option.

    If override_deliverables is provided, use those legs directly (broker-statement
    import path). Otherwise read active Deliverable rows for the option at as_of
    and generate legs from them (deliverable-driven path).

    The trade timestamp (per ADR-0012) is used for deliverable lookup so that
    exercises across corporate-action boundaries get the correct deliverable.
    """
```

Worked examples from the original ADR (PFE1 exercise straddling the 2020-11-17 cutover) still hold; the implementation just lives in brokerage instead of having core reach into option-specific schema.

## Consequences

**Easier:**

- Core stays unopinionated (per ADR-0020). Options are not a special concept to core; they're just Instruments.
- Adopters who don't trade options can install core + trades (or core alone) without option-shaped tables sitting unused. The brokerage app is opt-in.
- Brokerage owns the option lifecycle end-to-end: representation (OptionMeta), corporate-action handling (Deliverable, CorporateAction), and template execution (exercise_option). Single place to maintain the option model.
- Other sibling sub-packages can add their own asset-type-specific extensions following the same pattern (e.g., a future bond app could add `BondMeta` with coupon schedules).

**Harder:**

- Hosts that want option support import from `django_assets.brokerage`. Per ADR-0015, the single Django app means all sub-packages are present once `django_assets` is installed; this is a code-organization concern, not an install-time choice.
- The FK from `Identifier.corporate_action` to a brokerage model is an exception to "core knows nothing about brokerage internals." The exception is justified (corporate actions affect identifiers, and the linkage is valuable for audit) but it does cross a boundary. Documented carefully.
- Cross-app schema references (brokerage's `OptionMeta.instrument → core.Instrument`) require careful migration ordering. Standard Django pattern; not a real problem.

## Related

- ADR-0020 (Core ships only numeric integrity) — the principle that moved this schema to brokerage.
- ADR-0004 (DDL install hybrid) — the integrity machinery that validates exercise transactions.
- ADR-0009 (Instrument identity) — establishes the Instrument and Identifier models that OptionMeta extends.
- ADR-0012 (Transaction timestamps) — exercise template uses `trade_timestamp` for deliverable lookup.
- OQ-2 (Option contract decomposition) is resolved by this ADR.
