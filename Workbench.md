# Workbench — MarketData connector build (phase 1)

> Progress doc per `prompts/marketdata-connector/PROMPT.md`. The plan below is the
> phase-1 gate deliverable; the maintainer delegated sign-off ("work 100%
> independently … I trust your judgement", 2026-07-10), so it is self-approved and
> execution continues without stopping. Status log at the bottom.

## Plan

### 1. v2 contract in core (`django_assets/core/prices.py`)

Implement ADR-0039 exactly, plus one amendment discovered by this build (below):

- Vocabulary: `PriceKind(REALTIME|DELAYED|EOD)`, `Resolution(DAY|WEEK|MONTH)`,
  `DateRange(min,max)` (inclusive), `PriceCapabilities(realtime, delayed, eod,
  historical: DateRange|None)`, `Candle(session, o/h/l/c: Decimal,
  volume: Decimal|None)`, `OHLCVSeries(instrument, currency, resolution, source,
  candles)`.
- Protocol: `capabilities(instrument)`, `get_quote(instrument, *, kind=None)`
  (None = best-available realtime→delayed→eod, downgrade visible on `quote.kind`),
  `get_quotes(instruments, *, kind=None)` (vendor batch, never a hidden loop),
  `get_close(instrument, on)`, `get_ohlcv(instrument, *, start, end, resolution)`.
- `PriceQuote.kind` narrows to the fixed vocabulary.
- Reference stubs: `StaticPriceSource` → eod-only, honest capabilities;
  `CSVPriceSource` (new) → the pedagogical full-contract connector from CSV/in-memory
  OHLCV (capabilities derived from data; weekly/monthly aggregated from daily per
  ADR-0039 §5); `CachedPriceSource` → kind-aware keys, `ttl` + `history_ttl`.
- **ADR-0039 amendment (finding from the real connector): option greeks.** The
  option-tracker (phase 2) must render greeks/IV through the library, and ADR-0039
  has no surface for them. Amend ADR-0039 before acceptance: add frozen
  `OptionQuote(PriceQuote)` with optional Decimal fields `iv, delta, gamma, theta,
  vega, underlying_price, open_interest, volume` (None = vendor didn't supply —
  honest absence) and a `greeks: bool` flag on `PriceCapabilities`. Sources MAY
  return `OptionQuote` from the same `get_quote` surface for option instruments;
  no new protocol method. Reference stubs report `greeks=False`.
- Migrate call sites per ADR-0039 §9: `core/queries.py` (`SupportsGetPrice` → v2
  protocol; `Portfolio.value(as_of=None)` → `get_quotes(kind=None)`, dated → per-
  instrument `get_close(on)`), `trades.Trade.calculate_pnl`, `lots.reports.unrealized`,
  tests, dev_project. Pre-1.0, no compat shim (PADR-0012).
- Ship the **conformance suite** in core as a reusable test mixin/factory
  (capabilities honesty, the None ladder, Decimal purity, bounds clipping,
  weekly/monthly aggregation, kind badging) run in-repo against Static + CSV and
  importable by connector packages (ADR-0039 §8).
- Move ADR-0039 Proposed → Accepted (with the greeks amendment recorded in the
  Decision + a dated note), flip ADR-0034's amendment pointer language if needed.

### 2. Connector package — sibling, outside core

- Location: `connectors/marketdata/` in this repo — its own distribution
  `django-assets-prices-marketdata` (src layout, package
  `django_assets_prices_marketdata`), depending on `django_assets` + `httpx`.
  Wired into the root uv workspace as a dev dependency so the repo test suite
  collects its tests; **nothing in `django_assets/` imports it** (enforced already
  by `scripts/check_import_direction.py` — core imports no siblings; connector is
  outside `django_assets/` entirely).
- HTTP layer: thin `httpx` client, NOT the vendor SDK. Concrete reason: the SDK
  parses JSON via `response.json()` → floats, which violates PADR-0006 at the
  boundary; we parse with `json.loads(text, parse_float=Decimal)` so a float never
  exists. Also gives us direct 203/402 handling and fixture recording. The SDK +
  llm-docs remain the endpoint reference.
- Endpoints used: `stocks/quotes` (single+batch via ?symbols=), `stocks/prices`
  (realtime tier), `stocks/candles/{res}/{sym}` (D/W/M native), `options/quotes`
  (current + `date=`/`from,to` EOD history), `options/chain` (batch option pricing
  per underlying; connector-level extra API, not core protocol), `markets/status`
  (trading calendar for EOD/`get_close`), `/user/` + probes for entitlement.
- Error discipline (ADR-0039 §7): vendor `no_data`/unknown symbol/outside
  entitlement → `None`; transport/auth/429/5xx → raise (`MarketDataError`);
  HTTP 203 == 200 (cache tier); 402 = entitlement boundary → capability truth,
  not an exception, where it answers a capability question; raise where it
  interrupts an entitled request.
- Symbol mapping via `Identifier` (ADR-0009): equities = active `ticker`
  identifier; options = active `opra` identifier, else OCC symbol synthesized from
  `OptionMeta` (underlying ticker + yymmdd + C/P + strike×1000, 8 digits) — if
  neither, `capabilities() → None`, never guessed. Currencies / unmappable → None.
- Quote price semantics (documented in the connector + ADR): equities REALTIME =
  `stocks/prices` price; DELAYED = `stocks/quotes` mid (bid/ask midpoint; falls
  back to last when bid/ask absent); EOD = official close from daily candles at
  the last completed session per `markets/status`. Options REALTIME/DELAYED =
  `options/quotes` mid (mark), EOD = the vendor's end-of-day quote (`date=` last
  session), all carrying greeks/IV via `OptionQuote`.
- `capabilities()`: entitlement discovered, never assumed. Lazy one-time probe per
  asset class (cached on the source instance, TTL-able): classify the freshness of
  a live `stocks/quotes` / `options/quotes` response (`updated` vs now vs last
  session) into realtime / delayed / eod-only; `stocks/prices` reachability decides
  equity realtime; 402s mark the boundary. Explicit constructor overrides
  (`equities_entitlement=`, `options_entitlement=`) for hosts that know their plan,
  probe by default. History bound: `min` from the earliest-candle discovery
  (no_data `nextTime` probe, 402-bisect if plan-limited; cached), `max` = last
  completed session from the calendar.
- Rate limits: respect `x-api-ratelimit-*` headers; bounded concurrency for
  option-symbol fan-out; batching via ?symbols= where the API supports it.
- Token: `MARKETDATA_TOKEN` from env only (`.env` already carries it; add the key
  to `.env.example` with a placeholder).

### 3. The measuring stick

- **Recorded fixtures + replay transport.** The client takes a transport seam
  (httpx-native). `RecordingTransport` writes (request → status, headers-subset,
  body) JSON fixtures; `ReplayTransport` serves them. The whole connector test
  suite runs on fixtures — zero quota in the loop. A small `record_fixtures.py`
  script refreshes them (metered, run sparingly).
- **Conformance suite** from core runs against the connector on fixtures.
- **Differential harness** (`connectors/marketdata/verify/differential.py`):
  pulls the same symbols raw from the vendor (independent code path, Decimal
  parse) and through connector→library, asserts value-for-value agreement
  (equity quote, candles incl. weekly/monthly aggregation vs vendor's native W/M,
  option quotes + greeks), kind badging vs measured freshness, Decimal-purity scan
  of every returned object, bounds honesty. Pass/fail exit code, made for the
  grader. Live calls only here.
- **ALIVE demo** (`connectors/marketdata/verify/demo_position.py`): builds a real
  multi-leg option position (e.g. SPY vertical + covered stock) in a scratch DB,
  values it via `Portfolio.value` / `calculate_pnl` with the connector, prints
  real prices/greeks; asserts non-None pricing end-to-end.
- **Grader:** fresh-context subagent, prompt contains only the bar + how to run
  the harness + live access; instructed to try to break contract-truth,
  vendor-truth, aliveness (its own probes encouraged, e.g. hand-computed OCC
  symbols, odd tickers, holidays, out-of-bounds ranges). Loop: grade → fix → tag.

### 4. Order of work (TDD throughout, PADR-0001)

1. Core v2 red→green (tests first per method/type), stubs, CSVPriceSource,
   conformance suite, call-site migration, ADR edits, full suite + lint + mypy.
2. Connector package scaffolding + client (fixtures recorded once, early, from a
   handful of cheap live calls), mapping, capabilities, quotes/close/ohlcv.
3. Harness + demo; first grader run; loop.

Commits per repo conventions (PADR-0009): milestone commits on feature branches,
PR-shaped; full `make check` before each merge to main.

### Design decisions flagged (would have asked; resolved by judgement)

- **Greeks in core as `OptionQuote` subclass** rather than a separate protocol —
  smallest honest surface that keeps phase 2 library-backed. Recorded as ADR-0039
  amendment.
- **Options EOD uses the vendor's EOD quote (mid), not a candle** — MarketData has
  no options-candles endpoint; the dated quote IS its end-of-day record.
- **Equity quote price = mid** (mark) with `last` fallback — consistent with the
  vendor's own realtime `stocks/prices` (midpoint-based) and options mark
  convention; differential harness verifies against the raw fields.
- **`stocks/prices` counts as REALTIME for equities if reachable** even on plans
  the maintainer described as "delayed" — probe decides; if the endpoint 402s or
  serves stale data, realtime honestly reports False. Capabilities never assume.
- **History bound discovery** favors cheap probes (`no_data.nextTime`, one-time
  402 bisect) cached per instrument/class over hard-coded plan tables.

## Status log

- 2026-07-10: recon complete (ADR-0034/0039, prices.py v1, call sites, Identifier/
  OptionMeta, vendor docs + SDK float issue, rate-limit/entitlement model). Plan
  recorded. Starting core v2.
- 2026-07-10 ~03:00 ET: core v2 landed (incl. two contract findings folded into
  ADR-0039 at acceptance: OptionQuote greeks surface; historical → closes/ohlcv
  split because options have dated EOD closes but no bar archive). Connector +
  measuring stick built. Live differential + conformance-on-live + ALIVE collar
  all pass against the real vendor.
- Grader round 1 (fresh context, live): vendor-true PASS, alive PASS,
  contract-true FAIL — never-listed OCC symbols (vendor 404 s:error "No option
  found") raised instead of None. Fixed test-first (404 = known negative per
  vendor docs; capabilities distinguishes brand-new vs never-listed contracts).
- Grader round 2 (fresh context, new attack surface: IWM/DIA, RDDT IPO bound
  discovery, opra-precedence, expired contracts, year-boundary aggregation,
  mixed batches, CachedPriceSource transparency, Decimal walks): ALL THREE BARS
  PASS — "could not break it after a genuine adversarial effort."
- Round-2 caveat pre-empted: option close series clipped to calendar-complete
  sessions (an in-progress session can never serve as a close).
- PR #40 open with grader evidence; auto-merge armed pending CI (main is
  protected). `make check` green locally: 594 passed.
- OPEN (market-hours only, ~09:30 ET): live confirmation of the equities
  realtime channel (capabilities honestly report realtime=False until the
  freshness probe confirms during RTH — no unverified claim is being made).
  Also re-run the option in-progress-session check live during RTH.
- Vendor observations recorded: bulk quotes endpoint serves mid=null overnight
  (batch falls back to last — both vendor-served); options quotes `to` param is
  exclusive; entitlement header x-options-data-permissions appears on every
  response.

## Phase 2 plan (option-tracker) — gate deliverable, self-approved per delegation

**Reference map** (recon 2026-07-10, screenshots + text dumps in scratchpad/ref):
top nav + sidebar (Option Positions / Equity Positions{Wheel Strategy, Equity
Positions} / Analytics{Cumulative, PnL Flow} / Calendar / History / Broker
Connection); Account Summary card everywhere (Total Value +%, Options Position,
Option Margin (Est.), Options PnL, Equity Position, Equity PnL, Cash; green/red);
Option Positions table (Symbol+live price, Type (contracts), Expiration (+dte,
red when ≤1d), PnL%, Market Value, Delta%, Moneyness x% ITM/OTM, Share) with
search / Strategy filter / TradingView links, rows expanding to Open Date,
Initial Premium, Premium Incl. Roll, AROI init/now, per-leg greeks table
(Side/Right, Strike, Price, IV, Delta, Gamma, Theta, Vega), Roll Selections
history (Open/Close Date, Initial Premium, Realized PnL), Roll Selection /
Modify Roll actions; Wheel campaigns (Shares, Cost Basis, Adjusted Cost ±%,
Market Value, PnL%, Total PnL, add-position); Cumulative analytics (Total
Profit, fees, Win Ratio W/L, avg/max win/loss, Strategies Count, Cumulative
Profits monthly/weekly + goal, Option Profit vs Account Value); PnL Flow
(symbol→Put/Call→Gain/Loss sankey, top-10, finalized only); Calendar (per-day
premium, N trades, xW yL, month/day views); History (Total Strategies, Total
Realized PnL, Strategy/Assigned/Date filters, closed strategies + per-leg
open/close prices, close status, fees); Broker Connection (staticish); light
theme toggle; demo-data banner.

**App**: `dev_project/optiontracker/` — host-side Django app (thin, presentation
only), Django templates + HTMX + minimal vanilla JS (row expand, theme), custom
CSS (dark default/light), inline-SVG charts rendered from library data. Routes:
`/tracker/` (option positions), `/tracker/wheel/`, `/tracker/equities/`,
`/tracker/analytics/`, `/tracker/analytics/flow/`, `/tracker/calendar/`,
`/tracker/history/`, `/tracker/broker/`. HTMX for search/filter/sort partials.

**Data**: `manage.py seed_tracker` — one demo user/account set; instruments +
OptionMeta contracts discovered from the LIVE chain at seed time (real, current
expirations; ~30–60 metered credits, run once), transactions shaped like the
reference dataset (same strategy mix incl. rolls, wheel campaigns, closed
history with fees); trades built through the repo's own detection engine
(confirm flow) so the app dogfoods ADR-0037. REAL prices/greeks at render time
via the phase-1 connector (CachedPriceSource-wrapped).

**Library gaps I already foresee** (all land in django_assets with TDD; ADR for
the reporting surface; GAPS.md kept current at dev_project/optiontracker/GAPS.md):
- `trades.reports.account_summary(user, price_source)` — split option/equity/
  cash values + PnL + estimated option margin (margin model needs a small ADR).
- `trades.reports.open_strategies(...)` — per open Trade: strategy tag,
  contracts, expiration, market value, PnL%, initial premium, AROI init/now,
  premium incl. rolls, per-leg quotes (OptionQuote greeks), moneyness, position
  delta%, roll segments (derived from the trade's allocation timeline; rolls
  are ADR-0037 `adjust` events — a `roll_segments` derivation API is the gap).
- `trades.reports.closed_strategies(...)`, `strategy_performance(...)` (win
  ratio, avg/max, per-strategy counts, cumulative series, fees),
  `premium_calendar(...)` (per-day realized premium, trade counts, W/L),
  `pnl_flow(...)` (symbol → put/call → gain/loss aggregation).
- Wheel: `lots`/trades cooperation for campaigns — shares, cost basis,
  premium-adjusted cost ("true cost basis"), campaign PnL.
- Position-level greeks aggregation helper (net delta etc. from leg quotes).

**Library-gap vs app-concern rule**: if it's arithmetic on money/greeks or any
domain classification → library API (tests first). If it's formatting, color
classes, sorting UI state, SVG path layout → app. `scripts/check_app_thinness.py`
will enforce mechanically: no Decimal arithmetic / no arithmetic on library
values inside dev_project/optiontracker (grader-runnable).

**Measuring stick**: `prompts/option-tracker/compare.mjs` drives the reference
and the local app side by side (same viewport), walks a per-screen checklist
(structure: headings/nav/columns/expand behavior/filters/theme; format rules:
color coding, dte badges, % signs; behavior: search narrows, filter filters,
rows expand, links go where they say) and emits pass/fail JSON + paired
screenshots. Values are NOT compared numerically against the reference (ours
are REAL connector prices on a freshly seeded ledger; the reference is frozen
mock data) — the checklist encodes the information architecture and behavior;
the grader additionally traces on-screen values to library APIs and hunts
domain logic in the app layer.

## Phase-2 status log

- Recon: reference driven headlessly (login solved by reference-login.mjs);
  every screen captured as screenshots + text dumps incl. expanded rows,
  strategy filter, PnL Flow, light theme (scratchpad/ref).
- Library: `django_assets/trades/reports.py` (ADR-0040) — the entire
  option-dashboard data layer. GAPS.md (dev_project/optiontracker/GAPS.md)
  records 25+ gaps found and resolved LIBRARY-side, incl. two genuine bugs
  the vertical exposed (assign()'s user/mirror split on multi-leg combos;
  assignment strike-cash attribution).
- App: dev_project/optiontracker — 8 screens, Django+HTMX, dark/light, SVG
  charts, thin by construction (scripts/check_app_thinness.py). Seeded by
  manage.py seed_tracker from LIVE chains (~37 credits/run): 20 open
  strategies incl. rolls, wheel campaigns, closed history, one real
  assignment. All prices/greeks at render time via the phase-1 connector.
- Measuring stick: prompts/option-tracker/compare.mjs — 106-point
  structural/behavioral checklist + reference capture.
- Grader round 1 (fresh context): 13 parity divergences + 3 library-bar
  findings → all fixed (donut/toggles/dual-axis/roll info-dot/roll dialogs/
  wheel & history & calendar & flow controls/assignment semantics…).
- Grader round 2 (fresh context): verified all 16 round-1 fixes landed;
  found 11 deeper divergences (assigned-mode table, cumulative chart
  semantics, header metric toggles, calendar month-aggregate view, wheel
  expanded anatomy, equity holdings page, close-status vocabulary, account
  selector, condor leg order, pagination, minors) + 2 library bugs
  (assignment close-price; wheel mixed-event basis) → library round landed
  (assignments(), wheel history rows, equity_holdings(), extrinsic values,
  ClosedLeg.status, premium_months()); UI round in progress.
- Grader round 3 (fresh context): LIBRARY-BACKED & THIN — **PASS** (checker +
  manual audit clean; all new surfaces traced to reports.py). Parity: verified
  all 11 round-2 fixes work; 9 remaining findings, now polish-level (date-range
  control vocabulary, grand-vs-filtered totals, empty-state strings, header
  case, chart tick/axis formats, tooltip style, info-dot style, 1280/900px
  layouts, cold-cache latency). premium_months made single-pass in the library
  the same hour (12× derivation → 1). Round-4 polish builder dispatched.
- Rounds 4–9 (fresh context each): LIBRARY-BACKED & THIN passed every round;
  parity converged from ~9 findings → 5 → 2 dialog interiors → 5 small items,
  each fixed library-first. New library capabilities the vertical surfaced and
  landed with tests/ADRs: OptionChainSource (ADR-0041, live-verified),
  roll_link_candidates (roll = link to prior closed trades), month_detail +
  best_day + realized_months/realized_weeks (calendar-cell consistency), the
  23-step guided tutorial captured verbatim from the reference. Grading loop
  continues until a fresh grader can't break parity.
- Rounds 10–18 (fresh context each): LIBRARY-BACKED & THIN passed every round.
  Parity converged down the long tail — each round a fresh grader probed deeper
  (roll finder as historical-trade linker, month-detail dialog, calendar
  Week/Month realized aggregates, Sunday-start weeks, fixed Strategy-filter
  vocabulary, summary/analytics tile chrome, Sankey per-symbol/gain-loss
  colors, sorted-column tint, sidebar active-subitem emphasis, expanded-panel
  info dots / leg alignment / header case / panel border, wheel sub-table
  vocabulary, broker logo layout). Every finding fixed library-first where it
  was domain data, app-side where it was pure presentation.
- Library capabilities the vertical surfaced and landed (tests + ADRs):
  ADR-0040 reporting surface, ADR-0041 OptionChainSource, roll_candidates,
  roll_link_candidates, month_detail (+best_day), realized_months/weeks,
  premium_months (single-pass), classify_trade, assignments, equity_holdings,
  wheel history, OptionQuote intrinsic/extrinsic, and the assignment cash
  policy. All Decimal-pure, thin app enforced by scripts/check_app_thinness.py.
- Round 21 (fresh context): **BOTH BARS PASS — phase 2 signed off.** After
  confirming all fixes in both themes against the live reference, both
  regression suites (compare.mjs 106/106 + exercise4.mjs 52/52), thinness
  re-verified with two exact number traces into trades/reports.py, and a
  full screen-by-screen live-reference comparison (positions incl. expanded
  greeks/rolls, wheel, equities, analytics incl. dual-axis, PnL flow,
  calendar, history, broker, roll-finder dialog, TradingView panel,
  responsive 1280/900, dark+light), the grader "could not distinguish the
  clone from the reference within the counting criteria."

## Phase 2 DONE (2026-07-10)

Both DONE bars met by the fresh-context grader: (1) BEHAVIORAL PARITY —
indistinguishable from the reference; (2) LIBRARY-BACKED & THIN — every
on-screen value traces to a django_assets API, the app layer is arithmetic-
free (scripts/check_app_thinness.py), and every gap was closed in the
library with tests + ADRs (ADR-0040 reporting surface, ADR-0041
OptionChainSource), not worked around. Phases 1 and 2 both complete.
- OUTSTANDING (market hours): phase-1 RTH re-verification (realtime channel
  freshness) at/after 09:30 ET — capabilities honestly report realtime=False
  until confirmable, so no unverified claim is live meanwhile.

## Phase-1 declaration

Phase 1 is DONE per the GOAL bar (fresh-context grader, live vendor, all three
bars) subject to the market-hours re-verification above. Sign-off was delegated
(maintainer, 2026-07-10: "work 100% independently… I trust your judgement"),
so phase 2 begins; the RTH check runs as soon as the market opens.
