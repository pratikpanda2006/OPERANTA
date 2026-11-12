"""
backend/app/schemas/agent.py
============================
Pydantic contracts for the M13.0 Gemini business agent chat endpoint.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class AgentMessage(BaseModel):
    """A single bounded conversation turn (user or assistant)."""

    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., min_length=1)


class AgentChatRequest(BaseModel):
    """Client request to the business agent."""

    message: str = Field(..., min_length=1)
    history: List[AgentMessage] = Field(
        default_factory=list,
        max_length=20,
        description="Optional bounded conversation history. Never trusted for tenant/actor identity.",
    )
    tool_profile: Literal["standard", "inventory_only"] = Field(
        default="standard",
        description=(
            "Server-defined tool profile. 'standard' (default) exposes all tools; "
            "'inventory_only' exposes exactly the read-only get_inventory tool. "
            "Arbitrary per-client allowlists are not accepted."
        ),
    )


class AgentToolCall(BaseModel):
    """One tool call attempted by the agent, with its result or error."""

    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class AgentToolBlock(BaseModel):
    """A tool call that was blocked by a safety gate."""

    name: str
    reason: str


class AgentChatResponse(BaseModel):
    """Structured agent response for frontend rendering."""

    answer: str
    status: str = "complete"
    tool_calls: List[AgentToolCall] = Field(default_factory=list)
    tool_results: List[Dict[str, Any]] = Field(default_factory=list)
    blocked: List[AgentToolBlock] = Field(default_factory=list)
    draft_reference: Optional[str] = None
    approval_required: bool = False
    approval_reference: Optional[str] = None
