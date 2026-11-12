"""
backend/app/services/openrouter/client.py
=========================================
Server-side OpenRouter (OpenAI-compatible) client used as the secondary AI
provider for the business agent. It translates between the internal Gemini-style
request/response shapes (used by the AgentService orchestration) and the OpenAI
chat-completions wire format.

Security & operational rules:
- The API key is read only from ``settings.openrouter_api_key`` (SecretStr) and
  is never logged, rendered, or exposed to the client.
- Requests go out over HTTPS with a bounded timeout; provider errors are
  sanitized and never leak the key or raw bodies.
- No tools are executed here. This adapter only formats requests and normalizes
  responses; tool execution stays in the AgentService tool-execution loop.
"""

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_OPENROUTER_TIMEOUT_SECONDS = 30.0
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterConfigurationError(RuntimeError):
    """Raised when OpenRouter is unavailable (missing key)."""


class OpenRouterAPITimeout(RuntimeError):
    """Raised when an OpenRouter call exceeds its timeout."""


class OpenRouterAPIError(RuntimeError):
    """Raised when an OpenRouter call fails for provider/sanitized reasons."""


def _lowercase_schema(obj: Any) -> Any:
    """Recursively lowercase JSON-schema type names (OBJECT -> object, etc.)."""
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for key, value in obj.items():
            if key == "type" and isinstance(value, str):
                out[key] = value.lower()
            else:
                out[key] = _lowercase_schema(value)
        return out
    if isinstance(obj, list):
        return [_lowercase_schema(item) for item in obj]
    return obj


def translate_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert Gemini ``function_declarations`` containers to OpenAI tool schemas.

    Gemini passes tools as ``[{"function_declarations": [...]}]``. OpenRouter
    (OpenAI-compatible) expects ``[{"type": "function", "function": {...}}]``.
    JSON-schema type names are normalized to lowercase recursively.
    """
    result: List[Dict[str, Any]] = []
    for container in tools:
        declarations = container.get("function_declarations") or []
        for decl in declarations:
            fn: Dict[str, Any] = {}
            name = decl.get("name")
            if name is not None:
                fn["name"] = name
            description = decl.get("description")
            if description is not None:
                fn["description"] = description
            params = decl.get("parameters")
            if params:
                fn["parameters"] = _lowercase_schema(params)
            result.append({"type": "function", "function": fn})
    return result


def translate_contents(contents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert the internal Gemini-style ``contents`` list to OpenAI messages.

    Mapping:
    - ``role: model`` -> assistant; function-call parts become ``tool_calls``
      (preserving their IDs), text parts become the assistant content.
    - ``role: user`` with text parts -> user message.
    - ``role: user`` with ``functionResponse`` parts -> ``tool`` messages whose
      ``tool_call_id`` matches the preserved call ID.
    """
    messages: List[Dict[str, Any]] = []
    # Sequence counter used to synthesize stable tool-call IDs when the upstream
    # Gemini response did not include one (OpenAI requires a non-null tool_call_id).
    seq = 0

    def next_call_id() -> str:
        nonlocal seq
        seq += 1
        return f"call_{seq}"

    for item in contents:
        role = item.get("role")
        parts = item.get("parts") or []
        if role == "model":
            text_parts = [
                p.get("text") for p in parts if isinstance(p, dict) and p.get("text")
            ]
            calls: List[Dict[str, Any]] = []
            for p in parts:
                if not isinstance(p, dict):
                    continue
                fc = p.get("functionCall")
                if not isinstance(fc, dict) or not fc.get("name"):
                    continue
                call: Dict[str, Any] = {
                    "id": fc.get("id") or next_call_id(),
                    "type": "function",
                    "function": {
                        "name": fc["name"],
                        "arguments": json.dumps(fc.get("args") or {}),
                    },
                }
                calls.append(call)
            msg: Dict[str, Any] = {"role": "assistant"}
            if text_parts:
                msg["content"] = "\n".join(text_parts)
            if calls:
                msg["tool_calls"] = calls
                # OpenAI requires a content field (may be empty) when tool_calls
                # are present; omit it to avoid a provider rejection.
                msg.setdefault("content", "")
            messages.append(msg)
        elif role == "user":
            has_tool_response = any(
                isinstance(p, dict) and "functionResponse" in p for p in parts
            )
            if has_tool_response:
                for p in parts:
                    if not isinstance(p, dict):
                        continue
                    fr = p.get("functionResponse")
                    if not isinstance(fr, dict):
                        continue
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": fr.get("id") or next_call_id(),
                            "content": json.dumps(fr.get("response") or {}, default=str),
                        }
                    )
            else:
                text = "\n".join(
                    p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")
                )
                if text:
                    messages.append({"role": "user", "content": text})
        else:
            # Unexpected role: keep any text as a user turn to stay lossless.
            text = "\n".join(
                p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")
            )
            if text:
                messages.append({"role": "user", "content": text})
    return messages


def normalize_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize an OpenAI chat-completions response into the canonical Gemini
    shape consumed by ``parse_function_calls`` / ``extract_text``.

    Canonical shape: ``{"candidates": [{"content": {"parts": [...]}}]}`` where
    tool-call parts are ``{"functionCall": {"name", "args", "id"}}`` and text
    parts are ``{"text": ...}``.
    """
    choices = data.get("choices") or []
    if not choices:
        return {"candidates": []}
    message = choices[0].get("message") or {}
    parts: List[Dict[str, Any]] = []
    content = message.get("content")
    if content:
        parts.append({"text": content})
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        args_raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(args_raw)
            if not isinstance(args, dict):
                args = {}
        except (ValueError, TypeError):
            args = {}
        fc: Dict[str, Any] = {"name": name, "args": args}
        call_id = tc.get("id")
        if call_id is not None:
            fc["id"] = call_id
        parts.append({"functionCall": fc})
    if not parts:
        return {"candidates": []}
    return {"candidates": [{"content": {"parts": parts}}]}


class OpenRouterClient:
    """Async HTTP client for the OpenRouter (OpenAI-compatible) API."""

    def __init__(
        self,
        api_key: str,
        model: str = "openrouter/free",
        timeout_seconds: float = _OPENROUTER_TIMEOUT_SECONDS,
        http: Optional[httpx.AsyncClient] = None,
    ):
        if not api_key:
            raise OpenRouterConfigurationError("OPENROUTER_API_KEY is not configured.")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds))

    async def chat(
        self,
        request: Dict[str, Any],
        tools: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Run one OpenRouter turn, bounded by a timeout. Returns canonical dict."""
        messages = translate_contents(request.get("contents") or [])
        system = request.get("system_instruction")
        if system:
            messages = [{"role": "system", "content": system}] + messages

        payload: Dict[str, Any] = {"model": self.model, "messages": messages}
        openai_tools = translate_tools(tools)
        if openai_tools:
            payload["tools"] = openai_tools
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            try:
                resp = await self._http.post(
                    _OPENROUTER_BASE_URL + "/chat/completions",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
            except httpx.TimeoutException as exc:
                logger.error("OpenRouter request timed out after %.1fs", self.timeout_seconds)
                raise OpenRouterAPITimeout(
                    f"OpenRouter request timed out after {self.timeout_seconds:.1f}s."
                ) from exc
            except httpx.HTTPStatusError as exc:
                # Sanitize: never include body/headers (may contain sensitive data).
                status_code = getattr(exc.response, "status_code", None)
                logger.error("OpenRouter API error (status=%s)", status_code)
                raise OpenRouterAPIError(
                    f"OpenRouter API error (status={status_code})."
                ) from exc
            except httpx.TransportError as exc:
                logger.error("OpenRouter transport error (type=%s)", type(exc).__name__)
                raise OpenRouterAPIError("OpenRouter transport error.") from exc

            try:
                data = resp.json()
            except ValueError as exc:
                raise OpenRouterAPIError("OpenRouter returned invalid JSON.") from exc
            return normalize_response(data)
        except (OpenRouterAPITimeout, OpenRouterAPIError):
            raise
        except Exception as exc:
            # Defensive sanitization: never let a raw provider exception escape
            # (its repr can embed the request URL and Authorization header).
            logger.error("OpenRouter call failed (type=%s)", type(exc).__name__)
            raise OpenRouterAPIError("OpenRouter API call failed.") from exc

    async def aclose(self) -> None:
        """Best-effort close of the underlying HTTP client."""
        try:
            await self._http.aclose()
        except Exception:  # pragma: no cover - cleanup only
            pass
