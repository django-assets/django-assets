"""B4: schema registry (ADR-0027)."""

import pytest
from django.core.exceptions import ImproperlyConfigured

from django_assets.brokerage.exceptions import SchemaNotRegistered
from django_assets.brokerage.schemas import ImportSchema, register_schema, registry

pytestmark = pytest.mark.django_db


def test_builtin_schwab_schema_registered():
    schema = registry.get("schwab", "trades", "csv", "2026.1")
    assert isinstance(schema, ImportSchema)
    assert schema.broker == "schwab"


def test_duplicate_registration_refused():
    with pytest.raises(ImproperlyConfigured, match="already registered"):

        @register_schema(
            broker="schwab", document_kind="trades", format_kind="csv", version="2026.1"
        )
        class Duplicate(ImportSchema):
            pass


def test_missing_historical_key_raises():
    with pytest.raises(SchemaNotRegistered):
        registry.get("lehman", "trades", "csv", "2008.1")


def test_batch_get_schema(batch):
    assert batch.get_schema().broker == "schwab"
    batch.schema_version = "1899.1"
    with pytest.raises(SchemaNotRegistered):
        batch.get_schema()


def test_host_app_schema_autodiscovered():
    """autodiscover_modules('schemas') picks up dev_project.hostapp."""
    schema = registry.get("hosttest", "statements", "csv", "1")
    assert schema.name == "Host test schema"


def test_immortality_convention_documented():
    """Shipped schemas are append-only; format changes are new versions."""
    import django_assets.brokerage.schemas as schemas_module

    assert "append-only" in (schemas_module.__doc__ or "")
