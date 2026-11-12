"""
Merchant & Procurement Spending Policy Schemas.
"""

from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field


class SpendEnvelopeResponse(BaseModel):
    """
    Spend envelope metrics for the active demo week.
    Enforces exact Decimal representation of monetary values.
    """
    model_config = ConfigDict(from_attributes=True)

    merchant_id: str = Field(description="Unique tenant identifier")
    demo_envelope_cap: Decimal = Field(description="Weekly spending envelope cap ($500.00 for Northstar Living)")
    max_order_cost_ceiling: Decimal = Field(description="Maximum landed cost permitted for a single purchase order ($150.00)")
    committed_spend: Decimal = Field(description="Cumulative spend commitments incurred in the active week ($175.00 in demo baseline)")
    available_spend: Decimal = Field(description="Remaining spend available within the weekly envelope ($325.00 in demo baseline)")
    merchant_created_at: datetime = Field(description="Timestamp marking merchant account creation")
    currency: str = Field(default="USD", description="Currency code")
