"""C7 [D-8]: core imports cleanly when DRF is not installed.

Runs a subprocess whose import machinery pretends rest_framework does not
exist, then boots Django with django_assets installed and touches the
model layer. Only django_assets.serializers may complain, and it must do
so with a helpful error.
"""

import subprocess
import sys

SCRIPT = """
import sys

class BlockDRF:
    def find_module(self, name, path=None):
        return self if name.split(".")[0] == "rest_framework" else None
    def load_module(self, name):
        raise ImportError(f"{name} blocked: simulating DRF absent")

sys.meta_path.insert(0, BlockDRF())

import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dev_project.settings.test")
import django
django.setup()

# Core works without DRF (D-8)…
from django_assets.core.builder import TransactionBuilder
from django_assets.core.models import Transaction
from django_assets.core.queries import Portfolio

# …and the serializers module fails loudly but helpfully.
try:
    import django_assets.serializers
except ImportError as exc:
    assert "django-assets[drf]" in str(exc), f"unhelpful error: {exc}"
else:
    raise AssertionError("django_assets.serializers imported without DRF")
print("OK")
"""


def test_core_imports_without_drf():
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"
