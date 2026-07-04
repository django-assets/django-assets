from django.apps import AppConfig


class DjangoAssetsConfig(AppConfig):
    """The single Django app shipped by the django-assets distribution.

    Sub-packages (core, instruments, brokerage, trades, lots) are a code
    organization within this one app; all models share this app label and
    one migration sequence.
    """

    name = "django_assets"
    label = "django_assets"
    verbose_name = "Django Assets"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # Wiring order is fixed; steps land with their milestones:
        # 1. DDL install post_migrate handler (hybrid mode only).
        # 2. System checks (PostgreSQL backend + version floor).
        # 3. Import-schema autodiscovery (brokerage).
        # 4. Reconciliation signal handlers (brokerage).
        return
