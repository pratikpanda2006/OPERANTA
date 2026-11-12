"""
backend/app/api/v1/endpoints/procurement.py
===========================================
API endpoints for spend envelope guardrails, supplier directory, reorder proposals,
purchase order lifecycle, and owner approval decisions.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status
import asyncpg

from app.api.deps import get_db, get_merchant_context
from app.db.repositories.merchant_repo import get_spend_envelope
from app.db.repositories.supplier_repo import list_suppliers_with_offers
from app.db.repositories.procurement_repo import (
    generate_reorder_plan,
    create_purchase_order,
    list_purchase_orders,
    get_purchase_order_by_id,
    process_owner_decision,
)
from app.schemas.merchant import SpendEnvelopeResponse
from app.schemas.supplier import SupplierListResponse
from app.schemas.procurement import (
    ReorderPlanResponse,
    PurchaseOrderCreateRequest,
    PurchaseOrderResponse,
    PurchaseOrderListResponse,
    OwnerDecisionRequest,
    OwnerDecisionResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/procurement", tags=["Procurement"])


@router.get("/spend-envelope", response_model=SpendEnvelopeResponse)
async def get_merchant_spend_envelope(
    merchant_id: str = Depends(get_merchant_context),
    conn: asyncpg.Connection = Depends(get_db),
) -> SpendEnvelopeResponse:
    """
    Retrieve the active weekly spend envelope metrics for the validated merchant.
    Derives committed spend and remaining balance from the append-only financial ledger.
    """
    try:
        envelope = await get_spend_envelope(conn, merchant_id)
        if envelope is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Merchant profile '{merchant_id}' not found.",
            )
        return SpendEnvelopeResponse.model_validate(envelope)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error retrieving spend envelope for merchant '%s': %s", merchant_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve spend envelope data.",
        )


@router.get("/suppliers", response_model=SupplierListResponse)
async def get_suppliers(
    merchant_id: str = Depends(get_merchant_context),
    conn: asyncpg.Connection = Depends(get_db),
) -> SupplierListResponse:
    """
    Retrieve the qualified supplier directory, delivery reliability ratings,
    and active catalog product offers for the validated merchant.
    """
    try:
        suppliers = await list_suppliers_with_offers(conn, merchant_id)
        return SupplierListResponse(
            merchant_id=merchant_id,
            suppliers=suppliers,
            total_suppliers=len(suppliers),
        )
    except Exception as e:
        logger.error("Error retrieving suppliers for merchant '%s': %s", merchant_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve supplier catalog data.",
        )


@router.post("/plan", response_model=ReorderPlanResponse)
async def get_reorder_plan(
    merchant_id: str = Depends(get_merchant_context),
    conn: asyncpg.Connection = Depends(get_db),
) -> ReorderPlanResponse:
    """
    Evaluates store inventory against reorder points and dynamically calculates replenishment plans
    with multi-factor supplier evaluation.
    """
    try:
        items = await generate_reorder_plan(conn, merchant_id)
        return ReorderPlanResponse(
            merchant_id=merchant_id,
            items=items,
            total_recommendations=len(items),
        )
    except Exception as e:
        logger.error("Error generating reorder plan for merchant '%s': %s", merchant_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate reorder plan.",
        )


@router.post("/purchase-orders", response_model=PurchaseOrderResponse)
async def create_po(
    po_req: PurchaseOrderCreateRequest,
    merchant_id: str = Depends(get_merchant_context),
    conn: asyncpg.Connection = Depends(get_db),
) -> PurchaseOrderResponse:
    """
    Drafts a new purchase order and executes deterministic backend policy evaluation.
    Enforces merchant-first row locking and derives canonical supplier pricing server-side.
    Checks:
    - $150.00 landed cost ceiling.
    - $500.00 weekly spend envelope availability.
    - Supplier allowlist trust tier.
    - 24-hour rolling anti-order-splitting guardrail.
    """
    try:
        async with conn.transaction():
            po = await create_purchase_order(conn, merchant_id, po_req)
            return PurchaseOrderResponse.model_validate(po)
    except ValueError as ve:
        logger.warning("PO validation failed for merchant '%s': %s", merchant_id, ve)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ve),
        )
    except Exception as e:
        logger.error("Error creating purchase order for merchant '%s': %s", merchant_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create purchase order.",
        )


@router.get("/purchase-orders", response_model=PurchaseOrderListResponse)
async def get_purchase_orders(
    limit: int = 50,
    merchant_id: str = Depends(get_merchant_context),
    conn: asyncpg.Connection = Depends(get_db),
) -> PurchaseOrderListResponse:
    """
    List purchase orders on file with status and line items.
    """
    try:
        pos = await list_purchase_orders(conn, merchant_id, limit=limit)
        return PurchaseOrderListResponse(
            merchant_id=merchant_id,
            purchase_orders=[PurchaseOrderResponse.model_validate(p) for p in pos],
            total_orders=len(pos),
        )
    except Exception as e:
        logger.error("Error retrieving purchase orders for merchant '%s': %s", merchant_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve purchase orders.",
        )


@router.post("/purchase-orders/{po_id}/decision", response_model=OwnerDecisionResponse)
async def decide_purchase_order(
    po_id: str,
    decision_req: OwnerDecisionRequest,
    merchant_id: str = Depends(get_merchant_context),
    conn: asyncpg.Connection = Depends(get_db),
) -> OwnerDecisionResponse:
    """
    Execute an atomic human-in-the-loop owner approval or rejection decision.
    - Locks merchant row first, then target PO row with SELECT ... FOR UPDATE.
    - Derives actor identity server-side ('demo_merchant_owner' in DEMO_MODE) to prevent client impersonation.
    - Re-evaluates live policy against available weekly envelope and 24h supplier limits.
    - On approval: creates spend_reservations record, appends financial_ledger entry, updates PO to 'ordered'.
    - On rejection: marks PO 'rejected' without reserving funds.
    - Records purchase_order_approvals and operational_audit_log entries.
    """
    # Authorization Note: In DEMO_MODE, actor identity is derived server-side.
    # DEMO_MODE does not provide production-grade user authentication.
    server_derived_actor = f"owner_{merchant_id}"

    try:
        async with conn.transaction():
            res = await process_owner_decision(
                conn,
                merchant_id,
                po_id,
                decision_req,
                decided_by_actor=server_derived_actor,
            )
            return OwnerDecisionResponse(
                purchase_order=PurchaseOrderResponse.model_validate(res["purchase_order"]),
                decision=res["decision"],
                spend_reserved=res["spend_reserved"],
                remaining_envelope_available=res["remaining_envelope_available"],
                audit_logged=res["audit_logged"],
            )
    except ValueError as ve:
        logger.warning("PO decision rejected for merchant '%s', po '%s': %s", merchant_id, po_id, ve)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ve),
        )
    except Exception as e:
        logger.error("Error processing PO decision for merchant '%s', po '%s': %s", merchant_id, po_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process owner approval decision.",
        )
