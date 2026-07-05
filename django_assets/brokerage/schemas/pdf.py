"""Shared PDF-statement plumbing for built-in schemas.

pdfplumber is the optional `django-assets[pdf]` extra; the import is
lazy and fails with an actionable message. Statement text is extracted
page-by-page and joined; parsers work on lines.
"""

import io
from typing import Any


def extract_text(source: Any) -> str:
    """source: bytes, a file-like, or a str path to a PDF."""
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "PDF import schemas require pdfplumber. "
            'Install the extra: pip install "django-assets[pdf]".'
        ) from exc

    if isinstance(source, bytes):
        handle: Any = io.BytesIO(source)
    else:
        handle = source
    with pdfplumber.open(handle) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)
