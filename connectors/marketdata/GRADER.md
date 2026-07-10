# Grading the MarketData connector (phase 1)

You are grading, not building. Your job is to **prove this connector
wrong** against the LIVE vendor. Only concrete failures count — a value
that disagrees with what MarketData actually serves, a float that
reaches library types, a capability that lies, a bound that's off, EOD
resolved without the vendor calendar.

## The bar (from prompts/GOAL.md)

1. **CONTRACT-TRUE** — ADR-0039 v2 semantics exact: kinds badged right,
   None = unpriced, Decimal throughout, history clipped to the
   discoverable bound, EOD via the vendor calendar.
2. **VENDOR-TRUE** — every value equals what MarketData serves for that
   symbol at that moment.
3. **ALIVE** — a real multi-leg option position values through
   django_assets via the connector.

## Setup

- Postgres: `make up`
- Token: already in `.env` (`MARKETDATA_TOKEN`); the live suites load it.
- API is METERED: one full live pass costs ~60–150 credits (limit
  100,000/day) — run the live suites a handful of times, not in a loop.

## The measuring stick

1. Offline (no quota, scripted vendor with recorded shapes):

       uv run pytest connectors/marketdata/tests -q

2. Live differential + conformance-against-live + ALIVE demo:

       uv run pytest connectors/marketdata/verify/live_differential.py \
                     connectors/marketdata/verify/live_alive.py -v

3. Full repo gate: `make check` (lint, mypy, whole test suite).

## What to attack beyond the shipped checks

The shipped harness is the builder's; distrust it. Ideas:

- Pick YOUR OWN symbols (any listed US stock/ETF; any live OCC
  contract) and compare connector output against raw
  `https://api.marketdata.app/v1/...` calls yourself
  (`Authorization: Bearer $MARKETDATA_TOKEN`; HTTP 203 == 200; parse
  with `parse_float=Decimal`).
- Kind exactness: `get_quote(kind=REALTIME/DELAYED/EOD)` vs
  `capabilities()` for stocks AND options, during and outside market
  hours. capabilities().realtime for equities is claimed ONLY after the
  prices channel is observed fresh during regular hours — check its
  consistency with behavior, both states.
- Bounds: `capabilities().closes/.ohlcv` vs reality — request beyond
  them, at them, holidays (2026-01-01, 2026-07-03), weekends; verify
  `get_close(bound.min)` answers and earlier dates don't.
- Decimal purity: walk every returned object (quotes incl. greeks,
  candles) — `isinstance(x, Decimal)`; no float anywhere.
- Aggregation: WEEK/MONTH bars must equal ADR-0039 §5 aggregation of
  the daily sessions (aggregate_candles); complete weeks should agree
  with the vendor's native `W` closes.
- Options: synthesize OCC symbols from OptionMeta rows you create
  (strike with fractional mills must be unpriceable); expired contracts
  must not claim live kinds but still serve dated closes.
- Instruments the vendor can't know: no identifiers, EUR-priced,
  currencies → capabilities None, quotes None. Batch `get_quotes` mixing
  good/bad symbols.
- The ledger path: build your own multi-leg position (TransactionBuilder)
  and value it via `Portfolio.value(account, source)` — no unpriced
  leftovers, totals exactly Σ quantized(qty × price × multiplier).

## Verdict format

Report PASS or FAIL per bar (contract-true / vendor-true / alive), with
a reproducible command + observed-vs-expected for every failure. No
opinions — only evidence.
