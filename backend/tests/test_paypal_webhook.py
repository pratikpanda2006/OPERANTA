"""
backend/tests/test_paypal_webhook.py
====================================
Security-focused tests for the PayPal webhook route:
signature validation (valid/invalid), replay/idempotency, and malformed-payload
handling. All run offline with no network and no real PayPal.

Idempotency is enforced at the storage layer by the `webhook_events` primary
key (provider, event_id) + INSERT .. ON CONFLICT DO NOTHING.
"""

import contextlib

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.deps import get_db
from app.core.config import settings
from app.main import app
from app.services.paypal_webhook import verify_webhook_signature

EVENT_ID = "WH-WEBHOOK-1234"


class FakeConn:
    def __init__(self, insert_result: str = "INSERT 0 1", existing: dict | None = None):
        self.insert_result = insert_result
        self.existing = existing
        self.executed = []

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield self

    async def execute(self, sql: str, *args):
        self.executed.append((sql, args))
        if "INSERT INTO webhook_events" in sql:
            return self.insert_result
        return "UPDATE 1"

    async def fetchrow(self, sql: str, *args):
        return self.existing


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _override_db(conn: FakeConn):
    async def _db():
        yield conn
    app.dependency_overrides[get_db] = _db


def _payload(**overrides) -> dict:
    data = {
        "id": EVENT_ID,
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {"id": "0FW75185A1129424B", "amount": {"value": "25.92", "currency_code": "USD"}},
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# 1. Fail closed when webhook is not configured
# ---------------------------------------------------------------------------

def test_webhook_fail_closed_when_not_configured(client):
    _override_db(FakeConn())
    assert settings.paypal_webhook_id is None
    resp = client.post("/api/v1/webhooks/paypal", json=_payload())
    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 2. Invalid signature -> 400
# ---------------------------------------------------------------------------

def test_webhook_invalid_signature_rejected(client, monkeypatch):
    _override_db(FakeConn())
    monkeypatch.setattr(settings, "paypal_webhook_id", SecretStr("wh_test"))

    async def fake_verify(*a, **k):
        return False

    monkeypatch.setattr("app.api.v1.endpoints.payments.verify_webhook_signature", fake_verify)
    resp = client.post("/api/v1/webhooks/paypal", json=_payload())
    assert resp.status_code == 400
    assert "Invalid webhook signature" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 3. Valid signature path proceeds (malformed payload -> 400)
# ---------------------------------------------------------------------------

def test_webhook_malformed_payload_400(client, monkeypatch):
    _override_db(FakeConn())
    monkeypatch.setattr(settings, "paypal_webhook_id", SecretStr("wh_test"))

    async def fake_verify(*a, **k):
        return True

    monkeypatch.setattr("app.api.v1.endpoints.payments.verify_webhook_signature", fake_verify)
    resp = client.post("/api/v1/webhooks/paypal", content="this is not json", headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert "Malformed" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 4. Missing event id -> 400
# ---------------------------------------------------------------------------

def test_webhook_missing_event_id_rejected(client, monkeypatch):
    _override_db(FakeConn())
    monkeypatch.setattr(settings, "paypal_webhook_id", SecretStr("wh_test"))

    async def fake_verify(*a, **k):
        return True

    monkeypatch.setattr("app.api.v1.endpoints.payments.verify_webhook_signature", fake_verify)
    resp = client.post("/api/v1/webhooks/paypal", json=_payload(id=None))
    assert resp.status_code == 400
    assert "missing event id" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 5. Replay of a processed event is acknowledged, not re-settled
# ---------------------------------------------------------------------------

def test_webhook_duplicate_replay_acknowledged(client, monkeypatch):
    conn = FakeConn(insert_result="INSERT 0 0", existing={"status": "processed"})
    _override_db(conn)
    monkeypatch.setattr(settings, "paypal_webhook_id", SecretStr("wh_test"))

    async def fake_verify(*a, **k):
        return True

    monkeypatch.setattr("app.api.v1.endpoints.payments.verify_webhook_signature", fake_verify)
    resp = client.post("/api/v1/webhooks/paypal", json=_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["event_id"] == EVENT_ID
    assert body["status"] == "duplicate"


# ---------------------------------------------------------------------------
# 6. Signature verifier rejects bad inputs (offline unit checks)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_signature_rejects_missing_headers():
    valid = await verify_webhook_signature(
        transmission_id="", transmission_time="", webhook_id="", signature_b64="", cert_url="", auth_algo="", raw_body="",
        http=None,
    )
    assert valid is False


@pytest.mark.asyncio
async def test_verify_signature_rejects_bad_base64():
    valid = await verify_webhook_signature(
        transmission_id="t1",
        transmission_time="2024-01-01T00:00:00Z",
        webhook_id="wh",
        signature_b64="!!!not-base64!!!",
        cert_url="https://example.com/cert.pem",
        auth_algo="SHA256withRSA",
        raw_body="{}",
        http=None,
    )
    assert valid is False


@pytest.mark.asyncio
async def test_verify_signature_rejects_unsupported_algo():
    valid = await verify_webhook_signature(
        transmission_id="t1",
        transmission_time="2024-01-01T00:00:00Z",
        webhook_id="wh",
        signature_b64="c2ln",
        cert_url="https://example.com/cert.pem",
        auth_algo="MD5withRSA",
        raw_body="{}",
        http=None,
    )
    assert valid is False
