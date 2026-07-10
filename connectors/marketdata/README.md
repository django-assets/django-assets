# django-assets-prices-marketdata

The first real `PriceSource` connector for
[django-assets](../../README.md), backed by [MarketData.app](https://www.marketdata.app).
Implements the ADR-0039 v2 price contract for US equities and options:

- `capabilities(instrument)` — probe-discovered, never optimistic:
  equities realtime is claimed only after the vendor's realtime channel
  is observed fresh during market hours; the delayed entitlement is
  classified by dating the probe quote to the current session; options
  entitlement comes from the vendor's own `x-options-data-permissions`
  header. History bounds are discovered per instrument (year-bisection
  over the candle archive; the option EOD-quote series).
- `get_quote` / `get_quotes` — REALTIME (`/stocks/prices`), DELAYED
  (`/stocks/quotes`, `/options/quotes` — options carry greeks/IV as
  `OptionQuote`), EOD (official close via `/stocks/candles` + the vendor
  trading calendar; the vendor's EOD quote row for options). A quote's
  price is the vendor midpoint (mark), falling back to `last`.
- `get_close(on)` — exact-session closes, `None` for non-sessions and
  out-of-bounds dates.
- `get_ohlcv(start, end, resolution)` — daily candles clipped to the
  discovered bound; weekly/monthly aggregated per ADR-0039 §5. Options
  honestly report no bar archive (`capabilities().ohlcv is None`).

Decimal-pure by construction: payloads are parsed with
`json.loads(..., parse_float=Decimal)` — the vendor SDK is deliberately
not used because it parses through `float` (PADR-0006).

Token: `MARKETDATA_TOKEN` in the environment (or `token=`). The library
never stores prices; every cache here is in-process and read-time only.

```python
from django_assets_prices_marketdata import MarketDataPriceSource
from django_assets.core.queries import Portfolio

source = MarketDataPriceSource()
print(Portfolio.value(account, source))
```

Verification lives in `verify/`: a differential harness that replays the
same questions against the raw vendor API and asserts agreement, and a
demo that values a real multi-leg option position through the library.
Tests replay recorded shapes through a scripted vendor — no quota burned.
