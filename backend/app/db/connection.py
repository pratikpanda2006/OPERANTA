"""
backend/app/db/connection.py
============================
Manages the asyncpg connection pool for MarginGuard's application runtime.

KEY DESIGN DECISIONS:
- Uses DATABASE_URL (port 6543, Supabase transaction-mode pooler) for runtime operations.
- Sets statement_cache_size=0 because the transaction pooler does NOT support
  prepared statements — connections are handed off between requests, so the
  server-side prepared statement cache would be invalid on the next request.
- SSL is strictly required for remote hosts; connections to loopback addresses
  (127.0.0.1, [::1], localhost) may disable SSL when local tests run without TLS.
- The pool is initialised once at application startup and stored in `app.state.db_pool`,
  then cleanly torn down at shutdown via FastAPI's lifespan context manager.
- Credentials are NEVER logged — only the masked safe_database_url is used.

DATABASE ROLE SEPARATION:
- `marginguard_app_user` is a NOLOGIN permission role created by migration 001.
  It carries least-privilege DML grants on domain tables and explicitly lacks LOGIN.
- Runtime connections authenticate using a dedicated LOGIN role (e.g. `marginguard_runtime`)
  provisioned in Supabase via Dashboard or Management API, which inherits permissions via:
      GRANT marginguard_app_user TO <runtime_login_role>;
- The application runtime MUST NOT authenticate as `postgres` or weaken the NOLOGIN
  boundary of `marginguard_app_user`.
"""

import logging
import urllib.parse
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

import asyncpg

from app.core.config import settings

logger = logging.getLogger(__name__)

# Module-level pool reference for backwards-compatibility and non-FastAPI callers
_pool: Optional[asyncpg.Pool] = None  # type: ignore[type-arg]


async def init_db_pool(
    dsn_override: Optional[str] = None,
    min_size: Optional[int] = None,
    max_size: Optional[int] = None,
) -> Optional[asyncpg.Pool]:  # type: ignore[type-arg]
    """
    Create the asyncpg connection pool and return it.

    Called during application lifespan startup. If DATABASE_URL is not configured
    (and no override is provided), returns None without raising, allowing the app
    to start in supported unconfigured / health-check-only mode.

    Why statement_cache_size=0?
        Supabase's transaction-mode pooler (port 6543) uses PgBouncer in
        transaction mode. Setting cache size to 0 tells asyncpg to send
        statements as simple queries rather than preparing them first.
    """
    global _pool

    raw_dsn = dsn_override or (settings.database_url.get_secret_value() if settings.database_url else None)
    if not raw_dsn:
        logger.warning(
            "DATABASE_URL is not configured. "
            "The database pool was not initialised. "
            "Application running in unconfigured / health-check-only mode."
        )
        return None

    # Determine SSL requirement: remote hosts require SSL; local loopback allows plain TLS.
    # NOTE: IPv6 loopback must be written as [::1] in a DSN URL per RFC 3986.
    # urllib.parse.urlsplit returns hostname="::1" (without brackets) for [::1],
    # and hostname=None for bare ::1 (which is not a valid URL).  Both are treated
    # as loopback.  All other hostnames (including None from an unparseable non-loopback
    # DSN) default to ssl="require" for safety.
    parsed = urllib.parse.urlsplit(raw_dsn)
    hostname = (parsed.hostname or "").lower()
    is_loopback = hostname in ("127.0.0.1", "::1", "localhost")
    ssl_setting = False if is_loopback else "require"

    masked_url = settings.mask_connection_string(settings.database_url) if not dsn_override else "[OVERRIDE_URL]"
    logger.info("Initialising asyncpg connection pool → %s", masked_url)

    pool_min = min_size or settings.db_min_connections
    pool_max = max_size or settings.db_max_connections

    pool = await asyncpg.create_pool(
        dsn=raw_dsn,
        min_size=pool_min,
        max_size=pool_max,
        statement_cache_size=0,
        ssl=ssl_setting,
    )

    _pool = pool
    logger.info("asyncpg pool ready (min=%d, max=%d).", pool_min, pool_max)
    return pool


async def close_db_pool(pool: Optional[asyncpg.Pool] = None) -> None:  # type: ignore[type-arg]
    """
    Gracefully drain and close the asyncpg connection pool.

    Called during application lifespan shutdown. Idempotent.
    """
    global _pool

    target_pool = pool or _pool
    if target_pool is None:
        logger.debug("close_db_pool called but pool is None; skipping.")
        return

    logger.info("Closing asyncpg connection pool…")
    await target_pool.close()
    if target_pool is _pool:
        _pool = None
    logger.info("asyncpg connection pool closed.")


def is_pool_ready(pool: Optional[asyncpg.Pool] = None) -> bool:  # type: ignore[type-arg]
    """
    Return True if the specified pool (or global fallback) is active and open.
    """
    target = pool or _pool
    if target is None:
        return False
    # Check if pool has been closed
    return not getattr(target, "_closed", False)


@asynccontextmanager
async def get_connection(
    pool_override: Optional[asyncpg.Pool] = None,  # type: ignore[type-arg]
) -> AsyncGenerator[asyncpg.Connection, None]:  # type: ignore[type-arg]
    """
    Async context manager that acquires a connection from the pool, yields it,
    and returns it to the pool when the `async with` block exits.
    """
    target_pool = pool_override or _pool
    if target_pool is None:
        raise RuntimeError(
            "Database pool is not initialised. "
            "Ensure DATABASE_URL is set and init_db_pool() was awaited at startup."
        )

    async with target_pool.acquire() as connection:
        yield connection
