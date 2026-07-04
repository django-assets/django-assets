"""App-shell wiring tests (distribution plan P0.4; PADR-0011 / spec D-40).

The single AppConfig hosts all sub-package wiring. These tests pin the
switch behavior and idempotency; the *behavior* of the DDL installer is
core C2's scope.
"""

import subprocess
import sys

from django.apps import apps
from django.db.models.signals import post_migrate
from django.test import override_settings

UID = "django_assets.install_ddl"


def _config():
    return apps.get_app_config("django_assets")


def _connected(cfg) -> bool:
    # disconnect() reports whether the receiver was registered; reconnect if so.
    was = post_migrate.disconnect(sender=cfg, dispatch_uid=UID)
    if was:
        from django_assets.core.ddl import install_ddl

        post_migrate.connect(install_ddl, sender=cfg, dispatch_uid=UID)
    return was


def test_post_migrate_wired_in_default_hybrid_mode():
    assert _connected(_config()) is True


def test_post_migrate_not_wired_in_external_mode():
    cfg = _config()
    post_migrate.disconnect(sender=cfg, dispatch_uid=UID)
    try:
        with override_settings(DJANGO_ASSETS_DDL_INSTALL_MODE="external"):
            cfg.ready()
            assert _connected(cfg) is False
    finally:
        cfg.ready()  # restore default wiring for other tests
    assert _connected(cfg) is True


def test_ready_is_idempotent():
    cfg = _config()
    cfg.ready()
    cfg.ready()
    matches = [r for r in post_migrate.receivers if r[0][0] == UID]
    assert len(matches) == 1


def test_package_import_is_cheap():
    """Importing django_assets must not drag in DRF or a DB driver."""
    code = (
        "import sys; import django_assets; "
        "assert 'rest_framework' not in sys.modules, 'DRF imported'; "
        "assert 'psycopg' not in sys.modules, 'DB driver imported'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
