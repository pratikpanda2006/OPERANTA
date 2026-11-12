"""
backend/app/schemas/orders.py
=============================
Pydantic v2 validation models for POS Sales and Order Ingestion.
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class POSSaleItemRequest(BaseModel):
    """Line item in a POS customer sale."""
    product_id: str = Field(..., description="Target product identifier")
    quantity: int = Field(..., ge=1, description="Positive units purchased")


class POSSaleRequest(BaseModel):
    """
    Request payload for executing an in-store POS transaction.
    """
    items: List[POSSaleItemRequest] = Field(..., min_length=1, description="List of items sold at the counter")
    idempotency_key: str = Field(..., min_length=1, max_length=128, description="Unique client key preventing double-charging")
    payment_method: str = Field(default="card_in_store", description="Payment method: card_in_store, cash, etc.")


class OrderItemResponse(BaseModel):
    """Line item in an order response."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    product_id: str
    product_name: Optional[str] = None
    sku: Optional[str] = None
    quantity: int
    unit_price: Decimal
    line_total: Decimal


class OrderResponse(BaseModel):
    """Summary of a customer order (POS or Online)."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    merchant_id: str
    order_number: str
    channel: str
    status: str
    subtotal: Decimal
    tax_amount: Decimal
    shipping_amount: Decimal
    total_amount: Decimal
    payment_method: str
    created_at: datetime
    items: List[OrderItemResponse] = Field(default_factory=list)


class OrderListResponse(BaseModel):
    """Paginated or listed order records for the merchant."""
    merchant_id: str
    orders: List[OrderResponse]
    total_orders: int


class POSSaleResponse(BaseModel):
    """
    Response returned upon successful atomic POS sale execution.
    Includes the created order and the updated inventory states.
    """
    order: OrderResponse
    updated_inventory: List[dict] = Field(default_factory=list, description="Updated stock states for purchased products")
