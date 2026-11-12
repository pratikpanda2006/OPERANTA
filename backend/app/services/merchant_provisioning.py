"""
backend/app/services/merchant_provisioning.py
===============================================
Idempotent starter workspace provisioning for NEW normal SaaS merchants.

A freshly registered SaaS merchant gets a tiny, self-contained starter catalog
so the cockpit is usable immediately. Every row is scoped strictly to the new
merchant's own ``merchant_id`` — no Northstar historical data (orders, payments,
revenue, approvals, credentials) is ever copied or referenced.

This runs inside the same registration transaction as the merchant + owner
creation, so it rolls back atomically if anything fails.
"""

import secrets
import uuid
from typing import Any

# ---------------------------------------------------------------------------
# Starter catalog (generic, not derived from Northstar historical records)
# ---------------------------------------------------------------------------
STARTER_PRODUCTS = [
    {
        "id": "p-starter-1",
        "sku": "SKU-STARTER-0001",
        "title": "Starter Coffee Beans (1kg)",
        "category": "Pantry",
        "base_cost": "12.50",
        "selling_price": "19.99",
    },
    {
        "id": "p-starter-2",
        "sku": "SKU-STARTER-0002",
        "title": "Eco Tote Bag",
        "category": "Merchandise",
        "base_cost": "3.20",
        "selling_price": "8.99",
    },
]

STARTER_SUPPLIER = {
    "id": "s-starter-1",
    "name": "Starter Supply Co.",
    "contact_email": "supply@example.com",
    "trust_tier": "allowlisted",
    "reliability_score": 90,
}

STARTER_OFFERS = [
    # (supplier_id, product_id, unit_cost, shipping_cost, lead_days, moq)
    ("s-starter-1", "p-starter-1", "10.00", "4.50", 3, 5),
    ("s-starter-1", "p-starter-2", "2.75", "1.50", 2, 10),
]

STARTER_INVENTORY = [
    # (product_id, stock_in_store, stock_online, reorder_point, daily_demand, lead_days, safety_stock)
    ("p-starter-1", 25, 15, 8, 3, 3, 6),
    ("p-starter-2", 40, 30, 10, 2, 2, 4),
]


def new_merchant_id() -> str:
    """Generate a unique, server-side merchant id (never accepted from client input)."""
    return f"merchant_{uuid.uuid4().hex[:12]}"


async def provision_starter_workspace(conn: Any, merchant_id: str) -> None:
    """Insert starter products, a supplier, offers and inventory for a new merchant."""
    for p in STARTER_PRODUCTS:
        await conn.execute(
            """
            INSERT INTO products (merchant_id, id, sku, title, category, base_cost, selling_price)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            merchant_id,
            p["id"],
            p["sku"],
            p["title"],
            p["category"],
            p["base_cost"],
            p["selling_price"],
        )

    s = STARTER_SUPPLIER
    await conn.execute(
        """
        INSERT INTO suppliers (merchant_id, id, name, contact_email, trust_tier, reliability_score)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        merchant_id,
        s["id"],
        s["name"],
        s["contact_email"],
        s["trust_tier"],
        s["reliability_score"],
    )

    for supplier_id, product_id, unit_cost, shipping_cost, lead_days, moq in STARTER_OFFERS:
        await conn.execute(
            """
            INSERT INTO supplier_offers
                (merchant_id, id, supplier_id, product_id, unit_cost, shipping_cost, lead_time_days, moq)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            merchant_id,
            f"offer-{secrets.token_hex(4)}",
            supplier_id,
            product_id,
            unit_cost,
            shipping_cost,
            lead_days,
            moq,
        )

    for product_id, stock_store, stock_online, reorder, demand, lead, safety in STARTER_INVENTORY:
        await conn.execute(
            """
            INSERT INTO inventory_items
                (merchant_id, id, product_id, stock_in_store, stock_online,
                 reorder_point, daily_demand, lead_days, safety_stock)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            merchant_id,
            f"inv-{secrets.token_hex(4)}",
            product_id,
            stock_store,
            stock_online,
            reorder,
            demand,
            lead,
            safety,
        )
