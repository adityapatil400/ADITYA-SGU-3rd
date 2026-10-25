from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import StoreType


# ==============================================================================
# Warehouse Schemas
# ==============================================================================

class WarehouseBase(BaseModel):
    code: str = Field(..., max_length=50, examples=["WH-MUMBAI-01"])
    name: str = Field(..., max_length=150, examples=["Western Regional Distribution Center"])
    region: str = Field(..., max_length=50, examples=["West"])
    address: str = Field(default="", max_length=255, examples=["Bhiwandi Logistics Park, Mumbai"])
    capacity_limit: int = Field(default=500000, gt=0, examples=[500000])
    is_active: bool = True


class WarehouseCreate(WarehouseBase):
    pass


class WarehouseUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=150)
    region: Optional[str] = Field(default=None, max_length=50)
    address: Optional[str] = Field(default=None, max_length=255)
    capacity_limit: Optional[int] = Field(default=None, gt=0)
    is_active: Optional[bool] = None


class WarehouseRead(WarehouseBase):
    id: int
    current_inventory_version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ==============================================================================
# Store / Retailer Schemas
# ==============================================================================

class StoreBase(BaseModel):
    code: str = Field(..., max_length=50, examples=["STORE-PUNE-01"])
    name: str = Field(..., max_length=150, examples=["Pune Central Retail Hub"])
    region: str = Field(..., max_length=50, examples=["West"])
    address: str = Field(default="", max_length=255, examples=["FC Road, Pune"])
    store_type: StoreType = Field(default=StoreType.REGIONAL_RETAIL)
    priority_tier: int = Field(default=1, ge=1, le=5, examples=[1])
    is_active: bool = True


class StoreCreate(StoreBase):
    pass


class StoreUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=150)
    region: Optional[str] = Field(default=None, max_length=50)
    address: Optional[str] = Field(default=None, max_length=255)
    store_type: Optional[StoreType] = None
    priority_tier: Optional[int] = Field(default=None, ge=1, le=5)
    is_active: Optional[bool] = None


class StoreRead(StoreBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
