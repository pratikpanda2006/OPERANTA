"""
backend/app/schemas/payments.py
===============================
Pydantic contracts for the M12.0 PayPal Sandbox customer checkout.

These map to the request/response bodies of the online checkout endpoints and
the webhook receipt payload. Amounts are represented as Decimal to avoid
floating-point drift.
"""

from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field


class OnlineCheckoutItemRequest(BaseModel):
    product_id: str = Field(..., min_length=1)
    quantity: int = Field(..., gt=0)


class OnlineCheckoutRequest(BaseModel):
    items: List[OnlineCheckoutItemRequest] = Field(..., min_length=1)


class OnlineCheckoutItemResponse(BaseModel):
    product_id: str
    product_name: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal


class OnlineCheckoutResponse(BaseModel):
    merchant_id: str
    order_id: str
    order_number: str
    paypal_order_id: Optional[str] = None
    approval_url: Optional[str] = None
    subtotal: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    status: str
    items: List[OnlineCheckoutItemResponse]


class OnlineCaptureResult(BaseModel):
    """Result of settling a captured PayPal payment for an internal order."""

    merchant_id: str
    order_id: str
    order_number: str
    order_status: str
    capture_status: str
    paypal_capture_id: str
    captured_amount: Decimal
    currency: str
    settled: bool = False
    already_settled: bool = False
    revenue_ledger_id: Optional[int] = None


class PaymentCancelResponse(BaseModel):
    """Outcome of cancelling a still-pending internal online checkout."""

    merchant_id: str
    order_id: str
    status: str
    cancelled: bool
    message: str


class WebhookReceipt(BaseModel):
    """Acknowledgement returned to PayPal for a delivered webhook event."""

    received: bool = True
    event_id: str
    event_type: str
    status: str  # 'processed' | 'ignored' | 'duplicate' | 'failed'
    message: Optional[str] = None
