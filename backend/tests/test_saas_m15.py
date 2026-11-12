"""
backend/tests/test_saas_m15.py
===============================
M15.0 SaaS registration / demo access / PayPal credential resolution tests.

These are mocked unit tests (no DB, no network). They cover:
- RegisterRequest validation rules (email, password length, confirmation).
- Fernet secret encryption round-trip.
- PayPal credential resolution rules (Northstar env vs merchant-specific stored,
  no fallback across tenants).
- get_merchant_context: authenticated session precedence over demo fallback,
  demo fallback, and deny when neither is available.
- PayPalClient uses provided merchant credentials for OAuth basic auth.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr, ValidationError
from starlette.requests import Request

# Ensure backend/ is importable regardless of pytest's cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api import deps as deps_mod
from app.schemas.auth import RegisterRequest
from app.services.auth import create_session_token
from app.services.paypal_client import PayPalClient
from app.services.paypal_credentials import (
    decrypt_secret,
    encrypt_secret,
    resolve_paypal_credentials,
)
from app.services.merchant_provisioning import new_merchant_id

NORTHSTAR = "merchant_northstar"
SANDBOX_BASE = "https://api-m.sandbox.paypal.com"


# ---------------------------------------------------------------------------
# RegisterRequest validation
# ---------------------------------------------------------------------------

def test_register_request_rejects_weak_password():
    with pytest.raises(ValidationError):
        RegisterRequest(
            business_name="Biz",
            full_name="A B",
            email="a@b.com",
            password="short",
            confirm_password="short",
        )


def test_register_request_rejects_password_confirmation_mismatch():
    with pytest.raises(ValidationError):
        RegisterRequest(
            business_name="Biz",
            full_name="A B",
            email="a@b.com",
            password="strong-enough-pass",
            confirm_password="different-pass",
        )


def test_register_request_rejects_invalid_email():
    with pytest.raises(ValidationError):
        RegisterRequest(
            business_name="Biz",
            full_name="A B",
            email="not-an-email",
            password="strong-enough-pass",
            confirm_password="strong-enough-pass",
        )


def test_register_request_normalizes_email_and_accepts_valid():
    req = RegisterRequest(
        business_name="Biz",
        full_name="A B",
        email="  A@B.Com ",
        password="strong-enough-pass",
        confirm_password="strong-enough-pass",
    )
    assert req.email == "a@b.com"


# ---------------------------------------------------------------------------
# Secret encryption
# ---------------------------------------------------------------------------

def test_encrypt_decrypt_roundtrip():
    key = "test-key-12345"
    ciphertext = encrypt_secret("plain-secret", key)
    assert ciphertext != "plain-secret"
    assert decrypt_secret(ciphertext, key) == "plain-secret"


def test_encrypt_decrypt_wrong_key_fails():
    key = "test-key-12345"
    ciphertext = encrypt_secret("plain-secret", key)
    with pytest.raises(ValueError):
        decrypt_secret(ciphertext, "a-different-key")


# ---------------------------------------------------------------------------
# PayPal credential resolution
# ---------------------------------------------------------------------------

def make_settings(
    demo_mid=NORTHSTAR,
    client_id="NW_CID",
    client_secret="NW_SEC",
    webhook="wh-1",
    base=SANDBOX_BASE,
    enc_key="enc-key-12345",
):
    return SimpleNamespace(
        demo_merchant_id=demo_mid,
        paypal_client_id=SecretStr(client_id) if client_id else None,
        paypal_client_secret=SecretStr(client_secret) if client_secret else None,
        paypal_webhook_id=SecretStr(webhook) if webhook else None,
        paypal_base_url=base,
        paypal_credentials_encryption_key=SecretStr(enc_key) if enc_key else None,
    )


class FakeConn:
    def __init__(self, row=None):
        self.row = row

    async def fetchrow(self, sql, *args):
        return self.row


def test_resolve_northstar_uses_environment_credentials():
    settings = make_settings()
    creds = asyncio_run(resolve_paypal_credentials(settings, NORTHSTAR, FakeConn()))
    assert creds == {
        "client_id": "NW_CID",
        "client_secret": "NW_SEC",
        "base_url": SANDBOX_BASE,
        "webhook_id": "wh-1",
    }


def test_resolve_northstar_missing_env_returns_none():
    settings = make_settings(client_id=None, client_secret=None)
    creds = asyncio_run(resolve_paypal_credentials(settings, NORTHSTAR, FakeConn()))
    assert creds is None


def test_resolve_normal_merchant_uses_stored_credentials():
    settings = make_settings()
    secret_plain = "merchant-secret"
    enc = encrypt_secret(secret_plain, settings.paypal_credentials_encryption_key.get_secret_value())
    row = {
        "environment": "sandbox",
        "client_id": "M_CID",
        "encrypted_client_secret": enc,
        "webhook_id": None,
        "connection_status": "connected",
    }
    creds = asyncio_run(resolve_paypal_credentials(settings, "merchant_abc", FakeConn(row)))
    assert creds["client_id"] == "M_CID"
    assert creds["client_secret"] == secret_plain
    assert creds["base_url"] == SANDBOX_BASE
    assert creds["webhook_id"] is None


def test_resolve_normal_merchant_no_integration_returns_none():
    settings = make_settings()
    creds = asyncio_run(resolve_paypal_credentials(settings, "merchant_abc", FakeConn(None)))
    assert creds is None


def test_resolve_normal_merchant_never_falls_back_to_northstar():
    # A normal merchant with no own integration must NOT receive Northstar creds.
    settings = make_settings(client_id="NW_CID", client_secret="NW_SEC")
    creds = asyncio_run(resolve_paypal_credentials(settings, "merchant_abc", FakeConn(None)))
    assert creds is None


def test_resolve_normal_merchant_disconnected_returns_none():
    settings = make_settings()
    enc = encrypt_secret("s", settings.paypal_credentials_encryption_key.get_secret_value())
    row = {
        "environment": "sandbox",
        "client_id": "M_CID",
        "encrypted_client_secret": enc,
        "webhook_id": None,
        "connection_status": "disconnected",
    }
    creds = asyncio_run(resolve_paypal_credentials(settings, "merchant_abc", FakeConn(row)))
    assert creds is None


# ---------------------------------------------------------------------------
# get_merchant_context precedence / demo fallback
# ---------------------------------------------------------------------------

def _make_request(token: str | None = None, query_merchant: str | None = None):
    headers = []
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    qs = b""
    if query_merchant is not None:
        qs = f"merchant_id={query_merchant}".encode()
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/inventory",
        "raw_path": b"/api/v1/inventory",
        "query_string": qs,
        "headers": headers,
        "client": ("127.0.0.1", 80),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return Request(scope)


def _demo_settings(session_secret="secret", demo_mode=True, demo_mid=NORTHSTAR):
    return SimpleNamespace(
        auth_session_secret=SecretStr(session_secret),
        demo_mode=demo_mode,
        demo_merchant_id=demo_mid,
    )


def test_merchant_context_session_precedence_over_demo(monkeypatch):
    secret = "session-secret"
    token = create_session_token(secret, "user-1", "merchant_A", 60)
    monkeypatch.setattr(deps_mod, "settings", _demo_settings(session_secret=secret, demo_mode=True))
    req = _make_request(token=token, query_merchant="merchant_evil")
    assert deps_mod.get_merchant_context(req) == "merchant_A"


def test_merchant_context_demo_fallback_without_session(monkeypatch):
    monkeypatch.setattr(deps_mod, "settings", _demo_settings(demo_mode=True))
    assert deps_mod.get_merchant_context(_make_request()) == NORTHSTAR


def test_merchant_context_denied_without_session_in_production(monkeypatch):
    monkeypatch.setattr(deps_mod, "settings", _demo_settings(demo_mode=False))
    with pytest.raises(Exception) as excinfo:
        deps_mod.get_merchant_context(_make_request())
    assert excinfo.value.status_code == 403


def test_merchant_context_invalid_token_denied_in_production(monkeypatch):
    monkeypatch.setattr(deps_mod, "settings", _demo_settings(demo_mode=False))
    with pytest.raises(Exception) as excinfo:
        deps_mod.get_merchant_context(_make_request(token="garbage.token"))
    assert excinfo.value.status_code == 403


# ---------------------------------------------------------------------------
# PayPalClient uses provided credentials
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json = json_data
        self.text = "{}"

    def json(self):
        return self._json


class FakeHTTP:
    def __init__(self):
        self.calls = []

    async def post(self, url, headers=None, json=None, data=None, auth=None):
        self.calls.append({"url": url, "auth": auth, "headers": headers, "data": data})
        return FakeResponse(200, {"access_token": "tok", "expires_in": 3600})

    async def aclose(self):
        return None


def test_paypal_client_uses_provided_credentials_for_oauth():
    http = FakeHTTP()
    client = PayPalClient(
        make_settings(),
        http=http,
        client_id="M_CID",
        client_secret="M_SEC",
        base_url=SANDBOX_BASE,
    )
    token = asyncio_run(client.get_access_token())
    assert token == "tok"
    assert http.calls
    auth = http.calls[0]["auth"]
    # httpx.AsyncClient accepts an (username, password) tuple for HTTPBasicAuth.
    assert auth == ("M_CID", "M_SEC")


def _asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


asyncio_run = _asyncio_run
