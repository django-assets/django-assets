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
| 7 | Closed-strategy history with per-leg open/close prices, close status, fees | calculate_pnl only (aggregate) | `closed_option_strategies()`; per-leg prices derive from single-instrument fills. The reference DOES show per-leg prices for combos (grader round-1 corrected an earlier false claim here), so `_events` now merges same-timestamp fills into one market event: combos booked per leg (broker reality) keep combo-level premiums/cohorts while per-leg prices stay derivable |
| 8 | Analytics (win ratio, avg/largest win/loss, per-strategy counts, cumulative series, fees) | Nothing | `strategy_performance()` |
| 9 | Premium calendar (per-day net premium, event counts, W/L of closures) | Nothing | `premium_calendar()` |
| 10 | PnL Flow (symbol → put/call → gain/loss) | Nothing | `pnl_flow()` |
| 11 | Wheel campaigns with premium-adjusted ("true") cost basis | Lots track basis; no premium adjustment view | `wheel_campaigns()`: adjusted = (share cost − option premiums) / shares |
| 12 | Multi-leg combo booking (one fill, several contracts) must allocate every leg to the user side | **Bug**: `assign()`'s user/mirror heuristic counted sibling *asset* legs as cash and flipped legs to the counterparty | Fixed in `trades/models.py` (`_split_position_and_mirror` judges by CASH coherence only) + regression test |
| 13 | Strategy tags for seeded/closed trades | `classify_structure` nets to zero on closed trades → "stock" | ~~app-side workaround~~ → **`reports.classify_trade(trade)`** in the library (live trades: all legs; closed trades: opening-event structure); the seeder now just calls it (grader round-1 flagged the workaround) |

App-side by design (presentation, not domain): strategy slug → display
label mapping ("bull_put_spread" → "Put Credit Spread"), percent/money
formatting, color coding, SVG chart layout, sorting/filter UI state.
`scripts/check_app_thinness.py` mechanically rejects Decimal arithmetic
in this app's views/templates.

Post-UI additions (found while building the screens, resolved in the
library the same day):

| # | Needed by the app | Library had? | Resolution (library) |
|---|-------------------|--------------|----------------------|
| 14 | Wheel "Total PnL" headline + per-campaign absolute PnL + adjusted-cost discount % | Per-campaign ratios only | `WheelCampaign.pnl`, `.adjusted_cost_pct`, `wheel_total_pnl()` |
| 15 | Account Summary total-return percent ("(50.4%)") | No contributions concept | `AccountSummary.contributions` (pure-cash transactions) + `.total_return_pct` |
| 16 | "Option Profit vs Account Value" chart's account-value line | Nothing | `account_value_series()` — daily cash+positions at session closes, carry-forward marks (documented policy) |
| 17 | PnL-flow node totals and share-of-total percentages | Per-(symbol,right,outcome) rows only | `FlowSummary` / `pnl_flow_summary()` with `share_of_total()` |
| 18 | Per-leg fees in history rows | Strategy-level fee total only | `ClosedLeg.fees` (transaction fees pro-rated across touched legs) |

Grader round-1 additions (all resolved in the library):

| # | Needed by the app | Library had? | Resolution (library) |
|---|-------------------|--------------|----------------------|
| 19 | Roll-inclusive PnL%% on rolled position rows (reference info-dot semantics) | Option-side pnl_pct only | `OpenStrategy.pnl_pct_incl_rolls` = (unrealized + Σ rolled realized) / premium incl. rolls |
| 20 | Weekly cumulative-profit buckets (Analytics Weekly toggle) | Monthly + daily only | `PerformanceStats.weekly_profit` (ISO-week Mondays) |
| 21 | Symbol search on Analytics / Calendar / PnL Flow; strategy+date filters on Flow | No filter params | `strategy_performance(underlyings=…)`, `premium_calendar(underlyings=…)`, `pnl_flow_summary(strategies=…, underlyings=…, start=…, end=…)` |
| 22 | History "Assigned" filter | No assignment concept on closed rows | `ClosedStrategy.assigned` (shares moved inside the trade) |
| 23 | Per-leg open/close prices for combos (reference parity) | Net-cash-only combos couldn't price legs | Same-timestamp event merge in `_events` + per-transaction price derivation (`_transaction_events`) |
| 24 | Assigned CSPs in history must show the premium as realized (strike cash = share basis) | Naive Σ event cash counted the strike payment as an option loss | `_option_event_cash()`: assignment/exercise events attribute cash to the opened share position (same policy as calculate_pnl); premium calendar uses it too |
| 25 | History label for assigned trades (whole-trade classification says "stock" once shares arrive) | classify_trade only | `_classify_option_opening()` fallback in `closed_option_strategies` — the label is the option structure at its opening event |

Grader round-2 additions (library surface was extended before this round;
one OPEN performance gap remains):

| # | Needed by the app | Library had? | Resolution (library) |
|---|-------------------|--------------|----------------------|
| 26 | History "Assigned" table (per-assignment rows) | Added this round | `Assignment` / `assignments(user)` |
| 27 | Wheel expanded row (total premium + per-contract lifecycle w/ status) | Added this round | `WheelCampaign.total_premium`, `.history` (`WheelHistoryRow.status`) |
| 28 | Equity Positions page | Added this round | `EquityHolding` (incl. `.pnl`) / `equity_holdings()` |
| 29 | Calendar Month view (Jan–Dec aggregates) | Added this round | `premium_months(user, year, underlyings=…)` |
| 30 | Header toggles PnL ($) / Extrinsic ($); leg close status | Added this round | `OpenStrategy.pnl_incl_rolls`, `.extrinsic_value`; `ClosedLeg.status` |
| 31 | **OPEN**: Month view render cost | `premium_months()` re-derives `closed_option_strategies` once per month (12× per render, ~30 s on the demo book) | Needs a single-pass yearly aggregate in the library; `<!-- GAP -->` marker left in `_calendar_body.html` |
