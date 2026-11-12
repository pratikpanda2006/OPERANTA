"""
Supplier & Catalog Offer Schemas.

SCHEMA ALIGNMENT NOTES (migration 001):
  supplier_offers has NO is_preferred column.
  suppliers has NO payment_terms column.
  suppliers has trust_tier (VARCHAR) — is_allowlisted is derived as trust_tier='allowlisted'.
  supplier-level lead_time_days is derived as min(offer lead_time_days).
"""

from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class SupplierOfferSummary(BaseModel):
    """
    Catalog offering for a product from a specific supplier.

    NOTE: is_preferred is not stored in migration 001's supplier_offers table.
    It is excluded from this schema to prevent fabricating data.
    """
    model_config = ConfigDict(from_attributes=True)

    offer_id: str = Field(description="Unique supplier offer record identifier")
    product_id: str = Field(description="Target product identifier")
    unit_cost: Decimal = Field(description="Wholesale purchase price per unit")
    shipping_cost: Decimal = Field(description="Fixed or base freight cost for shipment")
    lead_time_days: int = Field(description="Days from PO issuance to dock delivery for this offer")
    moq: int = Field(description="Minimum Order Quantity required by supplier")
    is_illustrative: bool = Field(default=True, description="Tag marking illustrative demonstration data")


class SupplierResponse(BaseModel):
    """
    Supplier directory record with operational ratings and catalog offers.

    NOTE: payment_terms is not stored in migration 001's suppliers table.
    It is excluded from this schema to prevent fabricating data.
    trust_tier is included as the canonical field; is_allowlisted is a derived convenience.
    """
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Supplier record identifier")
    merchant_id: str = Field(description="Tenant identifier")
    name: str = Field(description="Supplier corporate name")
    contact_email: Optional[str] = Field(default=None, description="Contact email address")
    trust_tier: str = Field(description="Canonical trust classification: 'allowlisted' or 'requires_approval'")
    is_allowlisted: bool = Field(description="Convenience flag: True if trust_tier is 'allowlisted'")
    reliability_score: Decimal = Field(description="Historical fulfillment reliability rating (0–100, integer seed value)")
    lead_time_days: Optional[int] = Field(
        default=None,
        description="Minimum lead time in days derived from supplier's offer catalog (None if no offers)"
    )
    is_illustrative: bool = Field(default=True, description="Tag marking illustrative demonstration data")
    offers: List[SupplierOfferSummary] = Field(default_factory=list, description="Catalog offers provided by this supplier")


class SupplierListResponse(BaseModel):
    """
    Payload for supplier directory and multi-supplier evaluation matrix.
    """
    model_config = ConfigDict(from_attributes=True)

    merchant_id: str = Field(description="Tenant identifier")
    suppliers: List[SupplierResponse] = Field(description="List of qualified and candidate suppliers")
    total_suppliers: int = Field(description="Total count of suppliers on file")
