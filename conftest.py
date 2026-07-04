"""Repo-root pytest configuration.

The `ledger` mark (PADR-0003): any test exercising deferred constraints must
run with real COMMITs — Django's default TestCase rollback would silently
skip the trigger, making violation tests pass against a database that never
enforced anything. @pytest.mark.ledger expands to django_db(transaction=True).
"""

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "ledger: ledger-integrity test; implies django_db(transaction=True) so deferred "
        "constraints actually fire at COMMIT (PADR-0003)",
    )


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.get_closest_marker("ledger") is not None:
            item.add_marker(pytest.mark.django_db(transaction=True))
