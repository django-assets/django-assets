# Build the option-tracking app

> Hand this whole file to your agent to kick off the build. Setup first:
> `cp .env.example .env` and fill in the reference-app creds (`.env` is gitignored), then
> `npx playwright install chromium`. Verify the reference driver works:
> `node --env-file=.env reference-login.mjs` — it should log in and screenshot the mock
> dashboard.

## The goal

`django-assets` is a **library**. Its job is to model and store assets, trades, positions,
strategies, and the accounting around them. This build is **not** about shipping a UI product
on top of the library — it is a real-world test *of the library itself*: can you build a
genuine option-tracking application backed entirely by `django-assets`? Where the library is
missing a model, a field, a computation, or an API you need, that is the whole point — you've
found a gap. The app is the vehicle; **validating and improving the library is the deliverable.**

The reference app defines what a real option tracker must store and compute (positions,
multi-leg strategies, rolls, cost basis, P&L, greeks). Treat it as the **requirements
source**, not primarily a visual target: build an app that reproduces its behavior and data,
and in doing so, prove the library can back it — or find out exactly where it can't.

    REFERENCE APP: the "mock data" dashboard at OI_URL (see .env)

    LOGIN: Clerk two-step modal. Click "Sign in" → enter email → click the email form's
    "Continue" (NOT "Continue with Google") → enter password → click Clerk's primary button.
    Credentials live in `prompts/option-tracker/.env` (gitignored) as OI_EMAIL / OI_PASSWORD.
    A proven, reusable Playwright login+screenshot script already exists next to this file at
    `reference-login.mjs` — run it with `node --env-file=.env reference-login.mjs [outDir]`
    to drive the reference app headlessly for comparison. Don't rediscover the login flow;
    it's solved. Never hard-code or paste the credentials anywhere — read them from the env.

Two things come out of this build:
1. **A working example app** — deliberately thin — that renders the reference's screens
   entirely from `django-assets` data and APIs.
2. **A record of every place the library fell short**, and how you resolved it — ideally by
   improving the library (a new/changed model, field, method, manager, or API), following
   this repo's ADR + TDD conventions, rather than by working around it in the app. Keep this
   as a running `GAPS.md` alongside the app: what you needed, whether the library had it, and
   what you changed.

I'm not going to tell you how to structure the views, the URLs, the templates, or the
components — you're better at figuring that out than I am, and every step I dictate is just me
overriding your judgment with mine. Find the best way there. Use the domain model already in
this repo (trades / instruments / distribution / brokerage) as your foundation — but treat it
as the thing under test, not a finished given.

## House rules (never cross these, however you get to the goal)

1. **Keep the app thin; the library does the work.** No domain logic in the app layer — no
   P&L math, no strategy classification, no cost-basis or roll accounting, no money
   arithmetic in views, templates, or app helpers. All of it lives in `django_assets` behind
   a real API; the app does presentation only. If a screen needs something the library can't
   give it, **do not compute it in the app** — that's a library gap: log it in `GAPS.md` and
   extend the library (with tests, and an ADR if the change is architectural). Papering over
   gaps in the app makes the whole exercise worthless, because then you've only proven the
   *app* works, not the *library*.
2. **Whatever builds something never grades it.** When you think a screen is done, spin up
   a *separate sub-agent with a fresh context window*, point it at the actual running app
   (real pixels, real interactions — drive it, don't read the code), give it the reference
   app side by side, and tell it to **prove the clone is failing**: find where they differ.
   The build agent has a whole trajectory of "why I made these choices" it will use to
   convince itself it's done. The grader has none of that. Only the grader's verdict counts.
   The grader checks **both** bars below: visual/behavioral parity, *and* that every value on
   screen traces to a `django-assets` API with no domain logic hiding in the app layer.
3. **Don't fight the framework.** Server-rendered Django templates + HTMX, in this repo's
   grain. Build components from scratch rather than adopting a component library whose
   conventions you'll spend the whole build fighting. If you find yourself working around a
   dependency more than working with it, throw the dependency out — a clean foundation makes
   everything on top of it easier.
4. **No hard-coded special cases.** When behavior varies, describe the rule and let code /
   the domain model reason it out. Don't reach for a one-off branch to paper over a specific
   case you saw in the reference.
5. **Match the repo's conventions and its bar for "done."** Read the ADRs in
   `docs-internal/adr/` and follow them — TDD (0001), no-float money (0006), migration
   conventions (0008), and especially the definition-of-done (0010). Nothing merges unless
   the full test suite passes and the build stays green.

## The bar for "done" — and you invent the measuring stick

Do not stop at your own idea of "good enough" — it's lower than mine. "High quality" is not
a target you can check yourself against, so I'm not giving you an adjective. I'm giving you
two hard bars, and both must pass:

> **1. Behavioral parity:** a person who knows the reference app cannot tell your app from it
> — same information, same flows, same feel, on every screen a user actually touches.
>
> **2. Library-backed and thin:** every number and behavior on those screens comes from
> `django-assets`, not from logic living in the app. The grader must be able to point at any
> value on screen and trace it to a library API — and find no domain logic in the app layer.
> Every gap you hit is recorded in `GAPS.md` and resolved in the library, not worked around.

The first bar proves the app is real. The second proves the *library* is what made it real —
which is the entire reason we're building this.

The reference dashboard, concretely, is: a dark-themed app (with a light toggle) with a top
nav and a left sidebar (Option Positions, Equity Positions, Analytics, Calendar, History,
Broker Connection); an **Account Summary** card (Total Value, Options Position, Option Margin,
Options PnL, Equity Position, Equity PnL, Cash — PnL color-coded green/red); and an **Option
Positions** table with columns Symbol (+ live price), Type (contracts), Expiration (days to
exp), PnL %, Market Value, Delta %, Moneyness (ITM/OTM), Share — whose rows expand to per-leg
greeks (Strike/Price/IV/Delta/Gamma/Theta/Vega) and a Roll Selections history sub-table. The
strategies shown (Put Credit Spread, Covered Call, the Wheel/CSP, rolls, true cost basis) are
the same taxonomy this repo already implements — use that domain layer, don't rebuild it.

I don't know exactly how to *measure* that, so that's your problem to solve too. Invent the
measuring stick. Some ways you might (your call — do what actually works): drive the
reference app and yours with the same inputs and diff the rendered result; screen-record the
reference in use and turn it into a heat map of where things move, then match it; build a
per-screen checklist of what a user can see and do, and verify each one against the live
reference. Whatever you build, it has to be concrete enough that the fresh-context grader in
house-rule #1 can run it and return a pass/fail, not an opinion.

## Loop until it hits the bar

Put yourself on a loop against that bar and go. Build a screen → have the fresh-context
grader try to prove it's failing → close the biggest gap it finds → grade again. You don't
get to decide you're finished; there's always a next gap. Stop only when the grader
genuinely can't find a way the app differs from the reference, or when I say stop.

As you work, keep a progress doc updated on Workbench.md — screenshots of each screen next to
the reference, what's passing, what's still off — so I can glance at my phone and see where
things stand and steer you without stopping the run.

## Build on what's already here

Don't re-derive things this repo already knows. Before you start:
- Read `docs-internal/specs/{trades,instruments,distribution,brokerage}/{spec,plan}.md` and
  the ADRs — the domain is already designed. Understand what the library *intends* to offer
  so you can tell a real gap from something you just haven't found yet.
- Read the traces of the recent Claude Code sessions that built the trades / options-strategy
  taxonomy (commits `#36`–`#39`). Learn what worked and what didn't there instead of
  rediscovering it. Reuse patterns, don't reinvent them.
- When you do change the library to close a gap, change it *as a library author would* — a
  clean, general API that fits the existing design, with tests and (if architectural) an ADR
  — not a one-off bolt-on that only this app needs.

## Get out of your own way

Make your own calls and only come back to me if you're truly blocked or hit something only I
can decide (a real product tradeoff, spending real money, deleting real data).
- The only credential you need is the reference-app login (in `.env`). No paid external
  services are required — you build and run everything locally against this repo.
- Don't ask me for permission per step. Ask up front, in a batch, anything you're unsure about.

**The one gate:** this is a big, foundational build, so before you write any app code, give
me a plan — your proposed screens, routes, template/HTMX structure, how you'll build and run
the measuring stick, and **how you'll decide "library gap vs. app concern" and where library
changes will land** (which module, how you'll keep them general, how `GAPS.md` gets kept up
to date) — and ask me everything you're unsure about. Library-shaping changes are the
consequential decisions here; surface the ones you foresee. Once I sign off, run without
stopping.

## How to run it

Engineering mode: you can fan out a small team of sub-agents (one per screen or flow), each
triple-checking its own work with the fresh-context grader and opening a PR with the grader's
evidence attached. Then one integrator sub-agent does nothing but merge PRs, run everything,
drive the app like a real user, and keep the whole thing green. Where two screens share state
or components, have one watch the other's traces and stay compatible.
