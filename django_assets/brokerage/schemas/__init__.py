"""Import-schema registry (brokerage spec §5.2, ADR-0027).

IMMORTALITY CONVENTION: shipped schema classes are append-only, like
migrations — a format change is a NEW `version` registration, never an
edit to an existing class. Historical batches must always resolve the
exact parser that produced them.

Built-ins live under schemas/builtin/<broker>/; hosts and third-party
apps register through the same decorator, discovered via a `schemas`
module in any installed app (autodiscover_modules in AppConfig.ready()).
"""

import datetime
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, ClassVar

from django.core.exceptions import ImproperlyConfigured

from django_assets.brokerage.exceptions import SchemaNotRegistered

if TYPE_CHECKING:
    from django_assets.brokerage.models import ImportBatch, ImportLine
    from django_assets.core.models import Transaction


class ImportSchema:
    """Base class for one broker document format (ADR-0027).

    Subclasses parse raw evidence into ImportLines and materialize
    matchable lines into Transactions by calling templates. Schemas stay
    dumb about ledger state — reconciliation is the orchestrator's job.
    """

    broker: ClassVar[str] = ""
    document_kind: ClassVar[str] = ""
    format_kind: ClassVar[str] = ""
    version: ClassVar[str] = ""
    name: ClassVar[str] = ""
    definition: ClassVar[dict[str, Any]] = {}

    def parse_batch(self, batch: "ImportBatch", source: Any) -> "Iterator[ImportLine]":
        """Yield UNSAVED ImportLines (the orchestrator persists them)."""
        raise NotImplementedError

    def materialize_line(self, line: "ImportLine") -> "list[Transaction]":
        """Build the line's Transactions via templates; [] for
        informational lines."""
        raise NotImplementedError

    def match_criteria(self, line: "ImportLine") -> Any:
        """Dedup criteria for transactional schemas (ADR-0029, B7);
        pure-informational schemas may leave this unimplemented."""
        raise NotImplementedError


class SchemaRegistry:
    def __init__(self) -> None:
        self._schemas: dict[tuple[str, str, str, str], ImportSchema] = {}

    def register(self, schema_class: type[ImportSchema]) -> None:
        key = (
            schema_class.broker,
            schema_class.document_kind,
            schema_class.format_kind,
            schema_class.version,
        )
        if key in self._schemas:
            raise ImproperlyConfigured(
                f"import schema {key} already registered "
                f"({type(self._schemas[key]).__name__}); shipped schemas are "
                f"append-only — register a new version instead"
            )
        self._schemas[key] = schema_class()

    def get(self, broker: str, document_kind: str, format_kind: str, version: str) -> ImportSchema:
        key = (broker, document_kind, format_kind, version)
        try:
            return self._schemas[key]
        except KeyError:
            raise SchemaNotRegistered(
                f"no import schema registered for {key}; historical batches "
                f"require their original schema class (immortality convention)"
            ) from None


registry = SchemaRegistry()


def register_schema(
    *, broker: str, document_kind: str, format_kind: str, version: str, name: str = ""
) -> Any:
    def decorator(cls: type[ImportSchema]) -> type[ImportSchema]:
        cls.broker = broker
        cls.document_kind = document_kind
        cls.format_kind = format_kind
        cls.version = version
        cls.name = name or cls.name or cls.__name__
        registry.register(cls)
        return cls

    return decorator


def parse_us_date(value: str) -> datetime.date:
    """MM/DD/YYYY — the common US broker convention."""
    return datetime.datetime.strptime(value, "%m/%d/%Y").date()  # noqa: DTZ007
