# GAPS.md — library gaps found while building the option tracker

The running record required by the build brief: what the app needed,
whether `django_assets` had it, and what changed in the LIBRARY (never
papered over in the app). Phase-1 (connector) findings included, since
the app renders through the same contract.

| # | Needed by the app | Library had? | Resolution (library) |
|---|-------------------|--------------|----------------------|
| 1 | Live prices + greeks/IV per option leg | No greeks surface in ADR-0039 v2 as proposed | `OptionQuote(PriceQuote)` with iv/delta/gamma/theta/vega/underlying_price/OI/volume + `PriceCapabilities.greeks` (ADR-0039 §5a amendment); MarketData connector returns them |
| 2 | Dated marks for options (history views) where the vendor has EOD quotes but no bars | Single `historical` bound coupled closes to bars | `PriceCapabilities.historical` split into `closes` / `ohlcv` bounds, `ohlcv ⊆ closes` (ADR-0039 amendment) |
| 3 | Every dashboard number: positions table, per-leg greeks panel, market value, PnL%, initial premium, AROI, moneyness, delta% | No reporting layer at all | `django_assets/trades/reports.py`: `open_option_strategies()` (ADR-0040) |
| 4 | Roll history rows (open/close dates, per-segment premium, realized) + "Premium Incl. Roll" | Rolls exist only as `adjust` events inside a Trade (ADR-0037); no derivation | Cohort partitioning + `roll_segments()`; `premium_incl_rolls = live premium + Σ closed-segment realized` (ADR-0040) |
| 5 | Account Summary card (options/equity/cash split, options PnL, equity PnL, margin estimate, total) | Portfolio.value gives one total; no split, no PnL, no margin | `account_summary()` incl. avg-cost equity basis walk; margin ESTIMATE model documented in ADR-0040 |
| 6 | Option margin (Est.) | Nothing | Display-grade estimate in reports: defined-risk width (verticals/condors), strike×100 (naked puts), 20% notional (naked calls), 0 (covered), premium (debit) — ADR-0040 |
| 7 | Closed-strategy history with per-leg open/close prices, close status, fees | calculate_pnl only (aggregate) | `closed_option_strategies()`; per-leg prices derive only from single-instrument fills — the ledger stores net cash, so combo legs honestly show None (the reference shows "—" for the same reason) |
| 8 | Analytics (win ratio, avg/largest win/loss, per-strategy counts, cumulative series, fees) | Nothing | `strategy_performance()` |
| 9 | Premium calendar (per-day net premium, event counts, W/L of closures) | Nothing | `premium_calendar()` |
| 10 | PnL Flow (symbol → put/call → gain/loss) | Nothing | `pnl_flow()` |
| 11 | Wheel campaigns with premium-adjusted ("true") cost basis | Lots track basis; no premium adjustment view | `wheel_campaigns()`: adjusted = (share cost − option premiums) / shares |
| 12 | Multi-leg combo booking (one fill, several contracts) must allocate every leg to the user side | **Bug**: `assign()`'s user/mirror heuristic counted sibling *asset* legs as cash and flipped legs to the counterparty | Fixed in `trades/models.py` (`_split_position_and_mirror` judges by CASH coherence only) + regression test |
| 13 | Strategy tags for seeded/closed trades | `classify_structure` nets to zero on closed trades → "stock" | App concern resolved app-side in seeding (classify the opening structure); a library-side `classify_trade(trade)` convenience is a candidate follow-up, not required by any screen |

App-side by design (presentation, not domain): strategy slug → display
label mapping ("bull_put_spread" → "Put Credit Spread"), percent/money
formatting, color coding, SVG chart layout, sorting/filter UI state.
`scripts/check_app_thinness.py` mechanically rejects Decimal arithmetic
in this app's views/templates.
