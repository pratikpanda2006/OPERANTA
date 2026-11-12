"""
backend/app/api/v1/router.py
============================
Aggregates version 1 API endpoints for MarginGuard.
"""

from fastapi import APIRouter

from app.api.v1.endpoints.inventory import router as inventory_router
from app.api.v1.endpoints.procurement import router as procurement_router
from app.api.v1.endpoints.orders import router as orders_router
from app.api.v1.endpoints.payments import router as payments_router
from app.api.v1.endpoints.payments import webhook_router as paypal_webhook_router
from app.api.v1.endpoints.agent import router as agent_router
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.merchants import router as merchants_router

api_router = APIRouter()

api_router.include_router(inventory_router)
api_router.include_router(procurement_router)
api_router.include_router(orders_router)
api_router.include_router(payments_router)
api_router.include_router(paypal_webhook_router)
api_router.include_router(agent_router)
api_router.include_router(auth_router)
api_router.include_router(merchants_router)
