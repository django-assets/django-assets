"""Host-registered schema — picked up by autodiscover_modules("schemas")."""

from django_assets.brokerage.schemas import ImportSchema, register_schema


@register_schema(
    broker="hosttest",
    document_kind="statements",
    format_kind="csv",
    version="1",
    name="Host test schema",
)
class HostTestSchema(ImportSchema):
    pass
