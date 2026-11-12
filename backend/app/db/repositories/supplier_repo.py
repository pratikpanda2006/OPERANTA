"""
backend/app/db/repositories/supplier_repo.py
============================================
Data access layer for qualified suppliers, catalog offers, and trade-off comparison metrics.
Strictly scoped to the validated tenant identifier with zero N+1 query patterns.

SCHEMA ALIGNMENT NOTES (migration 001):
  suppliers columns:
    - trust_tier        (VARCHAR 32: 'allowlisted' | 'requires_approval')
    - reliability_score (INT 0–100)
    - NO is_allowlisted column (derived from trust_tier = 'allowlisted')
    - NO lead_time_days column on suppliers (lead_time_days is on supplier_offers)
    - NO payment_terms column
  supplier_offers columns:
    - lead_time_days  (INT — this is the offer-level lead time)
    - NO is_preferred column (not in migration 001)
"""

from decimal import Decimal
from typing import List
import asyncpg


async def list_suppliers_with_offers(
    conn: asyncpg.Connection,
    merchant_id: str,
) -> List[dict]:
    """
    List all suppliers and their associated product offers for the specified merchant.
    Executes two batched tenant-scoped queries to prevent N+1 performance bottlenecks.

    Derivations:
      is_allowlisted  = (trust_tier = 'allowlisted')
      lead_time_days on supplier → minimum across all offers for that supplier (or None)
      payment_terms   → not in schema; excluded from response

    Returns:
        List of dicts conforming to SupplierResponse.
    """
    supplier_rows = await conn.fetch(
        """
        SELECT
            id,
            merchant_id,
            name,
            contact_email,
            trust_tier,
            reliability_score,
            is_illustrative
        FROM suppliers
        WHERE merchant_id = $1
        ORDER BY name ASC
        """,
        merchant_id,
    )

    offer_rows = await conn.fetch(
        """
        SELECT
            id              AS offer_id,
            merchant_id,
            supplier_id,
            product_id,
            unit_cost,
            shipping_cost,
            lead_time_days,
            moq,
            is_illustrative
        FROM supplier_offers
        WHERE merchant_id = $1
        ORDER BY unit_cost ASC
        """,
        merchant_id,
    )

    # Group offers by supplier_id
    offers_by_supplier: dict[str, list[dict]] = {}
    for o in offer_rows:
        sup_id = o["supplier_id"]
        if sup_id not in offers_by_supplier:
            offers_by_supplier[sup_id] = []
        offers_by_supplier[sup_id].append({
            "offer_id": o["offer_id"],
            "product_id": o["product_id"],
            "unit_cost": Decimal(str(o["unit_cost"])),
            "shipping_cost": Decimal(str(o["shipping_cost"])),
            "lead_time_days": o["lead_time_days"],
            "moq": o["moq"],
            "is_illustrative": o["is_illustrative"],
        })

    suppliers = []
    for s in supplier_rows:
        sup_id = s["id"]
        # Derive is_allowlisted from trust_tier (migration 001 uses trust_tier, not is_allowlisted)
        is_allowlisted = s["trust_tier"] == "allowlisted"

        # Derive supplier-level lead_time_days as minimum across offers (None if no offers)
        sup_offers = offers_by_supplier.get(sup_id, [])
        if sup_offers:
            lead_time_days = min(o["lead_time_days"] for o in sup_offers)
        else:
            lead_time_days = None

        suppliers.append({
            "id": sup_id,
            "merchant_id": s["merchant_id"],
            "name": s["name"],
            "contact_email": s["contact_email"],
            "trust_tier": s["trust_tier"],
            "is_allowlisted": is_allowlisted,
            "reliability_score": Decimal(str(s["reliability_score"])),
            "lead_time_days": lead_time_days,
            "is_illustrative": s["is_illustrative"],
            "offers": sup_offers,
        })

    return suppliers
