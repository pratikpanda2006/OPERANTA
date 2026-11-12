"""
backend/tests/test_auth.py
==========================
Tests for OPERANTA authentication: password hashing, signed session tokens,
and the login/me endpoints (using dependency overrides — no live database).
"""

from typing import Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.deps import get_current_user, get_db
from app.api.v1.endpoints.auth import router as auth_router
from app.core.config import settings
from app.services.auth import (
    create_session_token,
    hash_password,
    verify_password,
    verify_session_token,
)

TEST_SECRET = "unit-test-secret-0123456789abcdef"


class FakeConn:
    """Minimal stand-in for an asyncpg connection used by the login endpoint."""

    def __init__(self, row: Optional[dict] = None):
        self.row = row

    async def fetchrow(self, query: str, *args):
        return self.row

    async def execute(self, query: str, *args):
        return None


def _make_app(row: Optional[dict]) -> TestClient:
    async def _fake_db():
        yield FakeConn(row)

    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app)


@pytest.fixture(autouse=True)
def _patch_secret(monkeypatch):
    monkeypatch.setattr(settings, "auth_session_secret", SecretStr(TEST_SECRET))
    yield
    monkeypatch.undo()


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
def test_hash_and_verify_password():
    h = hash_password("S3cure-password!")
    assert h.startswith("pbkdf2_sha256$")
    assert verify_password("S3cure-password!", h) is True
    assert verify_password("wrong-password", h) is False


def test_verify_password_rejects_malformed_stored():
    assert verify_password("abc", "not-a-hash") is False


def test_verify_password_rejects_unknown_algo():
    h = hash_password("x")
    bad = h.replace("pbkdf2_sha256", "md5")
    assert verify_password("x", bad) is False


# ---------------------------------------------------------------------------
# Session tokens
# ---------------------------------------------------------------------------
def test_session_token_roundtrip():
    token = create_session_token(TEST_SECRET, "user-1", "merchant_northstar", 30)
    payload = verify_session_token(TEST_SECRET, token)
    assert payload is not None
    assert payload["sub"] == "user-1"
    assert payload["mid"] == "merchant_northstar"
    assert payload["exp"] > 0


def test_session_token_rejects_wrong_secret():
    token = create_session_token(TEST_SECRET, "user-1", "merchant_northstar", 30)
    assert verify_session_token("different-secret", token) is None


def test_session_token_rejects_tampered():
    token = create_session_token(TEST_SECRET, "user-1", "merchant_northstar", 30)
    assert verify_session_token(TEST_SECRET, token[:-3] + "abc") is None


def test_session_token_expired():
    token = create_session_token(TEST_SECRET, "user-1", "merchant_northstar", ttl_minutes=-1)
    assert verify_session_token(TEST_SECRET, token) is None


# ---------------------------------------------------------------------------
# Login endpoint
# ---------------------------------------------------------------------------
def _user_row(email="owner@operanta.io", active=True, password="Correct-Horse-Battery"):
    return {
        "id": "11111111-2222-3333-4444-555555555555",
        "merchant_id": "merchant_northstar",
        "email": email,
        "display_name": "Owner",
        "role": "owner",
        "is_active": active,
        "password_hash": hash_password(password),
    }


def test_login_success_returns_token_and_user():
    client = _make_app(_user_row())
    resp = client.post("/auth/login", json={"email": "OWNER@operanta.io", "password": "Correct-Horse-Battery"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    payload = verify_session_token(TEST_SECRET, body["access_token"])
    assert payload["mid"] == "merchant_northstar"
    assert body["user"]["email"] == "owner@operanta.io"


def test_login_invalid_password_returns_401():
    client = _make_app(_user_row())
    resp = client.post("/auth/login", json={"email": "owner@operanta.io", "password": "wrong-password"})
    assert resp.status_code == 401


def test_login_unknown_user_returns_401():
    client = _make_app(None)
    resp = client.post("/auth/login", json={"email": "nobody@operanta.io", "password": "whatever123"})
    assert resp.status_code == 401


def test_login_inactive_user_returns_401():
    client = _make_app(_user_row(active=False))
    resp = client.post("/auth/login", json={"email": "owner@operanta.io", "password": "Correct-Horse-Battery"})
    assert resp.status_code == 401


def test_login_bad_email_format_returns_422():
    client = _make_app(None)
    resp = client.post("/auth/login", json={"email": "not-an-email", "password": "whatever123"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /auth/me endpoint
# ---------------------------------------------------------------------------
def test_me_returns_current_user():
    app = FastAPI()
    app.include_router(auth_router)

    async def _fake_me():
        return {
            "id": "11111111-2222-3333-4444-555555555555",
            "merchant_id": "merchant_northstar",
            "email": "owner@operanta.io",
            "display_name": "Owner",
            "role": "owner",
        }

    app.dependency_overrides[get_current_user] = _fake_me
    client = TestClient(app)
    resp = client.get("/auth/me", headers={"Authorization": "Bearer whatever"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "owner@operanta.io"
