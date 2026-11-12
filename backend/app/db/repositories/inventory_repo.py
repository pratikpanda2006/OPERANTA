"""
backend/app/db/repositories/inventory_repo.py
=============================================
Data access layer for unified omnichannel inventory and SKU replenishment status.
Enforces multi-tenant composite primary/foreign key isolation on all queries.

SCHEMA ALIGNMENT NOTES (migration 001):
  products columns:
    - title         (NOT "name")
    - selling_price (NOT "retail_price")
  inventory_items columns:
    - stock_in_store, reserved_in_store, available_in_store (GENERATED ALWAYS AS)
    - stock_online,   reserved_online,   available_online   (GENERATED ALWAYS AS)
    - daily_demand: INT (seed value 4)
    - NO stored total_on_hand or total_available columns — computed in SQL expression.
"""

import json
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

import asyncpg


async def list_inventory_items(
    conn: asyncpg.Connection,
    merchant_id: str,
) -> List[dict]:
    """
    List all inventory items with joined product details for the specified merchant.
    Calculates total_on_hand and total_available as SQL expressions (not stored columns).
    Derives reorder_recommended from total_available <= reorder_point.

    Column mapping from migration 001:
      products.title          → product_name
      products.selling_price  → retail_price
      (stock_in_store + stock_online)             → total_on_hand   (expression)
      (available_in_store + available_online)      → total_available (expression)

    Returns:
        List of dicts conforming to InventoryItemResponse.
    """
    rows = await conn.fetch(
        """
        SELECT
            i.id,
            i.merchant_id,
            i.product_id,
            p.sku,
            p.title          AS product_name,
            p.category,
            p.selling_price  AS retail_price,
            p.base_cost,
            i.stock_in_store,
            i.reserved_in_store,
            i.available_in_store,
            i.stock_online,
            i.reserved_online,
            i.available_online,
            (i.stock_in_store + i.stock_online)               AS total_on_hand,
            (i.available_in_store + i.available_online)        AS total_available,
            i.reorder_point,
            i.daily_demand,
            i.lead_days,
            i.safety_stock
        FROM inventory_items i
        INNER JOIN products p
            ON p.merchant_id = i.merchant_id
           AND p.id = i.product_id
        WHERE i.merchant_id = $1
        ORDER BY p.title ASC
        """,
        merchant_id,
    )

    items = []
    for r in rows:
        tot_avail = r["total_available"]
        reorder_pt = r["reorder_point"]
        reorder_recommended = tot_avail <= reorder_pt

        items.append({
            "id": r["id"],
            "merchant_id": r["merchant_id"],
            "product_id": r["product_id"],
            "sku": r["sku"],
            "product_name": r["product_name"],
            "category": r["category"],
            "retail_price": Decimal(str(r["retail_price"])),
            "base_cost": Decimal(str(r["base_cost"])) if r["base_cost"] is not None else None,
            "stock_in_store": r["stock_in_store"],
            "reserved_in_store": r["reserved_in_store"],
            "available_in_store": r["available_in_store"],
            "stock_online": r["stock_online"],
            "reserved_online": r["reserved_online"],
            "available_online": r["available_online"],
            "total_on_hand": r["total_on_hand"],
            "total_available": tot_avail,
            "reorder_point": reorder_pt,
            "daily_demand": Decimal(str(r["daily_demand"])),
            "lead_days": r["lead_days"],
            "safety_stock": r["safety_stock"],
            "reorder_recommended": reorder_recommended,
        })

    return items


# ---------------------------------------------------------------------------
# Store → online channel transfer (M12.3.4)
# ---------------------------------------------------------------------------

class InventoryTransferError(Exception):
    """Base error for a failed store-to-online transfer."""


class TransferNotFoundError(InventoryTransferError):
    """The target product does not belong to the current merchant."""


class TransferInsufficientStockError(InventoryTransferError):
    """Not enough unreserved store stock for the requested quantity."""


class TransferDuplicateError(InventoryTransferError):
    """An identical transfer was already applied (idempotency key collision)."""


async def transfer_store_to_online(
    conn: asyncpg.Connection,
    *,
    merchant_id: str,
    product_id: str,
    quantity: int,
    idempotency_key: str,
    actor_type: str = "merchant_owner",
    actor_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Atomically transfer ``quantity`` units from the physical store channel to
    the online channel for a single product owned by ``merchant_id``.

    Safety guarantees (all inside one transaction):
      - The inventory row is locked (SELECT ... FOR UPDATE) before validation.
      - Only products belonging to the current merchant are visible.
      - Available store stock (stock_in_store - reserved_in_store) must be >=
        quantity; reservations in both channels are preserved untouched.
      - Decrement stock_in_store and increment stock_online by the same amount,
        preserving total physical on-hand stock.
      - Two inventory movement rows (one per affected channel) are inserted using
        the existing 'transfer' movement/channel convention, each carrying a
        derived idempotency key; the shared `uq_inventory_movement_idempotency`
        unique constraint is the all-or-nothing guard against retry replays.
      - One operational audit event is appended with a server-derived actor.
      - Any failure before commit rolls back the entire change.
    """
    if quantity <= 0:
        raise InventoryTransferError("Transfer quantity must be a positive integer.")

    # Scope the idempotency keys per product+channel so the same client key can
    # be reused across different products without cross-collisions.
    store_key = f"{idempotency_key}_{product_id}:in_store"
    online_key = f"{idempotency_key}_{product_id}:online"

    async with conn.transaction():
        inv = await conn.fetchrow(
            """
            SELECT stock_in_store, reserved_in_store, stock_online, reserved_online
            FROM inventory_items
            WHERE merchant_id = $1 AND product_id = $2
            FOR UPDATE
            """,
            merchant_id,
            product_id,
        )
        if inv is None:
            raise TransferNotFoundError(
                f"Product '{product_id}' does not exist for this merchant."
            )

        # Idempotency pre-check: if either movement key already exists, the whole
        # transfer was already applied (the pair is written atomically).
        existing = await conn.fetch(
            """
            SELECT idempotency_key FROM inventory_movements
            WHERE merchant_id = $1 AND idempotency_key = ANY($2::text[])
            """,
            merchant_id,
            [store_key, online_key],
        )
        if existing:
            raise TransferDuplicateError(
                "Duplicate transfer request (idempotency_key already applied)."
            )

        available_store = inv["stock_in_store"] - inv["reserved_in_store"]
        if quantity > available_store:
            raise TransferInsufficientStockError(
                f"Insufficient unreserved store inventory for '{product_id}'. "
                f"Requested: {quantity}, Available in store: {available_store}."
            )

        new_store = inv["stock_in_store"] - quantity
        new_online = inv["stock_online"] + quantity

        # Update both channel balances in a single statement (reservations untouched).
        updated = await conn.execute(
            """
            UPDATE inventory_items
            SET stock_in_store = $3, stock_online = $4
            WHERE merchant_id = $1 AND product_id = $2
            """,
            merchant_id,
            product_id,
            new_store,
            new_online,
        )
        if updated == "UPDATE 0":
            raise TransferNotFoundError(
                f"Product '{product_id}' does not exist for this merchant."
            )

        reference_id = f"xfer_{uuid.uuid4().hex[:12]}"
        movement_ids: List[str] = []

        # One movement row per affected channel, using existing transfer convention.
        for channel, delta, balance, key in (
            ("in_store", -quantity, new_store, store_key),
            ("online", quantity, new_online, online_key),
        ):
            movement_id = f"mv_{uuid.uuid4().hex[:12]}"
            await conn.execute(
                """
                INSERT INTO inventory_movements (
                    id, merchant_id, product_id, channel, movement_type,
                    quantity_delta, reserved_delta, balance_after,
                    reference_type, reference_id, idempotency_key
                ) VALUES ($1, $2, $3, $4, 'transfer', $5, 0, $6, 'transfer', $7, $8)
                """,
                movement_id,
                merchant_id,
                product_id,
                channel,
                delta,
                balance,
                reference_id,
                key,
            )
            movement_ids.append(movement_id)

        # Operational audit event (server-derived actor only).
        await conn.execute(
            """
            INSERT INTO operational_audit_log (
                merchant_id, actor_type, actor_id, action,
                entity_type, entity_id, details
            ) VALUES ($1, $2, $3, 'inventory_transfer', 'inventory_item', $4, $5::jsonb)
            """,
            merchant_id,
            actor_type,
            actor_id or f"owner_{merchant_id}",
            product_id,
            json.dumps({
                "from_channel": "in_store",
                "to_channel": "online",
                "quantity": quantity,
                "reference_id": reference_id,
                "idempotency_key": idempotency_key,
                "reason": reason,
            }),
        )

        return {
            "merchant_id": merchant_id,
            "product_id": product_id,
            "quantity": quantity,
            "from_channel": "in_store",
            "to_channel": "online",
            "stock_in_store": new_store,
            "stock_online": new_online,
            "available_in_store": new_store - inv["reserved_in_store"],
            "available_online": new_online - inv["reserved_online"],
            "movement_ids": movement_ids,
            "reference_id": reference_id,
        }
