#!/usr/bin/env python
"""Enforce the sub-package dependency DAG (Product ADR-0015/0033, spec D-39).

Rules:
- core imports no sibling sub-package.
- instruments imports core only; its type packages (currencies, crypto,
  equities, options, ...) never import each other (ADR-0033 internal
  discipline) — cross-type needs go through core FKs or the instruments root.
- brokerage, trades, and lots may import core and instruments, never each
  other.

Run from the repo root: `uv run python scripts/check_import_direction.py`.
Exits non-zero with one line per violation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PKG = Path("django_assets")
SIBLINGS = ("core", "instruments", "brokerage", "trades", "lots")

# sub-package -> siblings it may import
ALLOWED: dict[str, frozenset[str]] = {
    "core": frozenset(),
    "instruments": frozenset({"core"}),
    "brokerage": frozenset({"core", "instruments"}),
    "trades": frozenset({"core", "instruments"}),
    "lots": frozenset({"core", "instruments"}),
}

IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+django_assets\.(" + "|".join(SIBLINGS) + r")(?:\.(\w+))?",
)


def type_package(path: Path) -> str | None:
    """Return the instruments type-package name for files under one, else None."""
    parts = path.parts
    if len(parts) >= 4 and parts[1] == "instruments" and (PKG / "instruments" / parts[2]).is_dir():
        return parts[2]
    return None


def main() -> int:
    violations: list[str] = []
    for sub, allowed in ALLOWED.items():
        for path in (PKG / sub).rglob("*.py"):
            if "/test/" in path.as_posix():
                continue  # tests may exercise anything
            for lineno, line in enumerate(path.read_text().splitlines(), start=1):
                match = IMPORT_RE.match(line)
                if match is None:
                    continue
                target, target_child = match.group(1), match.group(2)
                if target != sub and target not in allowed:
                    violations.append(
                        f"{path}:{lineno}: {sub} may not import django_assets.{target}"
                    )
                if target == "instruments" == sub:
                    # sideways type-package import ban (ADR-0033)
                    source_type = type_package(path)
                    if (
                        source_type is not None
                        and target_child is not None
                        and target_child != source_type
                        and (PKG / "instruments" / target_child).is_dir()
                    ):
                        violations.append(
                            f"{path}:{lineno}: instruments/{source_type} may not import "
                            f"instruments/{target_child} (go through core or the root)"
                        )
    for violation in violations:
        print(violation)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
