"""Thin MarketData.app HTTP client.

Deliberately NOT the vendor SDK: the SDK parses JSON through float
(`response.json()`), which would violate PADR-0006 at the boundary. Here
every payload is parsed with `parse_float=Decimal`, so a float never
exists, even transiently. HTTP 203 (cache tier) is success, exactly like
200. The status discipline follows ADR-0039 §7: "can't price" answers
(vendor `no_data`) become `NoData` for callers to map to None; "couldn't
ask" (transport, auth, rate limit, server errors) raises — converting a
network error into None would lie to the valuation layer.
"""

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

BASE_URL = "https://api.marketdata.app"
TOKEN_ENV_VAR = "MARKETDATA_TOKEN"


class MarketDataError(Exception):
    """Couldn't ask: transport failure, 5xx, rate limit. Never None."""


class MarketDataAuthError(MarketDataError):
    """Missing/rejected token (401/403), or no token configured."""


class MarketDataEntitlementError(MarketDataError):
    """402 — the request exceeds the token's plan (depth, endpoint,
    mode). Capability discovery treats this as an honest boundary."""


class MarketDataBadRequest(MarketDataError):
    """400 — the vendor rejected the request. MarketData answers both
    malformed parameters AND unknown symbols this way; callers that just
    mapped a symbol may interpret it as 'unknown symbol'."""


@dataclass(frozen=True)
class NoData:
    """Vendor answered 'no data for this question' (s=no_data)."""

    next_time: int | None = None
    prev_time: int | None = None


@dataclass
class MarketDataClient:
    """One authenticated httpx session. `transport` is the seam the
    recorded-fixture replay plugs into; production leaves it None."""

    token: str | None = None
    base_url: str = BASE_URL
    timeout: float = 30.0
    transport: httpx.BaseTransport | None = None
    permissions: frozenset[str] = field(default_factory=frozenset, init=False)
    credits_consumed: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        token = self.token or os.environ.get(TOKEN_ENV_VAR)
        if not token:
            raise MarketDataAuthError(
                f"no MarketData token: pass token= or set {TOKEN_ENV_VAR} in the environment"
            )
        self.token = token
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=self.timeout,
            transport=self.transport,
        )

    def get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any] | NoData:
        """GET one endpoint; returns the Decimal-parsed payload or NoData.

        Raises MarketDataAuthError (401/403), MarketDataEntitlementError
        (402), MarketDataBadRequest (400), MarketDataError (429/5xx/
        transport/unparseable).
        """
        try:
            response = self._http.get(path, params=params or {})
        except httpx.HTTPError as exc:
            raise MarketDataError(f"transport failure for {path}: {exc}") from exc

        header = response.headers.get("x-options-data-permissions")
        if header:
            self.permissions = frozenset(part.strip() for part in header.split(",") if part.strip())
        consumed = response.headers.get("x-api-ratelimit-consumed")
        if consumed and consumed.isdigit():
            self.credits_consumed += int(consumed)

        status = response.status_code
        if status in (200, 203, 400, 402, 404):
            try:
                payload: dict[str, Any] = json.loads(response.text, parse_float=Decimal)
            except json.JSONDecodeError as exc:
                raise MarketDataError(
                    f"unparseable body from {path} (HTTP {status}): {response.text[:200]!r}"
                ) from exc
        else:
            payload = {}

        if status in (200, 203):
            if payload.get("s") == "no_data":
                return self._no_data(payload)
            if payload.get("s") == "error":
                raise MarketDataError(
                    f"vendor error from {path}: {payload.get('errmsg', 'unknown')}"
                )
            return payload
        if status == 204:
            return NoData()
        if status == 404:
            # Vendor semantics: 404 = a valid request that simply has no
            # data — served as s:no_data (candles/quotes) or s:error with
            # "No option found" (never-listed contracts). Either way it is
            # a KNOWN negative, not a transport failure: honest None.
            return self._no_data(payload)
        if status == 400:
            raise MarketDataBadRequest(
                f"HTTP 400 from {path}: {payload.get('errmsg', 'bad request')}"
            )
        if status in (401, 403):
            raise MarketDataAuthError(f"HTTP {status} from {path}: token rejected")
        if status == 402:
            raise MarketDataEntitlementError(
                f"HTTP 402 from {path}: beyond this token's plan "
                f"({payload.get('errmsg', 'payment required')})"
            )
        if status == 429:
            raise MarketDataError(f"HTTP 429 from {path}: rate limited")
        raise MarketDataError(f"HTTP {status} from {path}")

    @staticmethod
    def _no_data(payload: dict[str, Any]) -> NoData:
        def as_int(value: Any) -> int | None:
            return int(value) if isinstance(value, int | Decimal) else None

        return NoData(
            next_time=as_int(payload.get("nextTime")), prev_time=as_int(payload.get("prevTime"))
        )
