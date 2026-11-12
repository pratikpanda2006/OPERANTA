-- =============================================================================
-- Migration: 004_paypal_online_checkout.sql
-- Description: Support the M12.0 PayPal Sandbox customer checkout lifecycle.
--
-- Changes:
--   1. Extend the `orders` status CHECK to include `'pending'`.
--      M12 creates ONE internal order BEFORE initiating PayPal checkout.  The
--      order is stored in the `pending` state until its payment is captured
--      (either by the capture endpoint or the verified webhook).  On capture,
--      the same internal order transitions to `completed`.  No second internal
--      order is ever created during capture.
--   2. Add `orders.paypal_order_id` to associate the PayPal Order with the
--      internal order (unique per merchant when populated).
--
-- This migration does NOT modify any previously applied migration.  It is an
-- additive, standalone change applied by the standard runner.
-- =============================================================================

-- Step 1: Relax the order status constraint to allow the pending checkout state.
ALTER TABLE orders
    DROP CONSTRAINT IF EXISTS chk_order_status;

ALTER TABLE orders
    ADD CONSTRAINT chk_order_status CHECK (
        status IN (
            'pending',
            'completed',
            'cancelled',
            'refunded',
            'partially_refunded'
        )
    );

-- Step 2: Associate the PayPal Order id with the internal order.
ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS paypal_order_id VARCHAR(128);

CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_paypal_order
    ON orders (merchant_id, paypal_order_id)
    WHERE paypal_order_id IS NOT NULL;
