# Build the first real price connector (MarketData.app)

> Hand this whole file to your agent. Setup first: `cp .env.example .env` and paste your
> MarketData.app token (`.env` is gitignored). The connector and the MarketData Python SDK
> both read `MARKETDATA_TOKEN` from the environment — never hard-code it.

## The goal

`django-assets` is a **library** that stores no prices — it defines a read-time `PriceSource`
protocol and ships only reference stubs (`StaticPriceSource`, `CachedPriceSource`). Every real
price comes from a connector that lives *outside* core (ADR-0034: "real providers are host or
sibling implementations"). Right now there is no real one. Build the first: a **MarketData.app
connector** that supplies live equity and options market data — quotes, historical candles,
and option chains with greeks and IV — to everything in the library that values a position.

This is the same kind of exercise as the option-tracker build: a real integration that tests
whether the library's price contract actually holds up against a live vendor. Two things come
out of it, and both matter:

1. **The v2 price contract, implemented in core.** The shipped code is still the ADR-0034 v1
   shape (`get_price` / `get_prices`). **ADR-0039** ("Price contract v2 — quote kinds,
   capability discovery, bounded history") is written but only *Proposed*. Building a real
   connector is the forcing function to finish it: implement v2 in `django_assets/core`
   (`capabilities()`, `get_quote(kind)`, `get_quotes`, `get_close(on)`, `get_ohlcv(start,
   end, resolution)`; the `REALTIME | DELAYED | EOD` quote kinds; `DAY | WEEK | MONTH`
   resolutions; the per-instrument `DateRange` bound), update the reference stubs to match,
   and move ADR-0039 from Proposed to Accepted per this repo's ADR conventions. If the real
   connector reveals the v2 contract itself is wrong or incomplete, that's a finding — fix the
   contract, don't contort the connector around it.
2. **The MarketData connector**, as a sibling package outside core — implementing the v2
   protocol, depending on `django_assets` (never the reverse), and shipping nothing back into
   core's provider-free surface.

Everything you need about the vendor is on this machine — use it instead of the public web:
the MarketData Python SDK and a condensed LLM reference at
`/home/selden/MarketDataApp/documentation/llm-docs/{sdk-py.md,api.md}`, the OPRA options feed
under `/home/selden/MarketDataApp/opra/`, and full SDKs in six languages under
`/home/selden/MarketDataApp/`.

I'm not going to tell you how to structure the client, the symbol mapping, or the caching —
you're better at that than I am. Find the best way there. Where the library or the v2 contract
falls short, that's the point; log it and fix it in the library, per the house rules.

## House rules (never cross these, however you get to the goal)

1. **The library never stores prices, and core stays provider-free.** The connector is
   read-time only (ADR-0034) — it never persists a price into the ledger, and nothing about
   MarketData ever gets imported by `django_assets/core`. Core depends on nothing; the
   connector depends on core.
2. **Decimal only — a float that reaches core fails loudly.** The vendor returns JSON
   numbers / floats. Convert at the connector boundary via the same intake guard the library
   uses (`to_decimal`); never let a float-derived value flow into a `PriceQuote`. This is
   PADR-0006 (type hints and no float) and it is not negotiable.
3. **`None` means unpriced, honestly — and `capabilities()` tells the truth.** Never fabricate,
   interpolate, or zero a price the vendor can't give. Capability discovery must reflect the
   *actual* entitlement the token has (realtime vs delayed, options access, history depth) —
   discovered or configured, never hard-coded optimism. A consumer must be able to ask "can I
   chart this / show a live tile / only a stale badge?" and get a true answer before rendering.
4. **Whatever builds something never grades it.** When you think the connector is done, spin
   up a *separate sub-agent with a fresh context window*, point it at the live vendor and the
   running library (real API calls, real values — not the code's own tests), and tell it to
   **prove the connector is wrong**: a value that disagrees with the vendor, a float that
   slipped through, a capability that lies, a bound that's off. The build agent will justify
   its own work; the grader has no such trajectory. Only the grader's verdict counts.
5. **Match the repo's conventions and its bar for "done."** Follow the ADRs — TDD (0001),
   no-float money (PADR-0006), migration conventions (0008), definition-of-done (0010) — and
   the ADR process for turning ADR-0039 Accepted. Nothing merges unless the full suite passes
   and the build stays green.

## The bar for "done" — and you invent the measuring stick

No adjectives. "Robust connector" is not something you can check yourself against. The bar:

> **1. Contract-true:** the connector satisfies every v2 method's semantics exactly — kinds
> badged right, `None` for unpriced, Decimal throughout, history clipped to the discoverable
> bound, EOD resolved with the vendor's trading-calendar (never caller-computed).
>
> **2. Vendor-true:** every value the connector returns matches what MarketData actually
> serves for that symbol at that moment — equities quotes and candles, and option contracts
> with their greeks and IV.
>
> **3. Alive end to end:** a real position values through the library with real data — plug it
> into the option-tracker app (the other prompt) so a real symbol's dashboard shows live
> prices and greeks; or, if that app isn't built yet, a runnable demo that values a real
> multi-leg option position through `django_assets` using this connector.

I don't know the best way to *measure* "vendor-true," so that's your problem too. Invent the
measuring stick — e.g. a differential harness that pulls the same symbols straight from the
MarketData API and through the connector and asserts they agree; a contract-conformance suite
that exercises every method against the fixed vocabulary; recorded-fixture replay so the suite
runs without burning quota on every loop. Whatever you build has to be concrete enough that the
fresh-context grader in house-rule #4 can run it and return pass/fail, not an opinion.

## Loop until it hits the bar

Put yourself on a loop: implement a slice → have the fresh-context grader try to prove it
wrong against the live vendor → close the biggest gap → grade again. You don't get to decide
you're finished; there's always a next gap (an untested kind, an entitlement edge, a symbol
class that maps wrong). Stop when the grader can't break it, or when I say stop. Keep a
progress doc on Workbench.md — what's contract-true, what's vendor-verified, what's still open.

## Build on what's already here

- Read `django_assets/core/prices.py` (the v1 code you're evolving), **ADR-0034** (storage
  posture, naming, contract rules — still authoritative) and **ADR-0039** (the v2 contract
  you're implementing), and `docs-internal/adr/product/0034`, `.../0039`.
- Read the historical `docs-internal/historical/django_assets_core_price_connectors_guide.md`
  for the intended connector pattern (vocabulary is older, but the shape holds).
- Read the MarketData Python SDK and `llm-docs/sdk-py.md` before writing client code — use its
  `stocks.quotes` / `stocks.candles` / `options.chain` / `options.quotes` methods rather than
  hand-rolling HTTP, unless you find a concrete reason not to.
- When you change the library or the contract, change it *as a library author would*: a clean,
  general API that fits the existing `…Source` family, with tests and updated ADRs — not a
  bolt-on that only MarketData needs.

## Get out of your own way

Make your own calls; only come back to me if you're truly blocked or hit something only I can
decide (a contract-design fork, spending real money, deleting real data).
- The only credential is `MARKETDATA_TOKEN` in `.env`. Read it from the environment; the SDK
  does this automatically. Never paste it into code, tests, fixtures, or the progress doc.
- **API calls are metered — treat quota as a budget.** Record fixtures once and replay them in
  the loop; don't hammer the live API on every grade. Reserve live calls for the vendor-true
  verification passes. If you're unsure of the plan's entitlement limits, ask me up front.
- Don't ask permission per step. Batch your questions.

**The one gate:** this is foundational — you're finalizing a core contract *and* landing the
first real provider. Before you write code, give me a plan: how you'll implement v2 in core and
migrate the reference stubs; where the connector package lives and how it stays out of core;
your symbol-mapping approach (equities and OCC-style option symbols); how `capabilities()`
discovers real entitlements; and how the measuring stick (differential + conformance +
fixtures) works. Flag the ADR-0039 design decisions you foresee — those are the consequential
ones. Once I sign off, run without stopping.

## How to run it

Engineering mode: you can split the work — one track lands the v2 contract and migrates the
stubs in core; another builds the MarketData client and mapping; another builds the measuring
stick. Each opens a PR with the grader's evidence attached. One integrator sub-agent merges,
runs the full suite, exercises the connector against the live vendor like a real consumer, and
keeps everything green. Where the contract and the connector must agree, have one watch the
other's traces and stay compatible.
