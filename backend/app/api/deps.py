"""
backend/app/api/deps.py
=======================
FastAPI dependency providers for MarginGuard / OPERANTA API.

CRITICAL SECURITY POLICIES:
1. Multi-Tenant Scoping:
   - Tenant identity is resolved strictly server-side.
   - Client-supplied query parameters or headers (e.g. ?merchant_id=... or X-Merchant-Id)
     are NEVER trusted to determine tenant identity.
   - When DEMO_MODE=false and production authentication is not present, access is
     strictly DENIED (HTTP 403 Forbidden).
2. Database Connection Management:
   - Connections are acquired from `request.app.state.db_pool`.
   - If the database pool is unavailable, requests fail fast with HTTP 503.
3. Authentication (M14.0):
   - In production (DEMO_MODE=false), a valid signed OPERANTA session token is
     required; the merchant_id embedded in that token is used as tenant scope.
   - In demo mode, the server-configured DEMO_MERCHANT_ID is used.
"""

import logging
from typing import AsyncGenerator, Dict, Optional

import asyncpg
from fastapi import Depends, HTTPException, Request, status

from app.core.config import settings
from app.services.auth import verify_session_token

logger = logging.getLogger(__name__)


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    if not authorization:
        return None
    scheme, _, rest = authorization.partition(" ")
    if scheme.lower() != "bearer" or not rest.strip():
        return None
    return rest.strip()


def _session_secret() -> Optional[str]:
    if not settings.auth_session_secret:
        return None
    return settings.auth_session_secret.get_secret_value()


def get_merchant_context(request: Request) -> str:
    """
    Resolve and validate the active tenant identifier (server-side).

    SECURITY ARCHITECTURE:
    - Never accepts merchant_id from arbitrary client-supplied parameters or headers.
    - A valid signed OPERANTA session token takes precedence and its embedded
      merchant_id (``mid``) is authoritative.
    - If no valid session is present and DEMO_MODE is True with a configured
      DEMO_MERCHANT_ID, the server-side configured demo tenant is used (local
      development fallback only).
    - If no valid session is present and DEMO_MODE is False, access is denied (403).
    """
    secret = _session_secret()
    token = _extract_bearer_token(request.headers.get("Authorization")) if request else None
    payload = verify_session_token(secret, token) if (secret and token) else None
    if payload and payload.get("mid"):
        return payload["mid"]

    if settings.demo_mode:
        if not settings.demo_merchant_id:
            logger.error("Configuration error: DEMO_MODE is enabled but DEMO_MERCHANT_ID is not configured.")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Configuration error: DEMO_MODE is enabled but DEMO_MERCHANT_ID is not set.",
            )
        return settings.demo_merchant_id

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Tenant access denied: a valid signed session is required.",
    )


async def get_db(request: Request) -> AsyncGenerator[asyncpg.Connection, None]:
    """
    FastAPI dependency yielding an asyncpg connection from the application pool
    stored in `app.state.db_pool`. Releases connection back to pool upon completion.
    Strictly uses app.state.db_pool with zero module-level fallback.
    """
    pool: asyncpg.Pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service unavailable: connection pool is not initialised.",
        )

    async with pool.acquire() as conn:
        yield conn


async def get_current_user(request: Request, conn: asyncpg.Connection = Depends(get_db)) -> Dict:
    """
    FastAPI dependency resolving the authenticated user from the session token.

    Verifies the token signature/expiry, then loads the matching user row from
    the database. Raises 403 unless a valid, active user is found.
    """
    secret = _session_secret()
    token = _extract_bearer_token(request.headers.get("Authorization"))
    payload = verify_session_token(secret, token) if (secret and token) else None
    if not payload or not payload.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant access denied: a valid signed session is required.",
        )

    row = await conn.fetchrow(
        "SELECT id, merchant_id, email, display_name, role, is_active "
        "FROM operanta_users WHERE id = $1",
        payload["sub"],
    )
    if row is None or not row["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant access denied: account is unavailable.",
        )
    return {
        "id": str(row["id"]),
        "merchant_id": row["merchant_id"],
        "email": row["email"],
        "display_name": row["display_name"],
        "role": row["role"],
    }


async def get_paypal_client(
    request: Request,
    merchant_id: str = Depends(get_merchant_context),
    conn: asyncpg.Connection = Depends(get_db),
) -> AsyncGenerator["PayPalClient", None]:
    """
    FastAPI dependency providing a PayPal REST client for the current request,
    using the correctly resolved merchant credentials.

    - Northstar demo merchant uses the existing environment-based PayPal Sandbox
      credentials (unchanged behavior).
    - Normal SaaS merchants use their own encrypted merchant-specific credentials.
    - Merchants with no configured integration get a clear 409 "PayPal not
      configured" response. No credentials are ever exposed to the client.
    """
    from app.services.paypal_client import PayPalClient
    from app.services.paypal_credentials import resolve_paypal_credentials

    try:
        creds = await resolve_paypal_credentials(settings, merchant_id, conn)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="PayPal is not configured for this merchant. Connect your PayPal Sandbox to enable checkout.",
        )

    client = PayPalClient(
        settings,
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
        base_url=creds["base_url"],
    )
    try:
        yield client
    finally:
        await client.aclose()
