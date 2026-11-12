"""
backend/app/services/agent/tools.py
===================================
Typed toolset for the M13.0 Gemini business agent.

Every tool is mapped to an existing, deterministic backend service/repository.
Tools NEVER accept a merchant_id, actor_id, approval identity, or spending
authority from Gemini or the client — tenant identity is always injected
server-side from ``get_merchant_context``.

Monetary values are derived server-side from canonical ``supplier_offers`` and
the spend envelope journal. The agent cannot approve, pay, refund, raise caps,
or modify the allowlist.
"""

import re
import uuid
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

import asyncpg
from pydantic import TypeAdapter

from app.db.repositories.inventory_repo import list_inventory_items
from app.db.repositories.merchant_repo import get_spend_envelope
from app.db.repositories.procurement_repo import (
    create_purchase_order,
    evaluate_spending_policy,
    generate_reorder_plan,
    get_purchase_order_by_id,
)
from app.schemas.procurement import (
    PolicyValidationResult,
    PurchaseOrderCreateRequest,
    PurchaseOrderItemCreate,
)


class AgentBlockedError(Exception):
    """A safety gate rejected a tool call."""


# ---------------------------------------------------------------------------
# Intent guard: draft creation only on explicit user request
# ---------------------------------------------------------------------------
_EXPLICIT_DRAFT_RE = re.compile(
    r"\b(create|make|draft|generate|prepare|start)\b[\s\S]{0,40}?"
    r"\b(purchase\s?order|po|replenishment)\b",
    re.IGNORECASE,
)


def is_explicit_draft_request(message: str) -> bool:
    """Deterministic server-side guard. A draft is never created on an
    ambiguous or injected prompt."""
    return bool(_EXPLICIT_DRAFT_RE.search(message or ""))


# ---------------------------------------------------------------------------
# Tool argument schemas (strict validation via Pydantic)
# ---------------------------------------------------------------------------
from pydantic import BaseModel, Field  # noqa: E402


class _InventoryArgs(BaseModel):
    pass


class _VelocityArgs(BaseModel):
    pass


class _PlanArgs(BaseModel):
    pass


class _CompareArgs(BaseModel):
    product_id: str = Field(..., min_length=1)


class _PolicyArgs(BaseModel):
    supplier_id: str = Field(..., min_length=1)
    product_id: str = Field(..., min_length=1)
    quantity: int = Field(..., ge=1)
    has_unknown_costs: bool = False
    unknown_cost_nte_ceiling: Optional[Decimal] = Field(default=None, ge=0, le=150)


class _DraftItem(BaseModel):
    product_id: str = Field(..., min_length=1)
    quantity: int = Field(..., ge=1)


class _DraftArgs(BaseModel):
    supplier_id: str = Field(..., min_length=1)
    items: List[_DraftItem] = Field(..., min_length=1)
    has_unknown_costs: bool = False
    unknown_cost_nte_ceiling: Optional[Decimal] = Field(default=None, ge=0, le=150)


class _ApprovalArgs(BaseModel):
    purchase_order_id: str = Field(..., min_length=1)


class _DashboardArgs(BaseModel):
    pass


# ---------------------------------------------------------------------------
# Handlers (all receive server-derived merchant_id; never from client/Gemini)
# ---------------------------------------------------------------------------

async def _get_inventory(conn: asyncpg.Connection, merchant_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    items = await list_inventory_items(conn, merchant_id)
    return {
        "merchant_id": merchant_id,
        "total_items": len(items),
        "items": items,
    }


async def _get_sales_velocity(conn: asyncpg.Connection, merchant_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    items = await list_inventory_items(conn, merchant_id)
    velocity = [
        {
            "product_id": it["product_id"],
            "product_name": it["product_name"],
            "daily_demand": str(it["daily_demand"]),
            "reorder_point": it["reorder_point"],
            "lead_days": it["lead_days"],
            "safety_stock": it["safety_stock"],
        }
        for it in items
    ]
    return {"merchant_id": merchant_id, "velocity": velocity}


async def _get_dashboard_summary(conn: asyncpg.Connection, merchant_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    envelope = await get_spend_envelope(conn, merchant_id)
    inventory = await list_inventory_items(conn, merchant_id)
    low_stock = [it for it in inventory if it["reorder_recommended"]]
    return {
        "merchant_id": merchant_id,
        "available_spend": str(envelope["available_spend"]) if envelope else "0.00",
        "weekly_cap": str(envelope["weekly_cap"]) if envelope and envelope.get("weekly_cap") is not None else "0.00",
        "total_skus": len(inventory),
        "low_stock_skus": len(low_stock),
        "low_stock_items": [
            {"product_id": it["product_id"], "product_name": it["product_name"], "total_available": it["total_available"]}
            for it in low_stock
        ],
    }


async def _calculate_reorder_plan(conn: asyncpg.Connection, merchant_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    plan = await generate_reorder_plan(conn, merchant_id)
    return {
        "merchant_id": merchant_id,
        "items": plan,
        "total_recommendations": len(plan),
    }


async def _compare_suppliers(conn: asyncpg.Connection, merchant_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    plan = await generate_reorder_plan(conn, merchant_id)
    for item in plan:
        if item["product_id"] == args["product_id"]:
            return {
                "product_id": args["product_id"],
                "product_name": item["product_name"],
                "recommended_units": item["recommended_units"],
                "preferred_supplier": item["preferred_supplier"],
                "alternative_suppliers": item["alternative_suppliers"],
            }
    return {
        "product_id": args["product_id"],
        "message": "Product is not currently flagged for replenishment; no supplier comparison generated.",
    }


async def _check_spend_policy(conn: asyncpg.Connection, merchant_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Hard backend policy gate. Subtotal is derived server-side from the
    canonical supplier offer — the model/client never supplies amounts."""
    offer = await conn.fetchrow(
        "SELECT unit_cost, shipping_cost FROM supplier_offers "
        "WHERE merchant_id = $1 AND supplier_id = $2 AND product_id = $3",
        merchant_id,
        args["supplier_id"],
        args["product_id"],
    )
    if not offer:
        raise AgentBlockedError(
            f"Supplier '{args['supplier_id']}' has no canonical offer for product '{args['product_id']}'."
        )
    subtotal = Decimal(str(offer["unit_cost"])) * args["quantity"]
    shipping = Decimal(str(offer["shipping_cost"]))
    result = await evaluate_spending_policy(
        conn,
        merchant_id,
        args["supplier_id"],
        subtotal,
        shipping,
        Decimal("0.00"),
        Decimal("0.00"),
        args.get("has_unknown_costs", False),
        args.get("unknown_cost_nte_ceiling"),
    )
    return PolicyValidationResult.model_validate(result).model_dump()


async def _create_purchase_order_draft(conn: asyncpg.Connection, merchant_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    items = [
        PurchaseOrderItemCreate(product_id=it["product_id"], quantity=it["quantity"])
        for it in args["items"]
    ]
    request = PurchaseOrderCreateRequest(
        supplier_id=args["supplier_id"],
        items=items,
        has_unknown_costs=args.get("has_unknown_costs", False),
        unknown_cost_nte_ceiling=args.get("unknown_cost_nte_ceiling"),
        idempotency_key=args.get("idempotency_key") or f"agent-draft-{uuid.uuid4().hex}",
    )
    result = await create_purchase_order(conn, merchant_id, request)
    return result


async def _request_approval(conn: asyncpg.Connection, merchant_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Advisory: surfaces a valid draft for owner approval. Does NOT approve —
    approval identity is derived server-side and the agent cannot self-approve."""
    po = await get_purchase_order_by_id(conn, merchant_id, args["purchase_order_id"])
    if not po:
        raise AgentBlockedError("Purchase order not found for this merchant.")
    status = po["status"]
    if status not in ("draft", "pending"):
        return {
            "purchase_order_id": args["purchase_order_id"],
            "status": status,
            "approval_required": False,
            "message": "This purchase order has already been processed.",
        }
    return {
        "purchase_order_id": args["purchase_order_id"],
        "status": status,
        "approval_required": True,
        "message": "Owner approval is required. Use the existing approval workflow to approve.",
        "approve_endpoint": f"/api/v1/procurement/purchase-orders/{args['purchase_order_id']}/decision",
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
TOOL_EXECUTORS: Dict[str, Callable[..., Any]] = {
    "get_dashboard_summary": _get_dashboard_summary,
    "get_inventory": _get_inventory,
    "get_sales_velocity": _get_sales_velocity,
    "calculate_reorder_plan": _calculate_reorder_plan,
    "compare_suppliers": _compare_suppliers,
    "check_spend_policy": _check_spend_policy,
    "create_purchase_order_draft": _create_purchase_order_draft,
    "request_approval": _request_approval,
}

TOOL_ARG_MODELS: Dict[str, TypeAdapter] = {
    "get_dashboard_summary": TypeAdapter(_DashboardArgs),
    "get_inventory": TypeAdapter(_InventoryArgs),
    "get_sales_velocity": TypeAdapter(_VelocityArgs),
    "calculate_reorder_plan": TypeAdapter(_PlanArgs),
    "compare_suppliers": TypeAdapter(_CompareArgs),
    "check_spend_policy": TypeAdapter(_PolicyArgs),
    "create_purchase_order_draft": TypeAdapter(_DraftArgs),
    "request_approval": TypeAdapter(_ApprovalArgs),
}

# ---------------------------------------------------------------------------
# Tool profiles (server-defined; never client-controlled)
# ---------------------------------------------------------------------------
# The profile -> permitted-tool mapping is authoritative and lives only on the
# server. Clients may select a predefined profile by name but may never supply
# an arbitrary allowlist.
TOOL_PROFILES: Dict[str, List[str]] = {
    # "standard" preserves legacy behavior: every registered tool.
    "standard": list(TOOL_EXECUTORS.keys()),
    # "inventory_only" exposes exactly the read-only inventory lookup.
    "inventory_only": ["get_inventory"],
}


def build_tool_declarations(profile: str = "standard") -> List[Dict[str, Any]]:
    """Return Gemini function declarations (JSON-schema style) for a profile.

    The profile -> allowed-tool mapping is server-defined (``TOOL_PROFILES``).
    ``standard`` (default) exposes all registered tools; ``inventory_only``
    exposes exactly ``get_inventory``. Unknown profiles are rejected.
    """
    if profile not in TOOL_PROFILES:
        raise ValueError(f"Unknown tool profile '{profile}'.")
    permitted = set(TOOL_PROFILES[profile])
    declarations = [
        {
            "name": "get_dashboard_summary",
            "description": ("Read-only dashboard summary: available spend envelope, SKU count, "
                            "low-stock items. No arguments."),
            "parameters": {"type": "OBJECT", "properties": {}, "required": []},
        },
        {
            "name": "get_inventory",
            "description": "Read-only inventory positions across in-store and online channels for the merchant.",
            "parameters": {"type": "OBJECT", "properties": {}, "required": []},
        },
        {
            "name": "get_sales_velocity",
            "description": "Read-only per-SKU baseline sales velocity (daily_demand) and reorder parameters.",
            "parameters": {"type": "OBJECT", "properties": {}, "required": []},
        },
        {
            "name": "calculate_reorder_plan",
            "description": "Deterministic replenishment plan computed by the backend from inventory + velocity + supplier offers.",
            "parameters": {"type": "OBJECT", "properties": {}, "required": []},
        },
        {
            "name": "compare_suppliers",
            "description": "Deterministic supplier comparison for a product from backend ranking.",
            "parameters": {
                "type": "OBJECT",
                "properties": {"product_id": {"type": "string"}},
                "required": ["product_id"],
            },
        },
        {
            "name": "check_spend_policy",
            "description": ("Hard spending-policy gate. The backend derives amounts from canonical supplier "
                            "offers and validates per-order cap, envelope, allowlist and anti-splitting."),
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "supplier_id": {"type": "string"},
                    "product_id": {"type": "string"},
                    "quantity": {"type": "integer"},
                    "has_unknown_costs": {"type": "boolean"},
                    "unknown_cost_nte_ceiling": {"type": "number"},
                },
                "required": ["supplier_id", "product_id", "quantity"],
            },
        },
        {
            "name": "create_purchase_order_draft",
            "description": ("Create an UNAPPROVED purchase-order draft. Only runs after an explicit user "
                            "request; never on ambiguous asks."),
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "supplier_id": {"type": "string"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "product_id": {"type": "string"},
                                "quantity": {"type": "integer"},
                            },
                            "required": ["product_id", "quantity"],
                        },
                    },
                    "has_unknown_costs": {"type": "boolean"},
                    "unknown_cost_nte_ceiling": {"type": "number"},
                },
                "required": ["supplier_id", "items"],
            },
        },
        {
            "name": "request_approval",
            "description": ("Surface an existing draft for owner approval. Does NOT approve anything; "
                            "approval happens through the existing owner workflow."),
            "parameters": {
                "type": "OBJECT",
                "properties": {"purchase_order_id": {"type": "string"}},
                "required": ["purchase_order_id"],
            },
        },
    ]
    filtered = [d for d in declarations if d["name"] in permitted]
    return [{"function_declarations": filtered}]


def validate_tool_args(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Strict validation. Raises ValueError on invalid/unknown/extra args."""
    adapter = TOOL_ARG_MODELS.get(name)
    if adapter is None:
        raise ValueError(f"Unknown tool '{name}'.")
    validated = adapter.validate_python(args)
    return validated.model_dump()


def sanitize_tool_args(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Drop any merchant/actor/approval identity the model might attempt to pass."""
    blocked_keys = ("merchant_id", "actor_id", "decided_by", "approval_identity", "owner_id")
    return {k: v for k, v in args.items() if k not in blocked_keys}
