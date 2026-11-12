-- =============================================================================
-- Migration: 003_fix_po_landed_cost_constraint.sql
-- Description: Replace the blanket per-order landed cost ceiling constraint on
--              purchase_orders with a status-aware variant that allows rejected
--              (and cancelled) purchase orders to persist their actual calculated
--              landed cost above $150 for auditability.
--
-- Motivation:
--   The original `chk_po_landed_cost_rule` constraint enforced
--     total_landed_cost IS NULL OR (total_landed_cost >= 0 AND total_landed_cost <= 150.00)
--   globally on every row, regardless of PO status.
--   The procurement policy correctly rejects POs that exceed $150, but it must
--   also persist the actual calculated cost (e.g. $180.20 for a 42-unit order)
--   so that the audit log and UI can explain WHY the order was rejected.
--   Silently replacing the rejected PO cost with NULL or $150.00 would
--   misrepresent the financial decision to the merchant.
--
-- New invariant:
--   • total_landed_cost IS NULL                        -- always OK (unknown-cost POs)
--   • total_landed_cost >= 0                           -- always required (no negatives)
--   • total_landed_cost <= 150.00                      -- required UNLESS status IN ('rejected','cancelled')
--
-- Spending-policy enforcement is NOT weakened:
--   The application-layer policy (evaluate_spending_policy in procurement_repo.py)
--   still blocks any non-rejected PO from exceeding $150.  This constraint only
--   removes the redundant DB-level ceiling for rows that have already been
--   rejected by that policy.
--
-- Target Engine: PostgreSQL 15+
-- =============================================================================

-- Step 1: Drop the existing constraint that blocks rejected POs from storing
--         their actual landed cost above $150.
ALTER TABLE purchase_orders
    DROP CONSTRAINT IF EXISTS chk_po_landed_cost_rule;

-- Step 2: Add the replacement constraint.
--   Semantics:
--     - NULL total_landed_cost is always permitted (has_unknown_costs path).
--     - Non-NULL total_landed_cost must be non-negative.
--     - Non-NULL total_landed_cost must be <= 150.00 UNLESS the PO is rejected
--       or cancelled (statuses that represent a completed negative decision).
ALTER TABLE purchase_orders
    ADD CONSTRAINT chk_po_landed_cost_rule CHECK (
        total_landed_cost IS NULL
        OR (
            total_landed_cost >= 0
            AND (
                total_landed_cost <= 150.00
                OR status IN ('rejected', 'cancelled')
            )
        )
    );
