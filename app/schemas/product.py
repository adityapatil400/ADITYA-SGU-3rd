from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class ProductBase(BaseModel):
    sku: str = Field(..., max_length=100, examples=["SKU-PHONE-X"])
    name: str = Field(..., max_length=200, examples=["Smartphone X Pro 128GB"])
    category: str = Field(default="Electronics", max_length=100, examples=["Electronics"])
    unit_of_measure: str = Field(default="EACH", max_length=20, examples=["EACH"])
    shelf_life_days: int = Field(default=365, ge=0, examples=[365])
    is_perishable: bool = Field(default=False)
    unit_cost: float = Field(default=0.0, ge=0.0, examples=[499.99])
    is_active: bool = True


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    category: Optional[str] = Field(default=None, max_length=100)
    unit_of_measure: Optional[str] = Field(default=None, max_length=20)
    shelf_life_days: Optional[int] = Field(default=None, ge=0)
    is_perishable: Optional[bool] = None
    unit_cost: Optional[float] = Field(default=None, ge=0.0)
    is_active: Optional[bool] = None


class ProductRead(ProductBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
