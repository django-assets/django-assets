from django.apps import AppConfig


class OptionTrackerConfig(AppConfig):
    """The option-tracker example app: a deliberately thin, host-side
    presentation layer over django_assets + the MarketData connector.
    No domain logic lives here — every figure comes finished from
    django_assets.trades.reports."""

    name = "dev_project.optiontracker"
    label = "optiontracker"

    def ready(self) -> None:
        import os
        import sys

        serving = any("runserver" in arg for arg in sys.argv) or "gunicorn" in sys.argv[0]
        if serving and not os.environ.get("OPTIONTRACKER_NO_WARM"):
            from dev_project.optiontracker import services

            services.warm_caches_async()
