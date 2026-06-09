# ADR-0012: Transaction settlement and trade timestamps

## Status

Accepted — 2026-06-02

## Context

Every Transaction in the ledger needs a temporal anchor. The choice between modeling options is load-bearing for `Portfolio.at(account, as_of)`, for tax reporting, for matching transactions to market-data candles, and for the simplicity of the ledger's mental model.

Two use cases pull in different directions:

1. **Account balance accuracy.** The transaction's effects on **account balances** materialize at settlement, not at execution. Cash leaves the user's account on settlement date; the share appears in custody on settlement date. Broker statements reconcile cash positions to settlement dates. This is the canonical "when did this transaction take effect in the ledger" moment.
2. **Market-context analytics.** Backtest reconstruction, execution analysis, and matching transactions against daily or intraday market-data candles need to know when the order **executed**, not when it settled. A buy on Tuesday at 10:30 AM ET with T+1 settlement happened during Tuesday's session and should appear in Tuesday's daily candle — but its cash effects don't appear in account balances until Wednesday.

A single timestamp cannot serve both purposes cleanly. Two columns can.

The framing that resolved the question, from design discussion: **what matters to the user is the moment the event happens.** When a user exercises an option, the option contract disappears and the deliverable instruments appear. The internal settlement choreography — OCC's delayed cash settlement window for adjusted contracts, the broker's clearing process, the T+1 mechanics for the strike payment — does not change what the user thinks happened. They exercised the option at a specific moment; the ledger records both that moment (trade) and when its effects landed in balances (settlement).

Every transaction in the system has this property:

- **Buy stock**: order executes at trade time; cash and shares exchange at settlement.
- **Sell stock**: order executes at trade time; cash and shares exchange at settlement.
- **Exercise option**: the holder elects to exercise at trade time; the strike payment, contract write-off, and deliverable receipt land at settlement.
- **Cash dividend**: ex-date / record date is the trade moment; payment date is the settlement.
- **Split, spinoff**: announcement or effective date is the trade moment; positions appear at settlement.

For asset classes without a meaningful settlement window — crypto, internal transfers, cash deposits — the trade and settlement moments are identical, so `trade_timestamp` can be left null without information loss.

The atomic-event framing still applies: one Transaction is one event, validated by the deferred balance trigger from ADR-0004 at COMMIT. The trigger sees all legs of a Transaction simultaneously and enforces per-instrument zero-sum across them. The two-timestamp model does not introduce stages or partial states. Hosts that want to model settlement as a separate event (e.g., a "cash payable" account that clears on settlement date) record a second Transaction for the clearance — same ledger primitives, no schema change.

`Portfolio.at(account, as_of)` keys off the settlement timestamp because that is when holdings actually exist in custody. Tax-lot tracking and candle matching key off the trade timestamp when it is present.

## Decision

### Schema

```python
class Transaction(models.Model):
    id = models.BigAutoField(primary_key=True)
    timestamp = models.DateTimeField(db_index=True)
    """Settlement timestamp. REQUIRED. When the transaction's effects materialize
    in account balances — the canonical ledger time. Used by Portfolio.at(account,
    as_of) for holding aggregations and by cash-balance queries."""

    trade_timestamp = models.DateTimeField(null=True, blank=True, db_index=True)
    """Execution timestamp. OPTIONAL. When the underlying action that triggered
    this transaction actually occurred. For trades, when the order executed.
    For exercises, when the holder elected to exercise. For corporate actions,
    optionally the ex-date or effective date.

    When null, callers that need execution time should fall back to timestamp.
    When non-null, this is what backtest tools, candle-matching analytics, and
    tax-lot calculations should use."""

    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    # ... account FK, other fields
```

Both columns are `DateTimeField` (TIMESTAMPTZ in UTC, per the global timezone requirement). `timestamp` is required and indexed. `trade_timestamp` is nullable and indexed (the index supports candle-matching queries and IRS holding-period queries).

### Semantics of `timestamp` (settlement)

- For trades: settlement date. T+1 for US equities and options as of May 2024.
- For cash deposits/withdrawals: the date the cash actually moved between accounts.
- For option exercises: the settlement date of the resulting cash and share flows.
- For dividends: payment date.
- For corporate actions that produce account-balance changes (splits, spinoffs, mergers): the date the new positions are reflected in custody.
- For internal transfers and crypto trades: typically the execution moment, since settlement is essentially instantaneous.

### Semantics of `trade_timestamp` (execution)

- For trades: when the order executed on the exchange. Used to match against daily/intraday market-data candles.
- For exercises: when the option holder elected to exercise. Used to look up the deliverable in force at the moment of exercise (relevant if an OCC adjustment is effective between exercise and settlement).
- For dividends: optionally the ex-date.
- For corporate actions: optionally the effective date.
- For instant flows (crypto, cash deposits, transfers): may be null, or set equal to `timestamp` to indicate "execution and settlement are the same moment."

### How API consumers choose between the two

| Use case | Field to use |
| --- | --- |
| `Portfolio.at(account, as_of)` holdings | `timestamp` |
| Cash balance at a given moment | `timestamp` |
| Broker statement reconciliation (cash columns) | `timestamp` |
| Matching against daily/intraday market data candles | `trade_timestamp` if non-null, else `timestamp` |
| IRS holding-period calculations (long-term vs short-term) | `trade_timestamp` if non-null, else `timestamp` |
| Deliverable lookup for option exercise | `trade_timestamp` if non-null, else `timestamp` |
| Tiebreaker for transactions with identical `timestamp` | `(timestamp, id)` ordering — the `id` (BigAutoField) breaks ties by insertion order |

### Atomicity is preserved

A Transaction is still a single atomic event from the deferred balance trigger's perspective. The trigger fires at COMMIT and validates per-instrument zero-sum across all the transaction's legs in one shot. The two-timestamp model does not introduce stages or partial states. Hosts that want to model settlement as a separate event (e.g., a "cash payable" account that clears on settlement date) still record a second Transaction for the clearance.

### Worked example: AAPL buy with T+1 settlement

```python
Transaction(
    timestamp=datetime(2024, 3, 13, 20, 0, tzinfo=UTC),       # Wed 4pm ET, settled
    trade_timestamp=datetime(2024, 3, 12, 14, 30, tzinfo=UTC),  # Tue 10:30am ET, executed
    description="Buy 100 AAPL @ 175.50",
)

Legs (all at the parent transaction's timestamp from the trigger's perspective):
  -$17,550 USD from user_cash
  +100 AAPL to user_brokerage
  +$17,550 USD to user_external
  -100 AAPL from user_external
```

Query behavior:

- `Portfolio.at(account, datetime(2024, 3, 12, 23, 0, tzinfo=UTC))` → returns holdings as of Tuesday evening. Does NOT include the AAPL (timestamp = Wednesday).
- `Portfolio.at(account, datetime(2024, 3, 13, 21, 0, tzinfo=UTC))` → includes the 100 AAPL and the cash debit.
- Backtest tool matching this trade against daily candles: `trade_timestamp.date() = 2024-03-12` → matches Tuesday's candle, correct.

### Worked example: option exercise across a corporate-action boundary

Continuing the PFE1 scenario from ADR-0010, suppose a user elects to exercise on 2020-11-17 (the day the OCC adjustment takes effect). The exercise instruction goes in on Tuesday; settlement of the cash leg is delayed per OCC memo #47935:

```python
Transaction(
    timestamp=datetime(2020, 11, 18, 20, 0, tzinfo=UTC),       # T+1 settlement, conventional
    trade_timestamp=datetime(2020, 11, 17, 15, 30, tzinfo=UTC), # Election moment, adjustment effective
    description="Exercise 1 PFE1 contract",
)
```

The deliverable lookup uses `trade_timestamp = 2020-11-17`. Per ADR-0010's half-open convention (`effective_from` inclusive, `effective_to` exclusive), the NEW deliverable rows are active on 2020-11-17 — the exercise produces the basket (100 PFE + 12 VTRS + $6.47), not the old single-instrument deliverable. Correct.

If `trade_timestamp` were null, the helper would fall back to `timestamp` (2020-11-18), which is after the cutover — same result in this case. The two-timestamp model gives the right answer regardless; the optional field exists for cases where execution and settlement straddle a boundary.

### What is NOT in the schema

- No bitemporal columns (`recorded_at`, `valid_from`, etc.). The ledger remains event-time-keyed; audit reconstruction is left to host-level snapshot or audit-log infrastructure.
- No per-leg timestamps. All legs share the parent Transaction's timestamps.
- No "pending"/"settled" status flag. Settlement-as-a-process is modeled with separate Transactions if a host needs it.

## Consequences

**Easier:**

- Backtest tools, execution analysis, and any analytics that matches transactions to market-data candles get the right answer. The trade landed in Tuesday's session; the analytics see it there.
- Tax-lot tracking calculates IRS holding periods correctly when `trade_timestamp` is populated. Falls back gracefully when it is not.
- Broker statement reconciliation works for the cash side: `timestamp = settlement` matches what brokers report in cash-balance columns.
- Option exercise across corporate-action boundaries gets the correct deliverable, derived from the moment of election rather than the moment of settlement.
- Crypto, internal transfers, cash deposits — anything without a meaningful settlement window — works cleanly: set `trade_timestamp = null` (or equal to `timestamp`), no information lost, no ceremony.

**Harder:**

- Two columns on every Transaction row. The trade-side index adds storage cost; queries that don't need it pay nothing.
- Helper APIs must consistently use the right field. The package's helpers document which they use; misuse by host code (e.g., matching transactions to candles via `timestamp` instead of `trade_timestamp`) silently produces wrong results.
- Brokers vary in what they report. Some statements include both dates; some only one. Import adapters must handle the variation. When only one date is available, populate `timestamp` and leave `trade_timestamp` null.
- The dual-field convention must be documented loudly. Users encountering the schema for the first time will ask "which one do I use?"

**Deferred:**

- Bitemporal columns if regulatory or audit needs demand them.
- Per-asset-class settlement-rule helpers (T+1 vs T+2 vs T+0 derivation from trade timestamp). Hosts compute settlement from broker data; the package does not encode the rules.

## Related

- ADR-0004 establishes the deferred balance trigger that the atomic-event framing relies on.
- ADR-0010 (option contract model) uses `trade_timestamp` for deliverable lookup at the moment of exercise.
- ADR-0011 (core is the ledger) — settlement-aware multi-stage modeling (separate trade and clearance Transactions) is still a host concern, not a core concern.
- OQ-3, OQ-4, and OQ-18 in `open-questions.md` are resolved by this ADR.
