"""
backend/tests/test_paypal_m12.py
================================
M12.0 PayPal Sandbox tests (mocked HTTP — no live network / no DB connection).

Covers:
- OAuth2 token acquisition + in-memory cache.
- Create-order success/failure + approval URL extraction.
- Server-derived price/total validation.
- Stock validation and reservation release.
- Capture success/failure/pending handling.
- Amount and currency mismatch rejection.
- Duplicate capture requests (idempotent settlement).
- Canonical settlement: exactly one inventory decrement + one movement + one
  revenue ledger entry per item/order.
- Webhook signature verification (valid/invalid).
- Tenant-scoped helpers.

These tests mock asyncpg connections and the PayPal HTTP client; they never
touch the demo database or the live PayPal API.
"""

import base64
import json
import zlib
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.db.repositories.payment_repo import (
    cancel_online_order_and_release,
    create_online_order,
    release_expired_reservations,
    settle_capture,
)
from app.schemas.payments import OnlineCheckoutItemRequest, OnlineCheckoutRequest
from app.services.paypal_client import PayPalAPIError, PayPalAuthError, PayPalClient
from app.services.paypal_webhook import verify_webhook_signature
from app.api.v1.endpoints.payments import (
    build_paypal_order_payload,
    parse_capture_response,
    receive_paypal_webhook,
)
from app.api.v1.endpoints import payments as payments_mod

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeHTTP:
    def __init__(self, responses):
        self.responses = list(responses)
        self.post_calls = []

    async def post(self, url, headers=None, json=None, data=None, auth=None):
        self.post_calls.append({"url": url, "headers": headers, "json": json, "data": data})
        return self.responses.pop(0) if self.responses else FakeResponse(200, {})

    async def get(self, url):
        return self.responses.pop(0) if self.responses else FakeResponse(200, text="")

    async def aclose(self):
        return None


class FakeConn:
    """Minimal asyncpg-like connection for repository unit tests."""

    def __init__(self):
        self.fetchrow_q = []
        self.fetch_q = []
        self.execute_sql = []
        self.all_sql = []
        self.execute_result_q = []
        self._fr = 0
        self._f = 0

    def add_fetchrow(self, row):
        self.fetchrow_q.append(row)

    def add_fetch(self, rows):
        self.fetch_q.append(rows)

    def add_execute_result(self, result):
        self.execute_result_q.append(result)

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def fetchrow(self, sql, *args):
        self.all_sql.append(sql)
        if self._fr < len(self.fetchrow_q):
            row = self.fetchrow_q[self._fr]
            self._fr += 1
            return row
        return None

    async def fetch(self, sql, *args):
        self.all_sql.append(sql)
        if self._f < len(self.fetch_q):
            rows = self.fetch_q[self._f]
            self._f += 1
            return rows
        return []

    async def execute(self, sql, *args):
        self.execute_sql.append(sql)
        self.all_sql.append(sql)
        if self.execute_result_q:
            return self.execute_result_q.pop(0)
        return "INSERT 0 1"


def _settings(**overrides):
    base = {
        "paypal_base_url": "https://api-m.sandbox.paypal.com",
        "paypal_client_id": None,
        "paypal_client_secret": None,
        "paypal_webhook_id": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _order_row(**overrides):
    row = {
        "merchant_id": "merchant_northstar",
        "id": "order_online_abc123",
        "order_number": "ONLINE-ABC123",
        "channel": "online",
        "status": "pending",
        "subtotal": Decimal("100.00"),
        "tax_amount": Decimal("8.00"),
        "shipping_amount": Decimal("0.00"),
        "total_amount": Decimal("108.00"),
        "payment_method": "paypal",
        "paypal_order_id": "PAYPAL_ORDER_1",
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# PayPal client: token acquisition + caching
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_paypal_token_acquisition_is_cached():
    http = FakeHTTP([FakeResponse(200, {"access_token": "tok", "expires_in": 3600})])
    client = PayPalClient(_settings(), http=http)
    token1 = await client.get_access_token()
    token2 = await client.get_access_token()
    assert token1 == token2 == "tok"
    assert len(http.post_calls) == 1  # cached, only one auth call
    await client.aclose()


@pytest.mark.asyncio
async def test_paypal_token_missing_raises_auth_error():
    http = FakeHTTP([FakeResponse(200, {})])
    client = PayPalClient(_settings(), http=http)
    with pytest.raises(PayPalAuthError):
        await client.get_access_token()
    await client.aclose()


# ---------------------------------------------------------------------------
# PayPal client: create order + capture
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_paypal_create_order_success_and_approve_url():
    order_payload = {
        "id": "PAYPAL_ORDER_1",
        "status": "CREATED",
        "links": [{"rel": "approve", "href": "https://sandbox.paypal.com/approve/1"}],
    }
    http = FakeHTTP(
        [
            FakeResponse(200, {"access_token": "tok", "expires_in": 3600}),
            FakeResponse(200, order_payload),
        ]
    )
    client = PayPalClient(_settings(), http=http)
    data = await client.create_order({"intent": "CAPTURE"})
    assert data["id"] == "PAYPAL_ORDER_1"
    assert PayPalClient.extract_approve_url(data) == "https://sandbox.paypal.com/approve/1"
    assert len(http.post_calls) == 2
    assert http.post_calls[1]["headers"]["Authorization"] == "Bearer tok"
    await client.aclose()


@pytest.mark.asyncio
async def test_paypal_api_error_mapping():
    http = FakeHTTP(
        [
            FakeResponse(200, {"access_token": "tok", "expires_in": 3600}),
            FakeResponse(422, {"message": "ORDER_ALREADY_CAPTURED"}),
        ]
    )
    client = PayPalClient(_settings(), http=http)
    with pytest.raises(PayPalAPIError) as exc:
        await client.capture_order("PAYPAL_ORDER_1")
    assert exc.value.status_code == 422
    assert "already_captured" in str(exc.value.reason).lower()
    await client.aclose()


# ---------------------------------------------------------------------------
# Payload builders / parsers
# ---------------------------------------------------------------------------
def test_build_paypal_payload_uses_server_totals():
    order = {
        "merchant_id": "merchant_northstar",
        "order_id": "order_online_abc",
        "order_number": "ONLINE-1",
        "subtotal": Decimal("100.00"),
        "tax_amount": Decimal("8.00"),
        "total_amount": Decimal("108.00"),
        "items": [
            {"product_id": "p1", "product_name": "Vase", "quantity": 2, "unit_price": Decimal("50.00")}
        ],
    }
    payload = build_paypal_order_payload(order)
    assert payload["intent"] == "CAPTURE"
    pu = payload["purchase_units"][0]
    assert pu["amount"]["value"] == "108.00"
    assert pu["amount"]["breakdown"]["item_total"]["value"] == "100.00"
    assert pu["amount"]["breakdown"]["tax_total"]["value"] == "8.00"
    assert pu["custom_id"] == "merchant_northstar:order_online_abc"
    assert pu["items"][0]["unit_amount"]["value"] == "50.00"


def test_parse_capture_response_completed():
    data = {
        "status": "COMPLETED",
        "purchase_units": [
            {
                "payments": {
                    "captures": [
                        {
                            "id": "CAP_1",
                            "status": "COMPLETED",
                            "amount": {"value": "108.00", "currency_code": "USD"},
                        }
                    ]
                }
            }
        ],
    }
    parsed = parse_capture_response(data, Decimal("108.00"))
    assert parsed["paypal_capture_id"] == "CAP_1"
    assert parsed["status"] == "completed"
    assert parsed["amount"] == Decimal("108.00")


# ---------------------------------------------------------------------------
# Canonical settlement
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_settle_capture_happy_path_single_revenue_and_movement():
    conn = FakeConn()
    conn.add_fetchrow(_order_row())  # order lock
    conn.add_fetchrow(None)          # existing capture -> none
    conn.add_fetch([{"product_id": "p1", "quantity": 2}])  # order items
    conn.add_fetchrow({"stock_online": 10, "reserved_online": 2})  # inventory lock
    conn.add_fetchrow({"running_envelope_balance": Decimal("325.00")})  # ledger last
    conn.add_fetchrow({"id": 7})     # ledger insert returning

    result = await settle_capture(
        conn,
        "merchant_northstar",
        "order_online_abc123",
        {
            "paypal_capture_id": "CAP_1",
            "amount": Decimal("108.00"),
            "currency": "USD",
            "status": "completed",
            "raw_payload": {"id": "CAP_1"},
        },
    )

    assert result["settled"] is True
    assert result["order_status"] == "completed"
    assert result["revenue_ledger_id"] == 7
    sql = "\n".join(conn.all_sql)
    # Exactly one revenue ledger entry.
    assert sql.count("'revenue'") == 1
    # Exactly one inventory movement insert for the purchased item.
    assert sql.count("inventory_movements") == 1
    # Order transitioned to completed.
    assert "status = 'completed'" in sql


@pytest.mark.asyncio
async def test_settle_capture_idempotent_repeat_is_noop():
    existing_completed = {
        "id": "capture_x",
        "status": "completed",
        "paypal_capture_id": "CAP_1",
        "captured_amount": Decimal("108.00"),
        "currency": "USD",
    }
    conn = FakeConn()
    conn.add_fetchrow(_order_row())
    conn.add_fetchrow(existing_completed)  # capture already completed
    result = await settle_capture(
        conn,
        "merchant_northstar",
        "order_online_abc123",
        {"paypal_capture_id": "CAP_1", "amount": Decimal("108.00"), "currency": "USD", "status": "completed"},
    )
    assert result["already_settled"] is True
    assert conn.execute_sql == []  # no re-settlement


@pytest.mark.asyncio
async def test_settle_capture_rejects_second_distinct_capture_on_completed_order():
    conn = FakeConn()
    conn.add_fetchrow(_order_row(status="completed"))  # order lock
    conn.add_fetchrow(None)  # this capture id is NOT on record
    with pytest.raises(ValueError, match="already completed"):
        await settle_capture(
            conn,
            "merchant_northstar",
            "order_online_abc123",
            {"paypal_capture_id": "CAP_2", "amount": Decimal("108.00"), "currency": "USD", "status": "completed"},
        )
    assert conn.execute_sql == []  # nothing written


@pytest.mark.asyncio
async def test_settle_capture_rejects_currency_mismatch():
    conn = FakeConn()
    conn.add_fetchrow(_order_row())
    conn.add_fetchrow(None)
    with pytest.raises(ValueError, match="Currency mismatch"):
        await settle_capture(
            conn,
            "merchant_northstar",
            "order_online_abc123",
            {"paypal_capture_id": "CAP_1", "amount": Decimal("108.00"), "currency": "EUR", "status": "completed"},
        )


@pytest.mark.asyncio
async def test_settle_capture_rejects_amount_mismatch():
    conn = FakeConn()
    conn.add_fetchrow(_order_row())
    conn.add_fetchrow(None)
    with pytest.raises(ValueError, match="amount mismatch"):
        await settle_capture(
            conn,
            "merchant_northstar",
            "order_online_abc123",
            {"paypal_capture_id": "CAP_1", "amount": Decimal("50.00"), "currency": "USD", "status": "completed"},
        )


@pytest.mark.asyncio
async def test_settle_capture_pending_persists_without_settlement():
    conn = FakeConn()
    conn.add_fetchrow(_order_row())
    conn.add_fetchrow(None)
    result = await settle_capture(
        conn,
        "merchant_northstar",
        "order_online_abc123",
        {"paypal_capture_id": "", "amount": Decimal("108.00"), "currency": "USD", "status": "pending"},
    )
    assert result["settled"] is False
    assert result["capture_status"] == "pending"
    sql = "\n".join(conn.execute_sql)
    assert "inventory_movements" not in sql
    assert "'revenue'" not in sql


# ---------------------------------------------------------------------------
# Internal order creation + reservation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_online_order_rejects_insufficient_stock():
    conn = FakeConn()
    conn.add_fetch([])  # no expired reservations
    conn.add_fetch(
        [
            {
                "product_id": "p1",
                "stock_online": 5,
                "reserved_online": 5,
                "available_online": 0,
                "product_name": "Vase",
                "sku": "VASE-1",
                "selling_price": Decimal("50.00"),
            }
        ]
    )
    req = OnlineCheckoutRequest(items=[OnlineCheckoutItemRequest(product_id="p1", quantity=1)])
    with pytest.raises(ValueError, match="Insufficient online inventory"):
        await create_online_order(conn, "merchant_northstar", req)


@pytest.mark.asyncio
async def test_release_expired_reservations_cancels_orders():
    conn = FakeConn()
    conn.add_fetch([{"id": "order_online_expired"}])  # candidates
    # _cancel_pending_order: order lock -> items -> inventory lock -> update -> movement -> order update
    conn.add_fetchrow({"status": "pending"})
    conn.add_fetch([{"product_id": "p1", "quantity": 2}])
    conn.add_fetchrow({"stock_online": 10, "reserved_online": 4})
    released = await release_expired_reservations(conn, "merchant_northstar", ttl_minutes=120)
    assert released == 1
    sql = "\n".join(conn.execute_sql)
    assert "status = 'cancelled'" in sql


# ---------------------------------------------------------------------------
# Webhook signature verification
# ---------------------------------------------------------------------------
def _make_rsa_pair():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )

    def sign(message: bytes) -> str:
        sig = private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(sig).decode()

    return public_pem, sign


class CertHTTP:
    def __init__(self, pem, status=200):
        self.pem = pem
        self.status = status
        self.calls = 0

    async def get(self, url):
        self.calls += 1
        return FakeResponse(self.status, text=self.pem)


@pytest.mark.asyncio
async def test_verify_webhook_signature_valid_and_invalid():
    public_pem, sign = _make_rsa_pair()
    tid, ttime, whid, body = "tid1", "2026-01-01T00:00:00Z", "wh_1", '{"event_type":"x"}'
    composite = f"{tid}{ttime}{whid}{body}"
    # Known-vector guard: PayPal signs the DECIMAL CRC32, not hex.
    crc_decimal = str(zlib.crc32(composite.encode()) & 0xFFFFFFFF)
    crc_hex = format(zlib.crc32(composite.encode()) & 0xFFFFFFFF, "08x")
    assert crc_decimal != crc_hex
    valid_sig = sign(crc_decimal.encode())

    ok = await verify_webhook_signature(
        transmission_id=tid,
        transmission_time=ttime,
        webhook_id=whid,
        raw_body=body,
        signature_b64=valid_sig,
        cert_url="https://www.paypalobjects.com/webhook.pem",
        auth_algo="SHA256withRSA",
        http=CertHTTP(public_pem),
    )
    assert ok is True

    tampered = await verify_webhook_signature(
        transmission_id=tid,
        transmission_time=ttime,
        webhook_id=whid,
        raw_body='{"event_type":"y"}',
        signature_b64=valid_sig,
        cert_url="https://www.paypalobjects.com/webhook.pem",
        auth_algo="SHA256withRSA",
        http=CertHTTP(public_pem),
    )
    assert tampered is False


@pytest.mark.asyncio
async def test_verify_webhook_rejects_wrong_algo():
    ok = await verify_webhook_signature(
        transmission_id="t",
        transmission_time="ti",
        webhook_id="w",
        raw_body="{}",
        signature_b64="c2ln",
        cert_url="https://cert.example/x",
        auth_algo="SHA1withRSA",
        http=CertHTTP(""),
    )
    assert ok is False


# ---------------------------------------------------------------------------
# M12.1 hardening tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_verify_webhook_rejects_non_paypal_cert_url():
    # SSRF guard: certificate URL must be HTTPS on a PayPal-owned host.
    http = CertHTTP("")
    ok = await verify_webhook_signature(
        transmission_id="t",
        transmission_time="ti",
        webhook_id="w",
        raw_body="{}",
        signature_b64="c2ln",
        cert_url="http://evil.example/steal.pem",
        auth_algo="SHA256withRSA",
        http=http,
    )
    assert ok is False
    assert http.calls == 0  # no certificate fetch attempted


@pytest.mark.asyncio
async def test_verify_webhook_rejects_non_https_cert_url():
    http = CertHTTP("")
    ok = await verify_webhook_signature(
        transmission_id="t",
        transmission_time="ti",
        webhook_id="w",
        raw_body="{}",
        signature_b64="c2ln",
        cert_url="http://paypal.com/x.pem",
        auth_algo="SHA256withRSA",
        http=http,
    )
    assert ok is False
    assert http.calls == 0


@pytest.mark.asyncio
async def test_verify_webhook_rejects_missing_headers():
    ok = await verify_webhook_signature(
        transmission_id="",
        transmission_time="ti",
        webhook_id="w",
        raw_body="{}",
        signature_b64="c2ln",
        cert_url="https://www.paypalobjects.com/x.pem",
        auth_algo="SHA256withRSA",
        http=CertHTTP(""),
    )
    assert ok is False


@pytest.mark.asyncio
async def test_create_order_sends_deterministic_request_id():
    http = FakeHTTP(
        [
            FakeResponse(200, {"access_token": "tok", "expires_in": 3600}),
            FakeResponse(200, {"id": "ORD1", "links": []}),
        ]
    )
    client = PayPalClient(_settings(), http=http)
    await client.create_order({"intent": "CAPTURE"}, request_id="mg-cr-merchant_northstar-order_x")
    assert http.post_calls[1]["headers"].get("PayPal-Request-Id") == "mg-cr-merchant_northstar-order_x"
    await client.aclose()


@pytest.mark.asyncio
async def test_capture_sends_deterministic_request_id():
    http = FakeHTTP(
        [
            FakeResponse(200, {"access_token": "tok", "expires_in": 3600}),
            FakeResponse(200, {"status": "COMPLETED"}),
        ]
    )
    client = PayPalClient(_settings(), http=http)
    await client.capture_order("ORD1", request_id="mg-cp-merchant_northstar-order_x")
    assert http.post_calls[1]["headers"].get("PayPal-Request-Id") == "mg-cp-merchant_northstar-order_x"
    await client.aclose()


@pytest.mark.asyncio
async def test_cancel_online_order_and_release_releases_reservation():
    conn = FakeConn()
    conn.add_fetchrow({"status": "pending"})
    conn.add_fetch([{"product_id": "p1", "quantity": 2}])
    conn.add_fetchrow({"stock_online": 10, "reserved_online": 4})
    cancelled = await cancel_online_order_and_release(conn, "merchant_northstar", "order_x")
    assert cancelled is True
    sql = "\n".join(conn.execute_sql)
    assert "status = 'cancelled'" in sql
    assert "inventory_movements" in sql  # reservation release recorded


# ---- Webhook deduplication / retry (unit level) ----
class FakeSecret:
    def __init__(self, value):
        self.value = value

    def get_secret_value(self):
        return self.value


class FakeRequest:
    def __init__(self, headers, body_bytes):
        self.headers = headers
        self._body = body_bytes

    async def body(self):
        return self._body


def _completed_event_body():
    return json.dumps(
        {
            "id": "ev1",
            "event_type": "PAYMENT.CAPTURE.COMPLETED",
            "resource": {
                "capture": {
                    "id": "CAP1",
                    "custom_id": "merchant_northstar:order_online_abc",
                    "amount": {"value": "51.84", "currency_code": "USD"},
                }
            },
        }
    ).encode()


def _webhook_headers():
    return {
        "PayPal-Transmission-Id": "txn1",
        "PayPal-Transmission-Time": "2026-01-01T00:00:00Z",
        "PayPal-Transmission-Sig": "c2ln",
        "PayPal-Cert-Url": "https://www.paypalobjects.com/x.pem",
        "PayPal-Auth-Algo": "SHA256withRSA",
    }


def _patch_webhook_deps(monkeypatch, process_impl=None, verify_result=True):
    async def fake_verify(**kwargs):
        return verify_result

    monkeypatch.setattr(payments_mod, "verify_webhook_signature", fake_verify)
    if process_impl is not None:
        monkeypatch.setattr(payments_mod, "_process_capture_completed", process_impl)
    monkeypatch.setattr(payments_mod, "settings", SimpleNamespace(paypal_webhook_id=FakeSecret("wh")))


@pytest.mark.asyncio
async def test_webhook_first_delivery_processes(monkeypatch):
    called = {"process": 0}

    async def fake_process(conn, event):
        called["process"] += 1

    _patch_webhook_deps(monkeypatch, process_impl=fake_process)
    conn = FakeConn()
    receipt = await receive_paypal_webhook(FakeRequest(_webhook_headers(), _completed_event_body()), conn)
    assert receipt.status == "processed"
    assert called["process"] == 1
    assert sum("INSERT INTO webhook_events" in s for s in conn.execute_sql) == 1
    assert any("UPDATE webhook_events" in s for s in conn.execute_sql)


@pytest.mark.asyncio
async def test_webhook_duplicate_after_success_is_acked(monkeypatch):
    called = {"process": 0}

    async def fake_process(conn, event):
        called["process"] += 1

    _patch_webhook_deps(monkeypatch, process_impl=fake_process)

    # Simulate a row already claimed and processed.
    conn = FakeConn()
    conn.add_execute_result("INSERT 0 0")  # conflict on INSERT
    conn.add_fetchrow({"status": "processed"})  # existing processed row

    receipt = await receive_paypal_webhook(FakeRequest(_webhook_headers(), _completed_event_body()), conn)
    assert receipt.status == "duplicate"
    assert called["process"] == 0  # settled again
    assert not any("UPDATE webhook_events" in s for s in conn.execute_sql)  # no re-mark


@pytest.mark.asyncio
async def test_webhook_failure_then_retry_succeeds(monkeypatch):
    fail_first = {"fail": True}
    called = {"process": 0}

    async def flaky_process(conn, event):
        called["process"] += 1
        if fail_first["fail"]:
            raise RuntimeError("settlement boom")

    _patch_webhook_deps(monkeypatch, process_impl=flaky_process)

    # First delivery: processing fails -> transaction rolls back -> 500 propagates.
    conn1 = FakeConn()
    with pytest.raises(RuntimeError):
        await receive_paypal_webhook(FakeRequest(_webhook_headers(), _completed_event_body()), conn1)
    assert called["process"] == 1

    # Retry: processing succeeds -> processed.
    fail_first["fail"] = False
    conn2 = FakeConn()
    receipt = await receive_paypal_webhook(FakeRequest(_webhook_headers(), _completed_event_body()), conn2)
    assert receipt.status == "processed"
    assert called["process"] == 2
    assert any("UPDATE webhook_events" in s for s in conn2.execute_sql)
