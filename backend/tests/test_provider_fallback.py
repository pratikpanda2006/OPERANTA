"""
backend/tests/test_provider_fallback.py
=======================================
Offline tests for the request-scoped primary/secondary provider fallback
(FallbackProvider). No live network calls.
"""

import contextlib

import pytest

from app.services.agent.provider import FallbackProvider
from app.services.agent.service import AgentService, extract_text
from app.services.gemini.client import GeminiTimeoutError


class FakeGeminiOk:
    def __init__(self, text="gemini-answer"):
        self.text = text
        self.calls = []

    async def chat(self, request, tools):
        self.calls.append(1)
        return {"candidates": [{"content": {"parts": [{"text": self.text}]}}]}


class FakeGeminiFail:
    def __init__(self):
        self.calls = []

    async def chat(self, request, tools):
        self.calls.append(1)
        raise GeminiTimeoutError("timeout")


class FakeGeminiFunctionCallToText:
    def __init__(self):
        self.calls = []

    async def chat(self, request, tools):
        self.calls.append(1)
        if len(self.calls) == 1:
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "get_inventory",
                                        "args": {"product_id": "prod_blue_vase"},
                                        "id": "call_1",
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        return {"candidates": [{"content": {"parts": [{"text": "final"}]}}]}


class FakeOpenRouter:
    def __init__(self, text="openrouter-answer"):
        self.text = text
        self.calls = []

    async def chat(self, request, tools):
        self.calls.append(1)
        return {"candidates": [{"content": {"parts": [{"text": self.text}]}}]}


class Conn:
    @contextlib.asynccontextmanager
    async def transaction(self):
        yield self

    async def fetchrow(self, sql, *args):
        return None

    async def fetch(self, sql, *args):
        return []

    async def execute(self, sql, *args):
        return "OK"


def _text(resp):
    return extract_text(resp)


# ---------------------------------------------------------------------------
# Fallback behavior
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gemini_primary_when_success():
    gemini = FakeGeminiOk()
    openrouter = FakeOpenRouter()
    provider = FallbackProvider(gemini=gemini, openrouter=openrouter)

    resp = await provider.chat({}, [])
    assert _text(resp) == "gemini-answer"
    assert len(gemini.calls) == 1
    assert openrouter.calls == []

    # Subsequent iterations stay on Gemini when it succeeds.
    await provider.chat({}, [])
    assert len(gemini.calls) == 2
    assert openrouter.calls == []


@pytest.mark.asyncio
async def test_fallback_activates_after_gemini_failure():
    gemini = FakeGeminiFail()
    openrouter = FakeOpenRouter()
    provider = FallbackProvider(gemini=gemini, openrouter=openrouter)

    resp = await provider.chat({}, [])
    assert _text(resp) == "openrouter-answer"
    assert len(gemini.calls) == 1
    assert len(openrouter.calls) == 1


@pytest.mark.asyncio
async def test_sticky_fallback_across_multiple_iterations():
    gemini = FakeGeminiFail()
    openrouter = FakeOpenRouter()
    provider = FallbackProvider(gemini=gemini, openrouter=openrouter)

    await provider.chat({}, [])
    await provider.chat({}, [])
    await provider.chat({}, [])

    # Gemini is attempted once; every later iteration uses OpenRouter (sticky).
    assert len(gemini.calls) == 1
    assert len(openrouter.calls) == 3


@pytest.mark.asyncio
async def test_gemini_primary_on_new_request():
    # A fresh provider instance starts on Gemini again, even though a previous
    # instance fell back (request-scoped stickiness).
    provider1 = FallbackProvider(gemini=FakeGeminiFail(), openrouter=FakeOpenRouter())
    await provider1.chat({}, [])
    assert provider1._fallback_active is True

    gemini2 = FakeGeminiOk()
    provider2 = FallbackProvider(gemini=gemini2, openrouter=FakeOpenRouter())
    resp = await provider2.chat({}, [])
    assert _text(resp) == "gemini-answer"
    assert len(gemini2.calls) == 1


@pytest.mark.asyncio
async def test_fallback_propagates_error_when_no_backup():
    provider = FallbackProvider(gemini=FakeGeminiFail(), openrouter=None)
    with pytest.raises(GeminiTimeoutError):
        await provider.chat({}, [])


@pytest.mark.asyncio
async def test_openrouter_only_operation():
    openrouter = FakeOpenRouter()
    provider = FallbackProvider(gemini=None, openrouter=openrouter)
    resp = await provider.chat({}, [])
    assert _text(resp) == "openrouter-answer"
    assert len(openrouter.calls) == 1
    # Remains on OpenRouter.
    await provider.chat({}, [])
    assert len(openrouter.calls) == 2


# ---------------------------------------------------------------------------
# Integration with AgentService (interface compatibility)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agentservice_works_with_fallback_provider():
    gemini = FakeGeminiFunctionCallToText()
    openrouter = FakeOpenRouter()
    provider = FallbackProvider(gemini=gemini, openrouter=openrouter)

    svc = AgentService(provider, Conn(), "merchant_northstar")
    resp = await svc.run("look up inventory", [], tool_profile="inventory_only")
    assert resp.status == "complete"
    assert resp.tool_calls[0].name == "get_inventory"
    assert resp.answer == "final"
    # Gemini handled the whole run (no fallback triggered).
    assert len(gemini.calls) >= 1
    assert openrouter.calls == []
