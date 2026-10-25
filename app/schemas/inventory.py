from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class InventoryBatchCreate(BaseModel):
    warehouse_id: int = Field(..., gt=0, examples=[1])
    product_id: int = Field(..., gt=0, examples=[1])
    batch_number: str = Field(..., max_length=100, examples=["BATCH-2026-10-A"])
    on_hand_quantity: int = Field(..., ge=0, examples=[500])
    manufactured_date: Optional[date] = Field(default=None, examples=["2026-10-01"])
    expiry_date: Optional[date] = Field(default=None, examples=["2027-10-01"])


class InventoryBatchRead(BaseModel):
    id: int
    warehouse_id: int
    warehouse_code: Optional[str] = None
    product_id: int
    sku: Optional[str] = None
    product_name: Optional[str] = None
    batch_number: str
    manufactured_date: Optional[date] = None
    expiry_date: Optional[date] = None
    
    # Authoritative balance states clearly distinguished
    on_hand_quantity: int = Field(..., ge=0, description="Total physical inventory in batch")
    physical_quantity: Optional[int] = Field(default=None, description="Alias for physical on-hand quantity")
    reserved_quantity: int = Field(..., ge=0, description="Units reserved for validated/pending plans")
    committed_quantity: int = Field(..., ge=0, description="Units finalized and locked for immediate dispatch")
    available_quantity: int = Field(..., ge=0, description="Free units available for new allocation proposals")
    available_stock: Optional[int] = Field(default=None, description="Alias for available stock (physical - reserved)")
    
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InventoryBatchAdjust(BaseModel):
    """Payload to adjust inventory on an existing batch with concurrency protection."""
    batch_id: Optional[int] = Field(default=None, gt=0, examples=[1])
    quantity_delta: int = Field(..., examples=[50], description="Positive to add stock, negative to reduce")
    reason: str = Field(..., min_length=3, max_length=255, examples=["Physical count reconciliation"])



class InventoryReservationRead(BaseModel):
    """Schema representing an active, committed, or released inventory reservation."""
    id: int
    reservation_code: str
    warehouse_id: int
    product_id: int
    batch_id: int
    plan_id: Optional[int] = None
    quantity: int
    status: str
    expires_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InventoryVerificationResult(BaseModel):
    """Audit verification result confirming authoritative inventory invariants."""
    is_valid: bool
    total_batches_checked: int
    total_warehouses_checked: int
    violations_count: int
    violations: list[str]
    migration_status: str
    details: dict


class InventorySummaryRead(BaseModel):
    """Aggregated inventory balance across all batches for a given warehouse and product."""
    warehouse_id: int
    warehouse_code: str
    product_id: int
    sku: str
    product_name: str
    total_on_hand: int
    total_reserved: int
    total_committed: int
    total_available: int
    batch_count: int
    earliest_expiry_date: Optional[date] = None

