-- =============================================================================
-- Migration: 002_seed_northstar_living.sql
-- Description: Deterministic seed data for Northstar Living boutique retailer.
-- Grounding: AGENTS.md Operating Blueprint for Northstar Living pilot store.
-- Target Engine: PostgreSQL 15+ (Supabase)
-- =============================================================================

-- =============================================================================
-- 1. ROOT TENANT: NORTHSTAR LIVING
-- =============================================================================
INSERT INTO merchants (
    id,
    name,
    storefront_type,
    active_demo_week_cap,
    per_order_limit,
    currency
) VALUES (
    'merchant_northstar',
    'Northstar Living',
    'omnichannel',
    500.00,
    150.00,
    'USD'
) ON CONFLICT (id) DO NOTHING;

-- =============================================================================
-- 2. SUPPLIERS DIRECTORY (EXACT DEMO SUPPLIERS)
-- Explicitly tagged as is_illustrative = TRUE per AGENTS.md.
-- =============================================================================
INSERT INTO suppliers (
    merchant_id,
    id,
    name,
    contact_email,
    trust_tier,
    reliability_score,
    is_illustrative
) VALUES
(
    'merchant_northstar',
    'sup_supplier_a',
    'Supplier A (Home Décor & Ceramics)',
    'orders@suppliera-demo.com',
    'allowlisted',
    94,
    TRUE
),
(
    'merchant_northstar',
    'sup_supplier_b',
    'Supplier B (Textiles & Lighting)',
    'sales@supplierb-demo.com',
    'requires_approval',
    81,
    TRUE
),
(
    'merchant_northstar',
    'sup_supplier_c',
    'Supplier C (Kitchenware & Dining)',
    'supply@supplierc-demo.com',
    'allowlisted',
    90,
    TRUE
) ON CONFLICT (merchant_id, id) DO NOTHING;

-- =============================================================================
-- 3. PRODUCT CATALOGUE
-- Demonstration SKU: Handcrafted Blue Ceramic Vase
-- Canonical product ID : prod_blue_vase
-- Canonical SKU        : NSL-VASE-001
-- Base cost            : $4.10 — matches Supplier C (preferred supplier) unit cost.
--                        Supplier A's lower unit cost of $4.00 was used in the
--                        original draft; $4.10 is the correct preferred-supplier figure.
-- =============================================================================
INSERT INTO products (
    merchant_id,
    id,
    sku,
    title,
    category,
    base_cost,
    selling_price
) VALUES (
    'merchant_northstar',
    'prod_blue_vase',
    'NSL-VASE-001',
    'Handcrafted Blue Ceramic Vase',
    'Home Décor',
    4.10,
    24.00
) ON CONFLICT (merchant_id, id) DO NOTHING;

-- =============================================================================
-- 4. SUPPLIER OFFERS (CATALOG MATRIX FOR BLUE CERAMIC VASE)
-- Supplier A: $4.00/unit, $12 shipping, 3-day lead, MOQ 20, reliability 94 (allowlisted)
-- Supplier B: $3.70/unit, $12 shipping, 8-day lead, MOQ 25, reliability 81 (requires_approval)
-- Supplier C: $4.10/unit, $8 shipping, 2-day lead, MOQ 20, reliability 90 (allowlisted) ← PREFERRED
-- Preferred supplier: Supplier C — lowest lead time (2 days) and highest reliability among
-- allowlisted options. base_cost on the product record matches Supplier C's unit cost ($4.10).
-- =============================================================================
INSERT INTO supplier_offers (
    merchant_id,
    id,
    supplier_id,
    product_id,
    unit_cost,
    shipping_cost,
    lead_time_days,
    moq,
    is_illustrative
) VALUES
(
    'merchant_northstar',
    'offer_sup_a_vase',
    'sup_supplier_a',
    'prod_blue_vase',
    4.00,
    12.00,
    3,
    20,
    TRUE
),
(
    'merchant_northstar',
    'offer_sup_b_vase',
    'sup_supplier_b',
    'prod_blue_vase',
    3.70,
    12.00,
    8,
    25,
    TRUE
),
(
    'merchant_northstar',
    'offer_sup_c_vase',
    'sup_supplier_c',
    'prod_blue_vase',
    4.10,
    8.00,
    2,
    20,
    TRUE
) ON CONFLICT (merchant_id, id) DO NOTHING;

-- =============================================================================
-- 5. POS CUSTOMER ORDER (FOR AUDITABLE POS SALE RECORD)
-- POS transaction selling 2 × Handcrafted Blue Ceramic Vases at $24.00 each
-- Subtotal: 2 × $24.00 = $48.00 | Tax (8%): $3.84 | Shipping: $0.00 | Total: $51.84
-- =============================================================================
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
) VALUES (
    'merchant_northstar',
    'order_pos_demo_01',
    'POS-2026-001',
    'pos',
    'completed',
    48.00,
    3.84,
    0.00,
    51.84,
    'card_in_store'
) ON CONFLICT (merchant_id, id) DO NOTHING;

INSERT INTO order_items (
    merchant_id,
    id,
    order_id,
    product_id,
    quantity,
    unit_price,
    line_total
) VALUES (
    'merchant_northstar',
    'item_pos_demo_01',
    'order_pos_demo_01',
    'prod_blue_vase',
    2,
    24.00,
    48.00
) ON CONFLICT (merchant_id, id) DO NOTHING;

-- =============================================================================
-- 6. INVENTORY MOVEMENTS (AUDIT TRAIL: OPENING +14, SALE -2 -> BALANCE 12)
-- =============================================================================
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
    idempotency_key,
    created_at
) VALUES
(
    'mov_opening_vase_01',
    'merchant_northstar',
    'prod_blue_vase',
    'in_store',
    'opening_balance',
    14,
    0,
    14,
    'opening_inventory',
    'opening_2026_w1',
    'seed_inv_opening_vase',
    now() - INTERVAL '2 days'
),
(
    'mov_pos_sale_vase_01',
    'merchant_northstar',
    'prod_blue_vase',
    'in_store',
    'pos_sale',
    -2,
    0,
    12,
    'order',
    'order_pos_demo_01',
    'seed_inv_sale_vase_01',
    now() - INTERVAL '1 day'
) ON CONFLICT (merchant_id, idempotency_key) DO NOTHING;

-- =============================================================================
-- 7. INVENTORY ITEMS (FINAL RECONCILED OMNICHANNEL BALANCES)
-- Stock in store       : 12 units  (opening 14 − POS sale 2)
-- Reorder point        : 14 units  (daily_demand 4 × lead_days 2 + safety_stock 6 = 14)
-- Preferred supplier   : Supplier C — 2-day lead time used in reorder formula
-- available_in_store   : GENERATED = (12 − 0) = 12 < reorder_point 14 → RESTOCK ALERT
-- =============================================================================
INSERT INTO inventory_items (
    merchant_id,
    id,
    product_id,
    stock_in_store,
    stock_online,
    reserved_in_store,
    reserved_online,
    reorder_point,
    daily_demand,
    lead_days,
    safety_stock
) VALUES (
    'merchant_northstar',
    'inv_blue_vase',
    'prod_blue_vase',
    12,
    0,
    0,
    0,
    14,
    4,
    2,
    6
) ON CONFLICT (merchant_id, id) DO NOTHING;

-- =============================================================================
-- 8. FINANCIAL ENVELOPE LEDGER
-- Allocation: $500.00 authorized weekly cap
-- Prior commitments: $175.00
-- Remaining available: $325.00
-- =============================================================================
INSERT INTO financial_ledger (
    merchant_id,
    entry_type,
    amount,
    running_envelope_balance,
    reference_type,
    reference_id,
    idempotency_key,
    description,
    created_at
) VALUES
(
    'merchant_northstar',
    'adjustment',
    500.00,
    500.00,
    'seed_allocation',
    'alloc_demo_week_01',
    'seed_ledger_alloc_week_01',
    'Authorized spend envelope cap for active demo week ($500.00)',
    now() - INTERVAL '3 days'
),
(
    'merchant_northstar',
    'spend_commitment',
    -175.00,
    325.00,
    'seed_commitment',
    'commit_prior_orders_01',
    'seed_ledger_prior_commitments',
    'Prior committed purchase orders for active demo week ($175.00)',
    now() - INTERVAL '2 days'
) ON CONFLICT (merchant_id, idempotency_key) DO NOTHING;
