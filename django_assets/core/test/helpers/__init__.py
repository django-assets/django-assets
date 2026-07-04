"""Shared test utilities (not collected as tests)."""

from django.test import TransactionTestCase


class LedgerTestCase(TransactionTestCase):
    """Base class for class-based ledger tests (PADR-0003).

    Uses real COMMITs so DEFERRABLE INITIALLY DEFERRED constraints fire.
    pytest-style tests use @pytest.mark.ledger instead.
    """
