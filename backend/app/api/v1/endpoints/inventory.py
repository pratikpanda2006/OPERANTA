"""
backend/app/api/v1/endpoints/inventory.py
=========================================
API endpoints for omnichannel inventory stock levels, allocations, and reorder signals.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status
import asyncpg

from app.api.deps import get_db, get_merchant_context
from app.db.repositories.inventory_repo import (
    InventoryTransferError,
    TransferDuplicateError,
    TransferInsufficientStockError,
    TransferNotFoundError,
    list_inventory_items,
    transfer_store_to_online,
)
from app.schemas.inventory import InventoryListResponse, InventoryTransferRequest, InventoryTransferResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/inventory", tags=["Inventory"])


@router.get("", response_model=InventoryListResponse)
async def get_inventory(
    merchant_id: str = Depends(get_merchant_context),
    conn: asyncpg.Connection = Depends(get_db),
) -> InventoryListResponse:
    """
    Retrieve unified stock status, reservations, and reorder alerts for all SKUs
    belonging to the validated tenant.
    """
    try:
        items = await list_inventory_items(conn, merchant_id)
        low_stock_count = sum(1 for item in items if item["reorder_recommended"])

        return InventoryListResponse(
            merchant_id=merchant_id,
            items=items,
            total_items=len(items),
            low_stock_count=low_stock_count,
        )
    except Exception as e:
        logger.error("Error retrieving inventory for merchant '%s': %s", merchant_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve inventory data.",
        )


@router.post("/transfers", response_model=InventoryTransferResponse, status_code=status.HTTP_201_CREATED)
async def create_inventory_transfer(
    transfer_req: InventoryTransferRequest,
    merchant_id: str = Depends(get_merchant_context),
    conn: asyncpg.Connection = Depends(get_db),
) -> InventoryTransferResponse:
    """
    Transfer a positive quantity of a single product from store to online.

    Tenant identity is derived strictly server-side from the authenticated/demo
    tenant dependency — merchant_id is NEVER accepted from the client. Actor
    identity for the audit trail is also derived server-side. Runs in a single
    transaction with the inventory row locked; rolls back on any failure.
    """
    # Server-derived actor (mirrors procurement decision convention).
    server_derived_actor = f"owner_{merchant_id}"

    try:
        result = await transfer_store_to_online(
            conn,
            merchant_id=merchant_id,
            product_id=transfer_req.product_id,
            quantity=transfer_req.quantity,
            idempotency_key=transfer_req.idempotency_key,
            actor_type="merchant_owner",
            actor_id=server_derived_actor,
            reason=transfer_req.reason,
        )
    except TransferNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TransferInsufficientStockError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except TransferDuplicateError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except InventoryTransferError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e

    return InventoryTransferResponse(**result)
