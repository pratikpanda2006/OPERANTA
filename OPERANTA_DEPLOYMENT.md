# OPERANTA — Deployment & Configuration Notes

This document captures the configuration and operational steps for the OPERANTA
authentication layer (M14.0). It exists because secret-bearing files such as
`backend/.env` are intentionally not modified by automation.

## How authentication works

- Users are stored in the `operanta_users` table (migration `005_operanta_auth.sql`).
- Passwords are hashed with salted PBKDF2-HMAC-SHA256 (`backend/app/services/auth.py`).
  Plaintext passwords are never stored or returned.
- On login, the server issues a signed session token (HMAC-SHA256 over a payload
  containing `user_id`, `merchant_id`, and an expiry). The token is self-contained
  so the tenant can be resolved synchronously.
- All API endpoints already depend on `get_merchant_context()` in
  `backend/app/api/deps.py`. In production (`DEMO_MODE=false`) that dependency now
  requires a valid signed session and returns the `merchant_id` embedded in the token.
  In demo mode the previous `DEMO_MERCHANT_ID` behaviour is preserved.

## Required backend configuration

Add to `backend/.env`:

```ini
# Disable the fixed demo merchant for real authentication
DEMO_MODE=false

# HMAC key used to sign session tokens — REQUIRED when DEMO_MODE=false
# Generate with:  python -c "import secrets; print(secrets.token_urlsafe(48))"
AUTH_SESSION_SECRET=<long-random-string>

# Optional: session lifetime in minutes (default 720)
AUTH_TOKEN_TTL_MINUTES=720
```

If `AUTH_SESSION_SECRET` is unset while `DEMO_MODE=false`, login returns HTTP 503
and protected endpoints return HTTP 403.

## Seeding the first owner account

```bash
# from the repository root
python backend/app/scripts/create_owner.py \
  --email owner@yourcompany.com \
  --password '<strong-password>' \
  --merchant-id merchant_northstar \
  --display-name "OPERANTA Owner"
```

Run it again to update the owner's password (idempotent). This is the supported
way to create the first operational (Northstar owner) account.

For normal end-user account creation, the public SaaS signup endpoint is
`POST /api/v1/auth/register` (see "SaaS registration & demo access" below). The
bootstrap script is **not** the SaaS signup mechanism.

## Verification / remaining steps

- The database migration `005_operanta_auth.sql` has been applied to the configured
  Supabase project; the schema is live.
- A bootstrap owner was created during verification. The credentials are not
  stored in this document — they were supplied privately during setup. If you
  need to reset the owner, re-run the bootstrap command above with a new
  password (see "Seeding the first owner account").
  - email `owner@<your-domain>` (placeholder — replace with the real address)
  - password `<strong-password>` (placeholder — never commit a real password)
  - **Change or remove this account before any production use.**

## Limitations / notes

- Session tokens are stateless (signed, expiring). There is no server-side
  revocation list; to revoke access, rotate `AUTH_SESSION_SECRET` or deactivate the
  user row (`is_active = false`), which `GET /api/v1/auth/me` and login enforce.
- The frontend stores the session token and user profile in `localStorage`
  (`operanta_session_token`, `operanta_user`).
- Existing business logic (spend caps, PO ceilings, allowlist, approvals, PayPal,
  agent tool permissions) is unchanged and remains enforced server-side.

---

# SaaS Registration, Demo Access & Merchant PayPal Integration (M15.0)

This section documents the dual-mode registration flow, the new environment
variables, and the merchant-specific PayPal credential model. It applies to
migration `006_operanta_saas_paypal.sql`.

## Dual-mode signup

`POST /api/v1/auth/register` accepts:

- `business_name`, `full_name`, `email`, `password`, `confirm_password`
- `workspace_access_code` (optional)

Two paths:

1. **DEMO access** — `workspace_access_code` equals the server-configured demo
   code. The user is attached to the **existing** `merchant_northstar` workspace
   with the restricted `operator` role. No new workspace is created, and no
   Northstar data is touched. The user is signed in immediately.
2. **Normal SaaS** — `workspace_access_code` is blank. The backend atomically
   creates a new isolated merchant workspace (new `merchant_id`, merchant row,
   starter catalog, and the user as `owner`). The user is signed in immediately.

Any other non-empty code is rejected (`400`). The frontend never performs the
authorization check and never ships the access code.

### New environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `OPERANTA_DEMO_ACCESS_CODE` | For demo access | The shared hackathon entry code (expected `DEMO`). Validated server-side with constant-time comparison. |
| `PAYPAL_CREDENTIALS_ENCRYPTION_KEY` | For normal SaaS PayPal storage | Fernet (AES-GCM) key used to encrypt merchant-specific PayPal client secrets at rest. If missing, normal-merchant PayPal storage fails safely with a clear error. |
| `AUTH_SESSION_SECRET` | Required when `DEMO_MODE=false` | HMAC session signing key (unchanged). |
| `DEMO_MODE`, `DEMO_MERCHANT_ID` | Demo/local fallback | Unchanged. A valid signed session always takes precedence over the demo fallback. |
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_MODE`, `PAYPAL_BASE_URL`, `PAYPAL_WEBHOOK_ID` | Northstar demo | Legacy environment-based Northstar PayPal credentials (unchanged). |

## PayPal credential resolution

- `merchant_northstar` continues to use the existing environment-based Sandbox
  credentials — untouched.
- Normal SaaS merchants store their own credentials in
  `merchant_paypal_integrations` (client secret encrypted with
  `PAYPAL_CREDENTIALS_ENCRYPTION_KEY`). Credentials are validated against PayPal
  before being saved, and decrypted only on the backend at request time.
- A merchant with no valid integration gets a clear "PayPal not configured"
  response; it never falls back to Northstar (or any other) credentials.

## Management endpoints

- `GET /api/v1/merchants/paypal-integration` — secret-free status.
- `POST /api/v1/merchants/paypal-integration` — connect/update (owner only; validated).
- `DELETE /api/v1/merchants/paypal-integration` — disconnect (owner only).

The Northstar demo merchant's global configuration can never be modified through
these endpoints (403).

## Deployment notes

- For a public deployment, set `DEMO_MODE=false` so every request requires a
  valid signed session. The demo access code is a deliberately shared hackathon
  entry code — it is not a replacement for authentication and is not a security
  boundary.
- Never print or expose `PAYPAL_CREDENTIALS_ENCRYPTION_KEY`,
  `AUTH_SESSION_SECRET` or any PayPal secret.
