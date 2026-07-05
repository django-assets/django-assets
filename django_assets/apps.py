from django.apps import AppConfig
from django.db.models.signals import (
    m2m_changed,
    post_delete,
    post_migrate,
    post_save,
    pre_delete,
    pre_save,
)


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
        # 2. System checks (PostgreSQL backend + version floor).
        # 3. AccountProfile reconciliation-flag guard (ADR-0014, spec §2).
        from django_assets.brokerage.models import AccountProfile, guard_reconciliation_flag
        from django_assets.core import checks  # noqa: F401  (registers on import)

        pre_save.connect(
            guard_reconciliation_flag,
            sender=AccountProfile,
            dispatch_uid="django_assets.guard_reconciliation_flag",
        )
        # 4. Import-schema autodiscovery (ADR-0027): built-ins register on
        #    import; host apps contribute via a `schemas` module.
        from django.utils.module_loading import autodiscover_modules

        from django_assets.brokerage.schemas import builtin  # noqa: F401

        autodiscover_modules("schemas")

        # 5. Reconciliation lock (ADR-0024, D-17): numeric facts of matched
        #    legs are broker ground truth; core stays unaware — the lock
        #    exists only while brokerage's handlers are wired.
        from django_assets.brokerage.reconciliation import (
            guard_locked_leg_delete,
            guard_locked_leg_save,
        )
        from django_assets.core.models import TransactionLeg

        pre_save.connect(
            guard_locked_leg_save,
            sender=TransactionLeg,
            dispatch_uid="django_assets.guard_locked_leg_save",
        )
        pre_delete.connect(
            guard_locked_leg_delete,
            sender=TransactionLeg,
            dispatch_uid="django_assets.guard_locked_leg_delete",
        )

        # 6. Trades tag hygiene: same-user attachment only (ADR-0030).
        from django_assets.trades.models import Trade, guard_same_user_tags

        m2m_changed.connect(
            guard_same_user_tags,
            sender=Trade.tags.through,
            dispatch_uid="django_assets.guard_same_user_tags",
        )

        # 7. Lots staleness marking (ADR-0032 §4): ledger edits invalidate
        #    exactly the touched (account, instrument) pair.
        from django_assets.lots.signals import mark_stale

        post_save.connect(
            mark_stale,
            sender=TransactionLeg,
            dispatch_uid="django_assets.lots_mark_stale_save",
        )
        post_delete.connect(
            mark_stale,
            sender=TransactionLeg,
            dispatch_uid="django_assets.lots_mark_stale_delete",
        )
