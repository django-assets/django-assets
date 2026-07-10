from django.apps import AppConfig


class OptionTrackerConfig(AppConfig):
    """The option-tracker example app: a deliberately thin, host-side
    presentation layer over django_assets + the MarketData connector.
    No domain logic lives here — see GAPS.md for the library changes
    this build produced."""

    name = "dev_project.optiontracker"
    label = "optiontracker"
