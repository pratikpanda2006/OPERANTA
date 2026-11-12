"""
backend/tests/test_paypal_context_m12_3_6.py
=============================================
Offline tests for the M12.3.6 PayPal hosted-checkout experience-context and
approval-link correction. No live network / DB / PayPal calls.
"""

import contextlib

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_merchant_context
from app.api.v1.endpoints.payments import build_paypal_order_payload
from app.core.config import settings
from app.main import app
from app.services.paypal_client import PayPalClient

MERCHANT = "merchant_northstar"
ORDER_ID = "order_online_c68eb0882980"


def _order(**overrides):
    base = {
        "order_id": ORDER_ID,
        "order_number": "ONLINE-ABC123",
        "merchant_id": MERCHANT,
        "subtotal": 1.50,
        "tax_amount": 0.12,
        "total_amount": 1.62,
        "items": [
            {
                "product_name": "Handcrafted Blue Ceramic Vase",
                "quantity": 1,
                "unit_price": 1.50,
            }
        ],
    }
    base.update(overrides)
    return base


class FakeConn:
    """Minimal asyncpg-like connection (same shape as other offline tests)."""

    def __init__(self):
        self.fetchrow_q = []
        self.fetch_q = []
        self.execute_sql = []
        self.all_sql = []
        self._fr = 0
        self._f = 0

    def add_fetchrow(self, row):
        self.fetchrow_q.append(row)

    def add_fetch(self, rows):
        self.fetch_q.append(rows)

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield self

    async def fetchrow(self, sql, *args):
        self.all_sql.append((sql, args))
        if self._fr < len(self.fetchrow_q):
            row = self.fetchrow_q[self._fr]
            self._fr += 1
            return row
        return None

    async def fetch(self, sql, *args):
        self.all_sql.append((sql, args))
        if self._f < len(self.fetch_q):
            rows = self.fetch_q[self._f]
            self._f += 1
            return rows
        return []

    async def execute(self, sql, *args):
        self.execute_sql.append(sql)
        self.all_sql.append((sql, args))
        return "UPDATE 1"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _override_db(conn):
    async def _get_db():
        yield conn
    app.dependency_overrides[get_db] = _get_db


def _override_merchant(merchant=MERCHANT):
    async def _get_merchant_context():
        return merchant
    app.dependency_overrides[get_merchant_context] = _get_merchant_context


def _clear_overrides():
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. Experience-context payload structure
# ---------------------------------------------------------------------------

def test_experience_context_payload_structure(monkeypatch):
    monkeypatch.setattr(settings, "app_base_url", "http://127.0.0.1:8000")
    payload = build_paypal_order_payload(_order())

    assert payload["intent"] == "CAPTURE"
    ec = payload["payment_source"]["paypal"]["experience_context"]
    assert ec["user_action"] == "CONTINUE"
    assert ec["brand_name"] == "MarginGuard"
    assert ec["return_url"] == (
        f"http://127.0.0.1:8000/api/v1/orders/online-checkout/{ORDER_ID}/return"
    )
    assert ec["cancel_url"] == (
        f"http://127.0.0.1:8000/api/v1/orders/online-checkout/{ORDER_ID}/cancel"
    )


def test_callback_urls_follow_configured_base(monkeypatch):
    monkeypatch.setattr(settings, "app_base_url", "https://mg.example.com")
    payload = build_paypal_order_payload(_order())
    ec = payload["payment_source"]["paypal"]["experience_context"]
    assert ec["return_url"].startswith("https://mg.example.com")
    assert ec["cancel_url"].startswith("https://mg.example.com")
    assert "127.0.0.1" not in ec["return_url"] and "localhost" not in ec["return_url"]


def test_non_localhost_http_base_rejected(monkeypatch):
    monkeypatch.setattr(settings, "app_base_url", "http://mg.example.com")
    with pytest.raises(ValueError):
        build_paypal_order_payload(_order())


def test_invalid_base_url_rejected(monkeypatch):
    monkeypatch.setattr(settings, "app_base_url", "not-a-url")
    with pytest.raises(ValueError):
        build_paypal_order_payload(_order())


# ---------------------------------------------------------------------------
# 2. Approval-link extraction
# ---------------------------------------------------------------------------

def test_payer_action_preferred():
    order = {
        "links": [
            {"rel": "self", "href": "https://api-m.sandbox.paypal.com/v2/checkout/orders/SC"},
            {"rel": "payer-action", "href": "https://www.sandbox.paypal.com/checkoutnow?token=SC"},
            {"rel": "approve", "href": "https://www.sandbox.paypal.com/checkoutnow?token=SC"},
        ]
    }
    assert PayPalClient.extract_approve_url(order) == (
        "https://www.sandbox.paypal.com/checkoutnow?token=SC"
    )


def test_approve_fallback():
    order = {"links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/checkoutnow?token=SC"}]}
    assert PayPalClient.extract_approve_url(order) == (
        "https://www.sandbox.paypal.com/checkoutnow?token=SC"
    )


def test_missing_approval_relation_returns_none():
    assert PayPalClient.extract_approve_url({"links": [{"rel": "self", "href": "x"}]}) is None
    assert PayPalClient.extract_approve_url({"links": []}) is None
    assert PayPalClient.extract_approve_url({}) is None


# ---------------------------------------------------------------------------
# 3. Return handler
# ---------------------------------------------------------------------------

def test_return_handler_validates_token_and_does_not_capture(client):
    conn = FakeConn()
    conn.add_fetchrow({  # get_online_order_by_id
        "id": ORDER_ID,
        "order_number": "ONLINE-ABC123",
        "channel": "online",
        "status": "pending",
        "paypal_order_id": "SC-TOKEN-1",
    })
    _override_db(conn)
    _override_merchant()
    try:
        resp = client.get(f"/api/v1/orders/online-checkout/{ORDER_ID}/return?token=SC-TOKEN-1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "pending"
        assert "capture" in body["message"].lower()
        # Read-only: no DB writes at all (no auto-capture).
        assert conn.execute_sql == []
    finally:
        _clear_overrides()


def test_return_handler_rejects_wrong_token(client):
    conn = FakeConn()
    conn.add_fetchrow({
        "id": ORDER_ID,
        "order_number": "ONLINE-ABC123",
        "channel": "online",
        "status": "pending",
        "paypal_order_id": "SC-TOKEN-1",
    })
    _override_db(conn)
    _override_merchant()
    try:
        resp = client.get(f"/api/v1/orders/online-checkout/{ORDER_ID}/return?token=WRONG")
        assert resp.status_code == 400
    finally:
        _clear_overrides()


# ---------------------------------------------------------------------------
# 4. Cancel handler
# ---------------------------------------------------------------------------

def _pending_order():
    return {
        "id": ORDER_ID,
        "order_number": "ONLINE-ABC123",
        "channel": "online",
        "status": "pending",
        "paypal_order_id": "SC-TOKEN-1",
        "subtotal": 1.50, "tax_amount": 0.12, "total_amount": 1.62,
    }


def _cancel_conn(extra_get=False):
    conn = FakeConn()
    # First get_online_order_by_id.
    conn.add_fetchrow(_pending_order())
    if extra_get:
        # PayPal callback path also fetches the order once more (callback).
        conn.add_fetchrow(_pending_order())
    # cancel_online_order_and_release: order FOR UPDATE
    conn.add_fetchrow(_pending_order())
    # items
    conn.add_fetch([{"product_id": "prod_blue_vase", "quantity": 1}])
    # inventory row per item
    conn.add_fetchrow({"stock_online": 1, "reserved_online": 1})
    return conn


def test_cancel_handler_releases_reservation_once(client):
    conn = _cancel_conn(extra_get=True)
    _override_db(conn)
    _override_merchant()
    try:
        resp = client.get(f"/api/v1/orders/online-checkout/{ORDER_ID}/cancel?token=SC-TOKEN-1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["cancelled"] is True
        assert body["status"] == "cancelled"

        # Order marked cancelled.
        assert any("UPDATE orders" in s and "cancelled" in s for s in conn.execute_sql)
        # Exactly one reservation-release movement (adjustment, reserved_delta<0).
        releases = [s for s in conn.execute_sql if "INSERT INTO inventory_movements" in s]
        assert len(releases) == 1
    finally:
        _clear_overrides()


def test_cancel_rejects_completed_order(client):
    conn = FakeConn()
    completed = {
        "id": ORDER_ID, "order_number": "ONLINE-ABC123", "channel": "online",
        "status": "completed", "paypal_order_id": "SC-TOKEN-1",
    }
    conn.add_fetchrow(completed)  # callback validation
    conn.add_fetchrow(completed)  # _cancel_pending_checkout
    _override_db(conn)
    _override_merchant()
    try:
        resp = client.get(f"/api/v1/orders/online-checkout/{ORDER_ID}/cancel?token=SC-TOKEN-1")
        assert resp.status_code == 409
        # No mutation attempted.
        assert conn.execute_sql == []
    finally:
        _clear_overrides()


def test_owner_cancel_post_releases_reservation(client):
    conn = _cancel_conn()
    _override_db(conn)
    _override_merchant()
    try:
        resp = client.post(f"/api/v1/orders/online-checkout/{ORDER_ID}/cancel")
        assert resp.status_code == 200
        assert resp.json()["cancelled"] is True
    finally:
        _clear_overrides()


def test_cancel_tenant_isolation_missing_order(client):
    conn = FakeConn()
    conn.add_fetchrow(None)  # order not found for this merchant
    _override_db(conn)
    _override_merchant()
    try:
        resp = client.post(f"/api/v1/orders/online-checkout/{ORDER_ID}/cancel")
        assert resp.status_code == 404
    finally:
        _clear_overrides()
