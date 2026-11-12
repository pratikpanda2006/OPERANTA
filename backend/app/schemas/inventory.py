"""
Inventory & Product Schemas.
"""

from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class InventoryItemResponse(BaseModel):
    """
    Detailed stock levels, reservation states, and replenishment indicators for a single SKU.
    """
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Unique inventory item record identifier")
    merchant_id: str = Field(description="Tenant identifier")
    product_id: str = Field(description="Associated product identifier")
    sku: str = Field(description="Stock Keeping Unit code")
    product_name: str = Field(description="Product display name")
    category: str = Field(description="Merchandising category")
    retail_price: Decimal = Field(description="Customer retail price")
    base_cost: Optional[Decimal] = Field(default=None, description="Known supplier base cost (nullable if unknown)")
    stock_in_store: int = Field(description="Physical stock on retail boutique floor")
    reserved_in_store: int = Field(description="Physical stock held for pending store orders or layaway")
    available_in_store: int = Field(description="Net physical stock available for immediate sale")
    stock_online: int = Field(description="Physical stock allocated to warehouse / e-commerce fulfillment")
    reserved_online: int = Field(description="Online stock reserved for uncaptured online checkouts")
    available_online: int = Field(description="Net online stock available for web checkout")
    total_on_hand: int = Field(description="Total physical units across all locations (stock_in_store + stock_online)")
    total_available: int = Field(description="Total unreserved sellable units across all channels")
    reorder_point: int = Field(description="Inventory threshold that triggers replenishment (14 in demo baseline)")
    daily_demand: Decimal = Field(description="Static baseline sales velocity in units per day (4.00 in demo baseline)")
    lead_days: int = Field(description="Supplier delivery lead time in days (2 days for Supplier C)")
    safety_stock: int = Field(description="Buffer stock to prevent stockouts (6 units in demo baseline)")
    reorder_recommended: bool = Field(description="True if total_available <= reorder_point")


class InventoryListResponse(BaseModel):
    """
    Summary payload for the inventory overview grid.
    """
    model_config = ConfigDict(from_attributes=True)

    merchant_id: str = Field(description="Tenant identifier")
    items: List[InventoryItemResponse] = Field(description="List of stock records")
    total_items: int = Field(description="Total count of active inventory SKUs")
    low_stock_count: int = Field(description="Count of SKUs currently at or below their reorder point")


class InventoryTransferRequest(BaseModel):
    """
    Request to transfer a positive quantity of a single product from the
    physical store channel to the online channel.

    Tenant identity is intentionally NOT accepted here; it is always derived
    server-side from the authenticated/demo tenant dependency.
    """
    product_id: str = Field(description="Product SKU identifier to transfer")
    quantity: int = Field(ge=1, description="Positive number of units to transfer from store to online")
    idempotency_key: str = Field(
        min_length=8,
        max_length=128,
        description="Client-supplied idempotency key used to deduplicate request retries.",
    )
    reason: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Optional human-readable reason recorded in the operational audit log.",
    )


class InventoryTransferResponse(BaseModel):
    """
    Result of a successful store-to-online inventory transfer.
    """
    merchant_id: str = Field(description="Tenant identifier")
    product_id: str = Field(description="Transferred product identifier")
    quantity: int = Field(description="Number of units transferred")
    from_channel: str = Field(default="in_store", description="Source channel")
    to_channel: str = Field(default="online", description="Destination channel")
    stock_in_store: int = Field(description="Store stock balance after transfer")
    stock_online: int = Field(description="Online stock balance after transfer")
    available_in_store: int = Field(description="Unreserved store availability after transfer")
    available_online: int = Field(description="Unreserved online availability after transfer")
    movement_ids: List[str] = Field(description="Inventory movement record ids written")
    reference_id: str = Field(description="Shared transfer reference id for the movement pair")
