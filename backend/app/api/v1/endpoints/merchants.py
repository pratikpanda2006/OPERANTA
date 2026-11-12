"""
backend/app/api/v1/endpoints/merchants.py
===========================================
Merchant-level settings endpoints for OPERANTA.

Currently exposes the optional, merchant-specific PayPal Sandbox integration
management for NORMAL SaaS merchants:

- ``GET /api/v1/merchants/paypal-integration``  -> public status (secret-free)
- ``POST /api/v1/merchants/paypal-integration`` -> connect / update (validated)
- ``DELETE /api/v1/merchants/paypal-integration`` -> disconnect

Security:
- Only the authenticated merchant's OWN integration can be read/modified.
- The Northstar demo merchant's global environment-based PayPal config can never
  be modified through these endpoints (403).
- Credentials are validated against PayPal before being stored, encrypted at
  rest (Fernet/AES-GCM), and never returned to the frontend.
"""

from typing import Dict, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user, get_db, get_merchant_context
from app.core.config import settings
from app.schemas.auth import PayPalIntegrationStatus, PayPalIntegrationUpsert
from app.services.paypal_client import PayPalAPIError, PayPalAuthError, PayPalClient
from app.services.paypal_credentials import (
    encrypt_secret,
    mask_client_id,
    _get_encryption_key,
)

router = APIRouter(prefix="/merchants", tags=["merchants"])

_PAYPAL_SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"


def _mask_webhook_id(webhook_id: Optional[str]) -> Optional[str]:
    if not webhook_id:
        return None
    if len(webhook_id) <= 8:
        return "****"
    return webhook_id[:4] + "…" + webhook_id[-4:]


@router.get("/paypal-integration", response_model=PayPalIntegrationStatus)
async def get_paypal_integration(
    user: Dict = Depends(get_current_user),
    merchant_id: str = Depends(get_merchant_context),
    conn: asyncpg.Connection = Depends(get_db),
) -> PayPalIntegrationStatus:
    """Return the secret-free status of the authenticated merchant's PayPal integration."""
    # Northstar demo merchant: report the existing environment-based Sandbox status.
    if merchant_id == settings.demo_merchant_id:
        connected = bool(
            settings.paypal_client_id and settings.paypal_client_secret
        )
        return PayPalIntegrationStatus(
            merchant_id=merchant_id,
            connected=connected,
            environment="sandbox",
            client_id_masked=(
                mask_client_id(settings.paypal_client_id.get_secret_value())
                if connected
                else None
            ),
        )

    row = await conn.fetchrow(
        "SELECT environment, client_id, webhook_id, connection_status, verified_at "
        "FROM merchant_paypal_integrations WHERE merchant_id = $1",
        merchant_id,
    )
    if row is None or row["connection_status"] != "connected":
        return PayPalIntegrationStatus(merchant_id=merchant_id, connected=False)

    return PayPalIntegrationStatus(
        merchant_id=merchant_id,
        connected=True,
        environment=row["environment"],
        client_id_masked=mask_client_id(row["client_id"]),
        webhook_id_masked=_mask_webhook_id(row["webhook_id"]),
        verified_at=row["verified_at"].isoformat() if row["verified_at"] else None,
    )


@router.post("/paypal-integration", response_model=PayPalIntegrationStatus)
async def upsert_paypal_integration(
    payload: PayPalIntegrationUpsert,
    user: Dict = Depends(get_current_user),
    merchant_id: str = Depends(get_merchant_context),
    conn: asyncpg.Connection = Depends(get_db),
) -> PayPalIntegrationStatus:
    """Connect or update the authenticated merchant's own PayPal Sandbox integration."""
    if merchant_id == settings.demo_merchant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The demo workspace uses its shared Northstar PayPal configuration and cannot be modified.",
        )
    if user.get("role") != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the workspace owner can manage PayPal integration credentials.",
        )

    # Validate credentials against PayPal before storing anything.
    base_url = _PAYPAL_SANDBOX_BASE_URL if payload.environment == "sandbox" else "https://api-m.paypal.com"
    probe = PayPalClient(
        settings,
        client_id=payload.client_id,
        client_secret=payload.client_secret,
        base_url=base_url,
    )
    try:
        await probe.get_access_token()
    except (PayPalAuthError, PayPalAPIError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PayPal rejected these Sandbox credentials. Please verify your Client ID and Client Secret.",
        ) from exc
    finally:
        await probe.aclose()

    key = _get_encryption_key(settings)
    encrypted_secret = encrypt_secret(payload.client_secret, key)

    await conn.execute(
        """
        INSERT INTO merchant_paypal_integrations
            (merchant_id, environment, client_id, encrypted_client_secret, webhook_id,
             connection_status, verified_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, 'connected', now(), now())
        ON CONFLICT (merchant_id) DO UPDATE SET
            environment = EXCLUDED.environment,
            client_id = EXCLUDED.client_id,
            encrypted_client_secret = EXCLUDED.encrypted_client_secret,
            webhook_id = EXCLUDED.webhook_id,
            connection_status = 'connected',
            verified_at = now(),
            updated_at = now()
        """,
        merchant_id,
        payload.environment,
        payload.client_id,
        encrypted_secret,
        payload.webhook_id,
    )

    return PayPalIntegrationStatus(
        merchant_id=merchant_id,
        connected=True,
        environment=payload.environment,
        client_id_masked=mask_client_id(payload.client_id),
        webhook_id_masked=_mask_webhook_id(payload.webhook_id),
        verified_at=None,
    )


@router.delete("/paypal-integration", response_model=PayPalIntegrationStatus)
async def disconnect_paypal_integration(
    user: Dict = Depends(get_current_user),
    merchant_id: str = Depends(get_merchant_context),
    conn: asyncpg.Connection = Depends(get_db),
) -> PayPalIntegrationStatus:
    """Disconnect the authenticated merchant's PayPal integration (mark disconnected)."""
    if merchant_id == settings.demo_merchant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The demo workspace uses its shared Northstar PayPal configuration and cannot be modified.",
        )
    if user.get("role") != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the workspace owner can manage PayPal integration credentials.",
        )

    await conn.execute(
        "UPDATE merchant_paypal_integrations SET connection_status = 'disconnected', updated_at = now() "
        "WHERE merchant_id = $1 AND connection_status = 'connected'",
        merchant_id,
    )
    return PayPalIntegrationStatus(merchant_id=merchant_id, connected=False)
