from django.apps import AppConfig
from django.db.models.signals import post_migrate


class DjangoAssetsConfig(AppConfig):
    """The single Django app shipped by the django-assets distribution.

    Sub-packages (core, instruments, brokerage, trades, lots) are a code
    organization within this one app; all models share this app label and
    one migration sequence. ready() hosts all sub-package wiring in a fixed
    order (PADR-0011); steps land with their milestones.
    """

    name = "django_assets"
    label = "django_assets"
    verbose_name = "Django Assets"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # 1. DDL install wiring (ADR-0004): hybrid mode only. dispatch_uid
        #    makes repeated ready() calls idempotent.
        from django_assets import conf
        from django_assets.core.ddl import install_ddl

        if conf.ddl_install_mode() == "hybrid":
            post_migrate.connect(install_ddl, sender=self, dispatch_uid="django_assets.install_ddl")
        else:
            post_migrate.disconnect(sender=self, dispatch_uid="django_assets.install_ddl")
        # 2. System checks (PostgreSQL backend + version floor) — core C1.
        # 3. Import-schema autodiscovery (ADR-0027) — brokerage B4.
        # 4. Reconciliation signal handlers (ADR-0024) — brokerage B6.
