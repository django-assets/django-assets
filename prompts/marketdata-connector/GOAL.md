Read and follow prompts/marketdata-connector/PROMPT.md in full — that file is the complete brief
(house rules, method, setup, on-machine vendor docs). This is the bar the loop measures against.

WHAT: Two joined deliverables. (a) Land ADR-0039's v2 price contract in django_assets/core:
capabilities() / get_quote(kind: REALTIME|DELAYED|EOD) / get_quotes / get_close(on) /
get_ohlcv(start,end,resolution: DAY|WEEK|MONTH) + a per-instrument DateRange bound; migrate the
StaticPriceSource/CachedPriceSource stubs; move ADR-0039 Proposed → Accepted. (b) Build the first
REAL provider — a MarketData.app connector as a sibling package OUTSIDE core — for equities +
options (greeks/IV) + history. Token in .env (MARKETDATA_TOKEN); vendor SDK + docs on-machine at
/home/selden/MarketDataApp/ (use llm-docs/{sdk-py.md,api.md}, not the public web). The connector
is the forcing function that finalizes the contract; if it reveals the contract is wrong, fix the
contract, don't contort the connector.

DONE = all three bars pass, verified by a FRESH-CONTEXT GRADER (separate clean-context agent
pointed at the LIVE vendor + running library, told to prove the connector WRONG — builder never
grades itself; only the grader's verdict counts):

  1. CONTRACT-TRUE — every v2 method's semantics exact: kinds badged right, None=unpriced,
     Decimal throughout, history clipped to the discoverable bound, EOD via the vendor calendar.
  2. VENDOR-TRUE — returned values match what MarketData actually serves (equities quotes/candles,
     option contracts with greeks/IV), proven by a differential harness vs the raw API.
  3. ALIVE END-TO-END — a real multi-leg option position values through django_assets via this
     connector (ideally wired into the option-tracker app).

NEVER CROSS:
  - Library never stores prices; core stays provider-free (core imports nothing of MarketData).
  - Decimal only — a vendor float reaching core fails loudly (PADR-0006).
  - None = unpriced, honestly; capabilities() reports the token's REAL entitlement (this token is
    DELAYED for equities — the API returns HTTP 203), never hard-coded optimism.
  - API is metered: record fixtures and replay them in the loop; reserve live calls for the
    vendor-true verification passes.
  - Builder never grades itself. Follow repo ADRs (TDD/no-float/migrations/definition-of-done);
    full suite green before anything merges.

GATE: before writing code, present a plan — v2-in-core + stub migration; connector package
location & how it stays out of core; symbol mapping (equities + OCC option symbols); how
capabilities() discovers the token's entitlement; and the measuring stick (differential +
conformance + fixtures). Run without stopping after I sign off. Loop until the grader can't break
any bar, or I say stop.
