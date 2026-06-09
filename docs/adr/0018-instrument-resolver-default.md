# ADR-0018: Instrument resolver — default normalization and API shape

## Status

Accepted — 2026-06-02

## Context

Per ADR-0009, the package's symbol resolution machinery is `Instrument.resolve(value, *, type="ticker", exchange=None, as_of=None)`, with the lookup running through the `Identifier` table. Two questions sit on top of that signature:

1. **What normalization does the default resolver apply to `value` before the lookup?** Users of the standalone `dev_project/` and host code expect `resolve("aapl")` to find AAPL. But many ticker conventions are genuinely case-sensitive (preferred shares like `CDRpB`, `BRKpA`, `BFpA`, `HEpA` use lowercase `p` between uppercase segments; some NASDAQ rights/warrants suffixes use lowercase letters). Aggressive normalization clobbers these.
2. **Does the resolver return a single Instrument or multiple candidates?** Backend code that needs a definitive answer (transaction recording, brokerage helpers, reconciliation) wants one match or a clear error. UI code (autocomplete, search, diagnostic tools) wants the candidate list.

For (1), three normalization strategies were considered:

- **Strict** — no transformation at all. `resolve("aapl")` misses. Cleanest semantically, most surprising.
- **Light** — strip whitespace + uppercase. `resolve("aapl")` finds "AAPL". Works for stocks, currencies (ISO 4217), crypto (uppercase by convention), and ISO-standard identifiers (ISIN/CUSIP/FIGI/OPRA). Fails for preferred shares: `resolve("CDRpB")` becomes `CDRPB`, which is not a real ticker.
- **Smart** — type-aware normalization (uppercase for known-uppercase identifier types, preserve case for tickers). Mostly right, but introduces type-dependent behavior that adds explanation cost.

Light normalization was initially proposed but was rejected once preferred-share tickers were brought up. Uppercasing makes the most common identifier type (tickers) wrong for a real subset of instruments. The clean alternative is to do only the universally-safe transformation: strip whitespace. Case is preserved.

Hosts that know their data is uppercase-uniform (because their data feed publishes that way) can wire a custom resolver that uppercases on the input. This is a one-class subclass, documented as a common host override.

For (2), the cleanest API mirrors Django's ORM split between `.get()` (one or raise) and `.filter()` (queryset). Two methods serve the two use cases cleanly without parameterized magic.

## Decision

### Two methods: `resolve` and `search`

```python
class Instrument:
    @classmethod
    def resolve(
        cls,
        value: str,
        *,
        type: str = "ticker",
        exchange: "Exchange | None" = None,
        as_of: "datetime | date | None" = None,
    ) -> "Instrument":
        """Return exactly one Instrument matching value.

        Raises InstrumentNotFoundError if no match.
        Raises AmbiguousInstrumentError (with .candidates attached) if multiple matches.
        """

    @classmethod
    def search(
        cls,
        value: str,
        *,
        type: str = "ticker",
        exchange: "Exchange | None" = None,
        as_of: "datetime | date | None" = None,
    ) -> "list[Instrument]":
        """Return all Instruments matching value (possibly empty, possibly many)."""
```

`resolve` is for backend code that needs a definitive answer: transaction recording, brokerage helpers, broker-statement import adapters, reconciliation jobs. The strict shape forces ambiguity to surface as an error rather than silently picking.

`search` is for UI code, diagnostic tools, and import adapters that want to see all candidates before choosing. Returns a list (possibly empty).

Both methods are implemented on the resolver class behind the scenes; `Instrument.resolve` and `Instrument.search` are thin classmethods that delegate to the configured resolver.

### Default normalization: strip whitespace only

```python
class DefaultInstrumentResolver:
    def resolve(self, value, *, type="ticker", exchange=None, as_of=None):
        value = value.strip()
        # ... Identifier table lookup ...

    def search(self, value, *, type="ticker", exchange=None, as_of=None):
        value = value.strip()
        # ... Identifier table lookup, returning list ...
```

- Whitespace is stripped (leading and trailing).
- Case is preserved.
- No other transformations.

Behavior examples:

| Input | Lookup value | Notes |
| --- | --- | --- |
| `"AAPL"` | `AAPL` | Works for normal stock tickers |
| `" AAPL "` | `AAPL` | Whitespace stripped |
| `"aapl"` | `aapl` | Miss if stored as `AAPL` — raises `InstrumentNotFoundError` |
| `"CDRpB"` | `CDRpB` | Preferred-share ticker preserved correctly |
| `"BRKpA"` | `BRKpA` | Preferred-share ticker preserved correctly |
| `"us0378331005"` | `us0378331005` | ISIN miss if stored uppercase (recommended ISO 4217 / ISO 6166 convention) |

The "user typed lowercase" miss is a real surprise cost but it is recoverable (clear error) and it avoids silently mapping case-sensitive tickers to the wrong instrument.

### Error shape

`AmbiguousInstrumentError` carries the candidates so callers that catch it can recover without a second query:

```python
class InstrumentNotFoundError(Exception):
    def __init__(self, value: str, type: str, exchange: "Exchange | None" = None):
        self.value = value
        self.type = type
        self.exchange = exchange
        super().__init__(f"No Instrument matching {type}={value!r} exchange={exchange}")


class AmbiguousInstrumentError(Exception):
    def __init__(self, value: str, candidates: "list[Instrument]"):
        self.value = value
        self.candidates = candidates
        super().__init__(
            f"{value!r} matches {len(candidates)} instruments: "
            f"{[i.code for i in candidates]}. Pass exchange= to disambiguate."
        )
```

Pattern for graceful handling:

```python
try:
    inst = Instrument.resolve("F")
except AmbiguousInstrumentError as e:
    inst = pick_by_user_preference(e.candidates)
```

### No `resolve_or_create` in core

The package does NOT ship a `resolve_or_create` convenience that creates a missing Instrument on the fly. Instrument creation is reference-data management — it should be a deliberate import job, an admin action, or a corporate-action ingestion step, not a side effect of a lookup. Hosts that want this pattern wrap it themselves:

```python
def get_or_create_instrument(code, **defaults):
    try:
        return Instrument.resolve(code)
    except InstrumentNotFoundError:
        return Instrument.objects.create(code=code, **defaults)
```

This keeps the resolver semantically pure (read-only).

### Override path

Per REQ-24 in the compatibility doc, hosts swap the resolver via settings:

```python
# settings.py
DJANGO_ASSETS_INSTRUMENT_RESOLVER = "host_app.resolvers.UppercasingResolver"
```

Common override pattern (documented in the package's docs):

```python
# host_app/resolvers.py
from django_assets.core.resolvers import DefaultInstrumentResolver

class UppercasingResolver(DefaultInstrumentResolver):
    """For hosts whose data is stored uppercase-uniform.
    Uppercases ticker lookups; preserves other identifier types."""

    def resolve(self, value, *, type="ticker", **kwargs):
        if type == "ticker":
            value = value.upper()
        return super().resolve(value, type=type, **kwargs)

    def search(self, value, *, type="ticker", **kwargs):
        if type == "ticker":
            value = value.upper()
        return super().search(value, type=type, **kwargs)
```

Hosts with more sophisticated needs (exchange-suffix parsing, broker-specific symbol mapping, OCC auto-detection) write their own resolver class and configure the setting.

### Seed-fixture convention

The package's shipped seed fixtures for currencies and major crypto store identifier values **as published by their issuing standard**: ISO 4217 codes uppercase, ticker-style identifiers uppercase by default. Preferred-share fixtures (when shipped) preserve the original case. This documents the recommended convention for hosts seeding their own data.

## Consequences

**Easier:**

- Preferred-share tickers (`CDRpB` and similar) work correctly with the default resolver.
- Backend code gets clean error semantics: `resolve` either returns one or raises a specific exception.
- UI code gets a clean list interface: `search` returns all candidates.
- `AmbiguousInstrumentError.candidates` makes graceful fallback possible without a second query.
- The default is conservative and predictable. Hosts that want richer normalization opt in.

**Harder:**

- Users typing lowercase tickers in the `dev_project/` shell get an `InstrumentNotFoundError` they have to read and fix. Documented as the convention.
- Hosts that want case-insensitive ticker lookups have to write a 5-line custom resolver. Documented and easy.
- The two-method API (`resolve` and `search`) is slightly more surface than a single parameterized method. The tradeoff: cleaner semantics for both use cases.

**Deferred:**

- `resolve_or_create` — host responsibility, not core.
- Smart auto-detection (recognize OCC format, ISIN format, CUSIP format) — would be a separate resolver class adopters can opt into; not in v0.1.
- Exchange-suffix parsing (`AAPL.NASDAQ` → ticker + exchange) — host responsibility.

## Related

- ADR-0009 establishes the Instrument identity model and the `Identifier` table the resolver queries.
- ADR-0011 establishes "core is the ledger primitive, not policy" — the conservative default normalization follows that principle.
- The host compatibility requirements document establishes the `DJANGO_ASSETS_INSTRUMENT_RESOLVER` settings override.
- OQ-9 in `open-questions.md` is resolved by this ADR.
