"""Client status discipline + Decimal purity at the parse boundary."""

import json
from decimal import Decimal

import httpx
import pytest
from django_assets_prices_marketdata.client import (
    MarketDataAuthError,
    MarketDataBadRequest,
    MarketDataClient,
    MarketDataEntitlementError,
    MarketDataError,
    NoData,
)


def client_for(status: int, body: dict, headers: dict | None = None) -> MarketDataClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            headers={"content-type": "application/json", **(headers or {})},
            text=json.dumps(body),
        )

    return MarketDataClient(token="t", transport=httpx.MockTransport(handler))


def test_parses_numbers_as_decimal_never_float():
    client = client_for(200, {"s": "ok", "mid": [751.463], "updated": [1783641585]})
    payload = client.get("/v1/stocks/prices/SPY/")
    assert isinstance(payload["mid"][0], Decimal)
    assert payload["mid"][0] == Decimal("751.463")
    assert isinstance(payload["updated"][0], int)


def test_203_cache_tier_is_success():
    client = client_for(203, {"s": "ok", "mid": [1.5]})
    assert client.get("/x")["mid"][0] == Decimal("1.5")


def test_no_data_body_returns_nodata_with_hints():
    client = client_for(200, {"s": "no_data", "nextTime": 1783641585, "prevTime": None})
    result = client.get("/x")
    assert isinstance(result, NoData)
    assert result.next_time == 1783641585
    assert result.prev_time is None


def test_404_no_data_is_nodata_not_error():
    client = client_for(404, {"s": "no_data", "nextTime": None, "prevTime": None})
    assert isinstance(client.get("/x"), NoData)


def test_400_raises_bad_request():
    client = client_for(400, {"s": "error", "errmsg": "Bad parameters"})
    with pytest.raises(MarketDataBadRequest):
        client.get("/x")


def test_402_raises_entitlement():
    client = client_for(402, {"s": "error", "errmsg": "upgrade"})
    with pytest.raises(MarketDataEntitlementError):
        client.get("/x")


def test_401_raises_auth():
    client = client_for(401, {"errmsg": "no"})
    with pytest.raises(MarketDataAuthError):
        client.get("/x")


def test_429_raises_not_none():
    client = client_for(429, {})
    with pytest.raises(MarketDataError):
        client.get("/x")


def test_5xx_raises_not_none():
    client = client_for(500, {})
    with pytest.raises(MarketDataError):
        client.get("/x")


def test_missing_token_fails_loudly(monkeypatch):
    monkeypatch.delenv("MARKETDATA_TOKEN", raising=False)
    with pytest.raises(MarketDataAuthError):
        MarketDataClient()


def test_permissions_header_captured():
    client = client_for(
        203,
        {"s": "ok"},
        headers={
            "x-options-data-permissions": "delayed_quotes_permission,historical_quotes_permission"
        },
    )
    client.get("/x")
    assert "delayed_quotes_permission" in client.permissions
    assert "historical_quotes_permission" in client.permissions


def test_vendor_error_with_200_raises():
    client = client_for(200, {"s": "error", "errmsg": "boom"})
    with pytest.raises(MarketDataError):
        client.get("/x")
