Read and follow prompts/option-tracker/PROMPT.md in full — that file is the complete brief
(house rules, method, setup, reference-app login). This is the bar the loop measures against.

WHAT: Build a thin Django-templates + HTMX option-tracking app on django-assets that reproduces
the reference dashboard at OI_URL (the "mock data" view; login + headless driver already solved
in reference-login.mjs). The library is the product; the app is a test harness — it proves the
library can back a real application and surfaces where it can't.

DONE = both bars pass, verified by a FRESH-CONTEXT GRADER (a separate agent with clean context,
pointed at the running app beside the live reference, told to prove it's FAILING — the builder
never grades its own work; only the grader's verdict counts):

  1. BEHAVIORAL PARITY — someone who knows the reference cannot tell the app from it: same
     information, flows, and feel on every screen a user actually touches.
  2. LIBRARY-BACKED & THIN — every on-screen value traces to a django_assets API; NO P&L,
     strategy, cost-basis, roll, or money logic in views/templates. Every library gap is
     recorded in GAPS.md and fixed IN THE LIBRARY (tests + ADR), never worked around in the app.

NEVER CROSS:
  - Builder never grades itself; the fresh-context grader's verdict is the only one that counts.
  - No domain logic and no float in the app layer (PADR-0006) — the library does the work.
  - Follow repo ADRs: TDD (0001), no-float (0006), migrations (0008), definition-of-done (0010).
    Full suite green before anything merges.

GATE: before writing any app code, present a plan — screens/routes/HTMX structure, the measuring
stick, and your "library gap vs. app concern" policy plus where library changes land. Run without
stopping after I sign off. Loop until the grader can't break either bar, or I say stop.
