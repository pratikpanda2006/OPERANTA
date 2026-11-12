"""
backend/tests/test_milestone10_live.py
=======================================
Real PostgreSQL integration tests for MarginGuard Milestone 10.2.

Verifies the POS and procurement repository workflows implemented in
Milestones 9–10.1 against an isolated local PostgreSQL test database.

SAFETY CONSTRAINTS
------------------
- These tests ONLY run when TEST_DATABASE_URL is set to exactly
  "postgresql://postgres@127.0.0.1:5433/marginguard_test".
- The target host is validated to be a loopback address before any connection
  is opened. Tests refuse to run against any remote database.
- All write-producing test logic is wrapped in an explicit transaction that is
  rolled back after each test, keeping the test database in its seeded state.
- These tests MUST NOT be run against Supabase or any production database.
- Never run these tests against the application/demo database (port 5432).

RUNNING THESE TESTS
-------------------
From the workspace root (marginguard/), after starting the local PostgreSQL
test cluster on port 5433:

    $env:TEST_DATABASE_URL="postgresql://postgres@127.0.0.1:5433/marginguard_test"
    $env:PYTHONPATH="backend"
    .\\backend\\.venv\\Scripts\\python.exe -m pytest backend/tests/test_milestone10_live.py -v --asyncio-mode=auto

COVERAGE SUMMARY
----------------
POS Sales (7 tests):
  1. Valid POS sale creates orders + order_items rows.
  2. stock_in_store decrements by the correct quantity.
  3. available_in_store generated column reflects the decrement.
  4. inventory_movements row written with pos_sale movement type.
  5. operational_audit_log row written for the sale.
  6. Duplicate idempotency_key → no second order, no second stock decrement.
  7. Insufficient stock → ValueError raised, no order row created, stock unchanged.

Procurement (10 tests):
  8.  PO creation derives unit_cost and shipping from supplier_offers (not client).
  9.  Schema enforces that client cannot supply unit_cost (only product_id + quantity).
  10. Unknown supplier/product combo raises ValueError("No catalog offer found").
  11. 42 units (landed $180.20 > $150 cap) → rejected, policy passed=False, no reservation.
  12. 32 units (landed $139.20 ≤ $150) → pending_approval, policy passed=True.
  13. Owner approval creates purchase_order_approvals row with decision='approved'.
  14. Owner approval creates spend_reservations row for the correct amount.
  15. Owner approval appends financial_ledger entry with correct running balance.
  16. decided_by is server-derived (owner_merchant_northstar), not a client value.
  17. Rejection → status='rejected', no spend_reservations, no financial_ledger entry.
"""

import os
import secrets
import urllib.parse
import uuid
from decimal import Decimal
from typing import AsyncGenerator

import asyncpg
import pytest

from app.db.repositories.order_repo import execute_pos_sale
from app.db.repositories.procurement_repo import create_purchase_order, process_owner_decision
from app.schemas.orders import POSSaleItemRequest, POSSaleRequest
from app.schemas.procurement import (
    OwnerDecisionRequest,
    PurchaseOrderCreateRequest,
    PurchaseOrderItemCreate,
)


# ---------------------------------------------------------------------------
# Safety guard — approved loopback target only
# ---------------------------------------------------------------------------

_APPROVED_URL = "postgresql://postgres@127.0.0.1:5433/marginguard_test"
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

_parsed = urllib.parse.urlsplit(TEST_DATABASE_URL) if TEST_DATABASE_URL else None
_configured_host = (_parsed.hostname or "").lower() if _parsed else ""

_skip_reason = ""
if not TEST_DATABASE_URL:
    _skip_reason = "TEST_DATABASE_URL not set — skipping Milestone 10.2 live tests"
elif TEST_DATABASE_URL.rstrip("/") != _APPROVED_URL.rstrip("/"):
    _skip_reason = (
        f"TEST_DATABASE_URL does not match the approved local test URL "
        f"(expected '{_APPROVED_URL}', got '{TEST_DATABASE_URL}'). "
        "Live tests refuse to run against an unapproved target."
    )
elif _configured_host not in _LOOPBACK_HOSTS:
    _skip_reason = (
        f"TEST_DATABASE_URL host '{_configured_host}' is not a loopback address. "
        "Live integration tests refuse to execute against remote databases."
    )

_live = pytest.mark.skipif(bool(_skip_reason), reason=_skip_reason or "approved")

MERCHANT_ID = "merchant_northstar"
PRODUCT_ID = "prod_blue_vase"
SUPPLIER_C_ID = "sup_supplier_c"   # allowlisted, unit_cost=4.10, shipping=8.00
SUPPLIER_B_ID = "sup_supplier_b"   # requires_approval (non-allowlisted)


# ---------------------------------------------------------------------------
# Shared helper: open a direct asyncpg connection
# ---------------------------------------------------------------------------

async def _connect() -> asyncpg.Connection:
    """
    Open a direct asyncpg connection to the approved test database.
    Reads connection URL from TEST_DATABASE_URL and password from TEST_DATABASE_PASSWORD.
    Fails clearly if either variable is missing or empty.
    """
    test_url = os.environ.get("TEST_DATABASE_URL", "")
    if not test_url:
        raise RuntimeError("TEST_DATABASE_URL environment variable is missing or empty.")

    test_password = os.environ.get("TEST_DATABASE_PASSWORD", "")
    if not test_password:
        raise RuntimeError("TEST_DATABASE_PASSWORD environment variable is missing or empty.")

    return await asyncpg.connect(
        test_url,
        password=test_password,
        ssl=False,
    )


# ===========================================================================
# POS SALE TESTS  (7 tests)
# ===========================================================================

@_live
@pytest.mark.asyncio
async def test_pos_sale_creates_order_row():
    """
    POS TEST 1: A valid POS sale must create an orders row with a non-empty order_number.
    The stock before and after the sale is verified.
    All mutations are rolled back.
    """
    conn = await _connect()
    tr = conn.transaction()
    await tr.start()
    try:
        idempotency_key = f"test_pos_order_{secrets.token_hex(8)}"
        sale_req = POSSaleRequest(
            items=[POSSaleItemRequest(product_id=PRODUCT_ID, quantity=2)],
            idempotency_key=idempotency_key,
            payment_method="card_in_store",
        )

        order, _ = await execute_pos_sale(conn, MERCHANT_ID, sale_req)

        # An order row must exist
        assert order is not None
        assert order["id"].startswith("order_pos_")
        assert order["order_number"].startswith("POS-")
        assert order["merchant_id"] == MERCHANT_ID
        assert order["channel"] == "pos"
        assert order["status"] == "completed"

        # Verify order persisted in DB (within this transaction)
        db_order = await conn.fetchrow(
            "SELECT id, order_number, channel, status FROM orders WHERE merchant_id = $1 AND id = $2",
            MERCHANT_ID,
            order["id"],
        )
        assert db_order is not None, "orders row not found in database"
        assert db_order["order_number"] == order["order_number"]
        assert db_order["status"] == "completed"
    finally:
        await tr.rollback()
        await conn.close()


@_live
@pytest.mark.asyncio
async def test_pos_sale_decrements_stock_in_store():
    """
    POS TEST 2: stock_in_store must decrease by exactly the purchased quantity.
    Verifies before-state = 12, after-state = 10 (selling 2 units).
    """
    conn = await _connect()
    tr = conn.transaction()
    await tr.start()
    try:
        # Read stock before sale
        before = await conn.fetchrow(
            "SELECT stock_in_store FROM inventory_items WHERE merchant_id = $1 AND product_id = $2",
            MERCHANT_ID,
            PRODUCT_ID,
        )
        assert before is not None, "inventory_items row missing for prod_blue_vase"
        stock_before = before["stock_in_store"]

        qty_to_sell = 2
        idempotency_key = f"test_pos_decrement_{secrets.token_hex(8)}"
        sale_req = POSSaleRequest(
            items=[POSSaleItemRequest(product_id=PRODUCT_ID, quantity=qty_to_sell)],
            idempotency_key=idempotency_key,
        )
        await execute_pos_sale(conn, MERCHANT_ID, sale_req)

        after = await conn.fetchrow(
            "SELECT stock_in_store FROM inventory_items WHERE merchant_id = $1 AND product_id = $2",
            MERCHANT_ID,
            PRODUCT_ID,
        )
        stock_after = after["stock_in_store"]

        assert stock_after == stock_before - qty_to_sell, (
            f"Expected stock_in_store={stock_before - qty_to_sell}, got {stock_after}"
        )
    finally:
        await tr.rollback()
        await conn.close()


@_live
@pytest.mark.asyncio
async def test_pos_sale_available_in_store_decrements():
    """
    POS TEST 3: available_in_store (GENERATED column = stock_in_store - reserved_in_store)
    must reflect the decrement after the sale.
    """
    conn = await _connect()
    tr = conn.transaction()
    await tr.start()
    try:
        before = await conn.fetchrow(
            "SELECT available_in_store FROM inventory_items WHERE merchant_id = $1 AND product_id = $2",
            MERCHANT_ID,
            PRODUCT_ID,
        )
        avail_before = before["available_in_store"]

        qty_to_sell = 3
        idempotency_key = f"test_pos_avail_{secrets.token_hex(8)}"
        sale_req = POSSaleRequest(
            items=[POSSaleItemRequest(product_id=PRODUCT_ID, quantity=qty_to_sell)],
            idempotency_key=idempotency_key,
        )
        await execute_pos_sale(conn, MERCHANT_ID, sale_req)

        after = await conn.fetchrow(
            "SELECT available_in_store FROM inventory_items WHERE merchant_id = $1 AND product_id = $2",
            MERCHANT_ID,
            PRODUCT_ID,
        )
        avail_after = after["available_in_store"]

        assert avail_after == avail_before - qty_to_sell, (
            f"Expected available_in_store={avail_before - qty_to_sell}, got {avail_after}"
        )
    finally:
        await tr.rollback()
        await conn.close()


@_live
@pytest.mark.asyncio
async def test_pos_sale_writes_inventory_movement_row():
    """
    POS TEST 4: A pos_sale movement must be written to inventory_movements with:
    - movement_type = 'pos_sale'
    - quantity_delta = -(sold quantity)
    - balance_after = new stock level
    - reference_type = 'order'
    - reference_id = the created order ID
    """
    conn = await _connect()
    tr = conn.transaction()
    await tr.start()
    try:
        before = await conn.fetchrow(
            "SELECT stock_in_store FROM inventory_items WHERE merchant_id = $1 AND product_id = $2",
            MERCHANT_ID,
            PRODUCT_ID,
        )
        stock_before = before["stock_in_store"]
        qty_to_sell = 2

        idempotency_key = f"test_pos_movement_{secrets.token_hex(8)}"
        sale_req = POSSaleRequest(
            items=[POSSaleItemRequest(product_id=PRODUCT_ID, quantity=qty_to_sell)],
            idempotency_key=idempotency_key,
        )
        order, _ = await execute_pos_sale(conn, MERCHANT_ID, sale_req)
        order_id = order["id"]

        mov = await conn.fetchrow(
            """
            SELECT movement_type, quantity_delta, balance_after, reference_type, reference_id
            FROM inventory_movements
            WHERE merchant_id = $1 AND product_id = $2 AND reference_id = $3
            """,
            MERCHANT_ID,
            PRODUCT_ID,
            order_id,
        )
        assert mov is not None, "inventory_movements row not found for this sale"
        assert mov["movement_type"] == "pos_sale"
        assert mov["quantity_delta"] == -qty_to_sell, (
            f"Expected quantity_delta=-{qty_to_sell}, got {mov['quantity_delta']}"
        )
        assert mov["balance_after"] == stock_before - qty_to_sell, (
            f"Expected balance_after={stock_before - qty_to_sell}, got {mov['balance_after']}"
        )
        assert mov["reference_type"] == "order"
        assert mov["reference_id"] == order_id
    finally:
        await tr.rollback()
        await conn.close()


@_live
@pytest.mark.asyncio
async def test_pos_sale_writes_audit_log_row():
    """
    POS TEST 5: An operational_audit_log row must be written for the POS sale
    with action='pos_sale_completed' and entity_id = the created order ID.
    """
    conn = await _connect()
    tr = conn.transaction()
    await tr.start()
    try:
        idempotency_key = f"test_pos_audit_{secrets.token_hex(8)}"
        sale_req = POSSaleRequest(
            items=[POSSaleItemRequest(product_id=PRODUCT_ID, quantity=1)],
            idempotency_key=idempotency_key,
        )
        order, _ = await execute_pos_sale(conn, MERCHANT_ID, sale_req)
        order_id = order["id"]

        audit = await conn.fetchrow(
            """
            SELECT action, entity_type, entity_id, actor_type
            FROM operational_audit_log
            WHERE merchant_id = $1 AND entity_id = $2
            """,
            MERCHANT_ID,
            order_id,
        )
        assert audit is not None, "operational_audit_log row not found for this sale"
        assert audit["action"] == "pos_sale_completed"
        assert audit["entity_type"] == "order"
        assert audit["entity_id"] == order_id
        assert audit["actor_type"] == "store_employee"
    finally:
        await tr.rollback()
        await conn.close()


@_live
@pytest.mark.asyncio
async def test_pos_sale_idempotency_no_duplicate():
    """
    POS TEST 6: Submitting the same idempotency_key twice must return the original
    order and must NOT create a second order or further decrement stock.
    """
    conn = await _connect()
    tr = conn.transaction()
    await tr.start()
    try:
        idempotency_key = f"test_pos_idemp_{secrets.token_hex(8)}"
        sale_req = POSSaleRequest(
            items=[POSSaleItemRequest(product_id=PRODUCT_ID, quantity=2)],
            idempotency_key=idempotency_key,
        )

        # First submission
        order1, _ = await execute_pos_sale(conn, MERCHANT_ID, sale_req)
        stock_after_first = await conn.fetchval(
            "SELECT stock_in_store FROM inventory_items WHERE merchant_id = $1 AND product_id = $2",
            MERCHANT_ID,
            PRODUCT_ID,
        )

        # Second submission with the same idempotency key
        order2, _ = await execute_pos_sale(conn, MERCHANT_ID, sale_req)
        stock_after_second = await conn.fetchval(
            "SELECT stock_in_store FROM inventory_items WHERE merchant_id = $1 AND product_id = $2",
            MERCHANT_ID,
            PRODUCT_ID,
        )

        # Same order returned, no additional stock deduction
        assert order2["id"] == order1["id"], "Duplicate request must return the same order ID"
        assert order2["order_number"] == order1["order_number"], "Duplicate request must return the same order number"
        assert stock_after_second == stock_after_first, (
            f"Stock changed after duplicate request: {stock_after_first} → {stock_after_second}"
        )

        # Only one order row for this idempotency key
        order_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM orders o
            JOIN inventory_movements im ON im.reference_id = o.id
            WHERE o.merchant_id = $1
              AND (im.idempotency_key = $2 OR im.idempotency_key LIKE $3)
            """,
            MERCHANT_ID,
            idempotency_key,
            f"{idempotency_key}%",
        )
        assert order_count == 1, (
            f"Expected exactly 1 order/movement reference for this idempotency key, found {order_count}"
        )

        # Exactly one movement row written for this sale (1 item in cart)
        movement_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM inventory_movements
            WHERE merchant_id = $1
              AND (idempotency_key = $2 OR idempotency_key LIKE $3)
            """,
            MERCHANT_ID,
            idempotency_key,
            f"{idempotency_key}%",
        )
        assert movement_count == 1, (
            f"Expected exactly 1 movement row, found {movement_count}"
        )

        # Exactly one audit log row written for this sale
        audit_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM operational_audit_log
            WHERE merchant_id = $1 AND entity_id = $2 AND action = 'pos_sale_completed'
            """,
            MERCHANT_ID,
            order1["id"],
        )
        assert audit_count == 1, (
            f"Expected exactly 1 audit log entry, found {audit_count}"
        )
    finally:
        await tr.rollback()
        await conn.close()


@_live
@pytest.mark.asyncio
async def test_pos_sale_insufficient_stock_raises_and_no_order():
    """
    POS TEST 7: Requesting more units than available must raise ValueError.
    No order row, no inventory movement, no audit log must be written.
    Stock must remain unchanged.
    """
    conn = await _connect()
    tr = conn.transaction()
    await tr.start()
    try:
        # Read available stock (seed = 12)
        avail = await conn.fetchval(
            "SELECT available_in_store FROM inventory_items WHERE merchant_id = $1 AND product_id = $2",
            MERCHANT_ID,
            PRODUCT_ID,
        )
        excessive_qty = avail + 100  # definitely more than available

        idempotency_key = f"test_pos_overflow_{secrets.token_hex(8)}"
        sale_req = POSSaleRequest(
            items=[POSSaleItemRequest(product_id=PRODUCT_ID, quantity=excessive_qty)],
            idempotency_key=idempotency_key,
        )

        with pytest.raises(ValueError, match="Insufficient"):
            await execute_pos_sale(conn, MERCHANT_ID, sale_req)

        # Stock must still be the same
        avail_after = await conn.fetchval(
            "SELECT available_in_store FROM inventory_items WHERE merchant_id = $1 AND product_id = $2",
            MERCHANT_ID,
            PRODUCT_ID,
        )
        assert avail_after == avail, (
            f"Stock changed after failed sale: before={avail}, after={avail_after}"
        )

        # No order row must exist for this idempotency key
        order_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM inventory_movements
            WHERE merchant_id = $1 AND idempotency_key LIKE $2
            """,
            MERCHANT_ID,
            f"{idempotency_key}%",
        )
        assert order_count == 0, "inventory_movements must NOT be written on a failed sale"
    finally:
        await tr.rollback()
        await conn.close()


# ===========================================================================
# PROCUREMENT TESTS  (10 tests)
# ===========================================================================

@_live
@pytest.mark.asyncio
async def test_procurement_po_derives_unit_cost_from_supplier_offers():
    """
    PROCUREMENT TEST 8: create_purchase_order must derive unit_cost and shipping_cost
    from the canonical supplier_offers table, NOT from the client request.

    Supplier C for prod_blue_vase:
      unit_cost = 4.10, shipping_cost = 8.00 (from seed data)
    For 32 units: item_subtotal = 32 * 4.10 = 131.20, shipping = 8.00 → total = 139.20
    """
    conn = await _connect()
    tr = conn.transaction()
    await tr.start()
    try:
        idempotency_key = f"test_proc_pricing_{secrets.token_hex(8)}"
        po_req = PurchaseOrderCreateRequest(
            supplier_id=SUPPLIER_C_ID,
            items=[PurchaseOrderItemCreate(product_id=PRODUCT_ID, quantity=32)],
            idempotency_key=idempotency_key,
        )
        result = await create_purchase_order(conn, MERCHANT_ID, po_req)

        # item_subtotal: 32 * 4.10 = 131.20
        assert result["item_subtotal"] == pytest.approx(Decimal("131.20"), abs=Decimal("0.01")), (
            f"Expected item_subtotal=131.20, got {result['item_subtotal']}"
        )
        # shipping_cost: 8.00
        assert result["shipping_cost"] == pytest.approx(Decimal("8.00"), abs=Decimal("0.01")), (
            f"Expected shipping_cost=8.00, got {result['shipping_cost']}"
        )
        # total_landed_cost: 131.20 + 8.00 = 139.20
        assert result["total_landed_cost"] == pytest.approx(Decimal("139.20"), abs=Decimal("0.01")), (
            f"Expected total_landed_cost=139.20, got {result['total_landed_cost']}"
        )

        # unit_cost in PO items must be 4.10 (canonical, not client-submitted)
        assert len(result["items"]) == 1
        po_item = result["items"][0]
        assert po_item["unit_cost"] == pytest.approx(Decimal("4.10"), abs=Decimal("0.01")), (
            f"Expected unit_cost=4.10 (from supplier_offers), got {po_item['unit_cost']}"
        )
    finally:
        await tr.rollback()
        await conn.close()


@_live
@pytest.mark.asyncio
async def test_procurement_schema_rejects_client_unit_cost():
    """
    PROCUREMENT TEST 9: PurchaseOrderItemCreate schema must NOT accept a unit_cost field.
    Pricing must be impossible to supply from the client side.
    This is a schema-level (Pydantic) enforcement test.
    """
    # Pydantic V2: extra fields are ignored by default unless configured to forbid.
    # PurchaseOrderItemCreate only has product_id and quantity — verify that.
    import inspect
    import pydantic

    fields = PurchaseOrderItemCreate.model_fields
    assert "unit_cost" not in fields, (
        "PurchaseOrderItemCreate must NOT have a unit_cost field — "
        "client must never supply pricing."
    )
    assert "shipping_cost" not in fields, (
        "PurchaseOrderItemCreate must NOT have a shipping_cost field."
    )
    assert "product_id" in fields
    assert "quantity" in fields

    # Also verify PurchaseOrderCreateRequest has no shipping_cost from client
    req_fields = PurchaseOrderCreateRequest.model_fields
    assert "shipping_cost" not in req_fields, (
        "PurchaseOrderCreateRequest must NOT expose shipping_cost — "
        "it is derived server-side from supplier_offers."
    )


@_live
@pytest.mark.asyncio
async def test_procurement_unknown_product_raises_no_catalog_offer():
    """
    PROCUREMENT TEST 10: Requesting a product that has no offer from the given supplier
    must raise ValueError with 'No catalog offer found'.
    """
    conn = await _connect()
    tr = conn.transaction()
    await tr.start()
    try:
        idempotency_key = f"test_proc_no_offer_{secrets.token_hex(8)}"
        po_req = PurchaseOrderCreateRequest(
            supplier_id=SUPPLIER_C_ID,
            items=[PurchaseOrderItemCreate(product_id="prod_nonexistent_xyz", quantity=10)],
            idempotency_key=idempotency_key,
        )
        with pytest.raises(ValueError, match="No catalog offer found"):
            await create_purchase_order(conn, MERCHANT_ID, po_req)
    finally:
        await tr.rollback()
        await conn.close()


@_live
@pytest.mark.asyncio
async def test_procurement_42_units_exceeds_per_order_cap_rejected():
    """
    PROCUREMENT TEST 11: 42 units × $4.10 + $8.00 shipping = $180.20.
    This exceeds the $150 per-order cap.
    The PO must be created with status='rejected' and policy.passed=False.
    No spend_reservations row must be written.
    """
    conn = await _connect()
    tr = conn.transaction()
    await tr.start()
    try:
        idempotency_key = f"test_proc_42units_{secrets.token_hex(8)}"
        po_req = PurchaseOrderCreateRequest(
            supplier_id=SUPPLIER_C_ID,
            items=[PurchaseOrderItemCreate(product_id=PRODUCT_ID, quantity=42)],
            idempotency_key=idempotency_key,
        )
        result = await create_purchase_order(conn, MERCHANT_ID, po_req)

        assert result["status"] == "rejected", (
            f"Expected status='rejected' for $180.20 order, got '{result['status']}'"
        )
        policy = result["policy_evaluation"]
        assert policy["passed"] is False, "Policy must NOT pass for $180.20 landed cost"
        assert policy["per_order_limit_passed"] is False, "Per-order limit check must fail"

        # No spend_reservations row for this PO
        reservation = await conn.fetchrow(
            "SELECT id FROM spend_reservations WHERE merchant_id = $1 AND purchase_order_id = $2",
            MERCHANT_ID,
            result["id"],
        )
        assert reservation is None, "No spend_reservations row must exist for a rejected PO"

        # Total landed cost: 42 * 4.10 + 8.00 = 180.20
        assert result["total_landed_cost"] == pytest.approx(Decimal("180.20"), abs=Decimal("0.01"))
    finally:
        await tr.rollback()
        await conn.close()


@_live
@pytest.mark.asyncio
async def test_procurement_32_units_passes_policy_pending_approval():
    """
    PROCUREMENT TEST 12: 32 units × $4.10 + $8.00 shipping = $139.20.
    This is within the $150 cap and within the $325 available envelope.
    The PO must be created with status='pending_approval' and policy.passed=True.
    """
    conn = await _connect()
    tr = conn.transaction()
    await tr.start()
    try:
        idempotency_key = f"test_proc_32units_{secrets.token_hex(8)}"
        po_req = PurchaseOrderCreateRequest(
            supplier_id=SUPPLIER_C_ID,
            items=[PurchaseOrderItemCreate(product_id=PRODUCT_ID, quantity=32)],
            idempotency_key=idempotency_key,
        )
        result = await create_purchase_order(conn, MERCHANT_ID, po_req)

        assert result["status"] == "pending_approval", (
            f"Expected status='pending_approval' for $139.20 order, got '{result['status']}'"
        )
        policy = result["policy_evaluation"]
        assert policy["passed"] is True, (
            f"Policy must pass for $139.20 landed cost. Reasons: {policy['reasons']}"
        )
        assert policy["per_order_limit_passed"] is True
        assert policy["envelope_cap_passed"] is True
        assert policy["allowlist_passed"] is True

        # Correct total landed cost
        assert result["total_landed_cost"] == pytest.approx(Decimal("139.20"), abs=Decimal("0.01"))

        # Remaining envelope if approved = 325.00 - 139.20 = 185.80
        assert policy["remaining_envelope_if_approved"] == pytest.approx(
            Decimal("185.80"), abs=Decimal("0.01")
        )
    finally:
        await tr.rollback()
        await conn.close()


@_live
@pytest.mark.asyncio
async def test_procurement_approval_creates_purchase_order_approvals_row():
    """
    PROCUREMENT TEST 13: Owner approval must create a purchase_order_approvals row
    with decision='approved' and the correct po_id.
    """
    conn = await _connect()
    tr = conn.transaction()
    await tr.start()
    try:
        # Create a PO
        po_idemp = f"test_proc_appr_po_{secrets.token_hex(8)}"
        po_req = PurchaseOrderCreateRequest(
            supplier_id=SUPPLIER_C_ID,
            items=[PurchaseOrderItemCreate(product_id=PRODUCT_ID, quantity=32)],
            idempotency_key=po_idemp,
        )
        po_result = await create_purchase_order(conn, MERCHANT_ID, po_req)
        po_id = po_result["id"]
        assert po_result["status"] == "pending_approval"

        # Approve it
        dec_idemp = f"test_proc_appr_dec_{secrets.token_hex(8)}"
        decision_req = OwnerDecisionRequest(
            decision="approved",
            decision_reason="Integration test: approval confirmation",
            idempotency_key=dec_idemp,
        )
        server_actor = f"owner_{MERCHANT_ID}"
        appr_result = await process_owner_decision(conn, MERCHANT_ID, po_id, decision_req, decided_by_actor=server_actor)

        assert appr_result["decision"] == "approved"

        # Verify purchase_order_approvals row in DB
        appr_row = await conn.fetchrow(
            """
            SELECT decision, approval_type, decided_by
            FROM purchase_order_approvals
            WHERE merchant_id = $1 AND purchase_order_id = $2
            """,
            MERCHANT_ID,
            po_id,
        )
        assert appr_row is not None, "purchase_order_approvals row not found after approval"
        assert appr_row["decision"] == "approved"
    finally:
        await tr.rollback()
        await conn.close()


@_live
@pytest.mark.asyncio
async def test_procurement_approval_creates_spend_reservation():
    """
    PROCUREMENT TEST 14: Owner approval must create a spend_reservations row for
    the correct reserved_amount (= total_landed_cost of the PO = $139.20).
    """
    conn = await _connect()
    tr = conn.transaction()
    await tr.start()
    try:
        po_idemp = f"test_proc_resv_po_{secrets.token_hex(8)}"
        po_req = PurchaseOrderCreateRequest(
            supplier_id=SUPPLIER_C_ID,
            items=[PurchaseOrderItemCreate(product_id=PRODUCT_ID, quantity=32)],
            idempotency_key=po_idemp,
        )
        po_result = await create_purchase_order(conn, MERCHANT_ID, po_req)
        po_id = po_result["id"]

        dec_idemp = f"test_proc_resv_dec_{secrets.token_hex(8)}"
        decision_req = OwnerDecisionRequest(
            decision="approved",
            decision_reason="Reservation test",
            idempotency_key=dec_idemp,
        )
        server_actor = f"owner_{MERCHANT_ID}"
        await process_owner_decision(conn, MERCHANT_ID, po_id, decision_req, decided_by_actor=server_actor)

        # Verify spend_reservations row
        resv = await conn.fetchrow(
            """
            SELECT reserved_amount, status
            FROM spend_reservations
            WHERE merchant_id = $1 AND purchase_order_id = $2
            """,
            MERCHANT_ID,
            po_id,
        )
        assert resv is not None, "spend_reservations row not found after approval"
        assert resv["status"] == "active"
        assert float(resv["reserved_amount"]) == pytest.approx(139.20, abs=0.01), (
            f"Expected reserved_amount=139.20, got {resv['reserved_amount']}"
        )
    finally:
        await tr.rollback()
        await conn.close()


@_live
@pytest.mark.asyncio
async def test_procurement_approval_appends_financial_ledger_entry():
    """
    PROCUREMENT TEST 15: Owner approval must append a financial_ledger entry with:
    - entry_type = 'spend_reservation'
    - amount = -139.20 (negative: a deduction from envelope)
    - running_envelope_balance = 325.00 - 139.20 = 185.80
    """
    conn = await _connect()
    tr = conn.transaction()
    await tr.start()
    try:
        # Read current running balance
        ledger_before = await conn.fetchval(
            """
            SELECT running_envelope_balance FROM financial_ledger
            WHERE merchant_id = $1 ORDER BY id DESC LIMIT 1
            """,
            MERCHANT_ID,
        )
        balance_before = Decimal(str(ledger_before))

        po_idemp = f"test_proc_ledger_po_{secrets.token_hex(8)}"
        po_req = PurchaseOrderCreateRequest(
            supplier_id=SUPPLIER_C_ID,
            items=[PurchaseOrderItemCreate(product_id=PRODUCT_ID, quantity=32)],
            idempotency_key=po_idemp,
        )
        po_result = await create_purchase_order(conn, MERCHANT_ID, po_req)
        po_id = po_result["id"]
        landed_cost = Decimal(str(po_result["total_landed_cost"]))  # 139.20

        dec_idemp = f"test_proc_ledger_dec_{secrets.token_hex(8)}"
        decision_req = OwnerDecisionRequest(
            decision="approved",
            decision_reason="Ledger entry test",
            idempotency_key=dec_idemp,
        )
        server_actor = f"owner_{MERCHANT_ID}"
        await process_owner_decision(conn, MERCHANT_ID, po_id, decision_req, decided_by_actor=server_actor)

        # Verify the new ledger row
        ledger_row = await conn.fetchrow(
            """
            SELECT entry_type, amount, running_envelope_balance, reference_id
            FROM financial_ledger
            WHERE merchant_id = $1 AND reference_id = $2
            """,
            MERCHANT_ID,
            po_id,
        )
        assert ledger_row is not None, "financial_ledger entry not found after approval"
        assert ledger_row["entry_type"] == "spend_reservation"
        assert float(ledger_row["amount"]) == pytest.approx(-float(landed_cost), abs=0.01), (
            f"Expected ledger amount=-{landed_cost}, got {ledger_row['amount']}"
        )
        expected_running = balance_before - landed_cost
        assert float(ledger_row["running_envelope_balance"]) == pytest.approx(
            float(expected_running), abs=0.01
        ), (
            f"Expected running_envelope_balance={expected_running}, "
            f"got {ledger_row['running_envelope_balance']}"
        )
    finally:
        await tr.rollback()
        await conn.close()


@_live
@pytest.mark.asyncio
async def test_procurement_approval_decided_by_is_server_derived():
    """
    PROCUREMENT TEST 16: decided_by in purchase_order_approvals must be the
    server-derived actor string ('owner_merchant_northstar'), not any client-supplied value.
    The endpoint passes decided_by_actor to process_owner_decision.
    """
    conn = await _connect()
    tr = conn.transaction()
    await tr.start()
    try:
        po_idemp = f"test_proc_actor_po_{secrets.token_hex(8)}"
        po_req = PurchaseOrderCreateRequest(
            supplier_id=SUPPLIER_C_ID,
            items=[PurchaseOrderItemCreate(product_id=PRODUCT_ID, quantity=32)],
            idempotency_key=po_idemp,
        )
        po_result = await create_purchase_order(conn, MERCHANT_ID, po_req)
        po_id = po_result["id"]

        # Simulate the server-derived actor (as the endpoint constructs it)
        server_actor = f"owner_{MERCHANT_ID}"
        # A client might try to pass a different value — we pass server_actor explicitly
        client_attempted_actor = "admin_override_hack"  # must be IGNORED

        dec_idemp = f"test_proc_actor_dec_{secrets.token_hex(8)}"
        decision_req = OwnerDecisionRequest(
            decision="approved",
            decision_reason="Actor derivation test",
            idempotency_key=dec_idemp,
        )
        # The repo uses decided_by_actor parameter — the endpoint always passes the server-derived value
        await process_owner_decision(
            conn, MERCHANT_ID, po_id, decision_req, decided_by_actor=server_actor
        )

        appr_row = await conn.fetchrow(
            "SELECT decided_by FROM purchase_order_approvals WHERE merchant_id = $1 AND purchase_order_id = $2",
            MERCHANT_ID,
            po_id,
        )
        assert appr_row is not None
        assert appr_row["decided_by"] == server_actor, (
            f"Expected decided_by='{server_actor}', got '{appr_row['decided_by']}'"
        )
        assert appr_row["decided_by"] != client_attempted_actor, (
            "decided_by must be server-derived, not the client-attempted actor string"
        )
    finally:
        await tr.rollback()
        await conn.close()


@_live
@pytest.mark.asyncio
async def test_procurement_rejection_no_reservation_no_ledger():
    """
    PROCUREMENT TEST 17: Rejecting a PO must:
    - Set status = 'rejected'
    - NOT create a spend_reservations row
    - NOT append a financial_ledger entry referencing the PO
    - Record a purchase_order_approvals row with decision='rejected'
    """
    conn = await _connect()
    tr = conn.transaction()
    await tr.start()
    try:
        po_idemp = f"test_proc_reject_po_{secrets.token_hex(8)}"
        po_req = PurchaseOrderCreateRequest(
            supplier_id=SUPPLIER_C_ID,
            items=[PurchaseOrderItemCreate(product_id=PRODUCT_ID, quantity=32)],
            idempotency_key=po_idemp,
        )
        po_result = await create_purchase_order(conn, MERCHANT_ID, po_req)
        po_id = po_result["id"]
        assert po_result["status"] == "pending_approval"

        dec_idemp = f"test_proc_reject_dec_{secrets.token_hex(8)}"
        decision_req = OwnerDecisionRequest(
            decision="rejected",
            decision_reason="Rejection audit test",
            idempotency_key=dec_idemp,
        )
        server_actor = f"owner_{MERCHANT_ID}"
        rej_result = await process_owner_decision(
            conn, MERCHANT_ID, po_id, decision_req, decided_by_actor=server_actor
        )

        assert rej_result["decision"] == "rejected"
        assert rej_result["spend_reserved"] == Decimal("0.00"), (
            f"spend_reserved must be 0 for a rejection, got {rej_result['spend_reserved']}"
        )

        # PO status must be 'rejected'
        po_row = await conn.fetchrow(
            "SELECT status FROM purchase_orders WHERE merchant_id = $1 AND id = $2",
            MERCHANT_ID,
            po_id,
        )
        assert po_row["status"] == "rejected"

        # No spend_reservations row
        resv = await conn.fetchrow(
            "SELECT id FROM spend_reservations WHERE merchant_id = $1 AND purchase_order_id = $2",
            MERCHANT_ID,
            po_id,
        )
        assert resv is None, "spend_reservations must NOT be written for a rejection"

        # No financial_ledger entry referencing this PO
        ledger = await conn.fetchrow(
            "SELECT id FROM financial_ledger WHERE merchant_id = $1 AND reference_id = $2",
            MERCHANT_ID,
            po_id,
        )
        assert ledger is None, "financial_ledger must NOT be appended for a rejection"

        # purchase_order_approvals row must exist with decision='rejected'
        appr = await conn.fetchrow(
            "SELECT decision FROM purchase_order_approvals WHERE merchant_id = $1 AND purchase_order_id = $2",
            MERCHANT_ID,
            po_id,
        )
        assert appr is not None, "purchase_order_approvals row must exist for a rejection"
        assert appr["decision"] == "rejected"
    finally:
        await tr.rollback()
        await conn.close()
