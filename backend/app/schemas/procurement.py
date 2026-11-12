"""
backend/app/schemas/procurement.py
==================================
Pydantic v2 schemas for Reorder Planning, Supplier Comparison, Purchase Orders, and Owner Decisions.
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class SupplierEvaluationSummary(BaseModel):
    """Evaluation metrics for a candidate supplier."""
    supplier_id: str
    supplier_name: str
    trust_tier: str
    is_allowlisted: bool
    reliability_score: Decimal
    lead_time_days: int
    moq: int
    unit_cost: Decimal
    shipping_cost: Decimal
    landed_cost: Optional[Decimal]
    is_illustrative: bool = True
    trade_off_notes: List[str] = Field(default_factory=list)


class ReorderPlanItem(BaseModel):
    """Reorder and replenishment recommendation for an SKU."""
    product_id: str
    product_name: str
    sku: str
    available_stock: int
    reorder_point: int
    daily_demand: Decimal
    recommended_units: int
    preferred_supplier: Optional[SupplierEvaluationSummary] = None
    alternative_suppliers: List[SupplierEvaluationSummary] = Field(default_factory=list)


class ReorderPlanResponse(BaseModel):
    """Full replenishment proposal across all low-stock items."""
    merchant_id: str
    items: List[ReorderPlanItem]
    total_recommendations: int


class PurchaseOrderItemCreate(BaseModel):
    """Line item in a proposed purchase order. Pricing is determined server-side from supplier_offers."""
    product_id: str
    quantity: int = Field(..., ge=1)


class PurchaseOrderCreateRequest(BaseModel):
    """
    Request to draft or propose a purchase order.
    Unit prices and shipping costs are derived server-side from canonical supplier_offers.
    """
    supplier_id: str
    items: List[PurchaseOrderItemCreate] = Field(..., min_length=1)
    tax_cost: Optional[Decimal] = Field(default=Decimal("0.00"), ge=0)
    labour_overhead_cost: Optional[Decimal] = Field(default=Decimal("0.00"), ge=0)
    has_unknown_costs: bool = False
    unknown_cost_nte_ceiling: Optional[Decimal] = Field(default=None, ge=0)
    idempotency_key: str = Field(..., min_length=1, max_length=128)


class PolicyValidationResult(BaseModel):
    """
    Detailed results of the deterministic backend spending policy evaluation.
    """
    passed: bool
    reasons: List[str] = Field(default_factory=list)
    per_order_limit_passed: bool
    envelope_cap_passed: bool
    allowlist_passed: bool
    anti_splitting_passed: bool
    calculated_landed_cost: Optional[Decimal]
    remaining_envelope_if_approved: Optional[Decimal]


class PurchaseOrderItemResponse(BaseModel):
    """Purchase order line item."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    product_id: str
    product_name: Optional[str] = None
    sku: Optional[str] = None
    quantity: int
    unit_cost: Decimal
    line_total: Decimal


class PurchaseOrderResponse(BaseModel):
    """Complete Purchase Order object with policy validation and status."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    merchant_id: str
    po_number: str
    supplier_id: str
    supplier_name: Optional[str] = None
    status: str
    item_subtotal: Decimal
    shipping_cost: Optional[Decimal]
    tax_cost: Optional[Decimal]
    labour_overhead_cost: Optional[Decimal]
    total_landed_cost: Optional[Decimal]
    has_unknown_costs: bool
    unknown_cost_nte_ceiling: Optional[Decimal]
    payment_status: str
    is_illustrative: bool
    created_at: datetime
    updated_at: datetime
    items: List[PurchaseOrderItemResponse] = Field(default_factory=list)
    policy_evaluation: Optional[PolicyValidationResult] = None


class PurchaseOrderListResponse(BaseModel):
    merchant_id: str
    purchase_orders: List[PurchaseOrderResponse]
    total_orders: int


class OwnerDecisionRequest(BaseModel):
    """
    Human-in-the-loop owner approval or rejection.
    decided_by is derived server-side in DEMO_MODE to prevent client impersonation.
    """
    decision: str = Field(..., description="'approved' or 'rejected'")
    decision_reason: str = Field(..., min_length=1, description="Audit reason for decision")
    approval_type: str = Field(default="standard", description="'standard', 'unknown_cost_override', 'vendor_qualification', 'policy_exception'")
    nte_ceiling: Optional[Decimal] = Field(default=None, ge=0, le=150)
    idempotency_key: str = Field(..., min_length=1, max_length=128)


class OwnerDecisionResponse(BaseModel):
    """
    Response returned after an approval or rejection has been processed.
    """
    purchase_order: PurchaseOrderResponse
    decision: str
    spend_reserved: Decimal
    remaining_envelope_available: Decimal
    audit_logged: bool
