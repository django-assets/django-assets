"""Step one of an upload: recognize WHICH registered format a file is,
then import it through that schema (goal: detect → process).

Detection is registry-driven: every ImportSchema may override
``sniff(sample)`` with format fingerprints against a text sample —
the decoded file for CSVs, extracted text for PDFs. The container kind
(PDF magic vs text) prunes candidates first, so a CSV never sniffs
PDF text and vice versa.

Exactly one schema may claim a file: zero matches raises
UnknownFormatError (never guess someone's money data), two-plus raises
AmbiguousFormatError naming the contenders — both are loud by design.
``import_upload`` is the one-call flow: detect, create the batch with
the detected identity, and hand the source to the orchestrator.
"""

from typing import Any

from django_assets.brokerage.schemas import ImportSchema, registry


class UnknownFormatError(Exception):
    """No registered schema recognizes the file."""


class AmbiguousFormatError(Exception):
    """More than one registered schema claims the file."""


def _sample_text(file_name: str, content: "bytes | str") -> "tuple[str, str]":
    """→ (container_kind, sample text). PDF magic wins over extension."""
    if isinstance(content, bytes):
        if content[:5] == b"%PDF-":
            from django_assets.brokerage.schemas.pdf import extract_text

            return "pdf", extract_text(content)
        return "csv", content.decode("utf-8-sig", errors="replace")
    if file_name.lower().endswith(".pdf"):
        return "pdf", content
    return "csv", content


def detect_format(file_name: str, content: "bytes | str") -> ImportSchema:
    """Match the file against every registered schema's fingerprint."""
    container, sample = _sample_text(file_name, content)
    matches = [
        schema
        for schema in registry.all()
        if schema.format_kind == container and type(schema).sniff(sample)
    ]
    if not matches:
        raise UnknownFormatError(
            f"{file_name!r}: no registered import schema recognizes this "
            f"{container.upper()} — supported formats: "
            + ", ".join(
                f"{s.broker}/{s.document_kind}/{s.format_kind}/{s.version}" for s in registry.all()
            )
        )
    if len(matches) > 1:
        raise AmbiguousFormatError(
            f"{file_name!r} matches multiple schemas: "
            + ", ".join(
                f"{s.broker}/{s.document_kind}/{s.format_kind}/{s.version}" for s in matches
            )
        )
    return matches[0]


def import_upload(*, account: Any, file_name: str, content: "bytes | str") -> Any:
    """The full upload flow: detect the format, then process the import
    through the detected schema (ADR-0027 orchestrator)."""
    from django_assets.brokerage.imports import process_batch
    from django_assets.brokerage.models import ImportBatch

    schema = detect_format(file_name, content)
    batch = ImportBatch.objects.create(
        account=account,
        schema_broker=schema.broker,
        schema_document_kind=schema.document_kind,
        schema_format_kind=schema.format_kind,
        schema_version=schema.version,
        file_name=file_name,
    )
    if schema.format_kind == "csv" and isinstance(content, bytes):
        source: Any = content.decode("utf-8-sig", errors="replace")
    else:
        source = content
    process_batch(batch, source)
    return batch
