"""Brokerage exception types."""


class SchemaNotRegistered(LookupError):
    """No ImportSchema matches the batch's natural key. Shipped schemas
    are append-only precisely so this never happens to historical
    batches; hitting it means a registration is missing."""


class ReconciledLegLocked(ValueError):
    """The leg is matched to broker evidence: its numeric facts
    (amount/account/instrument) and its existence are ground truth
    (D-17). Unflip the match first, then edit, then re-match."""
