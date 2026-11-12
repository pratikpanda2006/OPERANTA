-- =============================================================================
-- OPERANTA Authentication & Session Schema (M14.0)
-- =============================================================================
-- Adds a minimal, secure owner/user table for production authentication.
-- Passwords are stored ONLY as salted PBKDF2-HMAC-SHA256 hashes (never
-- plaintext). Session tokens are server-signed (HMAC-SHA256) and contain the
-- resolved merchant_id so tenant scope is never taken from client input.

CREATE TABLE IF NOT EXISTS operanta_users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id    VARCHAR(64) NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    email          VARCHAR(255) NOT NULL,
    password_hash  TEXT        NOT NULL,
    display_name   VARCHAR(120),
    role           VARCHAR(32) NOT NULL DEFAULT 'owner',
    is_active      BOOLEAN    NOT NULL DEFAULT TRUE,
    last_login_at  TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_operanta_users_role CHECK (role IN ('owner', 'operator', 'viewer'))
);

-- Case-insensitive unique email (expression cannot be a table constraint).
CREATE UNIQUE INDEX IF NOT EXISTS uq_operanta_users_email
    ON operanta_users (lower(email));

CREATE INDEX IF NOT EXISTS idx_operanta_users_merchant
    ON operanta_users (merchant_id);
