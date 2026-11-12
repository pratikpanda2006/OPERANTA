"""
backend/app/api/v1/endpoints/auth.py
====================================
OPERANTA authentication endpoints.

- ``POST /api/v1/auth/login`` verifies credentials against salted PBKDF2 hashes
  and issues a signed session token.
- ``GET /api/v1/auth/me`` returns the currently authenticated user.

Tenant scope is always resolved from the signed token (server-side), never from
client-supplied headers or parameters.
"""

import hmac
import logging
import uuid
from typing import Optional, Dict

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.schemas.auth import LoginRequest, LoginResponse, RegisterRequest, UserPublic
from app.services.auth import create_session_token, hash_password, verify_password
from app.services.merchant_provisioning import new_merchant_id, provision_starter_workspace

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _session_secret() -> str:
    secret = settings.auth_session_secret.get_secret_value() if settings.auth_session_secret else None
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured on this server.",
        )
    return secret


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, conn: asyncpg.Connection = Depends(get_db)) -> LoginResponse:
    row = await conn.fetchrow(
        "SELECT id, merchant_id, email, display_name, role, is_active, password_hash "
        "FROM operanta_users WHERE lower(email) = lower($1)",
        req.email.strip(),
    )
    if row is None or not row["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password."
        )
    if not verify_password(req.password, row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password."
        )

    secret = _session_secret()
    token = create_session_token(
        secret, str(row["id"]), row["merchant_id"], settings.auth_token_ttl_minutes
    )
    await conn.execute("UPDATE operanta_users SET last_login_at = now() WHERE id = $1", row["id"])

    user = UserPublic(
        id=str(row["id"]),
        merchant_id=row["merchant_id"],
        email=row["email"],
        display_name=row["display_name"],
        role=row["role"],
    )
    return LoginResponse(access_token=token, user=user)


@router.get("/me", response_model=UserPublic)
async def me(user: Dict = Depends(get_current_user)) -> UserPublic:
    return UserPublic(**user)


@router.post("/register", response_model=LoginResponse)
async def register(req: RegisterRequest, conn: asyncpg.Connection = Depends(get_db)) -> LoginResponse:
    """
    SaaS signup (M15.0).

    - ``workspace_access_code`` blank -> creates a brand-new isolated merchant
      workspace; the new user becomes its owner.
    - ``workspace_access_code`` == server-configured demo code -> the user is
      routed to the existing ``merchant_northstar`` demo workspace as a
      restricted operator. No new workspace is created and no Northstar data is
      touched.
    - Any other non-empty code -> rejected as invalid.

    The merchant_id and role are ALWAYS decided server-side; they are never
    accepted from client input.
    """
    secret = _session_secret()
    email = req.email.strip().lower()
    access_code = (req.workspace_access_code or "").strip()

    # Duplicate email check (case-insensitive unique index also enforces this).
    existing = await conn.fetchval(
        "SELECT 1 FROM operanta_users WHERE lower(email) = $1", email
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    # Resolve target workspace (server-side decision).
    if access_code:
        demo_code = settings.demo_access_code
        if not demo_code or not hmac.compare_digest(access_code, demo_code):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid workspace access code.",
            )
        merchant_id = settings.demo_merchant_id
        role = "operator"
        demo_merchant = await conn.fetchval(
            "SELECT id FROM merchants WHERE id = $1", merchant_id
        )
        if not demo_merchant:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="The demo workspace is not available on this server.",
            )
    else:
        merchant_id = new_merchant_id()
        role = "owner"

    password_hash = hash_password(req.password)

    try:
        async with conn.transaction():
            if role == "owner":
                # New independent SaaS merchant workspace + starter catalog.
                await conn.execute(
                    "INSERT INTO merchants (id, name) VALUES ($1, $2)",
                    merchant_id,
                    req.business_name.strip(),
                )
                await provision_starter_workspace(conn, merchant_id)

            user_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO operanta_users
                    (id, merchant_id, email, password_hash, display_name, role)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                user_id,
                merchant_id,
                email,
                password_hash,
                req.full_name.strip(),
                role,
            )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        ) from None

    token = create_session_token(
        secret, str(user_id), merchant_id, settings.auth_token_ttl_minutes
    )
    return LoginResponse(
        access_token=token,
        user=UserPublic(
            id=str(user_id),
            merchant_id=merchant_id,
            email=email,
            display_name=req.full_name.strip(),
            role=role,
        ),
    )
