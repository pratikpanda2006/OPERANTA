-- =============================================================================
-- Migration: 001_initial_schema.sql
-- Description: Initial 21-table schema for MarginGuard retail operations platform.
-- Multi-tenancy: Enforced structurally via tenant-scoped composite foreign keys.
--   NOTE ON TENANT ISOLATION: Composite foreign keys enforce tenant-consistent
--   relationships (i.e. child rows cannot point to parent rows of another merchant),
--   but they do NOT provide complete read/write access isolation at the database layer.
--   Every application query and service call MUST explicitly enforce authenticated
--   merchant scoping (`WHERE merchant_id = :active_merchant_id`). Row-Level Security
--   (RLS) is not enabled at this stage and requires separate architectural approval.
-- Security: Immutability triggers and least-privilege role separation.
-- Target Engine: PostgreSQL 15+ (Supabase)
-- =============================================================================

-- Ensure schema_migrations exists (also bootstrapped by migrate.py runner)
CREATE TABLE IF NOT EXISTS schema_migrations (
    id               SERIAL PRIMARY KEY,
    filename         TEXT        NOT NULL UNIQUE,
    checksum_sha256  TEXT        NOT NULL,
    applied_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =============================================================================
-- 1. TENANCY DOMAIN
-- =============================================================================

-- Table 1: merchants
-- Root multi-tenant accounts with operational spending guardrails.
CREATE TABLE merchants (
    id                   VARCHAR(64) PRIMARY KEY,
    name                 VARCHAR(255) NOT NULL,
    storefront_type      VARCHAR(64) NOT NULL DEFAULT 'omnichannel',
    active_demo_week_cap NUMERIC(10, 2) NOT NULL DEFAULT 500.00,
    per_order_limit      NUMERIC(10, 2) NOT NULL DEFAULT 150.00,
    currency             VARCHAR(3) NOT NULL DEFAULT 'USD',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_merchant_caps CHECK (active_demo_week_cap > 0 AND per_order_limit > 0)
);

-- =============================================================================
-- 2. CATALOGUE DOMAIN
-- =============================================================================

-- Table 2: products
-- Retail catalog items. Note: base_cost is NULLABLE to represent unknown acquisition costs.
CREATE TABLE products (
    merchant_id    VARCHAR(64) NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    id             VARCHAR(64) NOT NULL,
    sku            VARCHAR(64) NOT NULL,
    title          VARCHAR(255) NOT NULL,
    category       VARCHAR(100) NOT NULL,
    base_cost      NUMERIC(10, 2) NULL,
    selling_price  NUMERIC(10, 2) NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (merchant_id, id),
    CONSTRAINT uq_product_sku UNIQUE (merchant_id, sku),
    CONSTRAINT chk_product_base_cost CHECK (base_cost IS NULL OR base_cost >= 0),
    CONSTRAINT chk_product_selling_price CHECK (selling_price >= 0)
);

CREATE INDEX idx_products_merchant_category ON products(merchant_id, category);

-- =============================================================================
-- 3. SUPPLIER DOMAIN
-- =============================================================================

-- Table 3: suppliers
-- Vendor directory and trust tiers (allowlisted vs requires_approval).
CREATE TABLE suppliers (
    merchant_id       VARCHAR(64) NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    id                VARCHAR(64) NOT NULL,
    name              VARCHAR(255) NOT NULL,
    contact_email     VARCHAR(255) NULL,
    trust_tier        VARCHAR(32) NOT NULL,
    reliability_score INT NOT NULL,
    is_illustrative   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (merchant_id, id),
    CONSTRAINT chk_supplier_trust_tier CHECK (trust_tier IN ('allowlisted', 'requires_approval')),
    CONSTRAINT chk_supplier_reliability CHECK (reliability_score >= 0 AND reliability_score <= 100)
);

-- Table 4: supplier_offers
-- Multi-factor supplier pricing, minimum order quantities (MOQ), and shipping lead times.
CREATE TABLE supplier_offers (
    merchant_id     VARCHAR(64) NOT NULL,
    id              VARCHAR(64) NOT NULL,
    supplier_id     VARCHAR(64) NOT NULL,
    product_id      VARCHAR(64) NOT NULL,
    unit_cost       NUMERIC(10, 2) NOT NULL,
    shipping_cost   NUMERIC(10, 2) NOT NULL,
    lead_time_days  INT NOT NULL,
    moq             INT NOT NULL,
    is_illustrative BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (merchant_id, id),
    FOREIGN KEY (merchant_id, supplier_id) REFERENCES suppliers(merchant_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (merchant_id, product_id) REFERENCES products(merchant_id, id) ON DELETE RESTRICT,
    CONSTRAINT chk_offer_unit_cost CHECK (unit_cost >= 0),
    CONSTRAINT chk_offer_shipping_cost CHECK (shipping_cost >= 0),
    CONSTRAINT chk_offer_lead_time CHECK (lead_time_days >= 0),
    CONSTRAINT chk_offer_moq CHECK (moq >= 1),
    CONSTRAINT uq_supplier_product_offer UNIQUE (merchant_id, supplier_id, product_id)
);

-- =============================================================================
-- 4. INVENTORY DOMAIN
-- =============================================================================

-- Table 5: inventory_items
-- Omnichannel segregated stock balances and calculated available stock.
CREATE TABLE inventory_items (
    merchant_id         VARCHAR(64) NOT NULL,
    id                  VARCHAR(64) NOT NULL,
    product_id          VARCHAR(64) NOT NULL,
    stock_in_store      INT NOT NULL DEFAULT 0,
    stock_online        INT NOT NULL DEFAULT 0,
    reserved_in_store   INT NOT NULL DEFAULT 0,
    reserved_online     INT NOT NULL DEFAULT 0,
    available_in_store  INT GENERATED ALWAYS AS (stock_in_store - reserved_in_store) STORED,
    available_online    INT GENERATED ALWAYS AS (stock_online - reserved_online) STORED,
    reorder_point       INT NOT NULL DEFAULT 0,
    daily_demand        INT NOT NULL DEFAULT 0,
    lead_days           INT NOT NULL DEFAULT 0,
    safety_stock        INT NOT NULL DEFAULT 0,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (merchant_id, id),
    FOREIGN KEY (merchant_id, product_id) REFERENCES products(merchant_id, id) ON DELETE RESTRICT,
    CONSTRAINT uq_inventory_product UNIQUE (merchant_id, product_id),
    CONSTRAINT chk_stock_in_store_non_negative CHECK (stock_in_store >= 0),
    CONSTRAINT chk_stock_online_non_negative CHECK (stock_online >= 0),
    CONSTRAINT chk_reserved_in_store_non_negative CHECK (reserved_in_store >= 0),
    CONSTRAINT chk_reserved_online_non_negative CHECK (reserved_online >= 0),
    CONSTRAINT chk_available_in_store_non_negative CHECK (available_in_store >= 0),
    CONSTRAINT chk_available_online_non_negative CHECK (available_online >= 0)
);

-- Table 6: inventory_movements
-- Immutable journal of every inventory delta across channels.
CREATE TABLE inventory_movements (
    id                VARCHAR(64) PRIMARY KEY,
    merchant_id       VARCHAR(64) NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    product_id        VARCHAR(64) NOT NULL,
    channel           VARCHAR(32) NOT NULL,
    movement_type     VARCHAR(64) NOT NULL,
    quantity_delta    INT NOT NULL,
    reserved_delta    INT NOT NULL DEFAULT 0,
    balance_after     INT NOT NULL,
    reference_type    VARCHAR(64) NULL,
    reference_id      VARCHAR(64) NULL,
    idempotency_key   VARCHAR(128) NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (merchant_id, product_id) REFERENCES products(merchant_id, id) ON DELETE RESTRICT,
    CONSTRAINT chk_inv_channel CHECK (channel IN ('in_store', 'online', 'transfer')),
    CONSTRAINT chk_inv_movement_type CHECK (movement_type IN (
        'opening_balance', 'pos_sale', 'online_order', 'return_restock',
        'procurement_receive', 'adjustment', 'transfer'
    )),
    CONSTRAINT uq_inventory_movement_idempotency UNIQUE (merchant_id, idempotency_key)
);

CREATE INDEX idx_inv_movements_lookup ON inventory_movements(merchant_id, product_id, created_at DESC);

-- =============================================================================
-- 5. PROCUREMENT DOMAIN
-- =============================================================================

-- Table 7: procurement_requests
-- Deterministic grouping entity for batched procurement intent across suppliers.
CREATE TABLE procurement_requests (
    merchant_id     VARCHAR(64) NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    id              VARCHAR(64) NOT NULL,
    request_number  VARCHAR(64) NOT NULL,
    target_budget   NUMERIC(10, 2) NOT NULL,
    status          VARCHAR(32) NOT NULL,
    notes           TEXT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (merchant_id, id),
    CONSTRAINT uq_procurement_request_number UNIQUE (merchant_id, request_number),
    CONSTRAINT chk_proc_req_budget CHECK (target_budget >= 0),
    CONSTRAINT chk_proc_req_status CHECK (status IN (
        'draft', 'pending_approval', 'approved', 'rejected',
        'partially_fulfilled', 'completed', 'cancelled'
    ))
);

-- Table 8: purchase_orders
-- PO lifecycle with landed cost calculation and unknown-cost protection.
CREATE TABLE purchase_orders (
    merchant_id              VARCHAR(64) NOT NULL,
    id                       VARCHAR(64) NOT NULL,
    po_number                VARCHAR(64) NOT NULL,
    request_id               VARCHAR(64) NULL,
    supplier_id              VARCHAR(64) NOT NULL,
    status                   VARCHAR(32) NOT NULL,
    item_subtotal            NUMERIC(10, 2) NOT NULL,
    shipping_cost            NUMERIC(10, 2) NULL,
    tax_cost                 NUMERIC(10, 2) NULL,
    labour_overhead_cost     NUMERIC(10, 2) NULL,
    total_landed_cost        NUMERIC(10, 2) NULL,
    has_unknown_costs        BOOLEAN NOT NULL DEFAULT FALSE,
    unknown_cost_nte_ceiling NUMERIC(10, 2) NULL,
    payment_method           VARCHAR(64) NOT NULL DEFAULT 'paypal_payouts',
    payment_status           VARCHAR(32) NOT NULL DEFAULT 'unpaid',
    is_illustrative          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (merchant_id, id),
    -- Deletion of a procurement_request is restricted if purchase orders reference it.
    -- In standard composite foreign keys (merchant_id, request_id), ON DELETE SET NULL
    -- is prohibited or unsafe because merchant_id is NOT NULL (the tenant key).
    -- ON DELETE RESTRICT safely protects procurement audit integrity by disallowing
    -- request deletion while active purchase orders are linked.
    FOREIGN KEY (merchant_id, request_id) REFERENCES procurement_requests(merchant_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (merchant_id, supplier_id) REFERENCES suppliers(merchant_id, id) ON DELETE RESTRICT,
    CONSTRAINT uq_po_number UNIQUE (merchant_id, po_number),
    CONSTRAINT chk_po_status CHECK (status IN (
        'draft', 'pending_approval', 'ordered', 'received', 'reconciled', 'rejected', 'cancelled'
    )),
    CONSTRAINT chk_po_payment_status CHECK (payment_status IN (
        'unpaid', 'reserved', 'paid_sandbox', 'paid_demo', 'failed'
    )),
    CONSTRAINT chk_po_subtotal CHECK (item_subtotal >= 0),
    CONSTRAINT chk_po_shipping_cost CHECK (shipping_cost IS NULL OR shipping_cost >= 0),
    CONSTRAINT chk_po_tax_cost CHECK (tax_cost IS NULL OR tax_cost >= 0),
    CONSTRAINT chk_po_labour_overhead_cost CHECK (labour_overhead_cost IS NULL OR labour_overhead_cost >= 0),
    CONSTRAINT chk_po_landed_cost_rule CHECK (
        total_landed_cost IS NULL
        OR (total_landed_cost >= 0 AND total_landed_cost <= 150.00)
    ),
    CONSTRAINT chk_po_unknown_cost_ceiling CHECK (has_unknown_costs = FALSE OR unknown_cost_nte_ceiling IS NOT NULL),
    CONSTRAINT chk_po_nte_limit CHECK (
        unknown_cost_nte_ceiling IS NULL
        OR (unknown_cost_nte_ceiling >= 0 AND unknown_cost_nte_ceiling <= 150.00)
    ),
    -- D2: When costs are unknown the total_landed_cost must remain NULL.
    -- The NTE ceiling (unknown_cost_nte_ceiling) is used as the worst-case
    -- spending bound instead. Using COALESCE to treat unknown costs as zero
    -- is explicitly prohibited: it would understate financial exposure.
    -- NOTE: NTE ceiling validation against the available envelope ($500 minus
    -- committed spend) is an aggregate check that cannot be expressed as a
    -- single-row SQL CHECK constraint. That validation is enforced in the
    -- FastAPI application layer using SELECT FOR UPDATE on the merchants row.
    CONSTRAINT chk_po_unknown_cost_nullifies_total CHECK (
        has_unknown_costs = FALSE
        OR total_landed_cost IS NULL
    )
);

CREATE INDEX idx_po_merchant_status ON purchase_orders(merchant_id, status);

-- Table 9: purchase_order_items
-- Line items associated with purchase orders.
CREATE TABLE purchase_order_items (
    merchant_id        VARCHAR(64) NOT NULL,
    id                 VARCHAR(64) NOT NULL,
    purchase_order_id  VARCHAR(64) NOT NULL,
    product_id         VARCHAR(64) NOT NULL,
    quantity           INT NOT NULL,
    unit_cost          NUMERIC(10, 2) NOT NULL,
    line_total         NUMERIC(10, 2) NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (merchant_id, id),
    FOREIGN KEY (merchant_id, purchase_order_id) REFERENCES purchase_orders(merchant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (merchant_id, product_id) REFERENCES products(merchant_id, id) ON DELETE RESTRICT,
    CONSTRAINT chk_poi_quantity CHECK (quantity >= 1),
    CONSTRAINT chk_poi_unit_cost CHECK (unit_cost >= 0),
    CONSTRAINT chk_poi_line_total CHECK (line_total >= 0)
);

-- =============================================================================
-- 6. SPENDING & GOVERNANCE DOMAIN
-- =============================================================================

-- Table 10: spend_reservations
-- Pre-encumbrance of financial capacity for pending purchase orders against the $500 cap.
CREATE TABLE spend_reservations (
    merchant_id        VARCHAR(64) NOT NULL,
    id                 VARCHAR(64) NOT NULL,
    purchase_order_id  VARCHAR(64) NOT NULL,
    reserved_amount    NUMERIC(10, 2) NOT NULL,
    status             VARCHAR(32) NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at        TIMESTAMPTZ NULL,
    PRIMARY KEY (merchant_id, id),
    FOREIGN KEY (merchant_id, purchase_order_id) REFERENCES purchase_orders(merchant_id, id) ON DELETE RESTRICT,
    CONSTRAINT uq_spend_reservation_po UNIQUE (merchant_id, purchase_order_id),
    CONSTRAINT chk_resv_amount CHECK (reserved_amount > 0),
    CONSTRAINT chk_resv_status CHECK (status IN ('active', 'committed', 'released', 'expired'))
);

-- Table 11: purchase_order_approvals
-- Store owner human-in-the-loop decisions with explicit override types and NTE ceilings.
CREATE TABLE purchase_order_approvals (
    merchant_id        VARCHAR(64) NOT NULL,
    id                 VARCHAR(64) NOT NULL,
    purchase_order_id  VARCHAR(64) NOT NULL,
    decision           VARCHAR(32) NOT NULL,
    approval_type      VARCHAR(64) NOT NULL,
    nte_ceiling        NUMERIC(10, 2) NULL,
    decided_by         VARCHAR(255) NOT NULL,
    decision_reason    TEXT NOT NULL,
    decided_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (merchant_id, id),
    FOREIGN KEY (merchant_id, purchase_order_id) REFERENCES purchase_orders(merchant_id, id) ON DELETE CASCADE,
    CONSTRAINT chk_po_appr_decision CHECK (decision IN ('approved', 'rejected', 'cancelled')),
    CONSTRAINT chk_po_appr_type CHECK (approval_type IN (
        'standard', 'unknown_cost_override', 'vendor_qualification', 'policy_exception'
    )),
    CONSTRAINT chk_approval_nte CHECK (
        approval_type != 'unknown_cost_override'
        OR (nte_ceiling IS NOT NULL AND nte_ceiling >= 0 AND nte_ceiling <= 150.00)
    )
);

-- =============================================================================
-- 7. FINANCIAL LEDGER DOMAIN
-- =============================================================================

-- Table 12: financial_ledger
-- Append-only financial spending-envelope journal tracking authorized caps,
-- reservations, commitments, releases, disbursements, and refunds.
-- (Note: MarginGuard implements an operational spending-envelope ledger with
-- running envelope balances, not a full double-entry chart-of-accounts system).
CREATE TABLE financial_ledger (
    id                        BIGSERIAL PRIMARY KEY,
    merchant_id               VARCHAR(64) NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    entry_type                VARCHAR(32) NOT NULL,
    amount                    NUMERIC(10, 2) NOT NULL,
    running_envelope_balance  NUMERIC(10, 2) NOT NULL,
    reference_type            VARCHAR(64) NOT NULL,
    reference_id              VARCHAR(64) NOT NULL,
    idempotency_key           VARCHAR(128) NOT NULL,
    description               TEXT NOT NULL,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_ledger_idempotency UNIQUE (merchant_id, idempotency_key),
    CONSTRAINT chk_ledger_entry_type CHECK (entry_type IN (
        'spend_reservation', 'spend_commitment', 'spend_release',
        'disbursement', 'revenue', 'refund', 'adjustment'
    ))
);

CREATE INDEX idx_ledger_merchant_created ON financial_ledger(merchant_id, created_at DESC);

-- =============================================================================
-- 8. SALES & ORDERS DOMAIN
-- =============================================================================

-- Table 13: orders
-- Customer sales orders across POS and online storefront.
CREATE TABLE orders (
    merchant_id      VARCHAR(64) NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    id               VARCHAR(64) NOT NULL,
    order_number     VARCHAR(64) NOT NULL,
    channel          VARCHAR(32) NOT NULL,
    status           VARCHAR(32) NOT NULL,
    subtotal         NUMERIC(10, 2) NOT NULL,
    tax_amount       NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
    shipping_amount  NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
    total_amount     NUMERIC(10, 2) NOT NULL,
    payment_method   VARCHAR(64) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (merchant_id, id),
    CONSTRAINT uq_order_number UNIQUE (merchant_id, order_number),
    CONSTRAINT chk_order_channel CHECK (channel IN ('pos', 'online')),
    CONSTRAINT chk_order_status CHECK (status IN ('completed', 'cancelled', 'refunded', 'partially_refunded')),
    CONSTRAINT chk_order_amounts CHECK (
        subtotal >= 0 AND tax_amount >= 0 AND shipping_amount >= 0 AND total_amount >= 0
    )
);

-- Table 14: order_items
-- Line items associated with customer sales orders.
CREATE TABLE order_items (
    merchant_id  VARCHAR(64) NOT NULL,
    id           VARCHAR(64) NOT NULL,
    order_id     VARCHAR(64) NOT NULL,
    product_id   VARCHAR(64) NOT NULL,
    quantity     INT NOT NULL,
    unit_price   NUMERIC(10, 2) NOT NULL,
    line_total   NUMERIC(10, 2) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (merchant_id, id),
    FOREIGN KEY (merchant_id, order_id) REFERENCES orders(merchant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (merchant_id, product_id) REFERENCES products(merchant_id, id) ON DELETE RESTRICT,
    CONSTRAINT chk_oi_quantity CHECK (quantity >= 1),
    CONSTRAINT chk_oi_unit_price CHECK (unit_price >= 0),
    CONSTRAINT chk_oi_line_total CHECK (line_total >= 0)
);

-- =============================================================================
-- 9. PAYMENTS & REFUNDS DOMAIN
-- =============================================================================

-- Table 15: paypal_captures
-- Verified PayPal captures tracking original amount and cumulative refunds.
CREATE TABLE paypal_captures (
    merchant_id        VARCHAR(64) NOT NULL,
    id                 VARCHAR(64) NOT NULL,
    order_id           VARCHAR(64) NOT NULL,
    paypal_capture_id  VARCHAR(128) NOT NULL,
    status             VARCHAR(32) NOT NULL,
    captured_amount    NUMERIC(10, 2) NOT NULL,
    amount_refunded    NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
    currency           VARCHAR(3) NOT NULL DEFAULT 'USD',
    raw_payload        JSONB NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (merchant_id, id),
    FOREIGN KEY (merchant_id, order_id) REFERENCES orders(merchant_id, id) ON DELETE RESTRICT,
    CONSTRAINT uq_paypal_capture_id UNIQUE (merchant_id, paypal_capture_id),
    -- Composite unique key enabling order_refunds to enforce same-merchant and same-order integrity
    CONSTRAINT uq_paypal_capture_merchant_order UNIQUE (merchant_id, order_id, id),
    CONSTRAINT chk_capture_status CHECK (status IN ('completed', 'pending', 'refunded', 'partially_refunded')),
    CONSTRAINT chk_capture_amounts CHECK (captured_amount > 0 AND amount_refunded >= 0),
    CONSTRAINT chk_capture_refund_ceiling CHECK (amount_refunded <= captured_amount)
);

-- Table 16: order_refunds
-- Journal of partial or full refunds issued against captures.
CREATE TABLE order_refunds (
    merchant_id       VARCHAR(64) NOT NULL,
    id                VARCHAR(64) NOT NULL,
    order_id          VARCHAR(64) NOT NULL,
    capture_id        VARCHAR(64) NOT NULL,
    amount            NUMERIC(10, 2) NOT NULL,
    status            VARCHAR(32) NOT NULL,
    paypal_refund_id  VARCHAR(128) NULL,
    reason            TEXT NOT NULL,
    idempotency_key   VARCHAR(128) NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (merchant_id, id),
    FOREIGN KEY (merchant_id, order_id) REFERENCES orders(merchant_id, id) ON DELETE RESTRICT,
    -- Basic tenant-scoped composite foreign key directly to paypal_captures(merchant_id, id)
    FOREIGN KEY (merchant_id, capture_id) REFERENCES paypal_captures(merchant_id, id) ON DELETE RESTRICT,
    -- Strictly enforce that an order_refunds row can only reference a capture belonging
    -- to the exact same merchant AND the exact same order.
    FOREIGN KEY (merchant_id, order_id, capture_id) REFERENCES paypal_captures(merchant_id, order_id, id) ON DELETE RESTRICT,
    CONSTRAINT uq_order_refund_idempotency UNIQUE (merchant_id, idempotency_key),
    CONSTRAINT chk_refund_amount CHECK (amount > 0),
    CONSTRAINT chk_refund_status CHECK (status IN ('pending', 'processing', 'completed', 'failed'))
);

-- =============================================================================
-- 10. RETURNS & DISPUTES DOMAIN
-- =============================================================================

-- Table 17: dispute_cases
-- Return requests, condition disputes, and chargeback cases.
CREATE TABLE dispute_cases (
    merchant_id          VARCHAR(64) NOT NULL,
    id                   VARCHAR(64) NOT NULL,
    order_id             VARCHAR(64) NOT NULL,
    dispute_number       VARCHAR(64) NOT NULL,
    channel              VARCHAR(32) NOT NULL,
    status               VARCHAR(32) NOT NULL,
    reason               VARCHAR(64) NOT NULL,
    customer_notes       TEXT NULL,
    merchant_resolution  TEXT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (merchant_id, id),
    FOREIGN KEY (merchant_id, order_id) REFERENCES orders(merchant_id, id) ON DELETE RESTRICT,
    CONSTRAINT uq_dispute_number UNIQUE (merchant_id, dispute_number),
    CONSTRAINT chk_dispute_channel CHECK (channel IN ('pos', 'online')),
    CONSTRAINT chk_dispute_status CHECK (status IN (
        'opened', 'under_review', 'evidence_submitted', 'resolved_refunded', 'resolved_rejected', 'closed'
    ))
);

-- Table 18: dispute_evidence
-- Audit vault for dispute photographic evidence, postal tracking receipts, and notes.
CREATE TABLE dispute_evidence (
    id             VARCHAR(64) PRIMARY KEY,
    merchant_id    VARCHAR(64) NOT NULL,
    dispute_id     VARCHAR(64) NOT NULL,
    evidence_type  VARCHAR(64) NOT NULL,
    file_url       TEXT NOT NULL,
    notes          TEXT NULL,
    uploaded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (merchant_id, dispute_id) REFERENCES dispute_cases(merchant_id, id) ON DELETE CASCADE,
    CONSTRAINT chk_evidence_type CHECK (evidence_type IN (
        'condition_photo', 'shipping_receipt', 'tracking_proof', 'customer_communication', 'other'
    ))
);

-- =============================================================================
-- 11. INTEGRATIONS & AUDIT DOMAIN
-- =============================================================================

-- Table 19: webhook_events
-- Durable incoming webhook lifecycle for idempotent processing.
CREATE TABLE webhook_events (
    provider       VARCHAR(64) NOT NULL,
    event_id       VARCHAR(128) NOT NULL,
    merchant_id    VARCHAR(64) NULL REFERENCES merchants(id) ON DELETE SET NULL,
    event_type     VARCHAR(128) NOT NULL,
    status         VARCHAR(32) NOT NULL,
    payload        JSONB NOT NULL,
    error_message  TEXT NULL,
    retry_count    INT NOT NULL DEFAULT 0,
    received_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (provider, event_id),
    CONSTRAINT chk_webhook_status CHECK (status IN ('received', 'processing', 'processed', 'failed'))
);

CREATE INDEX idx_webhooks_status_heartbeat ON webhook_events(status, updated_at);

-- Table 20: operational_audit_log
-- Tamper-evident operational audit stream capturing all actor decisions.
CREATE TABLE operational_audit_log (
    id           BIGSERIAL PRIMARY KEY,
    merchant_id  VARCHAR(64) NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    actor_type   VARCHAR(64) NOT NULL,
    actor_id     VARCHAR(128) NOT NULL,
    action       VARCHAR(128) NOT NULL,
    entity_type  VARCHAR(64) NOT NULL,
    entity_id    VARCHAR(128) NOT NULL,
    details      JSONB NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_audit_actor_type CHECK (actor_type IN (
        'merchant_owner', 'store_employee', 'system_worker', 'ai_agent'
    ))
);

CREATE INDEX idx_audit_log_lookup ON operational_audit_log(merchant_id, created_at DESC);

-- =============================================================================
-- 12. IMMUTABILITY TRIGGERS & ENGINE-LEVEL GUARDS
-- =============================================================================

-- Function: prevent_ledger_tampering
-- Prevents UPDATE, DELETE, or TRUNCATE operations on append-only financial journals.
CREATE OR REPLACE FUNCTION prevent_ledger_tampering()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'financial_ledger is strictly append-only. UPDATE, DELETE, and TRUNCATE operations are forbidden.';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_financial_ledger_immutable
BEFORE UPDATE OR DELETE ON financial_ledger
FOR EACH ROW EXECUTE FUNCTION prevent_ledger_tampering();

CREATE TRIGGER trg_financial_ledger_no_truncate
BEFORE TRUNCATE ON financial_ledger
FOR EACH STATEMENT EXECUTE FUNCTION prevent_ledger_tampering();

-- Function: prevent_inventory_movement_tampering
-- Protects the inventory audit journal from post-facto modification.
CREATE OR REPLACE FUNCTION prevent_inventory_movement_tampering()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'inventory_movements is strictly append-only. UPDATE, DELETE, and TRUNCATE operations are forbidden.';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_inventory_movements_immutable
BEFORE UPDATE OR DELETE ON inventory_movements
FOR EACH ROW EXECUTE FUNCTION prevent_inventory_movement_tampering();

CREATE TRIGGER trg_inventory_movements_no_truncate
BEFORE TRUNCATE ON inventory_movements
FOR EACH STATEMENT EXECUTE FUNCTION prevent_inventory_movement_tampering();

-- Function: prevent_audit_log_tampering
-- Protects operational audit records from being altered.
CREATE OR REPLACE FUNCTION prevent_audit_log_tampering()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'operational_audit_log is strictly append-only. UPDATE, DELETE, and TRUNCATE operations are forbidden.';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_log_immutable
BEFORE UPDATE OR DELETE ON operational_audit_log
FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_tampering();

CREATE TRIGGER trg_audit_log_no_truncate
BEFORE TRUNCATE ON operational_audit_log
FOR EACH STATEMENT EXECUTE FUNCTION prevent_audit_log_tampering();

-- =============================================================================
-- 13. ROLE SEPARATION & PRIVILEGE ARCHITECTURE
-- =============================================================================
-- DESIGN OVERVIEW
-- ───────────────
-- This migration creates one database entity:
--
--   marginguard_app_user  (NOLOGIN — a permission role / privilege group)
--
-- A NOLOGIN role carries table-level privileges but has no authentication
-- identity and no password. It cannot connect to PostgreSQL by itself.
-- This is intentional: no credentials are ever stored in migration SQL.
--
-- RUNTIME LOGIN ROLE (provisioned outside this migration)
-- ────────────────────────────────────────────────────────
-- A separate LOGIN role (the "runtime authenticating role") must be created
-- through Supabase tooling — NOT via raw SQL in a migration file. Recommended
-- methods, in order of preference:
--
--   1. Supabase Dashboard → Database → Roles → "New Role"
--   2. Supabase Management API: POST /v1/projects/{ref}/database/roles
--
-- After provisioning, grant the permission role to the login role:
--
--   GRANT marginguard_app_user TO <your_runtime_login_role>;
--
-- The runtime login role must NOT be given the following:
--   - SUPERUSER, CREATEDB, CREATEROLE, REPLICATION
--   - Any direct access to migration infrastructure (schema_migrations)
--   - The DIRECT_URL credentials used by the migration runner
--
-- This separation ensures the application pool (DATABASE_URL) cannot perform
-- schema changes, and the migration runner (DIRECT_URL) credentials are never
-- deployed alongside the application.
--
-- WHY NOLOGIN IN SQL AND LOGIN ROLE VIA DASHBOARD?
-- In Supabase, LOGIN roles created by raw SQL bypass Supavisor registration
-- and do not appear in the dashboard for key rotation. Dashboard-managed roles
-- are correctly registered with the connection pooler and have proper
-- pg_hba.conf entries. NOLOGIN permission roles are safe to create via SQL
-- because they carry no authentication credentials.
--
-- D3 — PostgreSQL transaction safety of CREATE ROLE:
-- The original audit report claimed CREATE ROLE "survives transaction rollback"
-- (non-transactional behaviour inherited from MySQL/Oracle). This was an
-- UNVERIFIED assertion. In standard PostgreSQL, DDL — including CREATE ROLE —
-- IS transactional. A rolled-back transaction containing CREATE ROLE leaves no
-- orphaned role in pg_roles. The IF NOT EXISTS guard below is an idempotency
-- convenience (safe on re-run), not a rollback-safety workaround.
-- =============================================================================

-- Create the NOLOGIN permission role if it does not already exist.
-- This role accumulates table-level privileges; it does NOT grant login access.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'marginguard_app_user') THEN
        CREATE ROLE marginguard_app_user WITH NOLOGIN;
    END IF;
END;
$$;

-- Grant usage on public schema
GRANT USAGE ON SCHEMA public TO marginguard_app_user;

-- Grant standard DML on domain tables
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO marginguard_app_user;

-- Grant sequence permissions
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO marginguard_app_user;

-- Completely isolate schema migration history and its sequence from the application runtime role.
-- marginguard_app_user must not read, insert, update, delete, or truncate migration records.
REVOKE ALL PRIVILEGES ON schema_migrations FROM marginguard_app_user;
REVOKE ALL PRIVILEGES ON SEQUENCE schema_migrations_id_seq FROM marginguard_app_user;

-- Explicitly revoke destructive privileges on append-only ledgers and audit tables.
-- Triggers also enforce immutability at the engine level, but REVOKE removes the
-- SQL-level privilege so a future trigger bypass cannot accidentally succeed.
REVOKE UPDATE, DELETE, TRUNCATE ON financial_ledger FROM marginguard_app_user;
REVOKE UPDATE, DELETE, TRUNCATE ON inventory_movements FROM marginguard_app_user;
REVOKE UPDATE, DELETE, TRUNCATE ON operational_audit_log FROM marginguard_app_user;
REVOKE TRUNCATE ON ALL TABLES IN SCHEMA public FROM marginguard_app_user;

-- Configure future default privileges so tables added by later migrations
-- automatically inherit the same least-privilege grants for marginguard_app_user.
-- Note: ALTER DEFAULT PRIVILEGES applies only to objects created by the CURRENT
-- role (the migration runner). Objects created by other roles are not affected.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO marginguard_app_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO marginguard_app_user;

-- =============================================================================
-- 14. SUPABASE DATA API PRIVILEGE HARDENING & LEAST-PRIVILEGE LOCKDOWN
-- =============================================================================
-- In Supabase, the platform preconfigures default privileges (pg_default_acl)
-- that automatically grant ALL PRIVILEGES on newly created tables and sequences,
-- and EXECUTE on newly created routines, to anon, authenticated, and service_role.
--
-- MarginGuard uses a private FastAPI + asyncpg backend architecture and does NOT
-- use the Supabase Data API (PostgREST). Because MarginGuard currently relies
-- on composite foreign keys and application-level tenant isolation rather than
-- Row-Level Security (RLS) policies, leaving these Data API grants intact would
-- expose the 21 application tables to direct HTTP access via PostgREST keys.
--
-- The statements below:
--   1. Revoke all privileges on existing public tables, sequences, and routines
--      from anon, authenticated, and service_role (safe for vanilla PostgreSQL).
--   2. Revoke future default privileges on tables, sequences, and routines
--      created by postgres in schema public from anon, authenticated, service_role.
--   3. Revoke EXECUTE on all existing public routines from PUBLIC.
--   4. Revoke future default EXECUTE on routines created by postgres from PUBLIC
--      globally (PostgreSQL's built-in default grant to PUBLIC is global).
--
-- VANILLA POSTGRESQL SAFETY:
-- In local or standard PostgreSQL, the roles 'anon', 'authenticated', and
-- 'service_role' do not exist. To ensure this migration applies cleanly in both
-- local development/testing and remote Supabase environments, all role-specific
-- revocations are wrapped in a robust dynamic PL/pgSQL loop that verifies role
-- existence before executing each statement.
-- =============================================================================

-- Part A: Revoke existing object privileges from Supabase Data API roles
DO $$
DECLARE
    target_role text;
    api_roles text[] := ARRAY['anon', 'authenticated', 'service_role'];
BEGIN
    FOREACH target_role IN ARRAY api_roles LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target_role) THEN
            EXECUTE format('REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM %I', target_role);
            EXECUTE format('REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM %I', target_role);
            EXECUTE format('REVOKE ALL PRIVILEGES ON ALL ROUTINES IN SCHEMA public FROM %I', target_role);
        END IF;
    END LOOP;
END;
$$;

-- Part B: Revoke future default privileges in schema public from Data API roles
DO $$
DECLARE
    target_role text;
    api_roles text[] := ARRAY['anon', 'authenticated', 'service_role'];
BEGIN
    FOREACH target_role IN ARRAY api_roles LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target_role) THEN
            EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM %I', target_role);
            EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %I', target_role);
            EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON ROUTINES FROM %I', target_role);
        END IF;
    END LOOP;
END;
$$;

-- Part C: Revoke EXECUTE from PUBLIC on all existing routines in schema public.
-- In PostgreSQL, functions are granted EXECUTE to PUBLIC by default. Trigger
-- functions (prevent_ledger_tampering, etc.) are executed by the PostgreSQL
-- engine via triggers; neither application users nor PUBLIC require direct EXECUTE.
REVOKE EXECUTE ON ALL ROUTINES IN SCHEMA public FROM PUBLIC;

-- Part D: Revoke future default EXECUTE from PUBLIC on routines created by postgres.
-- IMPORTANT SCOPE NOTE: In PostgreSQL, default EXECUTE on functions granted to
-- PUBLIC is a system-wide global default. ALTER DEFAULT PRIVILEGES FOR ROLE postgres
-- REVOKE EXECUTE ON ROUTINES FROM PUBLIC applies to any function created by postgres
-- across all schemas in this database, not solely within schema public.
-- This ensures no future routine created by the migration role is unintentionally
-- exposed to PUBLIC.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres REVOKE EXECUTE ON ROUTINES FROM PUBLIC;
