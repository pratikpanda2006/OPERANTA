"""
backend/app/services/paypal_client.py
=====================================
Async PayPal REST client (REST v2) for the M12.0 Sandbox customer checkout.

Responsibilities:
- OAuth2 client-credentials token acquisition with an in-memory cached token.
- Order creation (create order) exposing the buyer approval URL.
- Capture of an approved order.

All network I/O funnels through ``self._http`` (an ``httpx.AsyncClient``).
Tests inject a fake client or patch ``_http`` to simulate PayPal responses.

SECURITY NOTE: credentials come from ``settings.paypal_client_id`` /
``settings.paypal_client_secret`` (Pydantic SecretStr). They are never logged,
rendered in the frontend, or exposed via API responses.
"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

PAYPAL_AUTH_PATH = "/v1/oauth2/token"
PAYPAL_ORDERS_PATH = "/v2/checkout/orders"
PAYPAL_CAPTURE_SUFFIX = "/capture"


class PayPalAuthError(RuntimeError):
    """Raised when OAuth2 token acquisition fails."""


class PayPalAPIError(RuntimeError):
    """Raised when a PayPal API call returns a non-2xx status."""

    def __init__(self, status_code: int, detail: str, reason: Any = None):
        self.status_code = status_code
        self.reason = reason
        super().__init__(detail)


class PayPalClient:
    """Thin async wrapper around the PayPal REST API."""

    def __init__(
        self,
        settings: Any,
        http: Optional[httpx.AsyncClient] = None,
        *,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self.settings = settings
        self._client_id = client_id
        self._client_secret = client_secret
        self.base_url = (base_url or settings.paypal_base_url).rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(20.0))
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------
    async def _post(
        self,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        form_data: Optional[Dict[str, str]] = None,
        token: Optional[str] = None,
        basic_auth: bool = False,
        request_id: Optional[str] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        headers: Dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        auth = None
        data = None
        if form_data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            data = form_data
        if basic_auth:
            if self._client_id is not None:
                # Merchant-specific credentials (normal SaaS merchants).
                auth = (self._client_id, self._client_secret or "")
            else:
                # Legacy environment-based credentials (Northstar demo merchant).
                client_id = (
                    self.settings.paypal_client_id.get_secret_value()
                    if self.settings.paypal_client_id
                    else ""
                )
                client_secret = (
                    self.settings.paypal_client_secret.get_secret_value()
                    if self.settings.paypal_client_secret
                    else ""
                )
                auth = (client_id, client_secret)

        resp = await self._http.post(url, headers=headers, json=json_body, data=data, auth=auth)
        return await self._parse_response(resp)

    @staticmethod
    async def _parse_response(resp: httpx.Response) -> Any:
        try:
            payload = resp.json()
        except Exception:
            payload = {"raw": resp.text}
        if resp.status_code >= 400:
            reason = None
            if isinstance(payload, dict):
                reason = payload.get("message") or payload.get("details")
            raise PayPalAPIError(resp.status_code, f"PayPal API error ({resp.status_code})", reason)
        return payload

    # ------------------------------------------------------------------
    # OAuth2 token management (cached)
    # ------------------------------------------------------------------
    async def get_access_token(self) -> str:
        """Return a valid access token, fetching + caching one if expired."""
        now = time.monotonic()
        if self._token and self._token_expires_at > now:
            return self._token

        async with self._lock:
            if self._token and self._token_expires_at > time.monotonic():
                return self._token

            payload = await self._post(
                PAYPAL_AUTH_PATH,
                form_data={"grant_type": "client_credentials"},
                basic_auth=True,
            )
            token = payload.get("access_token")
            if not token:
                raise PayPalAuthError("PayPal OAuth2 response missing access_token")
            expires_in = int(payload.get("expires_in", 3600))
            self._token = token
            self._token_expires_at = time.monotonic() + max(expires_in - 60, 30)
            return token

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------
    async def create_order(self, payload: Dict[str, Any], request_id: Optional[str] = None) -> Dict[str, Any]:
        """Create a PayPal order. Returns the raw PayPal order object."""
        token = await self.get_access_token()
        data = await self._post(PAYPAL_ORDERS_PATH, json_body=payload, token=token, request_id=request_id)
        return data

    async def capture_order(self, order_id: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        """Capture an approved PayPal order. Returns the raw PayPal capture response."""
        token = await self.get_access_token()
        path = f"{PAYPAL_ORDERS_PATH}/{order_id}{PAYPAL_CAPTURE_SUFFIX}"
        data = await self._post(path, json_body={}, token=token, request_id=request_id)
        return data

    # ------------------------------------------------------------------
    # Pure helpers
    # ------------------------------------------------------------------
    @staticmethod
    def extract_approve_url(order_data: Dict[str, Any]) -> Optional[str]:
        """
        Return the buyer approval action URL from a PayPal order object.

        Per the official Orders v2 payment-source flow the confirmation link uses
        ``rel == "payer-action"``; the classic response uses ``rel == "approve"``.
        We prefer ``payer-action``, fall back to ``approve`` for compatibility,
        and return ``None`` when neither relation is present (the caller rejects
        the response).
        """
        links = order_data.get("links", [])
        for rel in ("payer-action", "approve"):
            for link in links:
                if link.get("rel") == rel and link.get("href"):
                    return str(link["href"])
        return None
