"""Instruments-level exception types."""


class CapabilityError(ValueError):
    """A template refused because the account's AccountProfile capability
    flag disallows the operation (ADR-0014). Advisory — a host can bypass
    templates entirely; the ledger itself never enforces capabilities.
    """
