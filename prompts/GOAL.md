GOAL — build in order: (1) the MarketData price connector, THEN (2) the option-tracker app.
Two full briefs live beside this file; when you reach a phase, read and follow its brief in full:
prompts/marketdata-connector/PROMPT.md, then prompts/option-tracker/PROMPT.md. This file is the
bar the loop measures against. Do NOT start phase 2 until phase 1's grader passes and I sign off.

WHY THIS ORDER: the app must render real prices, greeks, and history — and that data comes from
the connector, which also finalizes the library's v2 price contract the app values through. Build
the foundation before the surface; don't build the app against mock data and rewire it later.

PHASE 1 — FIRST REAL PRICE CONNECTOR (MarketData.app). Follow marketdata-connector/PROMPT.md.
Land ADR-0039 v2 in django_assets/core (capabilities / get_quote(REALTIME|DELAYED|EOD) /
get_quotes / get_close / get_ohlcv(DAY|WEEK|MONTH) + per-instrument DateRange; migrate the
reference stubs; move ADR-0039 Proposed → Accepted). Build the connector as a sibling package
OUTSIDE core for equities + options (greeks/IV) + history. Token in .env (MARKETDATA_TOKEN);
vendor SDK + docs on-machine at /home/selden/MarketDataApp/ (use llm-docs/, not the public web).
DONE (phase 1) = a fresh-context grader pointed at the LIVE vendor cannot break any of:
  1. CONTRACT-TRUE — v2 semantics exact: kinds badged right, None=unpriced, Decimal throughout,
     history clipped to the discoverable bound, EOD via the vendor calendar.
  2. VENDOR-TRUE — values match what MarketData actually serves, proven by a differential harness.
  3. ALIVE — a real multi-leg option position values through django_assets via the connector.

PHASE 2 — OPTION-TRACKER APP. Follow option-tracker/PROMPT.md. A thin Django-templates + HTMX app
on django_assets that reproduces the reference dashboard at OI_URL (login + headless driver in
reference-login.mjs), now showing REAL data through the phase-1 connector.
DONE (phase 2) = a fresh-context grader, app beside the live reference, cannot break either of:
  1. BEHAVIORAL PARITY — someone who knows the reference cannot tell the app from it.
  2. LIBRARY-BACKED & THIN — every on-screen value traces to a django_assets API; no P&L /
     strategy / cost-basis / roll / money logic in views/templates; every gap logged in GAPS.md
     and fixed in the library.

NEVER CROSS (both phases):
  - Builder never grades itself; only the fresh-context grader's verdict counts.
  - Library never stores prices; core stays provider-free. Decimal only — a float reaching core
    fails loudly (PADR-0006). None = unpriced honestly; capabilities() reports the token's REAL
    entitlement (this token is DELAYED for equities — the API returns HTTP 203), never optimism.
  - MarketData API is metered: record fixtures and replay in the loop; reserve live calls for the
    vendor-true verification passes.
  - Follow repo ADRs (TDD/no-float/migrations/definition-of-done); full suite green before merge.

GATE: at the START of EACH phase, present a plan and get my sign-off before writing code; then run
that phase without stopping, looping until its grader can't break any bar. Do not begin phase 2
until phase 1 is done and signed off.
