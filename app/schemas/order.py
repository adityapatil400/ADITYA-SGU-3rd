from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import OrderStatus, OrderPriority


class DistributionOrderItemCreate(BaseModel):
    product_id: int = Field(..., gt=0, examples=[1])
    requested_quantity: int = Field(..., gt=0, examples=[100])


class DistributionOrderItemRead(BaseModel):
    id: int
    order_id: int
    product_id: int
    sku: Optional[str] = None
    product_name: Optional[str] = None
    requested_quantity: int
    fulfilled_quantity: int

    model_config = ConfigDict(from_attributes=True)


class DistributionOrderCreate(BaseModel):
    order_number: str = Field(..., max_length=64, examples=["ORD-20261024-001"])
    destination_store_id: int = Field(..., gt=0, examples=[1])
    source_warehouse_id: Optional[int] = Field(None, gt=0, examples=[1])
    priority: OrderPriority = Field(default=OrderPriority.NORMAL)
    required_delivery_date: Optional[datetime] = None
    items: List[DistributionOrderItemCreate] = Field(..., min_length=1)


class DistributionOrderRead(BaseModel):
    id: int
    order_number: str
    destination_store_id: int
    destination_store_code: Optional[str] = None
    source_warehouse_id: Optional[int] = None
    source_warehouse_code: Optional[str] = None
    status: OrderStatus
    priority: OrderPriority
    required_delivery_date: Optional[datetime]
    items: List[DistributionOrderItemRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
