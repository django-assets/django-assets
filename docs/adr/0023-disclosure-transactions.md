# ADR-0023: Recording disclosures of previously-hidden transaction details

## Status

Proposed — 2026-06-07

This ADR captures alternatives and is **not yet decided**. Comments and counter-proposals are welcome.

## Context

Reconciled broker imports represent the ground truth that was visible to the broker at the time of recording. But many real-world transactions have information that is hidden from the broker and revealed only later, from a different source. The canonical example:

A user holds an ADR. On a dividend pay date, the broker deposits **$100** in the user's brokerage account. That is what the broker's statement shows; it is what gets imported.

Later, the user downloads the dividend advice from the ADR sponsor's website (e.g., ADR.com). The advice reveals:

- Gross dividend: $115
- Foreign tax withheld at source: $14
- ADR sponsor fee retained: $1
- Net to broker: $100

The $15 difference was never visible on the broker statement. The user needs to capture this disclosed breakdown so they can:

1. Claim the foreign tax credit on their tax return (requires tracking $14 separately).
2. Track ADR sponsor fees as a recurring expense.
3. Report the gross dividend income, not just the net.

All without modifying the original reconciled broker import (which must continue to reconcile against the broker statement verbatim).

Similar patterns arise for:

- 1099 corrections and reclassifications received after year-end.
- K-1 schedules disclosing pass-through items for partnership investments.
- Mutual fund year-end character disclosures (ordinary vs. qualified vs. capital-gain).
- Corporate-action disclosures arriving after the initial event was imported.
- Broker statement reconciliations that surface fees or interest hidden from the trade-confirmation feed.

## Constraints from existing ADRs

This ADR must operate within the following established decisions:

- **ADR-0011, ADR-0020**: Core ships only numeric integrity. No opinionated structures in core; sub-packages and host code carry policy.
- **ADR-0017**: Fully editable admin; the reversal pattern is documented as best practice for production data corrections in regulated contexts.
- **ADR-0019**: `ImportBatch` and `TransactionImport` track which Transactions belong to which broker import. Reconciled imports must stay reconcilable against their source.
- **ADR-0021**: Brokerage templates follow the source's transaction shape. Multi-leg routing convention: one consolidated leg per external counterparty, one leg per user-side category.
- **ADR-0022**: No append-only enforcement is shipped; mutation is allowed at the model layer but discouraged for reconciled data by convention.
- **The "reconciled imports are inviolable" principle** (working consensus from recent design discussion): once a Transaction has been reconciled against a broker import, its legs touching broker-reported accounts (cash, holdings) must not be modified. Disclosures extend the record via new transactions; they do not edit reconciled legs.

## Sub-questions

### What does "reconciled" mean? (load-bearing)

**Resolved by ADR-0024: leg-level reconciliation, asset-account legs only, leg-level FK to source.** Only legs with a non-null `reconciled_by` FK are immutable; all other legs of the same Transaction are freely editable.

This means **Approaches 7 and 8 (edit-in-place under leg-level reconciliation) are now viable** and are likely the cleanest answer for the disclosure-capture question that this ADR is about. Approaches 1, 3, and 4 (separate adjustment Transaction, with or without a relationship model) remain available as alternatives for adopters who prefer an audit-trail-style approach with one Transaction per disclosure event. Approaches 2, 5, and 6 are off the table.

Concrete reasoning for the leg-level view: the broker only confirms its own deposits and withdrawals. The +$100 USD → brokerage_account leg of T1 is broker ground truth. But the offsetting -$100 USD from `external_dividend_payer` is the user's interpretive choice — the broker has no opinion on where that $100 came from in the user's mental model of the world. If we accept this distinction, then editing T1 to elaborate the counterparty side and add tracking legs preserves broker reconciliation perfectly:

```
Transaction T1 (after edit-in-place with leg-level reconciliation):
  +$100 USD  →  brokerage_account                (UNCHANGED — broker ground truth, locked)
  -$115 USD  ←  external_dividend_payer          (revised to gross, unlocked)
  +$14  USD  →  user_foreign_tax_paid             (added, tracked expense)
  +$1   USD  →  user_adr_fees_paid                (added, tracked expense)
```

USD balance: `+100 - 115 + 14 + 1 = 0` ✓. Broker reconciliation against the brokerage_account leg still works because that leg is byte-identical to what was imported.

Under whole-transaction reconciliation, the same outcome requires a separate adjustment Transaction (Approach 1).

If we go with leg-level reconciliation, we need to define:

- **How is "broker-reported" determined?** A flag on `TransactionLeg` set at import time? A property of the Account (`AccountProfile.is_broker_reported = True`)? Inferred from the leg's account being the one named in the `TransactionImport`?
- **Is the lock advisory or enforced?** Enforced via a `pre_save`/`pre_delete` signal that refuses to touch a locked leg? Or convention-only, with admin UI warning?
- **What about deletion of the parent Transaction?** Does it cascade to locked legs? (Yes, almost certainly — locking only protects against in-place edits.)
- **Can you UN-lock?** E.g., if the user discovers the broker statement itself was wrong. Probably yes, but it should require a deliberate action.

### Other sub-questions (dependent on the above)

1. **How is the disclosed adjustment recorded?** Edit-in-place (only valid under leg-level reconciliation), a separate Transaction, or a non-ledger metadata record?
2. **How is the relationship between the original and the disclosure tracked?** Metadata-only? A real FK? A separate relationship model? (Only matters if the answer involves separate Transactions.)
3. **Where does the helper API live?** Brokerage sub-package? Host code? Configurable?
4. **What information about the disclosure source is captured?** Just the breakdown amounts? Source identifier (e.g., "adr_dot_com_csv_2026-03-15.csv")? Document hash? Effective date of the disclosure?
5. **How is the user reminded that a Transaction may have undisclosed details?** Marker on the original? Periodic report? Out of scope for the package?
6. **Can a single original Transaction have multiple disclosures over time?** (e.g., dividend advice arrives, then a 1099-DIV correction arrives months later.) If yes, how do they compose? (For edit-in-place: subsequent disclosures continue to extend the same Transaction. For separate-Transaction approaches: a chain of adjustment Transactions builds up.)

## Alternative approaches

### Approach 1: Adjustment Transaction linked via metadata

The disclosure is a separate `Transaction` (T2) with its own legs, posted into the ledger alongside the original (T1). The relationship is stored in `Transaction.metadata`:

```python
T1.metadata["disclosure_transaction_ids"] = [T2.id]      # original points forward
T2.metadata["discloses_transaction_id"] = T1.id          # disclosure points back
T2.metadata["disclosure_source"] = "adr_dot_com_advice"
T2.metadata["disclosure_source_ref"] = "advice_2026-03-15.pdf"
```

T2 follows the ADR-0021 multi-leg convention:

```
Transaction T2:
  -$15 USD  ←  external_dividend_payer
  +$14 USD  →  user_foreign_tax_paid
  +$1  USD  →  user_adr_fees_paid
```

**Pros**: no schema changes; works against existing models; T1 stays untouched.
**Cons**: relationship is a soft link (no FK enforcement, no cascade); multiple disclosures over time accumulate in a JSON array; queries that need the relationship use JSONB lookups.

### Approach 2: A `discloses` self-FK on core's Transaction

Add a nullable `discloses` FK to `Transaction`:

```python
class Transaction(models.Model):
    discloses = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="disclosures")
    # ... existing fields
```

A disclosure Transaction has `discloses` set to the original. Queryable cleanly: `T1.disclosures.all()` returns all disclosures of T1.

**Pros**: explicit, queryable, cascade-safe; supports multiple disclosures naturally.
**Cons**: adds a field to core's Transaction (violates ADR-0020's "core is integrity-only"); embeds an opinion about how disclosures are structured.

### Approach 3: A `TransactionDisclosure` model in the brokerage sub-package

Brokerage ships a dedicated model that wraps the relationship:

```python
# django_assets.brokerage.models

class TransactionDisclosure(models.Model):
    original_transaction = models.ForeignKey(
        "django_assets.Transaction", related_name="disclosures",
        on_delete=models.CASCADE,
    )
    adjustment_transaction = models.OneToOneField(
        "django_assets.Transaction", related_name="discloses",
        on_delete=models.CASCADE,
    )
    source = models.CharField(max_length=100)           # "adr_dot_com", "k1_schedule", "1099_corr", ...
    source_reference = models.CharField(max_length=200, blank=True)
    disclosed_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)
```

**Pros**: keeps core clean per ADR-0020; rich source metadata; clean FKs; multiple disclosures supported natively.
**Cons**: an extra model to maintain; the relationship lives in brokerage even though the Transactions themselves are core.

### Approach 4: Hybrid — Approach 1's adjustment Transaction + Approach 3's relationship model

Brokerage helper posts the adjustment Transaction (T2) AND creates a `TransactionDisclosure` row linking T1 and T2. Two artifacts, one logical operation.

**Pros**: ledger integrity through T2; rich relationship metadata through the disclosure model; clean separation between numeric truth (core) and bookkeeping context (brokerage).
**Cons**: most schema/code surface; two writes per disclosure.

### Approach 5: No separate model; rely entirely on metadata + Transaction.description

T2 exists as a regular Transaction. Its connection to T1 lives only in `T2.description` (free-form text) or `T2.metadata`. The user is responsible for knowing which Transaction discloses which. The package ships no helper specifically for this pattern.

**Pros**: simplest possible; no new infrastructure.
**Cons**: requires user discipline; relationship is not queryable; no support for batch disclosures or source tracking.

### Approach 6: Edit T1 freely, including the broker-reported legs (ruled out)

Modify any leg of T1, including the one touching `brokerage_account`. Rejected because it breaks reconciliation against the broker statement — T1's brokerage_account leg no longer matches what the broker reported.

Documented for completeness; not under consideration.

### Approach 7: Edit T1 in place, preserving broker-reported legs (under leg-level reconciliation)

Only valid if we adopt **leg-level reconciliation** (see the sub-questions above). Under this model, the user (or the disclosure helper) edits T1 directly, adding new legs and revising unlocked legs, but cannot modify the broker-reported leg(s):

```
Transaction T1 (after edit-in-place):
  +$100 USD  →  brokerage_account                (UNCHANGED — locked)
  -$115 USD  ←  external_dividend_payer          (revised — unlocked)
  +$14  USD  →  user_foreign_tax_paid             (added)
  +$1   USD  →  user_adr_fees_paid                (added)
```

USD balance: `+100 - 115 + 14 + 1 = 0` ✓. Broker reconciliation still works because the +$100 brokerage_account leg is byte-identical to what was imported.

**Pros**: single Transaction holds the complete view of one logical event; no need for relationship metadata or a relationship model; queries that need "everything about this dividend" find it on one row.
**Cons**: requires defining and enforcing leg-level reconciliation semantics; need to track which legs are locked (schema or convention); editing reconciled transactions is conceptually riskier even when constrained; complicates the admin UX ("you can edit this transaction but not these specific legs").

### Approach 8: Hybrid — Approach 7 plus a `DisclosureEvent` record

Edit T1 in place per Approach 7, AND post a non-ledger `DisclosureEvent` row in the brokerage sub-package that records the source (`adr_dot_com`, `k1_schedule`, etc.), the document reference, the date disclosed, and what was added (a diff of legs). Pure audit/history record, not a ledger entry.

**Pros**: ledger correctness from in-place edit; rich disclosure-source tracking from the event record; supports multiple sequential disclosures cleanly (each one is a `DisclosureEvent`).
**Cons**: most schema; two writes per disclosure; the `DisclosureEvent` is informational only (no ledger impact), which some adopters may find confusing.

## What needs to be decided

In rough order of dependency:

1. **First: whole-transaction or leg-level reconciliation?** This unlocks (or rules out) Approaches 7 and 8.
2. **If leg-level**: how is "broker-reported" tracked on legs? Flag, FK, account-profile-based inference?
3. **If leg-level**: is the lock enforced (signal handler refuses edits) or advisory (admin warns, but allows)?
4. Which approach (or hybrid) does the package commit to overall?
5. Does the helper API live in `django_assets.brokerage.templates` or somewhere else?
6. What's the recommended `source` vocabulary? (`adr_dot_com`, `k1_schedule`, `1099_dividend`, `broker_reconciliation`, etc.)
7. How are multiple sequential disclosures handled? (E.g., dividend advice in March, then a 1099-DIV correction in February of the following year.)
8. Do we expose a way for the user to mark T1 as "expecting disclosure" so dashboards can surface pending reconciliations?
9. What happens if T1 is deleted while a disclosure exists? (CASCADE on the disclosure model handles it for Approach 3/4; metadata-only soft links in Approach 1/5 become dangling; under Approach 7/8 the disclosure data lives on T1's legs, so deletion is naturally clean.)

## Related

- ADR-0011, ADR-0017, ADR-0019, ADR-0020, ADR-0021, ADR-0022 — all establish constraints this ADR must respect.
- ADR-0024 (Reconciliation scope) — owns the upstream question of which legs are immutable. The outcome there determines which alternatives in this ADR are viable.
- Open question: should the same machinery handle non-financial disclosures (e.g., the source's settlement-date adjustment, expiry-date corrections)? Probably yes, but not in scope for v0.1.
