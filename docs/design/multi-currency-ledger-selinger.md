# The ledger vs. Selinger's multiple-currency accounting

**Status:** assessment, 2026-07-05
**Reference:** Peter Selinger, *Tutorial on multiple currency accounting*,
<https://www.mathstat.dal.ca/~selinger/accounting/tutorial.html>

Selinger's tutorial is the canonical treatment of doing double-entry
accounting honestly across multiple currencies — it is the lineage from
which commodity-ledger tools (ledger-cli, beancount, hledger) derive
their transaction model. This document reviews django-assets' core
ledger against it: where we conform, where we generalize, and where we
knowingly deviate.

The short version: **the core model is the commodity generalization of
exactly what Selinger proposes, enforced as a database constraint
rather than a bookkeeping convention.** One genuine gap exists
(cross-currency realized-gain decomposition), plus two structural
nuances.

---

## 1. Selinger's model in five rules

1. **Currency-specific accounts.** Foreign-currency holdings live in
   accounts denominated in that currency — never as translated values
   in a reference-currency account.
2. **Balance per currency, per transaction.** Every transaction must
   balance *in each currency separately*. Translating everything into
   one reference currency at booking time produces transactions that
   only appear balanced and silently orphans exchange-rate gains.
3. **Currency trading accounts.** Conversions route through special
   accounts that legally hold *mixed* positions (e.g. `+USD 100 −
   CAD 120`). The trading account's revaluation at current rates *is*
   the unrealized gain — no periodic revaluation entries are needed or
   permitted.
4. **Non-conversion transactions are ordinary double entry.**
5. **Realized gains are explicit.** At liquidation, the realized
   gain/loss moves from the trading account to an income account via a
   normal, balanced transaction; FIFO/LIFO/ACB are all valid bases for
   computing it.

And his don'ts: don't translate at booking time; don't force balances
with periodic adjustment entries that aren't themselves balanced
transactions; don't privilege a single reference currency structurally.

## 2. Where django-assets conforms

### The balance rule, as a deferred constraint

Selinger's Rule 2 is our foundational invariant, generalized from
currencies to instruments (ADR-0013: units of value are instruments —
USD, ARS, a share, a bond, and an option contract are all the same
kind of thing to the ledger). Every `TransactionLeg` moves a quantity
of one instrument into one account, and the ledger-balance constraint
trigger requires **Σ quantity = 0 per instrument within every
transaction**, checked deferred at COMMIT. His rule is a convention an
accountant follows; ours is a Postgres trigger that refuses the COMMIT.

The fundamental accounting equation holds by construction in the
closed-system formulation: external counterparties and the
income/expense tracker accounts are ledger accounts like any other, so
*all* accounts sum to zero per instrument, globally and per
transaction — which is `Assets − Liabilities − Capital − Income +
Expenses = 0`, rearranged.

### Currency trading accounts exist — as the external counterparty

Selinger's central invention is an account that holds a mixed-currency
expression. Our `external_counterparty` account is precisely that,
generalized: after buying shares for dollars it holds `+USD −AAPL`;
after a dollar-subaccount journal it holds `+DOLARUSA −USD`. Mixed
positions are not an anomaly to be cleared but the normal state of the
account, exactly as in the tutorial.

Consequently his Rule 3's corollary also holds: **unrealized gains are
never booked.** They are implicit in marking the mixed position to
current prices, which for us is a query-time concern (`Portfolio.at`),
not a ledger mutation.

### Currency-specific holdings

Rule 1 is satisfied in a slightly stronger form: accounts are
multi-instrument, and holdings are tracked per `(account, instrument)`
pair. The Argentine broker corpus is the working proof — pesos, main
dollars, and the foreign-custody dollar subaccount (`DOLARUSA`, its
own instrument, deliberately *not* merged with USD) each reconcile
independently, to the cent, against the broker's own opening/closing
snapshots, across every statement in the corpus.

### Booking in native currency, always

Import schemas record what the document says in the currency it says
it: ARS trades book with `currency=ARS`, dollar-paridad bond trades in
USD, dividends into the subaccount they actually landed in. No
translation happens at booking time anywhere in the codebase — there
is nothing resembling SSAP-20 translation, which is the approach the
tutorial exists to reject.

### No balance-forcing plugs

The only adjustment-like entries in the system are the Home Broker
`ajuste` lines, and they are the opposite of the "periodic adjustment
entries" Selinger forbids: each books a one-cent discrepancy that the
source statement *itself prints* in its own running-balance lines, as
an ordinary balanced transaction citing that evidence. We adjust to
match the document, never to force the equation.

### Realized gains on FIFO

Tutorial §5 endorses FIFO/LIFO/ACB computed at the moment of sale; the
lots engine is a FIFO implementation of that, with conservation
enforced by its own constraint trigger.

## 3. Deviations and gaps

### 3.1 Conversions book as two transactions, not one *(nuance)*

Selinger models a conversion as a single transaction routing both
currencies through the trading account. Our statement imports book the
two sides separately when the source document presents them as
separate rows (e.g. the Argentine subaccount journals: an `EGAJ`
withdrawal of subaccount dollars and its mirroring `NCCD` credit of
main dollars). Each transaction balances per instrument and the
external counterparty ends up holding the same mixed position, so the
*math* is Selinger's — but the structural pairing of the two sides,
and therefore the implied exchange rate, lives only in import-line
evidence and metadata rather than in the transaction graph.

*Cost of the deviation:* none for balances; some for queryability
(one cannot ask the transaction graph "what rate did this conversion
imply" without consulting the import evidence).

### 3.2 Realized *currency* gains are not decomposed *(the real gap)*

Rule 5 wants an explicit realized-FX-gain entry at liquidation. We
derive realized gains from lot matching instead, which is equivalent —
*when basis and proceeds are in the same currency.* For cross-currency
round trips (the Argentine MEP pattern: acquire a bond in pesos, sell
it for dollars, or vice versa), a lot's cost basis and its proceeds
are denominated in different currencies, and the realized-gains report
subtracts them numerically. Selinger's framework would isolate the FX
component of that gain; ours currently blends it into a single number
that is not coherent in either currency.

*Follow-up if this matters:* a realized-FX pass over cross-currency
lot matches — value both sides at the conversion-date rate (which the
source documents supply), split the gain into a security component and
a currency component, and book the currency component per Rule 5.
Until then, cross-currency lot gains should be treated as indicative,
not tax-grade.

### 3.3 One coarse trading account *(resolved)*

The tutorial suggests purpose-specific trading accounts (per customer,
per position) so that FX gain *attribution* survives aggregation.
Originally the ledger ran one `external_counterparty` per user; the
world side is now partitioned into four purpose accounts —
`market_counterparty` (trade mirrors), `owner_funding` (the owner's
own money and property crossing the boundary), `issuer_counterparty`
(dividends, interest in kind, corporate-action deliveries) and
`currency_conversions`, which is Selinger's currency trading account
proper: its per-instrument residue is the unrealized-FX position, and
the realized-FX decomposition of §3.2 reads it directly.

## 4. Verdict

| Selinger | django-assets | status |
| --- | --- | --- |
| Balance per currency per transaction | Σ = 0 per **instrument** per transaction, deferred trigger | conforms (stronger) |
| Currency-specific accounts | holdings per (account, instrument) | conforms (stronger) |
| Currency trading accounts, mixed positions | `external_counterparty` holds mixed instrument positions | conforms (generalized) |
| No booking-time translation | native-currency booking throughout | conforms |
| No unbalanced adjustment plugs | only evidence-backed, balanced `ajuste` bookings | conforms |
| Unrealized gains implicit, never booked | valuation is query-time (`Portfolio.at`) | conforms |
| Explicit realized gains (FIFO/LIFO/ACB) | FIFO lots engine | conforms for same-currency |
| Explicit realized **FX** gains at conversion | blended into cross-currency lot gains | **gap** (§3.2) |
| Single-transaction conversions via trading account | two balanced transactions when the source splits them | nuance (§3.1) |
| Purpose-specific trading accounts | four purpose accounts; `currency_conversions` is the FX register | conforms (§3.3) |
