#!/usr/bin/env python3
"""Guard: the optiontracker app must stay THIN — no domain arithmetic.

Walks dev_project/optiontracker/{views.py,services.py,templatetags,templates}
and exits 1 if it finds patterns that look like money/greek computation in
app code. This is a pragmatic greppy checker, not a type system.

WHAT IT ALLOWS (the sanctioned presentation-arithmetic zones):

- templatetags/tracker_format.py — the formatting filters file. Display
  transforms only: ratio->percent (x100), money strings, sign classes,
  day counts. Its arithmetic is presentation by definition.
- templatetags/tracker_charts.py — SVG chart geometry. Floats/pixel math
  are explicitly fine there (coordinates, not money).
- templates/optiontracker/charts/* — the chart SVG includes fed by
  tracker_charts (they render precomputed geometry).

WHAT IT FLAGS everywhere else:

- Python: Decimal( construction, sum( calls, and +-*/ arithmetic where a
  report-object attribute (``row.x``, ``stats.x``, ``summary.x``,
  ``leg.x``, ``campaign.x``, ``.amount``, ``_pnl``, ``premium``,
  ``value``) appears on either side of the operator. Int counters such
  as ``stats.wins + stats.losses`` (closure COUNTS, not money) are
  explicitly exempted below.
- Templates: |add: filters, {% widthratio %} tags — arithmetic between
  template variables.
"""

import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "dev_project" / "optiontracker"

ALLOWED_FILES = {
    APP / "templatetags" / "tracker_format.py",
    APP / "templatetags" / "tracker_charts.py",
}
ALLOWED_DIRS = {APP / "templates" / "optiontracker" / "charts"}

#: Exact source fragments that look arithmetic-ish but are sanctioned.
EXEMPT_FRAGMENTS = [
    "stats.wins + stats.losses",  # closure count (ints), endorsed by the build brief
]

#: Attribute stems that mark a value as coming from a reports object.
REPORT_STEMS = (
    r"row\.\w+",
    r"stats\.\w+",
    r"summary\.\w+",
    r"leg\.\w+",
    r"campaign\.\w+",
    r"roll\.\w+",
    r"entry\.\w+",
    r"\w*_pnl\b",
    r"\w*premium\w*",
    r"\.amount\b",
    r"market_value",
    r"margin_estimate",
)

STEM = "(?:" + "|".join(REPORT_STEMS) + ")"

PY_PATTERNS = [
    (re.compile(r"\bDecimal\s*\("), "Decimal( construction in app code"),
    (re.compile(r"\bsum\s*\("), "sum( in app code"),
    (re.compile(STEM + r"\s*[-+*/]"), "arithmetic on a report value (left operand)"),
    (re.compile(r"[-+*/]\s*" + STEM), "arithmetic on a report value (right operand)"),
]

TEMPLATE_PATTERNS = [
    (re.compile(r"\|\s*add\s*:"), "|add: filter (template arithmetic)"),
    (re.compile(r"{%\s*widthratio\b"), "{% widthratio %} (template arithmetic)"),
]


def is_allowed(path: Path) -> bool:
    if path in ALLOWED_FILES:
        return True
    return any(parent in ALLOWED_DIRS for parent in path.parents)


def strip_exempt(line: str) -> str:
    for fragment in EXEMPT_FRAGMENTS:
        line = line.replace(fragment, "")
    return line


def check_file(path: Path, patterns) -> "list[str]":
    problems = []
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        line = strip_exempt(raw)
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for pattern, message in patterns:
            if pattern.search(line):
                try:
                    shown = path.relative_to(APP.parent.parent)
                except ValueError:
                    shown = path
                problems.append(f"{shown}:{lineno}: {message}")
                break
    return problems


def main() -> int:
    problems: list[str] = []

    python_targets = [APP / "views.py", APP / "services.py"]
    python_targets += sorted((APP / "templatetags").glob("*.py"))
    for path in python_targets:
        if not path.exists() or is_allowed(path):
            continue
        problems += check_file(path, PY_PATTERNS)

    for path in sorted((APP / "templates").rglob("*.html")):
        if is_allowed(path):
            continue
        problems += check_file(path, TEMPLATE_PATTERNS)

    if problems:
        print("App-thinness violations found:\n")
        print("\n".join(problems))
        return 1
    print("optiontracker is thin: no domain arithmetic outside the allowed zones.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
