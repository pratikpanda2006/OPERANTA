"""
backend/app/main.py
===================
MarginGuard FastAPI Application Entrypoint.

Manages:
- Application lifespan (connection pool startup and graceful shutdown).
- CORS configuration for the React + Vite frontend.
- Health and readiness endpoints.
- Versioned API routing under /api/v1.
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.connection import close_db_pool, init_db_pool, is_pool_ready

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.
    Initialises the database pool during application startup and cleanly
    drains it during shutdown.
    """
    logger.info("MarginGuard backend starting up…")
    pool = None
    try:
        pool = await init_db_pool()
        app.state.db_pool = pool
    except Exception as e:
        logger.error("Failed to initialize database pool during startup: %s", e)
        # If the pool was acquired but the state assignment never completed, close
        # the partially-created pool so a later retry does not leak connections.
        if pool is not None:
            try:
                await close_db_pool(pool)
            except Exception as cleanup_exc:  # pragma: no cover - best-effort
                logger.warning("Failed to close partially-created pool during startup error handling: %s", cleanup_exc)
        app.state.db_pool = None
        # In configured mode where DATABASE_URL is set, failure to connect should fail startup
        if settings.database_url:
            raise

    yield

    logger.info("MarginGuard backend shutting down…")
    pool = getattr(app.state, "db_pool", None)
    await close_db_pool(pool)
    app.state.db_pool = None


# Initialize FastAPI application with lifespan management
app = FastAPI(
    title="MarginGuard API",
    description="Backend API for MarginGuard autonomous retail operations.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS configuration allowing the React + Vite frontend
origins = [
    "http://localhost:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    """
    Liveness health check endpoint.
    Maintains exact JSON response schema consumed by frontend:
    {status, service, version, pilot_merchant}.
    """
    return {
        "status": "healthy",
        "service": "marginguard-backend",
        "version": "0.1.0",
        "pilot_merchant": "Northstar Living",
    }


@app.get("/health/ready")
async def readiness_check(request: Request, response: Response):
    """
    Readiness health check endpoint.
    Validates whether the application can serve database-backed requests.
    Never exposes internal hostnames, passwords, or raw exceptions.
    """
    pool = getattr(request.app.state, "db_pool", None)

    # Supported unconfigured mode (e.g. initial setup without database)
    if not settings.database_url:
        return {
            "status": "ready",
            "database": "unconfigured",
            "version": "0.1.0",
        }

    # DATABASE_URL is set, but pool is not available
    if pool is None or not is_pool_ready(pool):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "not_ready",
            "database": "disconnected",
            "version": "0.1.0",
        }

    # Ping database with a lightweight probe
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {
            "status": "ready",
            "database": "connected",
            "version": "0.1.0",
        }
    except Exception as e:
        logger.warning("Readiness probe database ping failed: %s", e)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "not_ready",
            "database": "unavailable",
            "version": "0.1.0",
        }


# Mount Version 1 API routes
app.include_router(api_router, prefix="/api/v1")
