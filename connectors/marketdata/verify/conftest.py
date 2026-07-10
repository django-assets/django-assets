"""Fixtures for the LIVE verification suites (metered vendor calls).

These files are deliberately NOT named test_*.py: the normal repo suite
never touches the live vendor. Run them explicitly:

    uv run pytest connectors/marketdata/verify/live_differential.py \
                  connectors/marketdata/verify/live_alive.py -v
"""

import os
import pathlib

import pytest


def _ensure_token() -> None:
    if os.environ.get("MARKETDATA_TOKEN"):
        return
    env_path = pathlib.Path(__file__).resolve().parents[3] / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("MARKETDATA_TOKEN="):
                os.environ["MARKETDATA_TOKEN"] = line.split("=", 1)[1].strip().strip('"')
                return


@pytest.fixture(scope="session", autouse=True)
def marketdata_token():
    _ensure_token()
    if not os.environ.get("MARKETDATA_TOKEN"):
        pytest.skip("MARKETDATA_TOKEN not configured — live verification needs it")
    return os.environ["MARKETDATA_TOKEN"]


@pytest.fixture(scope="module")
def raw_client():
    """Independent path to the vendor: same HTTP surface, none of the
    connector's mapping/logic — the differential baseline."""
    import json
    from decimal import Decimal

    import httpx

    class Raw:
        def __init__(self) -> None:
            self._http = httpx.Client(
                base_url="https://api.marketdata.app",
                headers={"Authorization": f"Bearer {os.environ['MARKETDATA_TOKEN']}"},
                timeout=30.0,
            )

        def get(self, path: str, params: dict | None = None):
            response = self._http.get(path, params=params or {})
            assert response.status_code in (200, 203, 404), (
                f"raw {path} → HTTP {response.status_code}: {response.text[:200]}"
            )
            return json.loads(response.text, parse_float=Decimal)

    return Raw()
