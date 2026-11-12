-- =============================================================================
-- OPERANTA SaaS — Merchant-specific PayPal integration storage (M15.0)
-- =============================================================================
-- Adds an additive table holding OPTIONAL PayPal Sandbox credentials for NORMAL
-- SaaS merchants. The Northstar demo merchant (merchant_northstar) is NOT
-- stored here; it keeps using the existing environment-based PayPal credentials
-- (PAYPAL_CLIENT_ID / PAYYPAL_CLIENT_SECRET) so its integration is untouched.
--
-- Security properties:
--   * client_secret is stored ONLY as an authenticated-encryption ciphertext
--     (AES-GCM via Fernet) keyed by PAYPAL_CREDENTIALS_ENCRYPTION_KEY.
--   * The decrypted secret is never exposed to the frontend or returned by any
--     API response; it is decrypted on the backend only at request time.
--   * No temporary OAuth tokens are ever persisted here.
--
-- This is a purely additive migration. It does not modify any earlier migration
-- and can be applied safely to the existing database.

-- =============================================================================
-- merchant_paypal_integrations
-- =============================================================================
CREATE TABLE IF NOT EXISTS merchant_paypal_integrations (
    merchant_id                 VARCHAR(64) PRIMARY KEY REFERENCES merchants(id) ON DELETE RESTRICT,
    environment                 VARCHAR(16) NOT NULL DEFAULT 'sandbox',
    client_id                   TEXT        NOT NULL,
    encrypted_client_secret     TEXT        NOT NULL,
    webhook_id                  VARCHAR(128),
    connection_status           VARCHAR(20) NOT NULL DEFAULT 'connected',
    verified_at                 TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_mpi_environment CHECK (environment IN ('sandbox', 'live')),
    CONSTRAINT chk_mpi_status CHECK (connection_status IN ('connected', 'disconnected'))
);
