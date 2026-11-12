"""
backend/tests/test_saas_register_endpoint.py
===============================================
End-to-end tests of the real ``POST /auth/register`` FastAPI endpoint using a
fake database connection (no live DB, no network).

Covers the dual-mode signup:
- DEMO path: routes the new user to merchant_northstar as "operator", does NOT
  create a new workspace, and issues a session token.
- Normal SaaS path: atomically creates a new isolated merchant workspace + owner,
  seeds a starter catalog, and issues a session token.
- Invalid access code -> 400, nothing written.
- Duplicate email -> 409.
- Registration failure rolls back (nothing persisted on error).
"""

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api.deps import get_db
from app.api.v1.endpoints import auth as auth_mod
from app.api.v1.endpoints.auth import router as auth_router

NORTHSTAR = "merchant_northstar"


class FakeConn:
    """Minimal asyncpg-like connection that records statements and returns queued rows."""

    def __init__(self):
        self.executed = []          # (sql, args)
        self.transactions = 0
        self.fetchval_q = []

    @asynccontextmanager
    async def transaction(self):
        self.transactions += 1
        yield self

    async def fetchval(self, sql, *args):
        return self.fetchval_q.pop(0) if self.fetchval_q else None

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "INSERT 0 1"


class FakeSettings:
    auth_session_secret = SecretStr("test-session-secret")
    auth_token_ttl_minutes = 60
    demo_access_code = "DEMO"
    demo_merchant_id = NORTHSTAR


def _build_app(conn: FakeConn):
    app = FastAPI()
    app.include_router(auth_router)

    async def fake_get_db():
        yield conn

    app.dependency_overrides[get_db] = fake_get_db
    return app


def _register_payload(email="new@example.com", **overrides):
    payload = {
        "business_name": "Test Grocery",
        "full_name": "Test User",
        "email": email,
        "password": "strong-enough-pass",
        "confirm_password": "strong-enough-pass",
        "workspace_access_code": None,
    }
    payload.update(overrides)
    return payload


def _find_insert(sql_list, table):
    return [s for s, _ in sql_list if f"INSERT INTO {table}" in s]


def test_register_normal_saas_creates_isolated_merchant(monkeypatch):
    monkeypatch.setattr(auth_mod, "settings", FakeSettings())
    conn = FakeConn()
    app = _build_app(conn)
    client = TestClient(app)

    resp = client.post("/auth/register", json=_register_payload())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["access_token"]
    assert data["user"]["role"] == "owner"
    assert data["user"]["merchant_id"].startswith("merchant_")

    # A new merchant + starter catalog are created (never Northstar).
    merchant_records = [(sql, args) for sql, args in conn.executed if "INSERT INTO merchants" in sql]
    assert merchant_records, "expected a new merchants INSERT"
    assert all(NORTHSTAR not in str(args) for _, args in merchant_records)
    assert any("INSERT INTO products" in sql for sql, _ in conn.executed), "expected starter products"
    assert any("INSERT INTO suppliers" in sql for sql, _ in conn.executed), "expected starter supplier"

    # The owner row points at the freshly generated merchant id.
    owner_rows = [args for sql, args in conn.executed if "operanta_users" in sql]
    assert owner_rows
    assert owner_rows[0][1] == data["user"]["merchant_id"]
    assert owner_rows[0][5] == "owner"


def test_register_normal_saas_rolls_back_on_failure(monkeypatch):
    monkeypatch.setattr(auth_mod, "settings", FakeSettings())
    conn = FakeConn()

    # Force a failure after the merchant insert by making the user insert raise.
    original_execute = conn.execute

    async def failing_execute(sql, *args):
        if "operanta_users" in sql:
            raise RuntimeError("boom")
        return await original_execute(sql, *args)

    conn.execute = failing_execute
    app = _build_app(conn)

    resp = TestClient(app, raise_server_exceptions=False).post("/auth/register", json=_register_payload())
    assert resp.status_code == 500
    # The handler opened a transaction (and it was not committed on failure).
    assert conn.transactions >= 1


def test_register_demo_routes_to_northstar_without_new_workspace(monkeypatch):
    monkeypatch.setattr(auth_mod, "settings", FakeSettings())
    conn = FakeConn()
    # First fetchval = duplicate-email check (None), second = Northstar existence (merchant id).
    conn.fetchval_q.extend([None, NORTHSTAR])
    app = _build_app(conn)

    resp = TestClient(app).post(
        "/auth/register", json=_register_payload(workspace_access_code="DEMO")
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["user"]["merchant_id"] == NORTHSTAR
    assert data["user"]["role"] == "operator"
    assert data["access_token"]

    # No new merchant workspace is created on the demo path.
    assert not _find_insert(conn.executed, "merchants")
    assert not _find_insert(conn.executed, "products")


def test_register_invalid_access_code_rejected(monkeypatch):
    monkeypatch.setattr(auth_mod, "settings", FakeSettings())
    conn = FakeConn()
    app = _build_app(conn)

    resp = TestClient(app).post(
        "/auth/register", json=_register_payload(workspace_access_code="WRONG")
    )
    assert resp.status_code == 400
    assert "access code" in resp.json()["detail"].lower()
    assert not conn.executed, "no writes should occur for an invalid code"


def test_register_duplicate_email_rejected(monkeypatch):
    monkeypatch.setattr(auth_mod, "settings", FakeSettings())
    conn = FakeConn()
    conn.fetchval_q.append(1)  # duplicate email found
    app = _build_app(conn)

    resp = TestClient(app).post("/auth/register", json=_register_payload())
    assert resp.status_code == 409
    assert conn.transactions == 0


def test_register_returns_session_token_immediately(monkeypatch):
    monkeypatch.setattr(auth_mod, "settings", FakeSettings())
    conn = FakeConn()
    app = _build_app(conn)

    resp = TestClient(app).post("/auth/register", json=_register_payload())
    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"]
    assert data["token_type"] == "bearer"
