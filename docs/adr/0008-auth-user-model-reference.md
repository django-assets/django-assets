# ADR-0008: Reference the user model via settings.AUTH_USER_MODEL only

## Status

Accepted — 2026-06-02

## Context

The package needs to reference the Django user model from at least one place: the `Account.owner` ForeignKey.

There are two ways to do this:

- **String reference via `settings.AUTH_USER_MODEL`** — the Django-recommended pattern for reusable apps. The FK takes a string like `"auth.User"` or `"myapp.CustomUser"`; Django resolves it lazily at migration time.
- **Direct import** — `from django.contrib.auth.models import User` or `from <host_app>.models import User`. Resolves immediately; couples the package to the imported model.

Django's documentation is explicit: any reusable app that FKs the user model MUST use the string form, because adopters are free to override `AUTH_USER_MODEL` to point at a custom user model. A direct import bakes in `django.contrib.auth.User` as a hard dependency and breaks every adopter who customized their user model.

The primary target host currently does not override `AUTH_USER_MODEL` — it uses Django's default `auth.User`. But:

- The host could swap in a custom user model in the future. The package should not be a blocker.
- The package targets a broader audience than this one host. Other adopters definitely run custom user models.
- Direct imports require a `import` statement at module top level, which can introduce circular-import problems during Django's app-loading dance.

## Decision

The package references the user model exclusively via `settings.AUTH_USER_MODEL`:

```python
from django.conf import settings

class Account(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="accounts",
    )
```

The package:

- Never writes `from django.contrib.auth.models import User`.
- Never writes `from django.contrib.auth import get_user_model()` at module top level (it can be used inside functions where lazy resolution is needed).
- Documents in the README that the package supports any user model that satisfies the Django contract (has a primary key Django can FK to).

## Consequences

**Easier:**

- The package works with any user model the adopter chooses, current or future.
- No circular-import risk between the package and Django's auth module.
- Adopters who later customize their user model don't have to fork or patch the package.

**Harder:**

- Test fixtures that need to create a user must use `django.contrib.auth.get_user_model()` rather than importing `User` directly. This is one extra line of indirection in tests.
- Documentation examples must consistently show the string-reference pattern, not direct imports, to model the correct usage.

## Related

- ADR-0005 establishes that Account has a single owner.
- ADR-0006 establishes the CASCADE behavior on user deletion.
