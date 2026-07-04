"""Brokerage exception types."""


class SchemaNotRegistered(LookupError):
    """No ImportSchema matches the batch's natural key. Shipped schemas
    are append-only precisely so this never happens to historical
    batches; hitting it means a registration is missing."""
