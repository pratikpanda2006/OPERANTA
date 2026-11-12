"""
backend/app/db/repositories/procurement_repo.py
===============================================
Deterministic procurement planning, multi-factor supplier evaluation,
purchase order lifecycle, spending policy validation, and owner decision handling.
Enforces strict PostgreSQL transaction-level locking (merchant-first, then purchase order)
to eliminate concurrent approval and anti-splitting races.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Dict, Any, Optional
import asyncpg
from app.schemas.procurement import (
    PurchaseOrderCreateRequest,
    PolicyValidationResult,
    OwnerDecisionRequest,
)
from app.db.repositories.merchant_repo import get_spend_envelope


async def generate_reorder_plan(
    conn: asyncpg.Connection,
    merchant_id: str,
) -> List[Dict[str, Any]]:
    """
    Evaluates inventory status and reorder thresholds across all merchant products.
    For SKUs at or below their reorder point, compares available supplier offers
    and formulates a replenishment recommendation.
    """
    items = await conn.fetch(
        """
        SELECT
            ii.product_id,
            p.title as product_name,
            p.sku,
            ii.available_in_store + ii.available_online as available_stock,
            ii.reorder_point,
            ii.daily_demand,
            ii.lead_days,
            ii.safety_stock
        FROM inventory_items ii
        JOIN products p ON p.merchant_id = ii.merchant_id AND p.id = ii.product_id
        WHERE ii.merchant_id = $1
        ORDER BY p.title ASC
        """,
        merchant_id,
    )

    reorder_items = []
    for item in items:
        avail = item["available_stock"]
        rop = item["reorder_point"]

        # Candidate for reorder if available stock <= reorder point
        if avail <= rop:
            deficit = max(0, rop - avail)
            base_needed = deficit + item["safety_stock"]

            # Fetch canonical supplier offers for this product
            offers = await conn.fetch(
                """
                SELECT
                    so.id as offer_id,
                    so.supplier_id,
                    s.name as supplier_name,
                    s.trust_tier,
                    s.reliability_score,
                    so.unit_cost,
                    so.shipping_cost,
                    so.lead_time_days,
                    so.moq,
                    so.is_illustrative
                FROM supplier_offers so
                JOIN suppliers s ON s.merchant_id = so.merchant_id AND s.id = so.supplier_id
                WHERE so.merchant_id = $1 AND so.product_id = $2
                ORDER BY so.unit_cost ASC
                """,
                merchant_id,
                item["product_id"],
            )

            evaluated_suppliers = []
            for off in offers:
                moq = off["moq"]
                order_qty = max(base_needed, moq)
                unit_cost = Decimal(str(off["unit_cost"]))
                ship_cost = Decimal(str(off["shipping_cost"]))
                landed = (unit_cost * order_qty) + ship_cost

                notes = []
                is_allowlisted = off["trust_tier"] == "allowlisted"
                if not is_allowlisted:
                    notes.append("Requires Owner Qualification (Vendor not allowlisted)")
                if off["lead_time_days"] > 5:
                    notes.append(f"Extended lead time ({off['lead_time_days']} days) increases stockout risk")
                if off["reliability_score"] < 85:
                    notes.append(f"Lower fulfillment reliability ({off['reliability_score']}%)")

                evaluated_suppliers.append({
                    "supplier_id": off["supplier_id"],
                    "supplier_name": off["supplier_name"],
                    "trust_tier": off["trust_tier"],
                    "is_allowlisted": is_allowlisted,
                    "reliability_score": Decimal(str(off["reliability_score"])),
                    "lead_time_days": off["lead_time_days"],
                    "moq": moq,
                    "unit_cost": unit_cost,
                    "shipping_cost": ship_cost,
                    "landed_cost": landed,
                    "is_illustrative": off["is_illustrative"],
                    "trade_off_notes": notes,
                })

            # Sort suppliers: prefer allowlisted first, then by reliability desc, then by landed cost asc
            evaluated_suppliers.sort(
                key=lambda s: (
                    not s["is_allowlisted"],
                    -float(s["reliability_score"]),
                    float(s["landed_cost"] or 999999)
                )
            )

            pref = evaluated_suppliers[0] if evaluated_suppliers else None
            alt = evaluated_suppliers[1:] if len(evaluated_suppliers) > 1 else []
            rec_units = pref["moq"] if pref else base_needed

            reorder_items.append({
                "product_id": item["product_id"],
                "product_name": item["product_name"],
                "sku": item["sku"],
                "available_stock": avail,
                "reorder_point": rop,
                "daily_demand": Decimal(str(item["daily_demand"])),
                "recommended_units": rec_units,
                "preferred_supplier": pref,
                "alternative_suppliers": alt,
            })

    return reorder_items


async def evaluate_spending_policy(
    conn: asyncpg.Connection,
    merchant_id: str,
    supplier_id: str,
    item_subtotal: Decimal,
    shipping_cost: Optional[Decimal],
    tax_cost: Optional[Decimal],
    labour_overhead_cost: Optional[Decimal],
    has_unknown_costs: bool,
    unknown_cost_nte_ceiling: Optional[Decimal],
    exclude_po_id: Optional[str] = None,
) -> PolicyValidationResult:
    """
    Deterministic backend spending guardrail evaluation:
    1. Landed Cost Calculation:
       - Unknown costs must NEVER silently become zero.
       - If has_unknown_costs is True, total_landed_cost is NULL, and unknown_cost_nte_ceiling is the effective cost bound.
       - If costs are known, total_landed_cost = subtotal + shipping + tax + labour.
    2. Per-Order Limit ($150.00):
       - Effective cost must not exceed $150.00.
    3. Spend Envelope Cap ($500.00):
       - Available spend within the weekly envelope must cover the effective cost.
    4. Supplier Allowlist:
       - Supplier must have trust_tier == 'allowlisted'. Non-allowlisted suppliers require owner qualification.
    5. Anti-Order-Splitting Guardrail (Rolling 24 Hours):
       - Checks sum of active purchase orders for the same supplier created within the last 24 hours.
       - If exclude_po_id is provided (during approval revalidation), that specific PO is excluded from the prior sum.
       - If (existing_24h_spend + proposed_effective_cost) > $150.00, block as split-order attempt.
    """
    reasons = []

    # 1. Determine Effective Landed Cost
    if has_unknown_costs:
        if unknown_cost_nte_ceiling is None:
            reasons.append("Unknown costs declared but no Not-To-Exceed (NTE) ceiling specified.")
            effective_cost = Decimal("999999.00")
            calc_landed = None
        else:
            effective_cost = unknown_cost_nte_ceiling
            calc_landed = None
    else:
        calc_landed = item_subtotal
        if shipping_cost is not None:
            calc_landed += shipping_cost
        if tax_cost is not None:
            calc_landed += tax_cost
        if labour_overhead_cost is not None:
            calc_landed += labour_overhead_cost
        effective_cost = calc_landed

    # 2. Per-Order Limit Check ($150)
    per_order_passed = effective_cost <= Decimal("150.00")
    if not per_order_passed:
        reasons.append(f"Per-order limit exceeded: Effective landed cost ${effective_cost:.2f} exceeds the $150.00 cap.")

    # 3. Spend Envelope Cap Check ($500 total, available derived from ledger)
    envelope = await get_spend_envelope(conn, merchant_id)
    avail = envelope["available_spend"] if envelope else Decimal("0.00")
    envelope_passed = effective_cost <= avail
    if not envelope_passed:
        reasons.append(f"Spend envelope depleted: Order cost ${effective_cost:.2f} exceeds available weekly allowance of ${avail:.2f}.")

    remaining_if_approved = avail - effective_cost if envelope_passed else avail

    # 4. Supplier Allowlist Check
    supp = await conn.fetchrow(
        "SELECT trust_tier FROM suppliers WHERE merchant_id = $1 AND id = $2",
        merchant_id,
        supplier_id,
    )
    if not supp:
        allowlist_passed = False
        reasons.append(f"Supplier '{supplier_id}' not found.")
    else:
        allowlist_passed = supp["trust_tier"] == "allowlisted"
        if not allowlist_passed:
            reasons.append(f"Supplier trust tier '{supp['trust_tier']}' requires explicit owner qualification.")

    # 5. Anti-Order-Splitting Guardrail (Rolling 24 Hours)
    # Aggregates active purchase orders created in the last 24 hours for the same supplier
    if exclude_po_id:
        same_supplier_24h = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(
                CASE
                    WHEN has_unknown_costs THEN unknown_cost_nte_ceiling
                    ELSE total_landed_cost
                END
            ), 0.00) as rolling_sum
            FROM purchase_orders
            WHERE merchant_id = $1
              AND supplier_id = $2
              AND id != $3
              AND status IN ('pending_approval', 'ordered', 'received')
              AND created_at >= (now() - INTERVAL '24 hours')
            """,
            merchant_id,
            supplier_id,
            exclude_po_id,
        )
    else:
        same_supplier_24h = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(
                CASE
                    WHEN has_unknown_costs THEN unknown_cost_nte_ceiling
                    ELSE total_landed_cost
                END
            ), 0.00) as rolling_sum
            FROM purchase_orders
            WHERE merchant_id = $1
              AND supplier_id = $2
              AND status IN ('pending_approval', 'ordered', 'received')
              AND created_at >= (now() - INTERVAL '24 hours')
            """,
            merchant_id,
            supplier_id,
        )

    rolling_24h_sum = Decimal(str(same_supplier_24h["rolling_sum"]))
    anti_splitting_passed = (rolling_24h_sum + effective_cost) <= Decimal("150.00")
    if not anti_splitting_passed:
        reasons.append(
            f"Anti-order-splitting violation: Orders to this supplier in the last 24 hours total ${rolling_24h_sum:.2f}. "
            f"Adding ${effective_cost:.2f} would exceed the $150.00 per-supplier 24-hour limit."
        )

    passed = per_order_passed and envelope_passed and allowlist_passed and anti_splitting_passed

    return PolicyValidationResult(
        passed=passed,
        reasons=reasons,
        per_order_limit_passed=per_order_passed,
        envelope_cap_passed=envelope_passed,
        allowlist_passed=allowlist_passed,
        anti_splitting_passed=anti_splitting_passed,
        calculated_landed_cost=calc_landed,
        remaining_envelope_if_approved=remaining_if_approved if passed else None,
    )


async def create_purchase_order(
    conn: asyncpg.Connection,
    merchant_id: str,
    po_req: PurchaseOrderCreateRequest,
) -> Dict[str, Any]:
    """
    Creates a new draft purchase order and validates spending policy.
    CRITICAL: Acquires merchant row lock (SELECT id FROM merchants WHERE id = $1 FOR UPDATE)
    to serialize procurement mutations and prevent concurrent anti-splitting bypasses.
    DERIVES unit_cost and shipping_cost server-side from canonical supplier_offers table.
    """
    # 1. Acquire Merchant Lock First
    merchant_row = await conn.fetchrow(
        "SELECT id FROM merchants WHERE id = $1 FOR UPDATE",
        merchant_id,
    )
    if not merchant_row:
        raise ValueError(f"Merchant '{merchant_id}' not found.")

    # 2. Retrieve Canonical Supplier Offers for all requested items
    product_ids = [item.product_id for item in po_req.items]
    offers = await conn.fetch(
        """
        SELECT product_id, unit_cost, shipping_cost, moq
        FROM supplier_offers
        WHERE merchant_id = $1 AND supplier_id = $2 AND product_id = ANY($3::varchar[])
        """,
        merchant_id,
        po_req.supplier_id,
        product_ids,
    )
    offer_map = {o["product_id"]: o for o in offers}

    # Validate that every product has a matching offer from this supplier
    for itm in po_req.items:
        if itm.product_id not in offer_map:
            raise ValueError(
                f"No catalog offer found from supplier '{po_req.supplier_id}' for product '{itm.product_id}'."
            )

    # 3. Compute Item Subtotal and Derive Fixed Shipping Cost from Canonical Offers
    subtotal = Decimal("0.00")
    max_shipping_cost = Decimal("0.00")
    items_to_create = []

    for itm in po_req.items:
        canonical_offer = offer_map[itm.product_id]
        canonical_unit_cost = Decimal(str(canonical_offer["unit_cost"]))
        line_total = canonical_unit_cost * itm.quantity
        subtotal += line_total

        # Track maximum shipping charge across the supplier's offers
        offer_ship = Decimal(str(canonical_offer["shipping_cost"]))
        if offer_ship > max_shipping_cost:
            max_shipping_cost = offer_ship

        items_to_create.append({
            "product_id": itm.product_id,
            "quantity": itm.quantity,
            "unit_cost": canonical_unit_cost,
            "line_total": line_total,
        })

    canonical_shipping_cost = max_shipping_cost

    # 4. Policy Validation under Merchant Lock
    policy = await evaluate_spending_policy(
        conn=conn,
        merchant_id=merchant_id,
        supplier_id=po_req.supplier_id,
        item_subtotal=subtotal,
        shipping_cost=canonical_shipping_cost,
        tax_cost=po_req.tax_cost,
        labour_overhead_cost=po_req.labour_overhead_cost,
        has_unknown_costs=po_req.has_unknown_costs,
        unknown_cost_nte_ceiling=po_req.unknown_cost_nte_ceiling,
    )

    po_id = f"po_{uuid.uuid4().hex[:12]}"
    po_num = f"PO-{datetime.now(timezone.utc).strftime('%Y')}-{uuid.uuid4().hex[:6].upper()}"

    initial_status = "pending_approval" if policy.passed else "rejected"

    po_row = await conn.fetchrow(
        """
        INSERT INTO purchase_orders (
            merchant_id,
            id,
            po_number,
            supplier_id,
            status,
            item_subtotal,
            shipping_cost,
            tax_cost,
            labour_overhead_cost,
            total_landed_cost,
            has_unknown_costs,
            unknown_cost_nte_ceiling,
            payment_status,
            is_illustrative
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, 'unpaid', TRUE)
        RETURNING *
        """,
        merchant_id,
        po_id,
        po_num,
        po_req.supplier_id,
        initial_status,
        subtotal,
        canonical_shipping_cost,
        po_req.tax_cost,
        po_req.labour_overhead_cost,
        policy.calculated_landed_cost,
        po_req.has_unknown_costs,
        po_req.unknown_cost_nte_ceiling,
    )

    # Insert Items
    created_items = []
    for itm in items_to_create:
        poi_id = f"poi_{uuid.uuid4().hex[:12]}"
        await conn.execute(
            """
            INSERT INTO purchase_order_items (
                merchant_id,
                id,
                purchase_order_id,
                product_id,
                quantity,
                unit_cost,
                line_total
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            merchant_id,
            poi_id,
            po_id,
            itm["product_id"],
            itm["quantity"],
            itm["unit_cost"],
            itm["line_total"],
        )
        created_items.append({
            "id": poi_id,
            "product_id": itm["product_id"],
            "quantity": itm["quantity"],
            "unit_cost": itm["unit_cost"],
            "line_total": itm["line_total"],
        })

    # Operational Audit Log
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
        ) VALUES ($1, 'ai_agent', 'procurement_engine', 'purchase_order_proposed', 'purchase_order', $2, $3::jsonb)
        """,
        merchant_id,
        po_id,
        f'{{"po_number": "{po_num}", "passed_policy": {str(policy.passed).lower()}, "effective_cost": "{policy.calculated_landed_cost or po_req.unknown_cost_nte_ceiling}"}}',
    )

    res = dict(po_row)
    res["items"] = created_items
    res["policy_evaluation"] = policy.model_dump()
    return res


async def process_owner_decision(
    conn: asyncpg.Connection,
    merchant_id: str,
    po_id: str,
    decision_req: OwnerDecisionRequest,
    decided_by_actor: str = "merchant_owner",
) -> Dict[str, Any]:
    """
    Executes an atomic owner approval or rejection.
    LOCK ACQUISITION ORDER:
    1. Lock merchant row: SELECT id FROM merchants WHERE id = $1 FOR UPDATE
       This completely serializes envelope consumption across all purchase orders for this tenant.
    2. Lock target purchase order row: SELECT * FROM purchase_orders WHERE merchant_id = $1 AND id = $2 FOR UPDATE
    3. Re-validate all spending policy rules against live ledger balance and supplier 24h limits.
    4. On approval:
       - Insert spend_reservations record.
       - Append financial_ledger entry.
       - Update purchase_orders status = 'ordered', payment_status = 'reserved'.
    5. On rejection:
       - Update purchase_orders status = 'rejected'.
    6. Record purchase_order_approvals and operational_audit_log.
    """
    # 1. LOCK MERCHANT FIRST (Serializes all approval transactions for this tenant)
    merchant_row = await conn.fetchrow(
        "SELECT id FROM merchants WHERE id = $1 FOR UPDATE",
        merchant_id,
    )
    if not merchant_row:
        raise ValueError(f"Merchant '{merchant_id}' not found.")

    # 2. LOCK TARGET PURCHASE ORDER SECOND
    po = await conn.fetchrow(
        """
        SELECT * FROM purchase_orders
        WHERE merchant_id = $1 AND id = $2
        FOR UPDATE
        """,
        merchant_id,
        po_id,
    )
    if not po:
        raise ValueError(f"Purchase order '{po_id}' not found.")

    if po["status"] != "pending_approval":
        raise ValueError(f"Purchase order '{po_id}' is in status '{po['status']}', not pending approval.")

    effective_cost = po["unknown_cost_nte_ceiling"] if po["has_unknown_costs"] else po["total_landed_cost"]
    effective_cost = Decimal(str(effective_cost or "0.00"))

    appr_id = f"po_appr_{uuid.uuid4().hex[:12]}"
    decision = decision_req.decision.lower()

    if decision not in ("approved", "rejected"):
        raise ValueError("Decision must be either 'approved' or 'rejected'.")

    reserved_amt = Decimal("0.00")

    if decision == "approved":
        # 3. RE-VALIDATE COMPLETE POLICY UNDER TRANSACTION LOCK
        reval_policy = await evaluate_spending_policy(
            conn=conn,
            merchant_id=merchant_id,
            supplier_id=po["supplier_id"],
            item_subtotal=Decimal(str(po["item_subtotal"])),
            shipping_cost=Decimal(str(po["shipping_cost"])) if po["shipping_cost"] is not None else None,
            tax_cost=Decimal(str(po["tax_cost"])) if po["tax_cost"] is not None else None,
            labour_overhead_cost=Decimal(str(po["labour_overhead_cost"])) if po["labour_overhead_cost"] is not None else None,
            has_unknown_costs=po["has_unknown_costs"],
            unknown_cost_nte_ceiling=Decimal(str(po["unknown_cost_nte_ceiling"])) if po["unknown_cost_nte_ceiling"] is not None else None,
            exclude_po_id=po_id,
        )
        if not reval_policy.passed:
            raise ValueError(f"Cannot approve PO due to policy revalidation failure: {'; '.join(reval_policy.reasons)}")

        # Retrieve verified live envelope
        envelope = await get_spend_envelope(conn, merchant_id)
        avail = envelope["available_spend"] if envelope else Decimal("0.00")

        # 4a. Spend Reservation
        resv_id = f"resv_{uuid.uuid4().hex[:12]}"
        await conn.execute(
            """
            INSERT INTO spend_reservations (
                merchant_id,
                id,
                purchase_order_id,
                reserved_amount,
                status
            ) VALUES ($1, $2, $3, $4, 'active')
            """,
            merchant_id,
            resv_id,
            po_id,
            effective_cost,
        )

        # 4b. Financial Ledger Append
        new_running = avail - effective_cost
        ledger_idemp = f"ledger_resv_{po_id}_{decision_req.idempotency_key}"
        await conn.execute(
            """
            INSERT INTO financial_ledger (
                merchant_id,
                entry_type,
                amount,
                running_envelope_balance,
                reference_type,
                reference_id,
                idempotency_key,
                description
            ) VALUES ($1, 'spend_reservation', $2, $3, 'purchase_order', $4, $5, $6)
            """,
            merchant_id,
            -effective_cost,
            new_running,
            po_id,
            ledger_idemp,
            f"Owner approved PO {po['po_number']}: reserved ${effective_cost:.2f}",
        )

        # 4c. Update PO state
        await conn.execute(
            """
            UPDATE purchase_orders
            SET status = 'ordered', payment_status = 'reserved', updated_at = now()
            WHERE merchant_id = $1 AND id = $2
            """,
            merchant_id,
            po_id,
        )
        reserved_amt = effective_cost

    else:
        # Rejected
        await conn.execute(
            """
            UPDATE purchase_orders
            SET status = 'rejected', updated_at = now()
            WHERE merchant_id = $1 AND id = $2
            """,
            merchant_id,
            po_id,
        )

    # 6. Record Approval Decision with Server-Derived Actor
    await conn.execute(
        """
        INSERT INTO purchase_order_approvals (
            merchant_id,
            id,
            purchase_order_id,
            decision,
            approval_type,
            nte_ceiling,
            decided_by,
            decision_reason
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        merchant_id,
        appr_id,
        po_id,
        decision,
        decision_req.approval_type,
        decision_req.nte_ceiling,
        decided_by_actor,
        decision_req.decision_reason,
    )

    # 7. Operational Audit Log with Server-Derived Actor
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
        ) VALUES ($1, 'merchant_owner', $2, $3, 'purchase_order', $4, $5::jsonb)
        """,
        merchant_id,
        decided_by_actor,
        f"purchase_order_{decision}",
        po_id,
        f'{{"po_number": "{po["po_number"]}", "decision": "{decision}", "amount": "{effective_cost}"}}',
    )

    # Fetch updated PO and envelope
    updated_po = await get_purchase_order_by_id(conn, merchant_id, po_id)
    updated_envelope = await get_spend_envelope(conn, merchant_id)
    rem_avail = updated_envelope["available_spend"] if updated_envelope else Decimal("0.00")

    return {
        "purchase_order": updated_po,
        "decision": decision,
        "spend_reserved": reserved_amt,
        "remaining_envelope_available": rem_avail,
        "audit_logged": True,
    }


async def get_purchase_order_by_id(
    conn: asyncpg.Connection,
    merchant_id: str,
    po_id: str,
) -> Optional[Dict[str, Any]]:
    """Retrieve full purchase order with items and supplier metadata."""
    po_row = await conn.fetchrow(
        """
        SELECT
            po.*,
            s.name as supplier_name
        FROM purchase_orders po
        JOIN suppliers s ON s.merchant_id = po.merchant_id AND s.id = po.supplier_id
        WHERE po.merchant_id = $1 AND po.id = $2
        """,
        merchant_id,
        po_id,
    )
    if not po_row:
        return None

    items = await conn.fetch(
        """
        SELECT
            poi.*,
            p.title as product_name,
            p.sku
        FROM purchase_order_items poi
        JOIN products p ON p.merchant_id = poi.merchant_id AND p.id = poi.product_id
        WHERE poi.merchant_id = $1 AND poi.purchase_order_id = $2
        """,
        merchant_id,
        po_id,
    )
    po_dict = dict(po_row)
    po_dict["items"] = [dict(i) for i in items]
    return po_dict


async def list_purchase_orders(
    conn: asyncpg.Connection,
    merchant_id: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """List recent purchase orders with line items."""
    orders = await conn.fetch(
        """
        SELECT
            po.*,
            s.name as supplier_name
        FROM purchase_orders po
        JOIN suppliers s ON s.merchant_id = po.merchant_id AND s.id = po.supplier_id
        WHERE po.merchant_id = $1
        ORDER BY po.created_at DESC
        LIMIT $2
        """,
        merchant_id,
        limit,
    )
    if not orders:
        return []

    po_ids = [o["id"] for o in orders]
    items = await conn.fetch(
        """
        SELECT
            poi.*,
            p.title as product_name,
            p.sku
        FROM purchase_order_items poi
        JOIN products p ON p.merchant_id = poi.merchant_id AND p.id = poi.product_id
        WHERE poi.merchant_id = $1 AND poi.purchase_order_id = ANY($2::varchar[])
        """,
        merchant_id,
        po_ids,
    )

    items_by_po: Dict[str, list] = {}
    for i in items:
        items_by_po.setdefault(i["purchase_order_id"], []).append(dict(i))

    result = []
    for o in orders:
        pod = dict(o)
        pod["items"] = items_by_po.get(o["id"], [])
        result.append(pod)

    return result
