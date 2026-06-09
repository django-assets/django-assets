# Pending Host Confirmations

These are decisions that require input from the primary target host's developer, not from the `django-assets` package authors. They are tracked here separately from open design questions because the package authors cannot resolve them unilaterally.

When a host confirmation is received, the corresponding ADR (or amendment to an existing ADR) is added in this directory and the entry is removed from this file.

(none open)

## Resolved

### HC-1 → ADR-0004 amended

**Original question**: Is `post_migrate`-installed DDL acceptable to the host's change-control process?

**Resolution**: Investigation of the host environment revealed that the host manages all non-table DDL entirely outside Django — triggers, functions, views, and stored procedures live as raw `.sql` files in a `database/` directory tree, applied via shell scripts that pipe them into `psql`. No `post_migrate`, no `RunSQL`, no in-Django DDL of any kind for the host's domain-specific schema.

ADR-0004 was amended to introduce `DJANGO_ASSETS_DDL_INSTALL_MODE = "hybrid" | "external"`. In `"external"` mode, the package's `.sql` files are structurally compatible with the host's shell-script convention; the host applies them via their existing deployment tooling. The hybrid mode remains the default for adopters without specialized DDL tooling.

HC-1 is closed.

### HC-2 → moot

**Original question**: Will the host enable the pytest plugin in their conftest if HC-1 forces it?

**Resolution**: Moot. The host's preferred mode is `"external"` (per HC-1 resolution); no pytest plugin opt-in is required for the host's specific case. The pytest plugin pattern (if it exists at all) is only relevant for adopters using `"hybrid"` mode with `--nomigrations` who also don't want the `post_migrate` handler to fire — an edge case that does not apply to the host.

HC-2 is closed.
