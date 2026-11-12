"""
backend/app/db/repositories/payment_repo.py
===========================================
Transactional repository for the M12.0 PayPal Sandbox online checkout.

Covers:
- Internal order creation (ONE pending order + order items BEFORE PayPal checkout).
- Online stock reservation (reserved_online) with FOR UPDATE locking.
- Lazy release of expired/abandoned reservations.
- A SINGLE canonical settlement operation (`settle_capture`) used by BOTH the
  capture endpoint and the verified PAYMENT.CAPTURE.COMPLETED webhook.

Settlement guarantees (enforced by code + database constraints):
- One internal order (created once at checkout time; never a second order).
- One PayPal capture record (paypal_captures.uq_paypal_capture_id).
- One inventory decrement per item (serially locked inventory row).
- One inventory movement per item (inventory_movements.uq_inventory_movement_idempotency).
- One revenue ledger entry (financial_ledger.uq_ledger_idempotency).
- Safe repeated requests and duplicate webhook deliveries (idempotent re-entry).
"""

import json
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

import asyncpg

from app.schemas.payments import OnlineCheckoutItemRequest, OnlineCheckoutRequest

_TAX_RATE = Decimal("0.08")
_DEFAULT_ABANDON_TTL_MINUTES = 120


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Reservation lifecycle
# ---------------------------------------------------------------------------
async def _cancel_pending_order(conn: asyncpg.Connection, merchant_id: str, order_id: str) -> bool:
    """Cancel a pending online order and release its stock reservations."""
    order = await conn.fetchrow(
        "SELECT * FROM orders WHERE merchant_id = $1 AND id = $2 FOR UPDATE",
        merchant_id,
        order_id,
    )
    if not order or order["status"] != "pending":
        return False

    items = await conn.fetch(
        "SELECT product_id, quantity FROM order_items WHERE merchant_id = $1 AND order_id = $2",
        merchant_id,
        order_id,
    )
    for item in items:
        inv = await conn.fetchrow(
            "SELECT stock_online, reserved_online FROM inventory_items "
            "WHERE merchant_id = $1 AND product_id = $2 FOR UPDATE",
            merchant_id,
            item["product_id"],
        )
        if not inv or inv["reserved_online"] <= 0:
            continue
        new_reserved = max(inv["reserved_online"] - item["quantity"], 0)
        await conn.execute(
            "UPDATE inventory_items SET reserved_online = $3 "
            "WHERE merchant_id = $1 AND product_id = $2",
            merchant_id,
            item["product_id"],
            new_reserved,
        )
        movement_id = _new_id("mov_rel")
        await conn.execute(
            """
            INSERT INTO inventory_movements
                (id, merchant_id, product_id, channel, movement_type,
                 quantity_delta, reserved_delta, balance_after,
                 reference_type, reference_id, idempotency_key)
            VALUES ($1, $2, $3, 'online', 'adjustment',
                    0, $4, $5, 'order', $6, $7)
            """,
            movement_id,
            merchant_id,
            item["product_id"],
            -item["quantity"],
            inv["stock_online"],
            order_id,
            f"release_{order_id}_{item['product_id']}",
        )

    await conn.execute(
        "UPDATE orders SET status = 'cancelled', updated_at = now() "
        "WHERE merchant_id = $1 AND id = $2",
        merchant_id,
        order_id,
    )
    return True


async def release_expired_reservations(
    conn: asyncpg.Connection,
    merchant_id: str,
    ttl_minutes: int = _DEFAULT_ABANDON_TTL_MINUTES,
) -> int:
    """
    Release reservations held by pending online orders older than the TTL.

    Called lazily at the start of a new checkout so abandoned carts never leave
    permanent reservations. Returns the number of orders cancelled.
    """
    candidates = await conn.fetch(
        """
        SELECT id FROM orders
        WHERE merchant_id = $1
          AND channel = 'online'
          AND status = 'pending'
          AND created_at < now() - make_interval(mins => $2)
        ORDER BY created_at ASC
        LIMIT 50
        """,
        merchant_id,
        ttl_minutes,
    )
    released = 0
    for row in candidates:
        if await _cancel_pending_order(conn, merchant_id, row["id"]):
            released += 1
    return released


# ---------------------------------------------------------------------------
# Internal order creation
# ---------------------------------------------------------------------------
async def create_online_order(
    conn: asyncpg.Connection,
    merchant_id: str,
    request: OnlineCheckoutRequest,
) -> Dict[str, Any]:
    """
    Create ONE internal pending order + order items and reserve online stock.

    Must run inside the caller's transaction. Raises ValueError on validation
    failures (unknown product / insufficient available online stock).
    """
    await release_expired_reservations(conn, merchant_id)

    # Aggregate duplicate product lines, sorted for deterministic lock ordering.
    requested_qty: Dict[str, int] = {}
    for item in request.items:
        requested_qty[item.product_id] = requested_qty.get(item.product_id, 0) + item.quantity
    product_ids = sorted(requested_qty.keys())

    rows = await conn.fetch(
        """
        SELECT
            ii.product_id,
            ii.stock_online,
            ii.reserved_online,
            ii.available_online,
            p.title  AS product_name,
            p.sku   AS sku,
            p.selling_price
        FROM inventory_items ii
        JOIN products p
          ON p.merchant_id = ii.merchant_id AND p.id = ii.product_id
        WHERE ii.merchant_id = $1 AND ii.product_id = ANY($2::varchar[])
        FOR UPDATE OF ii
        """,
        merchant_id,
        product_ids,
    )
    found = {r["product_id"]: dict(r) for r in rows}

    for pid in product_ids:
        if pid not in found:
            raise ValueError(f"Product '{pid}' does not exist for merchant.")

    for pid, qty in requested_qty.items():
        inv = found[pid]
        if inv["available_online"] < qty:
            raise ValueError(
                f"Insufficient online inventory for '{inv['product_name']}' "
                f"(SKU: {inv['sku']}). Requested: {qty}, Available: {inv['available_online']}."
            )

    subtotal = Decimal("0.00")
    line_items: List[Dict[str, Any]] = []
    for item in request.items:
        inv = found[item.product_id]
        unit_price = Decimal(str(inv["selling_price"]))
        line_total = unit_price * item.quantity
        subtotal += line_total
        line_items.append(
            {
                "product_id": item.product_id,
                "product_name": inv["product_name"],
                "sku": inv["sku"],
                "quantity": item.quantity,
                "unit_price": unit_price,
                "line_total": line_total,
            }
        )

    tax_amount = (subtotal * _TAX_RATE).quantize(Decimal("0.01"))
    total_amount = subtotal + tax_amount

    order_id = _new_id("order_online")
    order_number = f"ONLINE-{uuid.uuid4().hex[:6].upper()}"

    await conn.execute(
        """
        INSERT INTO orders (
            merchant_id, id, order_number, channel, status,
            subtotal, tax_amount, shipping_amount, total_amount, payment_method
        ) VALUES ($1, $2, $3, 'online', 'pending', $4, $5, 0.00, $6, 'paypal')
        """,
        merchant_id,
        order_id,
        order_number,
        subtotal,
        tax_amount,
        total_amount,
    )

    for oi in line_items:
        item_id = _new_id("item")
        await conn.execute(
            """
            INSERT INTO order_items (
                merchant_id, id, order_id, product_id, quantity, unit_price, line_total
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

    # Reserve online stock (available_online is generated, so this reduces it).
    for pid, qty in requested_qty.items():
        inv = found[pid]
        new_reserved = inv["reserved_online"] + qty
        await conn.execute(
            "UPDATE inventory_items SET reserved_online = $3 "
            "WHERE merchant_id = $1 AND product_id = $2",
            merchant_id,
            pid,
            new_reserved,
        )

    return {
        "merchant_id": merchant_id,
        "order_id": order_id,
        "order_number": order_number,
        "subtotal": subtotal,
        "tax_amount": tax_amount,
        "total_amount": total_amount,
        "status": "pending",
        "items": line_items,
    }


async def attach_paypal_order(
    conn: asyncpg.Connection,
    merchant_id: str,
    order_id: str,
    paypal_order_id: str,
) -> None:
    """Associate a PayPal order id with the internal pending order."""
    await conn.execute(
        "UPDATE orders SET paypal_order_id = $3 "
        "WHERE merchant_id = $1 AND id = $2 AND status = 'pending'",
        merchant_id,
        order_id,
        paypal_order_id,
    )


async def cancel_online_order_and_release(
    conn: asyncpg.Connection,
    merchant_id: str,
    order_id: str,
) -> bool:
    """
    Cancel a pending online order and release its stock reservations.

    MUST be called from the caller's transaction. Returns True if an order was
    actually cancelled (i.e. it was still pending), False if there was nothing
    to cancel (already terminal).
    """
    return await _cancel_pending_order(conn, merchant_id, order_id)


async def get_online_order_by_id(
    conn: asyncpg.Connection,
    merchant_id: str,
    order_id: str,
) -> Optional[Dict[str, Any]]:
    row = await conn.fetchrow(
        "SELECT * FROM orders WHERE merchant_id = $1 AND id = $2",
        merchant_id,
        order_id,
    )
    return dict(row) if row else None


async def get_online_order_by_paypal_order(
    conn: asyncpg.Connection,
    merchant_id: str,
    paypal_order_id: str,
) -> Optional[Dict[str, Any]]:
    row = await conn.fetchrow(
        "SELECT * FROM orders WHERE merchant_id = $1 AND paypal_order_id = $2",
        merchant_id,
        paypal_order_id,
    )
    return dict(row) if row else None


async def get_capture_by_paypal_id(
    conn: asyncpg.Connection,
    merchant_id: str,
    paypal_capture_id: str,
) -> Optional[Dict[str, Any]]:
    row = await conn.fetchrow(
        "SELECT * FROM paypal_captures WHERE merchant_id = $1 AND paypal_capture_id = $2",
        merchant_id,
        paypal_capture_id,
    )
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Canonical settlement
# ---------------------------------------------------------------------------
async def settle_capture(
    conn: asyncpg.Connection,
    merchant_id: str,
    order_id: str,
    capture_details: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Canonical settlement for a PayPal capture. Used by the capture endpoint AND
    the verified webhook. Idempotent: re-entering with the same capture id is a
    safe no-op (returns the existing settlement).

    ``capture_details`` keys:
        paypal_capture_id (str), amount (Decimal), currency (str),
        status (str: 'completed' | 'pending'), raw_payload (Any)

    Must run inside the caller's transaction.
    """
    capture_id = capture_details["paypal_capture_id"]
    amount = Decimal(str(capture_details["amount"]))
    currency = str(capture_details.get("currency") or "USD").upper()
    capture_status = capture_details.get("status", "completed")

    # 1. Lock the internal order first (serializes settlement + cancellations).
    order = await conn.fetchrow(
        "SELECT * FROM orders WHERE merchant_id = $1 AND id = $2 FOR UPDATE",
        merchant_id,
        order_id,
    )
    if not order:
        raise ValueError("Internal order not found.")
    if order["channel"] != "online":
        raise ValueError("Capture only applies to online orders.")

    # 2. Idempotency: already-settled capture -> return existing result.
    existing = await get_capture_by_paypal_id(conn, merchant_id, capture_id)
    if existing and existing["status"] == "completed":
        return _build_settlement_result(order, existing, settled=True, already_settled=True)

    # 2b. If the internal order is already completed by a DIFFERENT PayPal capture
    # (i.e. this capture id is not the one on record), refuse to double-settle.
    # Paypal_captures.uq_paypal_capture_id already forbids a duplicate row for the
    # same PayPal capture; this guard additionally blocks a second, distinct capture
    # from settling the same internal order a second time.
    if order["status"] == "completed":
        raise ValueError("Order is already completed by another capture; refusing duplicate settlement.")

    # 3. Existing pending capture upgraded to completed (skip to inventory settle).
    upgrading_pending = existing is not None and existing["status"] == "pending"

    # 4. Amount/currency validation against the internal order total.
    expected_total = Decimal(str(order["total_amount"]))
    if currency != "USD":
        raise ValueError(f"Currency mismatch: expected USD, got {currency}.")
    if capture_status == "completed" and amount != expected_total:
        raise ValueError(f"Capture amount mismatch: expected {expected_total}, got {amount}.")

    raw_payload = capture_details.get("raw_payload")

    if capture_status == "pending":
        # Persist a pending capture; do not touch inventory/revenue yet.
        if not existing:
            cap_id_db = _new_id("capture")
            await conn.execute(
                """
                INSERT INTO paypal_captures (
                    merchant_id, id, order_id, paypal_capture_id, status,
                    captured_amount, currency, raw_payload
                ) VALUES ($1, $2, $3, $4, 'pending', $5, $6, $7::jsonb)
                """,
                merchant_id,
                cap_id_db,
                order_id,
                capture_id,
                amount,
                currency,
                json.dumps(raw_payload) if raw_payload is not None else "{}",
            )
            cap_id_db_final = cap_id_db
        else:
            cap_id_db_final = existing["id"]
        return _build_settlement_result(
            order,
            {
                "id": cap_id_db_final,
                "status": "pending",
                "captured_amount": amount,
                "currency": currency,
                "paypal_capture_id": capture_id,
            },
            settled=False,
            already_settled=False,
        )

    # ---- COMPLETED capture: settle inventory + revenue ----
    if not existing:
        cap_id_db = _new_id("capture")
        await conn.execute(
            """
            INSERT INTO paypal_captures (
                merchant_id, id, order_id, paypal_capture_id, status,
                captured_amount, currency, raw_payload
            ) VALUES ($1, $2, $3, $4, 'completed', $5, $6, $7::jsonb)
            """,
            merchant_id,
            cap_id_db,
            order_id,
            capture_id,
            amount,
            currency,
            json.dumps(raw_payload) if raw_payload is not None else "{}",
        )
    else:
        cap_id_db = existing["id"]
        await conn.execute(
            """
            UPDATE paypal_captures
            SET status = 'completed', captured_amount = $3, raw_payload = $4,
                updated_at = now()
            WHERE merchant_id = $1 AND id = $2
            """,
            merchant_id,
            cap_id_db,
            amount,
            json.dumps(raw_payload) if raw_payload is not None else "{}",
        )

    # 5. Settle inventory per item (one decrement + one movement per item).
    items = await conn.fetch(
        "SELECT product_id, quantity FROM order_items WHERE merchant_id = $1 AND order_id = $2",
        merchant_id,
        order_id,
    )
    for item in items:
        pid = item["product_id"]
        qty = item["quantity"]
        inv = await conn.fetchrow(
            "SELECT stock_online, reserved_online FROM inventory_items "
            "WHERE merchant_id = $1 AND product_id = $2 FOR UPDATE",
            merchant_id,
            pid,
        )
        if not inv:
            raise ValueError(f"Inventory row missing for product '{pid}'.")
        new_stock = inv["stock_online"] - qty
        new_reserved = inv["reserved_online"] - qty
        if new_stock < 0:
            raise ValueError(f"Insufficient online stock for product '{pid}'.")
        await conn.execute(
            "UPDATE inventory_items SET stock_online = $3, reserved_online = $4 "
            "WHERE merchant_id = $1 AND product_id = $2",
            merchant_id,
            pid,
            new_stock,
            new_reserved,
        )
        movement_id = _new_id("mov_online")
        await conn.execute(
            """
            INSERT INTO inventory_movements (
                id, merchant_id, product_id, channel, movement_type,
                quantity_delta, reserved_delta, balance_after,
                reference_type, reference_id, idempotency_key
            ) VALUES ($1, $2, $3, 'online', 'online_order',
                     $4, $5, $6, 'order', $7, $8)
            """,
            movement_id,
            merchant_id,
            pid,
            -qty,
            -qty,
            new_stock,
            order_id,
            f"{order_id}_{pid}",
        )

    # 6. One revenue ledger entry.
    last = await conn.fetchrow(
        "SELECT running_envelope_balance FROM financial_ledger "
        "WHERE merchant_id = $1 ORDER BY created_at DESC, id DESC LIMIT 1",
        merchant_id,
    )
    base = Decimal(str(last["running_envelope_balance"])) if last else Decimal("0.00")
    new_running = base + amount
    ledger_row = await conn.fetchrow(
        """
        INSERT INTO financial_ledger (
            merchant_id, entry_type, amount, running_envelope_balance,
            reference_type, reference_id, idempotency_key, description
        ) VALUES ($1, 'revenue', $2, $3, 'order', $4, $5, $6)
        RETURNING id
        """,
        merchant_id,
        amount,
        new_running,
        order_id,
        f"revenue_{order_id}",
        f"PayPal capture {capture_id} settled for order {order['order_number']}",
    )

    # 7. Transition the EXISTING internal order to completed.
    await conn.execute(
        "UPDATE orders SET status = 'completed', payment_method = 'paypal', updated_at = now() "
        "WHERE merchant_id = $1 AND id = $2",
        merchant_id,
        order_id,
    )

    final_capture = {
        "id": cap_id_db,
        "status": "completed",
        "paypal_capture_id": capture_id,
        "captured_amount": amount,
        "currency": currency,
    }
    result = _build_settlement_result(order, final_capture, settled=True, already_settled=False)
    result["order_status"] = "completed"
    result["revenue_ledger_id"] = ledger_row["id"]
    result["upgraded_pending"] = upgrading_pending
    return result


def _build_settlement_result(order: Any, capture: Dict[str, Any], *, settled: bool, already_settled: bool) -> Dict[str, Any]:
    return {
        "merchant_id": order["merchant_id"],
        "order_id": order["id"],
        "order_number": order["order_number"],
        "order_status": order["status"],
        "capture_status": capture["status"],
        "paypal_capture_id": capture["paypal_capture_id"],
        "captured_amount": Decimal(str(capture["captured_amount"])),
        "currency": capture["currency"],
        "settled": settled,
        "already_settled": already_settled,
        "revenue_ledger_id": None,
    }
