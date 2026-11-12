"""
backend/app/db/repositories/order_repo.py
=========================================
Transactional repository for POS sales, order creation, and atomic inventory decrement.
"""

import uuid
from decimal import Decimal
from typing import List, Tuple, Dict, Any, Optional
import asyncpg
from app.schemas.orders import POSSaleRequest


async def execute_pos_sale(
    conn: asyncpg.Connection,
    merchant_id: str,
    sale_req: POSSaleRequest,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Executes an atomic in-store POS transaction:
    1. Checks idempotency: if an order with this idempotency key already exists for this merchant, returns existing order.
    2. Locks product and inventory_item rows with SELECT ... FOR UPDATE.
    3. Validates available stock for each requested item.
    4. Calculates line totals, subtotal, and tax (8%).
    5. Inserts `orders` and `order_items`.
    6. Decrements `inventory_items.stock_in_store`.
    7. Records `inventory_movements` with `pos_sale` movement type.
    8. Records an entry in `operational_audit_log`.

    All within the active asyncpg connection transaction.
    """
    # 1. Idempotency Check via inventory_movements idempotency_key
    # When movements are created for a POS sale, each movement's idempotency_key is either:
    #   - f"{sale_req.idempotency_key}_{pid}" (item-level movement key)
    #   - sale_req.idempotency_key (exact key)
    # We query both exact key match and candidate item-level keys directly (avoiding pattern-matching issues),
    # as well as matching LIKE prefix.
    candidate_keys = [sale_req.idempotency_key] + [
        f"{sale_req.idempotency_key}_{item.product_id}" for item in sale_req.items
    ]
    existing_mov = await conn.fetchrow(
        """
        SELECT reference_id FROM inventory_movements
        WHERE merchant_id = $1
          AND (
              idempotency_key = ANY($2::varchar[])
              OR idempotency_key LIKE $3
          )
          AND reference_type = 'order'
        LIMIT 1
        """,
        merchant_id,
        candidate_keys,
        f"{sale_req.idempotency_key}_%",
    )
    if existing_mov and existing_mov["reference_id"]:
        existing_order = await get_order_by_id(conn, merchant_id, existing_mov["reference_id"])
        if existing_order:
            # Fetch current inventory states for these products
            p_ids = [item.product_id for item in sale_req.items]
            inv_rows = await conn.fetch(
                """
                SELECT product_id, stock_in_store, available_in_store,
                       (available_in_store + available_online) AS total_available
                FROM inventory_items WHERE merchant_id = $1 AND product_id = ANY($2::varchar[])
                """,
                merchant_id,
                p_ids,
            )
            return existing_order, [dict(r) for r in inv_rows]

    # Group item quantities in case the client requested the same product multiple times in one cart
    requested_qty: Dict[str, int] = {}
    for item in sale_req.items:
        requested_qty[item.product_id] = requested_qty.get(item.product_id, 0) + item.quantity

    product_ids = sorted(list(requested_qty.keys()))

    # 2. Lock Inventory Rows
    # Sorting ensures consistent lock acquisition order to prevent deadlocks
    inventory_rows = await conn.fetch(
        """
        SELECT
            ii.product_id,
            ii.stock_in_store,
            ii.reserved_in_store,
            ii.available_in_store,
            p.title as product_name,
            p.sku,
            p.selling_price
        FROM inventory_items ii
        JOIN products p ON p.merchant_id = ii.merchant_id AND p.id = ii.product_id
        WHERE ii.merchant_id = $1 AND ii.product_id = ANY($2::varchar[])
        FOR UPDATE OF ii
        """,
        merchant_id,
        product_ids,
    )

    found_products = {r["product_id"]: dict(r) for r in inventory_rows}

    # Validate that every requested product exists
    for pid in product_ids:
        if pid not in found_products:
            raise ValueError(f"Product '{pid}' does not exist for merchant.")

    # 3. Validate Stock Availability
    for pid, qty in requested_qty.items():
        inv = found_products[pid]
        avail = inv["available_in_store"]
        if avail < qty:
            raise ValueError(
                f"Insufficient in-store inventory for '{inv['product_name']}' (SKU: {inv['sku']}). "
                f"Requested: {qty}, Available: {avail}."
            )

    # 4. Compute Totals
    subtotal = Decimal("0.00")
    order_items_to_create = []

    for item in sale_req.items:
        inv = found_products[item.product_id]
        unit_price = Decimal(str(inv["selling_price"]))
        line_total = unit_price * item.quantity
        subtotal += line_total
        order_items_to_create.append({
            "product_id": item.product_id,
            "product_name": inv["product_name"],
            "sku": inv["sku"],
            "quantity": item.quantity,
            "unit_price": unit_price,
            "line_total": line_total,
        })

    tax_rate = Decimal("0.08")  # 8% standard in-store tax
    tax_amount = (subtotal * tax_rate).quantize(Decimal("0.01"))
    total_amount = subtotal + tax_amount

    order_id = f"order_pos_{uuid.uuid4().hex[:12]}"
    order_number = f"POS-{uuid.uuid4().hex[:6].upper()}"

    # 5. Insert Order
    order_row = await conn.fetchrow(
        """
        INSERT INTO orders (
            merchant_id,
            id,
            order_number,
            channel,
            status,
            subtotal,
            tax_amount,
            shipping_amount,
            total_amount,
            payment_method
        ) VALUES ($1, $2, $3, 'pos', 'completed', $4, $5, 0.00, $6, $7)
        RETURNING *
        """,
        merchant_id,
        order_id,
        order_number,
        subtotal,
        tax_amount,
        total_amount,
        sale_req.payment_method,
    )

    # Insert Order Items
    for oi in order_items_to_create:
        item_id = f"item_{uuid.uuid4().hex[:12]}"
        await conn.execute(
            """
            INSERT INTO order_items (
                merchant_id,
                id,
                order_id,
                product_id,
                quantity,
                unit_price,
                line_total
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            merchant_id,
            item_id,
            order_id,
            oi["product_id"],
            oi["quantity"],
            oi["unit_price"],
            oi["line_total"],
        )
        oi["id"] = item_id

    # 6. Decrement Inventory & 7. Record Movements
    updated_inventories = []
    for pid, qty in requested_qty.items():
        inv = found_products[pid]
        new_stock = inv["stock_in_store"] - qty

        # Update stock_in_store
        updated_row = await conn.fetchrow(
            """
            UPDATE inventory_items
            SET stock_in_store = $3
            WHERE merchant_id = $1 AND product_id = $2
            RETURNING product_id, stock_in_store, stock_online, available_in_store, available_online,
                      (available_in_store + available_online) AS total_available
            """,
            merchant_id,
            pid,
            new_stock,
        )
        updated_inventories.append(dict(updated_row))

        # Record inventory movement
        movement_id = f"mov_pos_{uuid.uuid4().hex[:12]}"
        # Unique idempotency key per item movement derived from sale idempotency key + product
        mov_idemp = f"{sale_req.idempotency_key}_{pid}"
        await conn.execute(
            """
            INSERT INTO inventory_movements (
                id,
                merchant_id,
                product_id,
                channel,
                movement_type,
                quantity_delta,
                reserved_delta,
                balance_after,
                reference_type,
                reference_id,
                idempotency_key
            ) VALUES ($1, $2, $3, 'in_store', 'pos_sale', $4, 0, $5, 'order', $6, $7)
            """,
            movement_id,
            merchant_id,
            pid,
            -qty,
            new_stock,
            order_id,
            mov_idemp,
        )

    # 8. Audit Log Entry
    await conn.execute(
        """
        INSERT INTO operational_audit_log (
            merchant_id,
            actor_type,
            actor_id,
            action,
            entity_type,
            entity_id,
            details
        ) VALUES ($1, 'store_employee', 'pos_terminal_01', 'pos_sale_completed', 'order', $2, $3::jsonb)
        """,
        merchant_id,
        order_id,
        f'{{"order_number": "{order_number}", "total_amount": "{total_amount}", "items_count": {len(order_items_to_create)}}}',
    )

    result_order = dict(order_row)
    result_order["items"] = order_items_to_create
    return result_order, updated_inventories


async def get_order_by_id(
    conn: asyncpg.Connection,
    merchant_id: str,
    order_id: str,
) -> Optional[Dict[str, Any]]:
    """Retrieve full order with line items."""
    order_row = await conn.fetchrow(
        "SELECT * FROM orders WHERE merchant_id = $1 AND id = $2",
        merchant_id,
        order_id,
    )
    if not order_row:
        return None

    items = await conn.fetch(
        """
        SELECT
            oi.*,
            p.title as product_name,
            p.sku
        FROM order_items oi
        JOIN products p ON p.merchant_id = oi.merchant_id AND p.id = oi.product_id
        WHERE oi.merchant_id = $1 AND oi.order_id = $2
        """,
        merchant_id,
        order_id,
    )
    order_dict = dict(order_row)
    order_dict["items"] = [dict(i) for i in items]
    return order_dict


async def list_orders(
    conn: asyncpg.Connection,
    merchant_id: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """List recent orders for merchant with item details."""
    orders = await conn.fetch(
        """
        SELECT * FROM orders
        WHERE merchant_id = $1
        ORDER BY created_at DESC
        LIMIT $2
        """,
        merchant_id,
        limit,
    )
    if not orders:
        return []

    order_ids = [o["id"] for o in orders]
    items = await conn.fetch(
        """
        SELECT
            oi.*,
            p.title as product_name,
            p.sku
        FROM order_items oi
        JOIN products p ON p.merchant_id = oi.merchant_id AND p.id = oi.product_id
        WHERE oi.merchant_id = $1 AND oi.order_id = ANY($2::varchar[])
        """,
        merchant_id,
        order_ids,
    )

    items_by_order: Dict[str, list] = {}
    for i in items:
        items_by_order.setdefault(i["order_id"], []).append(dict(i))

    result = []
    for o in orders:
        od = dict(o)
        od["items"] = items_by_order.get(o["id"], [])
        result.append(od)

    return result
