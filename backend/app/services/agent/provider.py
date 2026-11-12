"""
backend/app/services/agent/provider.py
=====================================
Request-scoped primary/secondary AI provider with sticky fallback.

The provider implements the same ``chat(request, tools)`` interface as the
Gemini client wrapper, so the AgentService orchestration does not change: it
continues to call ``await provider.chat(...)``.

Fallback semantics:
- Gemini is the primary provider and is always tried first.
- If Gemini times out or returns a provider error, the provider switches to
  OpenRouter for the remainder of the current request (sticky). It does NOT
  retry Gemini on each subsequent tool iteration.
- The provider instance is built fresh per HTTP request, so a new request
  starts on Gemini again.
- If OpenRouter is not configured and Gemini fails, the sanitized Gemini error
  is propagated.
"""

import logging
from typing import Any, List, Optional

from app.services.gemini.client import GeminiAPIError, GeminiTimeoutError
from app.services.openrouter.client import OpenRouterAPIError, OpenRouterAPITimeout

logger = logging.getLogger(__name__)

# Provider failures that trigger fallback.
_FALLBACK_TRIGGERS = (GeminiTimeoutError, GeminiAPIError)


class FallbackProvider:
    """Try Gemini first; on timeout/provider error switch to OpenRouter for the
    remainder of the current request (sticky)."""

    def __init__(self, gemini: Optional[Any] = None, openrouter: Optional[Any] = None):
        self.gemini = gemini
        self.openrouter = openrouter
        self._fallback_active = False

    async def chat(self, request: dict, tools: List[dict]) -> Any:
        if not self._fallback_active and self.gemini is not None:
            try:
                return await self.gemini.chat(request, tools)
            except _FALLBACK_TRIGGERS as exc:
                if self.openrouter is None:
                    # No backup: propagate the sanitized Gemini error.
                    raise
                logger.warning(
                    "Gemini failed (%s); switching to OpenRouter for this request.",
                    type(exc).__name__,
                )
                self._fallback_active = True
                return await self._openrouter_chat(request, tools)
            # Any other Gemini exception propagates untouched (sanitized upstream).

        if self._fallback_active or self.gemini is None:
            return await self._openrouter_chat(request, tools)

        raise RuntimeError("No AI provider is configured.")

    async def _openrouter_chat(self, request: dict, tools: List[dict]) -> Any:
        if self.openrouter is None:
            raise RuntimeError("OpenRouter backup is not configured.")
        try:
            return await self.openrouter.chat(request, tools)
        except (OpenRouterAPIError, OpenRouterAPITimeout) as exc:
            logger.error("OpenRouter call failed (type=%s)", type(exc).__name__)
            raise

    async def aclose(self) -> None:
        """Best-effort close of both underlying clients."""
        for client in (self.gemini, self.openrouter):
            if client is None:
                continue
            closer = getattr(client, "aclose", None)
            if closer is None:
                continue
            try:
                await closer()
            except Exception:  # pragma: no cover - cleanup only
                pass
