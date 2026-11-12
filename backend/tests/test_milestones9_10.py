"""
backend/tests/test_milestones9_10.py
====================================
Comprehensive offline test suite for Milestone 9 & 10 (including Milestone 10.1 Financial Integrity Corrections):
- POS sales validation, atomic inventory decrement, and movement recording.
- Deterministic procurement policy validation:
  - $150 per-order landed cost ceiling (including $180.20 blocked vs $139.20 accepted).
  - Spend envelope capacity ($500 cap, prior commitments).
  - Supplier allowlist trust tiers.
  - Rolling 24-hour anti-order-splitting guardrail.
- Milestone 10.1 Corrections:
  - Merchant-before-PO lock acquisition order to serialize concurrent approvals.
  - Canonical supplier offer price derivation (untrusted client price ignored/rejected).
  - Missing supplier offer rejected.
  - Server-derived owner identity (`decided_by_actor`) preventing client impersonation.
  - Anti-splitting policy revalidation during approval.
"""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, call

from app.schemas.orders import POSSaleRequest, POSSaleItemRequest
from app.schemas.procurement import (
    PurchaseOrderCreateRequest,
    PurchaseOrderItemCreate,
    OwnerDecisionRequest,
)
from app.db.repositories.order_repo import execute_pos_sale
from app.db.repositories.procurement_repo import (
    evaluate_spending_policy,
    create_purchase_order,
    process_owner_decision,
)


@pytest.mark.asyncio
async def test_pos_sale_rejects_insufficient_stock():
    """Verify that an order requesting more stock than available is rejected."""
    mock_conn = MagicMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)  # no prior idempotent order
    mock_conn.fetch = AsyncMock(return_value=[{
        "product_id": "prod_blue_vase",
        "stock_in_store": 12,
        "reserved_in_store": 0,
        "available_in_store": 12,
        "product_name": "Handcrafted Blue Ceramic Vase",
        "sku": "NSL-VASE-001",
        "selling_price": Decimal("24.00"),
    }])

    req = POSSaleRequest(
        items=[POSSaleItemRequest(product_id="prod_blue_vase", quantity=15)],
        idempotency_key="test_idemp_insufficient",
    )

    with pytest.raises(ValueError, match="Insufficient in-store inventory"):
        await execute_pos_sale(mock_conn, "merchant_northstar", req)


@pytest.mark.asyncio
async def test_pos_sale_rejects_unknown_product():
    """Verify that an order with a non-existent product ID raises ValueError."""
    mock_conn = MagicMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetch = AsyncMock(return_value=[])  # no products found

    req = POSSaleRequest(
        items=[POSSaleItemRequest(product_id="prod_unknown", quantity=1)],
        idempotency_key="test_idemp_unknown",
    )

    with pytest.raises(ValueError, match="does not exist"):
        await execute_pos_sale(mock_conn, "merchant_northstar", req)


@pytest.mark.asyncio
async def test_pos_sale_success_atomic_decrement_and_movements():
    """
    Verify successful POS sale:
    - 2 units sold at $24.00 = $48.00 subtotal + $3.84 tax (8%) = $51.84.
    - stock decremented by 2.
    - inventory movement recorded with pos_sale.
    - audit log created.
    """
    mock_conn = MagicMock()
    mock_conn.fetchrow = AsyncMock()
    mock_conn.fetchrow.side_effect = [
        None,  # Idempotency lookup
        {
            "id": "order_pos_123",
            "merchant_id": "merchant_northstar",
            "order_number": "POS-DEMO",
            "channel": "pos",
            "status": "completed",
            "subtotal": Decimal("48.00"),
            "tax_amount": Decimal("3.84"),
            "shipping_amount": Decimal("0.00"),
            "total_amount": Decimal("51.84"),
            "payment_method": "card_in_store",
            "created_at": "2026-11-10T20:00:00Z",
        },
        {
            "product_id": "prod_blue_vase",
            "stock_in_store": 10,
            "stock_online": 0,
            "available_in_store": 10,
            "available_online": 0,
            "total_available": 10,
        }
    ]

    mock_conn.fetch = AsyncMock(return_value=[{
        "product_id": "prod_blue_vase",
        "stock_in_store": 12,
        "reserved_in_store": 0,
        "available_in_store": 12,
        "product_name": "Handcrafted Blue Ceramic Vase",
        "sku": "NSL-VASE-001",
        "selling_price": Decimal("24.00"),
    }])
    mock_conn.execute = AsyncMock()

    req = POSSaleRequest(
        items=[POSSaleItemRequest(product_id="prod_blue_vase", quantity=2)],
        idempotency_key="pos_sale_demo_01",
    )

    order, updated_inv = await execute_pos_sale(mock_conn, "merchant_northstar", req)

    assert order["order_number"] == "POS-DEMO"
    assert order["subtotal"] == Decimal("48.00")
    assert order["total_amount"] == Decimal("51.84")
    assert len(order["items"]) == 1
    assert order["items"][0]["quantity"] == 2
    assert updated_inv[0]["stock_in_store"] == 10
    assert mock_conn.execute.call_count >= 3


@pytest.mark.asyncio
async def test_pos_sale_idempotency_duplicate_returns_original_order():
    """
    Verify that repeating a POS sale with the same idempotency key returns the original
    order and current stock without writing new records or decrementing stock.
    """
    mock_conn = MagicMock()
    mock_conn.fetchrow = AsyncMock(side_effect=[
        {"reference_id": "order_pos_123"},  # Idempotency lookup finds existing movement
        {                                    # get_order_by_id order record
            "id": "order_pos_123",
            "merchant_id": "merchant_northstar",
            "order_number": "POS-DEMO",
            "channel": "pos",
            "status": "completed",
            "subtotal": Decimal("48.00"),
            "tax_amount": Decimal("3.84"),
            "shipping_amount": Decimal("0.00"),
            "total_amount": Decimal("51.84"),
            "payment_method": "card_in_store",
            "created_at": "2026-11-10T20:00:00Z",
        },
    ])
    mock_conn.fetch = AsyncMock(side_effect=[
        [  # get_order_by_id line items
            {
                "id": "item_1",
                "merchant_id": "merchant_northstar",
                "order_id": "order_pos_123",
                "product_id": "prod_blue_vase",
                "quantity": 2,
                "unit_price": Decimal("24.00"),
                "line_total": Decimal("48.00"),
                "product_name": "Handcrafted Blue Ceramic Vase",
                "sku": "NSL-VASE-001",
            }
        ],
        [  # inventory states query
            {
                "product_id": "prod_blue_vase",
                "stock_in_store": 10,
                "available_in_store": 10,
                "total_available": 10,
            }
        ],
    ])
    mock_conn.execute = AsyncMock()

    req = POSSaleRequest(
        items=[POSSaleItemRequest(product_id="prod_blue_vase", quantity=2)],
        idempotency_key="pos_sale_demo_01",
    )

    order, updated_inv = await execute_pos_sale(mock_conn, "merchant_northstar", req)

    assert order["id"] == "order_pos_123"
    assert order["order_number"] == "POS-DEMO"
    assert len(order["items"]) == 1
    assert updated_inv[0]["stock_in_store"] == 10
    # Crucial: execute must not have been called (no order/items/movement/audit written)
    assert mock_conn.execute.call_count == 0


@pytest.mark.asyncio
async def test_policy_blocks_180_20_order_exceeding_150_ceiling():
    """
    Blueprint Scenario:
    Supplier C offer: $4.10/unit + $8.00 shipping.
    42 units * $4.10 = $172.20 + $8.00 shipping = $180.20 landed cost.
    Must be BLOCKED because it exceeds the $150.00 per-order ceiling.
    """
    mock_conn = MagicMock()
    with patch("app.db.repositories.procurement_repo.get_spend_envelope", AsyncMock(return_value={
        "demo_envelope_cap": Decimal("500.00"),
        "available_spend": Decimal("325.00"),
        "committed_spend": Decimal("175.00"),
    })):
        mock_conn.fetchrow = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            {"trust_tier": "allowlisted"},
            {"rolling_sum": Decimal("0.00")},
        ]

        item_subtotal = Decimal("172.20")  # 42 units * $4.10
        shipping_cost = Decimal("8.00")

        result = await evaluate_spending_policy(
            conn=mock_conn,
            merchant_id="merchant_northstar",
            supplier_id="sup_supplier_c",
            item_subtotal=item_subtotal,
            shipping_cost=shipping_cost,
            tax_cost=Decimal("0.00"),
            labour_overhead_cost=Decimal("0.00"),
            has_unknown_costs=False,
            unknown_cost_nte_ceiling=None,
        )

        assert result.passed is False
        assert result.per_order_limit_passed is False
        assert result.calculated_landed_cost == Decimal("180.20")
        assert any("$180.20 exceeds the $150.00 cap" in r for r in result.reasons)


@pytest.mark.asyncio
async def test_policy_accepts_139_20_order_under_150_ceiling():
    """
    Blueprint Scenario:
    Supplier C offer: $4.10/unit + $8.00 shipping.
    32 units * $4.10 = $131.20 + $8.00 shipping = $139.20 landed cost.
    Must PASS policy and have remaining envelope = $325.00 - $139.20 = $185.80.
    """
    mock_conn = MagicMock()
    with patch("app.db.repositories.procurement_repo.get_spend_envelope", AsyncMock(return_value={
        "demo_envelope_cap": Decimal("500.00"),
        "available_spend": Decimal("325.00"),
        "committed_spend": Decimal("175.00"),
    })):
        mock_conn.fetchrow = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            {"trust_tier": "allowlisted"},
            {"rolling_sum": Decimal("0.00")},
        ]

        item_subtotal = Decimal("131.20")  # 32 units * $4.10
        shipping_cost = Decimal("8.00")

        result = await evaluate_spending_policy(
            conn=mock_conn,
            merchant_id="merchant_northstar",
            supplier_id="sup_supplier_c",
            item_subtotal=item_subtotal,
            shipping_cost=shipping_cost,
            tax_cost=Decimal("0.00"),
            labour_overhead_cost=Decimal("0.00"),
            has_unknown_costs=False,
            unknown_cost_nte_ceiling=None,
        )

        assert result.passed is True
        assert result.per_order_limit_passed is True
        assert result.envelope_cap_passed is True
        assert result.calculated_landed_cost == Decimal("139.20")
        assert result.remaining_envelope_if_approved == Decimal("185.80")


@pytest.mark.asyncio
async def test_policy_enforces_supplier_allowlist():
    """Verify that Supplier B (requires_approval) is flagged by policy."""
    mock_conn = MagicMock()
    with patch("app.db.repositories.procurement_repo.get_spend_envelope", AsyncMock(return_value={
        "available_spend": Decimal("325.00"),
    })):
        mock_conn.fetchrow = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            {"trust_tier": "requires_approval"},
            {"rolling_sum": Decimal("0.00")},
        ]

        result = await evaluate_spending_policy(
            conn=mock_conn,
            merchant_id="merchant_northstar",
            supplier_id="sup_supplier_b",
            item_subtotal=Decimal("74.00"),
            shipping_cost=Decimal("12.00"),
            tax_cost=Decimal("0.00"),
            labour_overhead_cost=Decimal("0.00"),
            has_unknown_costs=False,
            unknown_cost_nte_ceiling=None,
        )

        assert result.passed is False
        assert result.allowlist_passed is False
        assert any("requires explicit owner qualification" in r for r in result.reasons)


@pytest.mark.asyncio
async def test_policy_anti_order_splitting_24h_window():
    """
    Verify anti-order-splitting guardrail:
    An order of $90 is proposed to Supplier A.
    Prior orders to Supplier A in the last 24h total $70.
    $70 + $90 = $160 > $150 limit -> BLOCKED.
    """
    mock_conn = MagicMock()
    with patch("app.db.repositories.procurement_repo.get_spend_envelope", AsyncMock(return_value={
        "available_spend": Decimal("325.00"),
    })):
        mock_conn.fetchrow = AsyncMock()
        mock_conn.fetchrow.side_effect = [
            {"trust_tier": "allowlisted"},
            {"rolling_sum": Decimal("70.00")},
        ]

        result = await evaluate_spending_policy(
            conn=mock_conn,
            merchant_id="merchant_northstar",
            supplier_id="sup_supplier_a",
            item_subtotal=Decimal("80.00"),
            shipping_cost=Decimal("10.00"),
            tax_cost=Decimal("0.00"),
            labour_overhead_cost=Decimal("0.00"),
            has_unknown_costs=False,
            unknown_cost_nte_ceiling=None,
        )

        assert result.passed is False
        assert result.anti_splitting_passed is False
        assert any("Anti-order-splitting violation" in r for r in result.reasons)


# ==============================================================================
# MILESTONE 10.1 REGRESSION TESTS: FINANCIAL INTEGRITY & LOCKING
# ==============================================================================

@pytest.mark.asyncio
async def test_po_creation_uses_canonical_supplier_prices():
    """
    Milestone 10.1: Verify that create_purchase_order retrieves unit_cost and shipping_cost
    from the canonical supplier_offers table rather than trusting client-provided prices.
    """
    mock_conn = MagicMock()
    # 1. Lock merchant row
    # 2. Insert PO
    mock_conn.fetchrow = AsyncMock(side_effect=[
        {"id": "merchant_northstar"},  # SELECT id FROM merchants ... FOR UPDATE
        {
            "id": "po_canonical_1",
            "merchant_id": "merchant_northstar",
            "po_number": "PO-2026-001",
            "supplier_id": "sup_supplier_c",
            "status": "pending_approval",
            "item_subtotal": Decimal("131.20"),
            "shipping_cost": Decimal("8.00"),
            "total_landed_cost": Decimal("139.20"),
            "tax_cost": Decimal("0.00"),
            "labour_overhead_cost": Decimal("0.00"),
            "has_unknown_costs": False,
            "unknown_cost_nte_ceiling": None,
            "payment_status": "unpaid",
            "is_illustrative": True,
            "created_at": "2026-11-10T21:00:00Z",
            "updated_at": "2026-11-10T21:00:00Z",
        }
    ])

    # Canonical offers fetched from DB
    mock_conn.fetch = AsyncMock(return_value=[{
        "product_id": "prod_blue_vase",
        "unit_cost": Decimal("4.10"),
        "shipping_cost": Decimal("8.00"),
        "moq": 20,
    }])
    mock_conn.execute = AsyncMock()

    with patch("app.db.repositories.procurement_repo.evaluate_spending_policy", AsyncMock(return_value=MagicMock(
        passed=True,
        calculated_landed_cost=Decimal("139.20"),
        model_dump=lambda: {"passed": True}
    ))):
        req = PurchaseOrderCreateRequest(
            supplier_id="sup_supplier_c",
            items=[PurchaseOrderItemCreate(product_id="prod_blue_vase", quantity=32)],
            idempotency_key="po_idemp_canonical_01",
        )

        res = await create_purchase_order(mock_conn, "merchant_northstar", req)

        # Confirm merchant lock acquired
        first_call = mock_conn.fetchrow.call_args_list[0]
        assert "SELECT id FROM merchants WHERE id = $1 FOR UPDATE" in first_call[0][0]

        # Confirm canonical cost was used
        assert res["item_subtotal"] == Decimal("131.20")
        assert res["items"][0]["unit_cost"] == Decimal("4.10")


@pytest.mark.asyncio
async def test_po_creation_rejects_missing_supplier_offer():
    """
    Milestone 10.1: Verify that proposing a PO for a product that the supplier does not offer raises ValueError.
    """
    mock_conn = MagicMock()
    mock_conn.fetchrow = AsyncMock(return_value={"id": "merchant_northstar"})
    mock_conn.fetch = AsyncMock(return_value=[])  # No matching offer in catalog

    req = PurchaseOrderCreateRequest(
        supplier_id="sup_supplier_c",
        items=[PurchaseOrderItemCreate(product_id="prod_unsupported", quantity=20)],
        idempotency_key="po_idemp_unsupported",
    )

    with pytest.raises(ValueError, match="No catalog offer found"):
        await create_purchase_order(mock_conn, "merchant_northstar", req)


@pytest.mark.asyncio
async def test_owner_approval_merchant_before_po_lock_order():
    """
    Milestone 10.1: Verify that process_owner_decision strictly enforces the lock order:
    1. SELECT id FROM merchants WHERE id = $1 FOR UPDATE
    2. SELECT * FROM purchase_orders WHERE merchant_id = $1 AND id = $2 FOR UPDATE
    Eliminating concurrent approval envelope races.
    """
    mock_conn = MagicMock()
    mock_conn.fetchrow = AsyncMock(side_effect=[
        {"id": "merchant_northstar"},  # Merchant lock
        {                             # PO lock
            "id": "po_demo_01",
            "merchant_id": "merchant_northstar",
            "supplier_id": "sup_supplier_c",
            "po_number": "PO-2026-001",
            "status": "pending_approval",
            "item_subtotal": Decimal("131.20"),
            "shipping_cost": Decimal("8.00"),
            "tax_cost": Decimal("0.00"),
            "labour_overhead_cost": Decimal("0.00"),
            "total_landed_cost": Decimal("139.20"),
            "has_unknown_costs": False,
            "unknown_cost_nte_ceiling": None,
        }
    ])
    mock_conn.execute = AsyncMock()

    with patch("app.db.repositories.procurement_repo.evaluate_spending_policy", AsyncMock(return_value=MagicMock(
        passed=True, reasons=[]
    ))):
        with patch("app.db.repositories.procurement_repo.get_spend_envelope", AsyncMock(side_effect=[
            {"available_spend": Decimal("325.00")},
            {"available_spend": Decimal("185.80")},
        ])):
            with patch("app.db.repositories.procurement_repo.get_purchase_order_by_id", AsyncMock(return_value={
                "id": "po_demo_01",
                "status": "ordered",
                "payment_status": "reserved",
                "items": [],
            })):
                req = OwnerDecisionRequest(
                    decision="approved",
                    decision_reason="Reorder approved within policy",
                    idempotency_key="appr_idemp_101",
                )

                res = await process_owner_decision(
                    conn=mock_conn,
                    merchant_id="merchant_northstar",
                    po_id="po_demo_01",
                    decision_req=req,
                    decided_by_actor="owner_merchant_northstar",
                )

                # Verify exact lock order
                fetchrow_calls = mock_conn.fetchrow.call_args_list
                assert "SELECT id FROM merchants WHERE id = $1 FOR UPDATE" in fetchrow_calls[0][0][0]
                assert "SELECT * FROM purchase_orders" in fetchrow_calls[1][0][0]
                assert "FOR UPDATE" in fetchrow_calls[1][0][0]

                # Verify server-derived actor was used in audit log
                audit_call = mock_conn.execute.call_args_list[-1]
                assert "owner_merchant_northstar" in audit_call[0][2]
                assert res["spend_reserved"] == Decimal("139.20")


@pytest.mark.asyncio
async def test_owner_approval_revalidates_and_blocks_depleted_envelope():
    """
    Milestone 10.1: Verify that if another transaction depleted the envelope between drafting
    and approval, the approval is rejected with an error during policy revalidation.
    """
    mock_conn = MagicMock()
    mock_conn.fetchrow = AsyncMock(side_effect=[
        {"id": "merchant_northstar"},
        {
            "id": "po_demo_01",
            "merchant_id": "merchant_northstar",
            "supplier_id": "sup_supplier_c",
            "po_number": "PO-2026-001",
            "status": "pending_approval",
            "item_subtotal": Decimal("131.20"),
            "shipping_cost": Decimal("8.00"),
            "tax_cost": Decimal("0.00"),
            "labour_overhead_cost": Decimal("0.00"),
            "total_landed_cost": Decimal("139.20"),
            "has_unknown_costs": False,
            "unknown_cost_nte_ceiling": None,
        }
    ])

    # Revalidation fails because envelope depleted
    with patch("app.db.repositories.procurement_repo.evaluate_spending_policy", AsyncMock(return_value=MagicMock(
        passed=False, reasons=["Spend envelope depleted: Order cost $139.20 exceeds available weekly allowance of $100.00."]
    ))):
        req = OwnerDecisionRequest(
            decision="approved",
            decision_reason="Attempting approval",
            idempotency_key="appr_fail_depleted",
        )

        with pytest.raises(ValueError, match="Cannot approve PO due to policy revalidation failure"):
            await process_owner_decision(
                conn=mock_conn,
                merchant_id="merchant_northstar",
                po_id="po_demo_01",
                decision_req=req,
                decided_by_actor="owner_merchant_northstar",
            )
