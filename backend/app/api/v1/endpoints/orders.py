"""
backend/app/api/v1/endpoints/orders.py
======================================
API routes for customer sales and POS simulator transactions.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status
import asyncpg

from app.api.deps import get_db, get_merchant_context
from app.db.repositories.order_repo import execute_pos_sale, list_orders
from app.schemas.orders import (
    POSSaleRequest,
    POSSaleResponse,
    OrderResponse,
    OrderListResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orders", tags=["Orders & POS"])


@router.post("/pos", response_model=POSSaleResponse)
async def create_pos_sale(
    sale_req: POSSaleRequest,
    merchant_id: str = Depends(get_merchant_context),
    conn: asyncpg.Connection = Depends(get_db),
) -> POSSaleResponse:
    """
    Execute an atomic in-store POS transaction:
    - Verifies available stock using SELECT ... FOR UPDATE.
    - Decrements inventory balance atomically.
    - Creates orders and order_items records.
    - Emits inventory_movements record with pos_sale type.
    - Records operational audit log.
    - Enforces idempotency via client-provided idempotency_key.
    """
    try:
        async with conn.transaction():
            order, updated_inv = await execute_pos_sale(conn, merchant_id, sale_req)
            return POSSaleResponse(
                order=OrderResponse.model_validate(order),
                updated_inventory=updated_inv,
            )
    except ValueError as ve:
        logger.warning("POS sale validation failed for merchant '%s': %s", merchant_id, ve)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ve),
        )
    except Exception as e:
        logger.error("POS transaction error for merchant '%s': %s", merchant_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process POS sale.",
        )


@router.get("", response_model=OrderListResponse)
async def get_orders(
    limit: int = 50,
    merchant_id: str = Depends(get_merchant_context),
    conn: asyncpg.Connection = Depends(get_db),
) -> OrderListResponse:
    """
    Retrieve omnichannel order stream (POS and online checkouts) for the validated merchant.
    """
    try:
        orders = await list_orders(conn, merchant_id, limit=limit)
        return OrderListResponse(
            merchant_id=merchant_id,
            orders=[OrderResponse.model_validate(o) for o in orders],
            total_orders=len(orders),
        )
    except Exception as e:
        logger.error("Error retrieving orders for merchant '%s': %s", merchant_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve orders.",
        )
