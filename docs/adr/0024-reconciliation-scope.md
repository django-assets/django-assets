# ADR-0024: Reconciliation scope — asset-account legs only, reconciliation system in brokerage

## Status

Accepted — 2026-06-07

Note: the manual-match path (Path 2 in "Two paths produce reconciliation") depends on the dedup mechanics specified in ADR-0028 (Transaction provenance), which is still Proposed. If ADR-0028 changes direction during ratification, Path 2's surface (not its principle) may need a corresponding update here.

## Context

The `django-assets` package needs a precise definition of **reconciliation**: which `TransactionLeg`s of an imported Transaction must remain immutable after import, and which legs are free to be edited or extended by the user as new information arrives.

The question first surfaced in ADR-0023 (disclosure transactions). The answer is load-bearing: it determines whether disclosure information can be applied by editing the original Transaction in place, or whether it must always be captured in a separate adjustment Transaction.

Three candidate scopes for reconciliation are on the table:

### Scope A: Whole-transaction reconciliation

Once a Transaction has been imported from a broker statement, ALL of its legs are immutable. Any additional information arriving later (foreign tax disclosure, 1099 reclassification, etc.) must live in a separate Transaction. Approaches 1–5 from ADR-0023 are the only valid responses.

### Scope B: Multi-leg reconciliation

Both the asset-account leg (what was deposited to or withdrawn from the user's brokerage account) AND the offsetting counterparty leg are immutable, on the grounds that the broker statement implicitly identifies both sides of the transaction. Added legs are allowed; modifying or deleting either of the originally-imported pair is not.

### Scope C: Asset-account-only reconciliation

Only the legs touching the user's broker-reported account(s) are immutable. The broker confirmed only what came into or left the user's brokerage cash and holdings. The counterparty side — `external_dividend_payer`, `external_counterparty`, whatever the user labeled it — is the user's interpretive choice and remains freely editable. The user can refine, split, recategorize, or replace the counterparty side as new information arrives.

## Worked example: dividend evolution through three phases

This is the example that motivated the question. It illustrates why Scope C is attractive — the counterparty side genuinely is an evolving interpretation that the user refines over time, even though the asset-side facts never change.

### Phase 1 — broker imports the $100 deposit

Only the broker's deposit confirmation is known. The user's first-pass labeling:

```
+$100 USD  →  brokerage_cash         [RECONCILED, locked]
-$100 USD  ←  dividend_payer         [user's labeling]
```

### Phase 2 — the user downloads the ADR.com dividend advice

Now the user knows the gross was $115, $14 was withheld for foreign tax, $1 was retained as ADR sponsor fees. Under Scope C, T1 can be edited in place:

```
+$100 USD  →  brokerage_cash             [RECONCILED, locked — unchanged]
-$115 USD  ←  dividend_payer             [revised: $100 → $115]
+$14  USD  →  user_foreign_tax_paid       [added]
+$1   USD  →  user_adr_fees_paid          [added]
```

Per-instrument balance: `+100 - 115 + 14 + 1 = 0` ✓. The reconciled asset-account leg is byte-identical to what was imported.

### Phase 3 — year-end 1099 reclassifies the dividend

The 1099-DIV reveals that $65 of the dividend was return-of-capital and $50 was an ordinary dividend. The user further refines T1:

```
+$100 USD  →  brokerage_cash                  [RECONCILED, locked — unchanged]
-$50  USD  ←  ordinary_dividends_payer        [reclassified portion]
-$65  USD  ←  return_of_capital_payer         [reclassified portion]
+$14  USD  →  user_foreign_tax_paid            [unchanged from Phase 2]
+$1   USD  →  user_adr_fees_paid               [unchanged from Phase 2]
```

Per-instrument balance: `+100 - 50 - 65 + 14 + 1 = 0` ✓. The brokerage_cash leg is still locked at +$100; the counterparty side has been split into two accounts to reflect the 1099's character determination.

At each phase, the broker's deposit confirmation continues to reconcile cleanly. The user's interpretive labeling gets richer with each new source of information, and accumulates into category-specific tracking accounts (`user_foreign_tax_paid`, `ordinary_dividends_payer`, etc.) that produce useful reports without any model-level changes.

## Decision

Adopt **asset-account-only reconciliation** (Scope C):

> A `TransactionLeg` is **reconciled** (immutable) when it represents a fact confirmed by an external source — specifically, when its account is one of the user's broker-reported asset accounts (brokerage cash, brokerage holdings) AND it was automatically imported from a broker statement / CSV / automatic transaction download, or matched against such a source after manual entry. All other legs of the same Transaction are editable.

### One CSV row → multiple legs, but only one is reconciled

A single row in a broker CSV often contains many monetary components: principal + commission + regulatory fees + tax withholding, etc. The resulting ledger Transaction has one leg per component:

```
Broker CSV row: "BUY 100 AAPL @ $175.50, $0.50 commission, $0.06 SEC fee"
                Total debited from brokerage cash: $17,550.56

Resulting Transaction T1:
  -$17,550.56 USD  ←  brokerage_cash                  [RECONCILED — asset account]
  +100        AAPL  →  brokerage_holdings              [RECONCILED — asset account]
  +$17,550.00 USD  →  external_counterparty           [NOT reconciled]
  +$0.50      USD  →  user_commissions_paid            [NOT reconciled]
  +$0.06      USD  →  user_regulatory_fees_paid        [NOT reconciled]
```

Even though the commission and regulatory fee numbers came from the same CSV row, **those legs are NOT reconciled** because their accounts are not broker-reported asset accounts. Only the brokerage_cash and brokerage_holdings legs are reconciled (locked). The user remains free to recategorize commissions, fees, and counterparty labels later as additional information arrives — that's the whole point of Scope C.

The `ImportLine` (per ADR-0025) FKs to one of these reconciled legs — typically the brokerage_cash leg — to represent "this CSV row produced this asset-side flow." Multi-asset-account CSV rows (a single line that touches both cash and holdings, like the AAPL buy above) match one leg per `ImportLine`; the brokerage importer creates either one `ImportLine` per asset leg, or one `ImportLine` with a many-to-many to multiple legs. That implementation choice is settled in ADR-0025.

### The entire reconciliation system lives in `django_assets.brokerage`

Per ADR-0020, core ships only numeric integrity. Reconciliation is workflow policy — it tracks claims about external sources, ownership of statements, manual attestations. None of that is integrity. **All reconciliation models, the linkage FK, and the lock enforcement live in the brokerage sub-package. Core's `TransactionLeg` has no reconciliation field at all.**

### The FK points from brokerage to core's `TransactionLeg`

The reconciliation linkage is a brokerage-side model with a FK to `django_assets.TransactionLeg`. This is consistent with ADR-0020 — brokerage knows about core (it depends on core); core does not know about brokerage.

The query direction inverts what an earlier sketch of this ADR proposed. Instead of "leg has a `reconciled_by` FK," the relationship is "a reconciliation record on the brokerage side knows which leg it reconciles." Asking "is this leg reconciled?" becomes a reverse-relationship query (`leg.reconciliation_lines.exists()`, per ADR-0026's M2M), and "what source reconciled it?" follows the reverse relation.

A sketch of the brokerage models is in ADR-0025 (broker download lines and matching workflow). The exact shape — whether reconciliation lives in a single `ImportLine` model, a separate `Reconciliation` linkage model, or both — is resolved in ADR-0025. ADR-0024 establishes only the principle: reconciliation is a brokerage concern, the FK points from brokerage to core's `TransactionLeg`, and core itself is unchanged.

### Two paths produce reconciliation — both require a broker import

A leg becomes reconciled in one of two ways, and **both require the existence of an actual broker import line** (an `ImportLine` row in the brokerage sub-package created from a structured source — CSV, OFX, QFX, automatic transaction download feed, etc.). There is no "user attestation" path: if a structured import doesn't exist, the leg simply remains unreconciled.

1. **Automatic import.** The asset-side leg is created by a brokerage importer at the same time it creates the `ImportLine`. The importer adds the leg to the `ImportLine.matched_legs` M2M as part of the import operation. Reconciliation is established as a side effect.

2. **Manual match against an existing import line.** The user previously entered the transaction by hand. Later, an import lands that includes a corresponding `ImportLine`. The user (or a matcher process) adds the existing leg to the `ImportLine.matched_legs` M2M, establishing the reconciliation after the fact.

Both paths result in the same observable state: an `ImportLine` exists with the leg in its `matched_legs`. When `django_assets.brokerage` is installed, the leg is locked from edits and deletion as long as it remains in any `ImportLine.matched_legs`.

### Legs without a corresponding broker import remain unreconciled

A user can still enter a transaction manually — e.g., they have only a PDF September 2025 broker statement, or they remember a cash deposit but can't import it from anywhere. The transaction is recorded, the balance trigger validates it, the ledger continues to work correctly. The asset-side leg simply has no `ImportLine` linkage, so it is not reconciled and remains freely editable.

Reconciliation is a claim about external structured evidence. If no such evidence exists, the leg can't be reconciled. The user lives with that and accepts the lower confidence level for those entries — that is the correct semantic. A "user attestation" path would let users assert reconciliation without underlying evidence, which dilutes the meaning of the reconciliation flag.

### Rationale

1. **Matches what the source actually confirms.** A broker statement attests to deposits and withdrawals from the user's brokerage account. It does NOT attest to the user's labeling of the offsetting side ("dividend_payer" vs "ordinary_dividends_payer", "external_counterparty" vs more specific counterparty accounts). Locking the asset-side leg is honest about what the source guarantees; locking more is overreach.

2. **Matches how disclosure works in practice.** Foreign tax disclosures, ADR sponsor advices, 1099 reclassifications, K-1 schedules, and broker statement reconciliations all arrive AFTER the initial import. They refine the counterparty side; they never contradict the asset-side fact.

3. **Keeps the ledger single-transaction-coherent.** Under this scope, the complete record of one logical event (e.g., one dividend payment) lives on one Transaction. Reports and queries don't need to chase relationships across multiple Transactions to assemble the full picture.

4. **Doesn't preclude separate adjustment Transactions when they're appropriate.** If the user prefers an audit-trail-style approach where each disclosure is its own Transaction, they can use that pattern under this scope — it's just no longer the only option.

5. **Leg-level FK is queryable and explicit.** "Which legs are reconciled YTD?" and "what source reconciled this leg?" are first-class queries against the foreign key, not derived properties or content-type lookups.

## Resolved sub-questions

### 1. How is "broker-reported" tracked on a leg?

Two pieces:

**What makes an account broker-reported?** The `AccountProfile.allows_reconciliation` boolean flag (per ADR-0014, added 2026-06-09). Set at account setup, this gates whether any leg touching the account is eligible for reconciliation. A leg "touches a broker-reported account" iff `leg.account.brokerage_profile.allows_reconciliation` is True. The flag can be toggled off only when no current reconciliation linkages reference legs on the account.

**How is reconciliation state itself tracked on a leg?** Via a brokerage-side reconciliation record that FKs to `core.TransactionLeg`. Core's `TransactionLeg` has no reconciliation field; the relationship is established and queried in the reverse direction (`leg.reconciliation_lines.exists()` per ADR-0026's M2M).

### 2. Where does the reconciliation model live and what shape does it take?

Resolved at the principle level by this ADR (in `django_assets.brokerage`, FK toward core, FK directly on `ImportLine`). The precise model shape is the subject of ADR-0025; the leading proposal there is a single `ImportLine` model with a `kind` discriminator that handles broker-CSV, automatic-download, manual-match, and user-attestation paths uniformly.

What's settled here:

- The FK target is `django_assets.TransactionLeg`.
- The model lives in brokerage, not core.
- The FK lives directly on `ImportLine` (`ImportLine.matched_leg`), not on a separate linkage model.
- The `matched_leg` always points at an asset-account leg of the resulting Transaction. Non-asset legs (commission tracking, fee tracking, counterparty) are not reconciled, even when the same broker CSV row contributed the commission/fee numbers used to construct them.

### 3. Is the lock enforced? How does it work?

**Enforced as a workflow gate, not as a hard prohibition.** The reconciled state is a switch the user must explicitly flip off ("unflip") before edits or deletion are allowed. Concretely:

- The brokerage sub-package installs `pre_save` and `pre_delete` signal handlers on `core.TransactionLeg` via its `AppConfig.ready()`.
- The `pre_save` handler raises if a leg with any matching brokerage-side reconciliation record has its `amount`, `account`, or `instrument` modified.
- The `pre_delete` handler raises if a leg with a reconciliation record is deleted.
- Unflipping (deleting or clearing the brokerage-side linkage) is what opens the gate.

The intended workflow:

1. To edit or delete a reconciled leg, the user (or host code) first removes the brokerage-side reconciliation linkage.
2. With the leg now unreconciled, edits and deletion proceed normally.
3. Later, the leg can be re-reconciled by creating (or restoring) a brokerage-side linkage.

Advisory-only enforcement was rejected because it would mean "this leg is reconciled but you can ignore that," which provides no real integrity benefit.

If `django_assets.brokerage` is NOT installed in `INSTALLED_APPS`, no signal handlers are wired and there is no lock at all. Core itself has no opinion on reconciliation.

### 4. Can a reconciled leg be unlocked? What happens to the source?

Yes: remove the brokerage-side linkage that ties the leg to its source. The source row itself (the `ImportLine` or equivalent) is not deleted by this — it remains in brokerage's records and returns to the unmatched pool, available for re-matching against a different leg if the original reconciliation was a mistake.

Typical scenarios:
- Broker issued a corrected statement; the original reconciliation is now stale. User unflips the leg, edits as needed, then re-reconciles against the corrected source.
- Initial match was wrong (importer matched the wrong row to the wrong leg). User unflips, fixes the leg, re-matches correctly.
- The leg was reconciled by mistake (user clicked the wrong button). Unflip restores the leg to fully-editable state.

### 5. What about multi-leg statement rows?

A single statement line can correspond to multiple reconciled legs (an internal transfer between two of the user's accounts produces two legs, both of which are broker-reported by the same statement line). The brokerage-side linkage is one record per leg; multiple linkage rows can reference the same source row (e.g., the same `ImportLine`). Whether this is modeled as multiple `Reconciliation` rows pointing at one `ImportLine`, or via a M2M, is a brokerage-implementation detail covered by ADR-0025.

### 6. Do non-broker imports also create reconciled legs?

Yes, by the same mechanism. The asset-account distinction is the dominant case for retail brokers, but the same principle applies to bank statement imports (cash deposits and withdrawals), crypto exchange CSV imports (trades and balances), and automatic transaction-download feeds. Each creates brokerage-side reconciliation records with an appropriate `kind`.

### 7. Bidirectional state — broker-download lines and the unmatched pool

For broker downloads, the user wants to keep raw rows around as evidence and to surface a queue of "unmatched broker lines" awaiting reconciliation. The bidirectional matched/unmatched view follows naturally from the brokerage-side reconciliation linkage:

- An import line is **matched** when a reconciliation linkage row exists pointing it at a leg.
- An import line is **unmatched** when no such linkage exists.

This is purely a brokerage concern — core has no `ImportLine`, no `Reconciliation`, no awareness of the workflow. The full design of `ImportLine` and the reconciliation linkage lives in ADR-0025.

## Consequences

**Easier:**

- Disclosure-arrival workflows (foreign tax, 1099 reclassification, K-1, late-fee disclosures) become single-Transaction edits rather than multi-Transaction adjustment chains. Reporting against the ledger is simpler.
- The complete record of a logical event lives on one Transaction, queryable without joins.
- Brokerage helpers for disclosure can do their work via a clean "edit T1, add/modify non-reconciled legs" API.
- **Core stays unchanged.** `TransactionLeg` has no new fields; no new model in core. Adopters who don't install brokerage get no reconciliation locking at all, which is correct — reconciliation is a brokerage concern.
- Reconciliation policy can evolve independently of core. Future brokerage versions can change the linkage model, add new `kind` values, or introduce new reconciliation paths without touching the core schema.

**Harder:**

- "Is this leg reconciled?" is a reverse-relationship query rather than an attribute access. Requires `select_related` / `prefetch_related` discipline for performance in admin and report views.
- The brokerage signal handlers that enforce the lock have to query their own models from inside `pre_save`/`pre_delete` on a core model. The signal wiring is a bit non-obvious — the signal is on a core model, but the handler logic and data all live in brokerage.
- The admin UX needs to distinguish editable from locked legs visually — Django admin doesn't support per-row read-only fields out of the box, so this requires custom admin code on `TransactionLeg` and on inline admins.
- Importer code (in brokerage and any host-built importers) must create the reconciliation linkage records during imports. This is a small but unavoidable bookkeeping step.
- Errors in reconciliation (mismatched legs, importer bugs) are stickier because of the unflip workflow — fixing a wrong match requires explicitly removing the linkage first. The act is deliberate and recoverable, but it is not silent.

**Deferred (to other ADRs / implementation):**

- The disclosure-workflow design itself — this is ADR-0023's scope. ADR-0024 only answers "what does reconciliation lock?" not "where does disclosure information get captured?"
- The exact reconciliation model shape in brokerage — `ImportLine`, `Reconciliation` linkage, or both — is the subject of ADR-0025.
- The set of recommended `kind` values (`"broker_csv"`, `"broker_qfx"`, `"auto_download"`, `"bank_csv"`, `"crypto_exchange_csv"`, etc.) — to be documented by the brokerage sub-package alongside its importers.

## Related

- ADR-0014 (Account capability flags) — defines `AccountProfile.allows_reconciliation`, the flag that determines whether legs touching an account are eligible for reconciliation. Amended 2026-06-09 to add this field.
- ADR-0019 (Bulk import primitives; import management) — establishes `ImportBatch` and `TransactionImport`. Reconciled legs would be marked at the time the import creates them.
- ADR-0021 (Brokerage templates follow the source's transaction shape) — the principle that recordings must match what the source confirmed; this ADR sharpens that to "match the asset-side ground truth specifically."
- ADR-0022 (No append-only enforcement) — Scope C doesn't conflict; mutation of non-reconciled legs is allowed by design.
- ADR-0023 (Disclosure transactions) — if Scope C is accepted, Approaches 7 and 8 in ADR-0023 become viable. If Scope A or B wins, those approaches are off the table.
- ADR-0028 (Transaction provenance) — specifies the dedup mechanics for Path 2 (manual match against a later-arriving import). Still Proposed; see Status note.
