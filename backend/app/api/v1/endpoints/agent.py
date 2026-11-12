"""
backend/app/api/v1/endpoints/agent.py
=====================================
M13.0 Gemini business agent chat endpoint (with OpenRouter secondary provider).

Tenant identity is resolved purely server-side via ``get_merchant_context``;
the client message/history is never trusted for identity or spending authority.
"""

import logging
from typing import AsyncGenerator

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_db, get_merchant_context
from app.core.config import settings
from app.schemas.agent import AgentChatRequest, AgentChatResponse
from app.services.agent.provider import FallbackProvider
from app.services.agent.service import AgentService
from app.services.gemini.client import GeminiClient, GeminiConfigurationError
from app.services.openrouter.client import OpenRouterClient, OpenRouterConfigurationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["Business Agent"])


async def get_agent_provider() -> AsyncGenerator[FallbackProvider, None]:
    """Build a request-scoped provider: Gemini primary, OpenRouter backup.

    - OpenRouter-only operation is permitted if Gemini is unconfigured.
    - HTTP 503 is returned only when neither provider is configured.
    """
    gemini = None
    if settings.gemini_api_key:
        try:
            gemini = GeminiClient(
                settings.gemini_api_key.get_secret_value(), settings.gemini_model
            )
        except GeminiConfigurationError as exc:
            logger.warning("Gemini unavailable: %s", exc)
            gemini = None

    openrouter = None
    if settings.openrouter_api_key:
        try:
            openrouter = OpenRouterClient(
                settings.openrouter_api_key.get_secret_value(), settings.openrouter_model
            )
        except OpenRouterConfigurationError as exc:
            logger.warning("OpenRouter unavailable: %s", exc)
            openrouter = None

    if gemini is None and openrouter is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No AI provider is configured. Set GEMINI_API_KEY or OPENROUTER_API_KEY in backend/.env.",
        )

    provider = FallbackProvider(gemini=gemini, openrouter=openrouter)
    try:
        yield provider
    finally:
        await provider.aclose()


@router.post("/chat", response_model=AgentChatResponse)
async def agent_chat(
    req: AgentChatRequest,
    merchant_id: str = Depends(get_merchant_context),
    conn: asyncpg.Connection = Depends(get_db),
    provider: FallbackProvider = Depends(get_agent_provider),
) -> AgentChatResponse:
    service = AgentService(provider, conn, merchant_id)
    return await service.run(req.message, req.history, req.tool_profile)
