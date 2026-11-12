"""
backend/tests/test_openrouter_client.py
=======================================
Offline tests for the OpenRouter (OpenAI-compatible) secondary provider client.
No live network calls. The HTTP layer is faked.
"""

import json

import httpx
import pytest

from app.core.config import settings
from app.services.openrouter.client import (
    OpenRouterAPIError,
    OpenRouterAPITimeout,
    OpenRouterClient,
    translate_contents,
    translate_tools,
    normalize_response,
)


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=httpx.Request("POST", "http://x"), response=self
            )

    def json(self):
        return self._payload


class FakeHTTP:
    def __init__(self, payload=None, exc=None):
        self.payload = payload
        self.exc = exc
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self.exc is not None:
            raise self.exc
        return FakeResponse(self.payload)


# ---------------------------------------------------------------------------
# Tool schema translation
# ---------------------------------------------------------------------------

def test_translate_tools_lowercases_schema_types():
    tools = [
        {
            "function_declarations": [
                {
                    "name": "get_inventory",
                    "description": "Look up inventory",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {"product_id": {"type": "string"}},
                        "required": ["product_id"],
                    },
                }
            ]
        }
    ]
    out = translate_tools(tools)
    assert len(out) == 1
    assert out[0]["type"] == "function"
    fn = out[0]["function"]
    assert fn["name"] == "get_inventory"
    assert fn["description"] == "Look up inventory"
    assert fn["parameters"] == {
        "type": "object",
        "properties": {"product_id": {"type": "string"}},
        "required": ["product_id"],
    }


def test_translate_tools_lowercases_nested_array_items():
    tools = [
        {
            "function_declarations": [
                {
                    "name": "create_purchase_order_draft",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "items": {
                                "type": "array",
                                "items": {
                                    "type": "OBJECT",
                                    "properties": {"quantity": {"type": "integer"}},
                                },
                            }
                        },
                    },
                }
            ]
        }
    ]
    out = translate_tools(tools)
    params = out[0]["function"]["parameters"]
    assert params["type"] == "object"
    assert params["properties"]["items"]["items"]["type"] == "object"
    assert params["properties"]["items"]["items"]["properties"]["quantity"]["type"] == "integer"


# ---------------------------------------------------------------------------
# Contents translation
# ---------------------------------------------------------------------------

def test_translate_contents_history_conversion():
    contents = [
        {"role": "model", "parts": [{"text": "assistant hello"}]},
        {"role": "user", "parts": [{"text": "user hello"}]},
    ]
    messages = translate_contents(contents)
    assert messages == [
        {"role": "assistant", "content": "assistant hello"},
        {"role": "user", "content": "user hello"},
    ]


def test_translate_contents_preserves_tool_call_ids():
    contents = [
        {
            "role": "model",
            "parts": [
                {
                    "functionCall": {
                        "name": "get_inventory",
                        "args": {"product_id": "p"},
                        "id": "call_1",
                    }
                }
            ],
        }
    ]
    messages = translate_contents(contents)
    assert messages[0]["role"] == "assistant"
    tc = messages[0]["tool_calls"][0]
    assert tc["id"] == "call_1"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "get_inventory"
    assert json.loads(tc["function"]["arguments"]) == {"product_id": "p"}


def test_translate_contents_function_response_to_tool_message():
    contents = [
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": "get_inventory",
                        "response": {"result": {"stock": 5}},
                        "id": "call_1",
                    }
                }
            ],
        }
    ]
    messages = translate_contents(contents)
    assert messages == [
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": json.dumps({"result": {"stock": 5}}),
        }
    ]


def test_translate_contents_assistant_has_content_when_tool_calls_present():
    contents = [
        {
            "role": "model",
            "parts": [
                {
                    "functionCall": {
                        "name": "get_inventory",
                        "args": {"product_id": "p"},
                        "id": "call_1",
                    }
                }
            ],
        }
    ]
    messages = translate_contents(contents)
    assert messages[0]["role"] == "assistant"
    assert "content" in messages[0]
    assert messages[0]["content"] == ""
    assert messages[0]["tool_calls"][0]["id"] == "call_1"


def test_translate_contents_synthesizes_assistant_id_when_missing():
    contents = [
        {
            "role": "model",
            "parts": [
                {"functionCall": {"name": "get_inventory", "args": {}, "id": None}}
            ],
        }
    ]
    messages = translate_contents(contents)
    assert messages[0]["tool_calls"][0]["id"] is not None
    assert messages[0]["content"] == ""


def test_translate_contents_synthesizes_tool_id_when_function_response_missing_id():
    contents = [
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": "get_inventory",
                        "response": {"result": {"stock": 5}},
                        "id": None,
                    }
                }
            ],
        }
    ]
    messages = translate_contents(contents)
    assert messages[0]["role"] == "tool"
    assert messages[0]["tool_call_id"] is not None
    assert isinstance(messages[0]["tool_call_id"], str)


# ---------------------------------------------------------------------------
# Response normalization
# ---------------------------------------------------------------------------

def test_normalize_response_with_tool_calls():
    data = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_9",
                            "type": "function",
                            "function": {
                                "name": "get_inventory",
                                "arguments": '{"product_id": "prod_blue_vase"}',
                            },
                        }
                    ],
                }
            }
        ]
    }
    out = normalize_response(data)
    part = out["candidates"][0]["content"]["parts"][0]["functionCall"]
    assert part["name"] == "get_inventory"
    assert part["args"] == {"product_id": "prod_blue_vase"}
    assert part["id"] == "call_9"


def test_normalize_response_with_text():
    data = {"choices": [{"message": {"content": "final answer", "tool_calls": None}}]}
    out = normalize_response(data)
    assert out["candidates"][0]["content"]["parts"] == [{"text": "final answer"}]


def test_normalize_response_bad_arguments_becomes_empty_dict():
    data = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "function": {"name": "get_inventory", "arguments": "not-json"},
                        }
                    ],
                }
            }
        ]
    }
    out = normalize_response(data)
    part = out["candidates"][0]["content"]["parts"][0]["functionCall"]
    assert part["args"] == {}


def test_normalize_response_no_choices():
    assert normalize_response({}) == {"candidates": []}


# ---------------------------------------------------------------------------
# Client chat() via fake HTTP
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_openrouter_chat_sends_openai_payload():
    fake = FakeHTTP(payload={"choices": [{"message": {"content": "ok", "tool_calls": None}}]})
    client = OpenRouterClient("sk-test", model="openrouter/free", http=fake)
    request = {
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
        "system_instruction": "system prompt",
    }
    tools = [
        {
            "function_declarations": [
                {"name": "get_inventory", "parameters": {"type": "OBJECT", "properties": {}}}
            ]
        }
    ]
    out = await client.chat(request, tools)
    assert out["candidates"][0]["content"]["parts"][0]["text"] == "ok"

    call = fake.calls[0]
    payload = call["json"]
    assert payload["model"] == "openrouter/free"
    assert payload["tool_choice"] == "auto"
    assert payload["tools"][0]["type"] == "function"
    assert payload["tools"][0]["function"]["parameters"]["type"] == "object"
    # system prompt prepended
    assert payload["messages"][0] == {"role": "system", "content": "system prompt"}
    assert payload["messages"][1] == {"role": "user", "content": "hi"}
    # Authorization header carries Bearer prefix; never assert the raw key value.
    assert call["headers"]["Authorization"].startswith("Bearer ")
    assert "sk-test" in call["headers"]["Authorization"]


@pytest.mark.asyncio
async def test_openrouter_chat_timeout_maps_to_timeout_error():
    fake = FakeHTTP(exc=httpx.TimeoutException("slow"))
    client = OpenRouterClient("sk-test", http=fake)
    with pytest.raises(OpenRouterAPITimeout):
        await client.chat({"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}, [])


@pytest.mark.asyncio
async def test_openrouter_chat_http_error_maps_to_api_error():
    fake = FakeHTTP(payload={"error": "nope"}, exc=httpx.HTTPStatusError(
        "boom", request=httpx.Request("POST", "http://x"), response=FakeResponse({}, status=401)
    ))
    client = OpenRouterClient("sk-test", http=fake)
    with pytest.raises(OpenRouterAPIError):
        await client.chat({"contents": []}, [])


def test_openrouter_client_missing_key_raises():
    with pytest.raises(Exception):
        OpenRouterClient("")


def test_settings_have_openrouter_config():
    assert hasattr(settings, "openrouter_api_key")
    assert hasattr(settings, "openrouter_model")
    assert settings.openrouter_model == "openrouter/free"
