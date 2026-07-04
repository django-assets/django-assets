from django.apps import AppConfig


class HostAppConfig(AppConfig):
    """Minimal host app proving schema autodiscovery (B4 test surface)."""

    name = "dev_project.hostapp"
    label = "hostapp"
