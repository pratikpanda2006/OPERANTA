"""
backend/tests/test_milestone7.py
================================
Unit and integration tests for MarginGuard Milestone 7:
- Stage 7.1: Lifespan management and /health, /health/ready endpoints.
- Stage 7.2: Tenant context dependency, DEMO_MODE enforcement, and Pydantic schemas.
- Stage 7.3: Repository queries, multi-tenant isolation, Decimal precision, empty state.
- Stage 7.4: API v1 route handlers, status codes, and database-unavailable degradation.
"""

import asyncio
import json
from decimal import Decimal
from typing import Any, Dict, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.deps import get_merchant_context
from app.core.config import Settings
from app.db.connection import close_db_pool, init_db_pool, is_pool_ready
from app.db.repositories.inventory_repo import list_inventory_items
from app.db.repositories.merchant_repo import get_spend_envelope
from app.db.repositories.supplier_repo import list_suppliers_with_offers
from app.main import app
from app.schemas.inventory import InventoryItemResponse, InventoryListResponse
from app.schemas.merchant import SpendEnvelopeResponse
from app.schemas.supplier import SupplierListResponse, SupplierResponse


# ---------------------------------------------------------------------------
# ASGI Test Helper (Zero extra dependencies)
# ---------------------------------------------------------------------------

async def make_asgi_request(
    target_app,
    method: str,
    path: str,
    headers: list = None,
    body: bytes = b"",
) -> Tuple[int, Dict[str, str], Any]:
    """
    Execute an in-memory ASGI request against the FastAPI application.
    Returns (status_code, headers_dict, parsed_json_or_text).
    """
    response_body = []
    response_status = 500
    response_headers = {}

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        nonlocal response_status, response_headers
        if message["type"] == "http.response.start":
            response_status = message["status"]
            response_headers = {
                k.decode("latin1").lower(): v.decode("latin1")
                for k, v in message.get("headers", [])
            }
        elif message["type"] == "http.response.body":
            response_body.append(message.get("body", b""))

    raw_path = path.encode("ascii")
    query_string = b""
    if b"?" in raw_path:
        raw_path, query_string = raw_path.split(b"?", 1)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": raw_path.decode("ascii"),
        "raw_path": raw_path,
        "query_string": query_string,
        "headers": [(k.lower().encode("latin1"), v.encode("latin1")) for k, v in (headers or [])],
        "app": target_app,
        "state": getattr(target_app, "state", MagicMock()),
    }

    await target_app(scope, receive, send)

    raw_text = b"".join(response_body).decode("utf-8")
    try:
        parsed_data = json.loads(raw_text)
    except Exception:
        parsed_data = raw_text

    return response_status, response_headers, parsed_data


# ---------------------------------------------------------------------------
# 1. Lifespan & Connection Management Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_init_db_pool_unconfigured():
    """When DATABASE_URL is not configured, init_db_pool returns None without failing."""
    with patch("app.db.connection.settings.database_url", None):
        pool = await init_db_pool()
        assert pool is None
        assert is_pool_ready(pool) is False


@pytest.mark.asyncio
async def test_close_db_pool_idempotent():
    """close_db_pool handles None gracefully and does not raise."""
    await close_db_pool(None)


@pytest.mark.asyncio
async def test_is_pool_ready_states():
    """is_pool_ready accurately reports whether a pool is open or closed."""
    assert is_pool_ready(None) is False

    mock_pool = MagicMock()
    mock_pool._closed = False
    assert is_pool_ready(mock_pool) is True

    mock_pool._closed = True
    assert is_pool_ready(mock_pool) is False


# ---------------------------------------------------------------------------
# 2. Health & Readiness Endpoint Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_liveness_preserves_frontend_contract():
    """GET /health must return the exact 4 string fields expected by frontend."""
    status_code, _, body = await make_asgi_request(app, "GET", "/health")
    assert status_code == 200
    assert body["status"] == "healthy"
    assert body["service"] == "marginguard-backend"
    assert body["version"] == "0.1.0"
    assert body["pilot_merchant"] == "Northstar Living"


@pytest.mark.asyncio
async def test_health_ready_unconfigured_mode():
    """GET /health/ready returns 200 ready when DATABASE_URL is unconfigured."""
    app.state.db_pool = None
    with patch("app.main.settings.database_url", None):
        status_code, _, body = await make_asgi_request(app, "GET", "/health/ready")
        assert status_code == 200
        assert body["status"] == "ready"
        assert body["database"] == "unconfigured"


@pytest.mark.asyncio
async def test_health_ready_disconnected_when_configured():
    """GET /health/ready returns 503 when DATABASE_URL is set but pool is not ready."""
    app.state.db_pool = None
    with patch("app.main.settings.database_url", MagicMock()):
        status_code, _, body = await make_asgi_request(app, "GET", "/health/ready")
        assert status_code == 503
        assert body["status"] == "not_ready"
        assert body["database"] == "disconnected"


@pytest.mark.asyncio
async def test_health_ready_connected_success():
    """GET /health/ready returns 200 when pool responds to ping probe."""
    mock_conn = AsyncMock()
    mock_conn.fetchval.return_value = 1

    mock_pool = MagicMock()
    mock_pool._closed = False
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

    app.state.db_pool = mock_pool
    with patch("app.main.settings.database_url", MagicMock()):
        status_code, _, body = await make_asgi_request(app, "GET", "/health/ready")
        assert status_code == 200
        assert body["status"] == "ready"
        assert body["database"] == "connected"


@pytest.mark.asyncio
async def test_health_ready_ping_failure():
    """GET /health/ready returns 503 and masks internal details if ping raises."""
    mock_conn = AsyncMock()
    mock_conn.fetchval.side_effect = Exception("Internal socket timeout")

    mock_pool = MagicMock()
    mock_pool._closed = False
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

    app.state.db_pool = mock_pool
    with patch("app.main.settings.database_url", MagicMock()):
        status_code, _, body = await make_asgi_request(app, "GET", "/health/ready")
        assert status_code == 503
        assert body["status"] == "not_ready"
        assert body["database"] == "unavailable"
        # Never leak exception text or connection details
        assert "Internal socket timeout" not in str(body)


# ---------------------------------------------------------------------------
# 3. Tenant Context & Multi-Tenancy Security Tests
# ---------------------------------------------------------------------------

def test_tenant_context_demo_mode_disabled():
    """When DEMO_MODE is False, get_merchant_context raises 403 Forbidden."""
    with patch("app.api.deps.settings.demo_mode", False):
        with pytest.raises(HTTPException) as exc_info:
            # request object is injected by FastAPI in real use; demo mode ignores it.
            get_merchant_context(None)
        assert exc_info.value.status_code == 403
        assert "Tenant access denied" in exc_info.value.detail


def test_tenant_context_demo_mode_enabled_valid():
    """When DEMO_MODE is True, get_merchant_context returns configured DEMO_MERCHANT_ID."""
    with patch("app.api.deps.settings.demo_mode", True):
        with patch("app.api.deps.settings.demo_merchant_id", "merchant_northstar"):
            merchant_id = get_merchant_context(None)
            assert merchant_id == "merchant_northstar"


def test_tenant_context_demo_mode_enabled_missing_id():
    """When DEMO_MODE is True but DEMO_MERCHANT_ID is empty, raises 500 error."""
    with patch("app.api.deps.settings.demo_mode", True):
        with patch("app.api.deps.settings.demo_merchant_id", None):
            with pytest.raises(HTTPException) as exc_info:
                get_merchant_context(None)
            assert exc_info.value.status_code == 500
            assert "DEMO_MERCHANT_ID is not set" in exc_info.value.detail


# ---------------------------------------------------------------------------
# 4. Pydantic Schemas Decimal Precision Tests
# ---------------------------------------------------------------------------

def test_spend_envelope_decimal_precision():
    """SpendEnvelopeResponse preserves exact Decimal monetary accuracy."""
    data = {
        "merchant_id": "merchant_northstar",
        "demo_envelope_cap": Decimal("500.00"),
        "max_order_cost_ceiling": Decimal("150.00"),
        "committed_spend": Decimal("175.00"),
        "available_spend": Decimal("325.00"),
        "merchant_created_at": "2026-11-09T00:00:00Z",
        "currency": "USD",
    }
    envelope = SpendEnvelopeResponse.model_validate(data)
    assert envelope.demo_envelope_cap == Decimal("500.00")
    assert envelope.available_spend == Decimal("325.00")
    assert isinstance(envelope.available_spend, Decimal)


def test_inventory_item_reorder_recommendation_flag():
    """InventoryItemResponse accurately calculates reorder_recommended boolean."""
    item_data = {
        "id": "inv_01",
        "merchant_id": "merchant_northstar",
        "product_id": "prod_01",
        "sku": "SKU-001",
        "product_name": "Test Vase",
        "category": "home_decor",
        "retail_price": Decimal("38.00"),
        "base_cost": Decimal("18.00"),
        "stock_in_store": 12,
        "reserved_in_store": 0,
        "available_in_store": 12,
        "stock_online": 0,
        "reserved_online": 0,
        "available_online": 0,
        "total_on_hand": 12,
        "total_available": 12,
        "reorder_point": 14,
        "daily_demand": Decimal("4.00"),
        "lead_days": 2,
        "safety_stock": 6,
        "reorder_recommended": True,
    }
    item = InventoryItemResponse.model_validate(item_data)
    assert item.reorder_recommended is True
    assert item.total_available <= item.reorder_point


# ---------------------------------------------------------------------------
# 5. Repository Layer Tests (Multi-Tenant Isolation & Positional Parameters)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_merchant_repo_queries_scoped():
    """get_spend_envelope queries merchants and financial_ledger with $1 merchant_id.

    Column alignment with migration 001:
      - merchants row keys use the SQL aliases: demo_envelope_cap, max_order_cost_ceiling,
        merchant_created_at (aliased from active_demo_week_cap, per_order_limit, created_at).
      - financial_ledger key is running_envelope_balance (not running_balance).
    """
    mock_conn = AsyncMock()
    mock_conn.fetchrow.side_effect = [
        # 1. merchants row — keys match SQL column aliases in merchant_repo.py
        {
            "merchant_id": "merchant_northstar",
            "demo_envelope_cap": Decimal("500.00"),
            "max_order_cost_ceiling": Decimal("150.00"),
            "merchant_created_at": "2026-11-09T00:00:00Z",
        },
        # 2. financial_ledger row — key matches actual column: running_envelope_balance
        {
            "running_envelope_balance": Decimal("325.00"),
        },
    ]

    envelope = await get_spend_envelope(mock_conn, "merchant_northstar")
    assert envelope is not None
    assert envelope["merchant_id"] == "merchant_northstar"
    assert envelope["demo_envelope_cap"] == Decimal("500.00")
    assert envelope["available_spend"] == Decimal("325.00")
    assert envelope["committed_spend"] == Decimal("175.00")
    assert envelope["merchant_created_at"] == "2026-11-09T00:00:00Z"

    # Verify both queries used positional parameter $1 with the exact merchant_id
    calls = mock_conn.fetchrow.call_args_list
    assert len(calls) == 2
    assert "$1" in calls[0][0][0] and calls[0][0][1] == "merchant_northstar"
    assert "$1" in calls[1][0][0] and calls[1][0][1] == "merchant_northstar"


@pytest.mark.asyncio
async def test_merchant_repo_empty_state_returns_none():
    """get_spend_envelope returns None when merchant is not in database."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = None

    envelope = await get_spend_envelope(mock_conn, "nonexistent_merchant")
    assert envelope is None


@pytest.mark.asyncio
async def test_inventory_repo_queries_scoped():
    """list_inventory_items queries inventory_items with $1 merchant_id and JOINs products."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [
        {
            "id": "inv_blue_vase",
            "merchant_id": "merchant_northstar",
            "product_id": "prod_blue_vase",
            "sku": "SKU-DEC-001",
            "product_name": "Handcrafted Blue Ceramic Vase",
            "category": "home_decor",
            "retail_price": Decimal("38.00"),
            "base_cost": Decimal("18.00"),
            "stock_in_store": 12,
            "reserved_in_store": 0,
            "available_in_store": 12,
            "stock_online": 0,
            "reserved_online": 0,
            "available_online": 0,
            "total_on_hand": 12,
            "total_available": 12,
            "reorder_point": 14,
            "daily_demand": Decimal("4.00"),
            "lead_days": 2,
            "safety_stock": 6,
        }
    ]

    items = await list_inventory_items(mock_conn, "merchant_northstar")
    assert len(items) == 1
    assert items[0]["sku"] == "SKU-DEC-001"
    assert items[0]["reorder_recommended"] is True  # 12 <= 14

    query, merchant_arg = mock_conn.fetch.call_args[0]
    assert "WHERE i.merchant_id = $1" in query
    assert merchant_arg == "merchant_northstar"


@pytest.mark.asyncio
async def test_supplier_repo_two_query_batching_no_n_plus_one():
    """list_suppliers_with_offers executes exactly two queries, both scoped to merchant_id.

    Mock data aligned with migration 001:
      - suppliers: has trust_tier (not is_allowlisted), no lead_time_days, no payment_terms
      - supplier_offers: has lead_time_days, NO is_preferred column
      - is_allowlisted is derived from trust_tier == 'allowlisted' in the repo layer
      - supplier-level lead_time_days is derived as min(offer lead_time_days)
    """
    mock_conn = AsyncMock()
    mock_conn.fetch.side_effect = [
        # Suppliers query — keys match migration 001 suppliers columns
        [
            {
                "id": "sup_supplier_c",
                "merchant_id": "merchant_northstar",
                "name": "Heritage Pottery Studio",
                "contact_email": "supplier_c@example.com",
                "trust_tier": "allowlisted",
                "reliability_score": 90,
                "is_illustrative": True,
            }
        ],
        # Offers query — keys match migration 001 supplier_offers columns (no is_preferred)
        [
            {
                "offer_id": "offer_c_01",
                "merchant_id": "merchant_northstar",
                "supplier_id": "sup_supplier_c",
                "product_id": "prod_blue_vase",
                "unit_cost": Decimal("4.10"),
                "shipping_cost": Decimal("8.00"),
                "lead_time_days": 2,
                "moq": 20,
                "is_illustrative": True,
            }
        ],
    ]

    suppliers = await list_suppliers_with_offers(mock_conn, "merchant_northstar")
    assert len(suppliers) == 1
    assert suppliers[0]["id"] == "sup_supplier_c"
    assert suppliers[0]["is_allowlisted"] is True       # derived from trust_tier
    assert suppliers[0]["lead_time_days"] == 2           # derived as min(offer lead_time_days)
    assert len(suppliers[0]["offers"]) == 1
    assert suppliers[0]["offers"][0]["unit_cost"] == Decimal("4.10")
    assert suppliers[0]["offers"][0]["lead_time_days"] == 2
    assert "is_preferred" not in suppliers[0]["offers"][0]  # not in migration 001 schema

    calls = mock_conn.fetch.call_args_list
    assert len(calls) == 2
    assert "WHERE merchant_id = $1" in calls[0][0][0]
    assert "WHERE merchant_id = $1" in calls[1][0][0]


# ---------------------------------------------------------------------------
# 6. REST API Endpoints & Error Handling Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_database_unavailable_returns_503():
    """When db_pool is None, calling any data endpoint returns 503 Service Unavailable."""
    app.state.db_pool = None
    with patch("app.api.deps.settings.demo_mode", True):
        with patch("app.api.deps.settings.demo_merchant_id", "merchant_northstar"):
            status_code, _, body = await make_asgi_request(app, "GET", "/api/v1/inventory")
            assert status_code == 503
            assert "Database service unavailable" in body["detail"]


@pytest.mark.asyncio
async def test_api_demo_mode_disabled_returns_403():
    """When DEMO_MODE is False, API endpoints return 403 Forbidden."""
    with patch("app.api.deps.settings.demo_mode", False):
        status_code, _, body = await make_asgi_request(app, "GET", "/api/v1/inventory")
        assert status_code == 403
        assert "Tenant access denied" in body["detail"]


@pytest.mark.asyncio
async def test_api_inventory_endpoint_success():
    """GET /api/v1/inventory returns validated InventoryListResponse."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = [
        {
            "id": "inv_blue_vase",
            "merchant_id": "merchant_northstar",
            "product_id": "prod_blue_vase",
            "sku": "SKU-DEC-001",
            "product_name": "Handcrafted Blue Ceramic Vase",
            "category": "home_decor",
            "retail_price": Decimal("38.00"),
            "base_cost": Decimal("18.00"),
            "stock_in_store": 12,
            "reserved_in_store": 0,
            "available_in_store": 12,
            "stock_online": 0,
            "reserved_online": 0,
            "available_online": 0,
            "total_on_hand": 12,
            "total_available": 12,
            "reorder_point": 14,
            "daily_demand": Decimal("4.00"),
            "lead_days": 2,
            "safety_stock": 6,
        }
    ]

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    app.state.db_pool = mock_pool

    with patch("app.api.deps.settings.demo_mode", True):
        with patch("app.api.deps.settings.demo_merchant_id", "merchant_northstar"):
            status_code, _, body = await make_asgi_request(app, "GET", "/api/v1/inventory")
            assert status_code == 200
            assert body["merchant_id"] == "merchant_northstar"
            assert body["total_items"] == 1
            assert body["low_stock_count"] == 1
            assert body["items"][0]["sku"] == "SKU-DEC-001"
            assert body["items"][0]["reorder_recommended"] is True


@pytest.mark.asyncio
async def test_api_client_cannot_override_merchant_identity():
    """Client cannot supply query param ?merchant_id=attacker to override tenant identity."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    app.state.db_pool = mock_pool

    with patch("app.api.deps.settings.demo_mode", True):
        with patch("app.api.deps.settings.demo_merchant_id", "merchant_northstar"):
            # Attempt to spoof tenant as "merchant_attacker" via query parameter and header
            status_code, _, body = await make_asgi_request(
                app,
                "GET",
                "/api/v1/inventory?merchant_id=merchant_attacker",
                headers=[("x-merchant-id", "merchant_attacker")],
            )
            assert status_code == 200
            # Body must reflect server-configured tenant, not the attacker's input
            assert body["merchant_id"] == "merchant_northstar"
            query_arg = mock_conn.fetch.call_args[0][1]
            assert query_arg == "merchant_northstar"


@pytest.mark.asyncio
async def test_api_spend_envelope_endpoint_success():
    """GET /api/v1/procurement/spend-envelope returns validated spend envelope."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow.side_effect = [
        {
            "merchant_id": "merchant_northstar",
            "demo_envelope_cap": Decimal("500.00"),
            "max_order_cost_ceiling": Decimal("150.00"),
            "merchant_created_at": "2026-11-09T00:00:00Z",
        },
        # financial_ledger column is running_envelope_balance (not running_balance)
        {
            "running_envelope_balance": Decimal("325.00"),
        },
    ]

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    app.state.db_pool = mock_pool

    with patch("app.api.deps.settings.demo_mode", True):
        with patch("app.api.deps.settings.demo_merchant_id", "merchant_northstar"):
            status_code, _, body = await make_asgi_request(
                app, "GET", "/api/v1/procurement/spend-envelope"
            )
            assert status_code == 200
            assert body["merchant_id"] == "merchant_northstar"
            assert float(body["demo_envelope_cap"]) == 500.00
            assert float(body["committed_spend"]) == 175.00
            assert float(body["available_spend"]) == 325.00
            assert float(body["max_order_cost_ceiling"]) == 150.00
            assert body["merchant_created_at"] is not None


@pytest.mark.asyncio
async def test_api_spend_envelope_not_found():
    """GET /api/v1/procurement/spend-envelope returns 404 if merchant not found."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = None

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    app.state.db_pool = mock_pool

    with patch("app.api.deps.settings.demo_mode", True):
        with patch("app.api.deps.settings.demo_merchant_id", "nonexistent"):
            status_code, _, body = await make_asgi_request(
                app, "GET", "/api/v1/procurement/spend-envelope"
            )
            assert status_code == 404
            assert "not found" in body["detail"]


@pytest.mark.asyncio
async def test_api_suppliers_endpoint_success():
    """GET /api/v1/procurement/suppliers returns list of suppliers with offers.

    Mock data aligned with migration 001 (no is_preferred on offers, no payment_terms
    on suppliers; trust_tier used as source-of-truth for allowlisting).
    """
    mock_conn = AsyncMock()
    mock_conn.fetch.side_effect = [
        [
            {
                "id": "sup_supplier_a",
                "merchant_id": "merchant_northstar",
                "name": "Artisan Clayworks",
                "contact_email": "supplier_a@example.com",
                "trust_tier": "allowlisted",
                "reliability_score": 94,
                "is_illustrative": True,
            }
        ],
        [
            {
                "offer_id": "offer_a_01",
                "merchant_id": "merchant_northstar",
                "supplier_id": "sup_supplier_a",
                "product_id": "prod_blue_vase",
                "unit_cost": Decimal("4.00"),
                "shipping_cost": Decimal("12.00"),
                "lead_time_days": 3,
                "moq": 20,
                "is_illustrative": True,
            }
        ],
    ]

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    app.state.db_pool = mock_pool

    with patch("app.api.deps.settings.demo_mode", True):
        with patch("app.api.deps.settings.demo_merchant_id", "merchant_northstar"):
            status_code, _, body = await make_asgi_request(
                app, "GET", "/api/v1/procurement/suppliers"
            )
            assert status_code == 200
            assert body["merchant_id"] == "merchant_northstar"
            assert body["total_suppliers"] == 1
            supplier = body["suppliers"][0]
            assert supplier["name"] == "Artisan Clayworks"
            assert supplier["is_allowlisted"] is True
            assert supplier["trust_tier"] == "allowlisted"
            assert supplier["lead_time_days"] == 3   # min(offer lead_time_days)
            assert len(supplier["offers"]) == 1
            assert float(supplier["offers"][0]["unit_cost"]) == 4.00
            assert supplier["offers"][0]["lead_time_days"] == 3
            assert "is_preferred" not in supplier["offers"][0]
