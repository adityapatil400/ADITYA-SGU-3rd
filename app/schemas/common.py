from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import NodeType


class DistributionNodeBase(BaseModel):
    code: str = Field(..., max_length=50, examples=["DC-NORTH-01"])
    name: str = Field(..., max_length=100, examples=["North Hub"])
    node_type: NodeType = NodeType.REGIONAL_DC
    region: str = Field(..., max_length=50, examples=["North"])
    capacity_limit: int = Field(default=100000, ge=1)
    is_active: bool = True


class DistributionNodeCreate(DistributionNodeBase):
    pass


class DistributionNodeRead(DistributionNodeBase):
    id: int
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class AuthoritativeInventoryRead(BaseModel):
    id: int
    node_id: int
    sku: str
    on_hand_quantity: int
    reserved_quantity: int
    safety_stock: int
    available_quantity: int
    free_above_safety_stock: int
    version: int
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)
