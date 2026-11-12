"""
backend/app/services/gemini/client.py
=====================================
Server-side Gemini client wrapper built on the official ``google-genai`` SDK.

Security & operational rules:
- The API key is read only from ``settings.gemini_api_key`` (SecretStr) and is
  never logged, rendered, or exposed to the client.
- Gemini calls happen only on the backend; the key is never shipped to React.
- The SDK is imported lazily so the app can boot without it installed and so
  automated tests run with a mocked client (no network, no key needed).
- Every call is bounded by a timeout; provider failures are sanitized.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_GEMINI_TIMEOUT_SECONDS = 1.0


class GeminiConfigurationError(RuntimeError):
    """Raised when Gemini is unavailable (missing key or SDK)."""


class GeminiTimeoutError(RuntimeError):
    """Raised when a Gemini call exceeds its timeout."""


class GeminiAPIError(RuntimeError):
    """Raised when a Gemini call fails for provider/sanitized reasons."""


class GeminiClient:
    """Thin async wrapper around ``google.genai`` for agent function calling."""

    def __init__(self, api_key: str, model: str, timeout_seconds: float = _GEMINI_TIMEOUT_SECONDS):
        if not api_key:
            raise GeminiConfigurationError("GEMINI_API_KEY is not configured.")
        self._sdk = self._load_sdk()
        self._client = self._sdk.Client(api_key=api_key)
        self.model = model
        self.timeout_seconds = timeout_seconds
        logger.info("Gemini agent client initialised (model=%s)", model)

    @staticmethod
    def _load_sdk() -> Any:
        try:
            from google import genai  # noqa: PLC0415
            return genai
        except Exception as exc:  # pragma: no cover - depends on env
            raise GeminiConfigurationError(
                "google-genai SDK is not installed. "
                "Install backend requirements (pip install -r backend/requirements.txt)."
            ) from exc

    async def chat(
        self,
        request: Dict[str, Any],
        tools: List[Dict[str, Any]],
    ) -> Any:
        """Run one Gemini turn with typed tools, bounded by a timeout."""
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._sync_generate, request, tools),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            logger.error("Gemini request timed out after %.1fs", self.timeout_seconds)
            raise GeminiTimeoutError(f"Gemini request timed out after {self.timeout_seconds:.1f}s.") from exc
        except GeminiConfigurationError:
            raise
        except Exception as exc:  # sanitize: never leak key or full response
            logger.error("Gemini API call failed (type=%s)", type(exc).__name__)
            raise GeminiAPIError("Gemini API call failed.") from exc

    def _sync_generate(self, request: Dict[str, Any], tools: List[Dict[str, Any]]) -> Any:
        return self._client.models.generate_content(
            model=self.model,
            contents=request["contents"],
            config={
                "tools": tools,
                "tool_config": {"function_calling_config": {"mode": "AUTO"}},
                "system_instruction": request.get("system_instruction"),
            },
        )

    async def aclose(self) -> None:
        """Best-effort close of the underlying SDK client."""
        closer = getattr(self._client, "aclose", None) or getattr(self._client, "close", None)
        if closer is None:
            return
        try:
            result = closer()
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # pragma: no cover - cleanup only
            pass
