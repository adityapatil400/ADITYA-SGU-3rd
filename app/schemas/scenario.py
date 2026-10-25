from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import ScenarioType


class ScenarioSeedRequest(BaseModel):
    """Payload to seed a distribution scenario."""
    scenario_code: str = Field(..., max_length=50, examples=["SCENARIO-DIWALI-PEAK-2026"])
    name: str = Field(..., max_length=150, examples=["Diwali Electronics Demand Surge"])
    scenario_type: ScenarioType = Field(default=ScenarioType.DEMAND_SURGE)
    description: str = Field(default="", max_length=500)
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        examples=[{"affected_skus": ["SKU-PHONE-X"], "surge_percentage": 45, "critical_nodes": ["WH-WEST-01"]}]
    )
    seed_network_topology: bool = Field(
        default=True,
        description="Whether to also seed deterministic sample warehouses, stores, products, and batches for testing."
    )
    overwrite: bool = Field(
        default=False,
        description="Must be explicitly set to True to replace an existing scenario; prevents silent overwrite."
    )


class ScenarioResponse(BaseModel):
    """Response representing a seeded scenario."""
    id: int
    scenario_code: str
    name: str
    scenario_type: ScenarioType
    description: str
    parameters: Dict[str, Any]
    is_active: bool
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class ScenarioSeedResult(BaseModel):
    """Result of loading a seeded scenario."""
    scenario: ScenarioResponse
    seeded_warehouses_count: int = 0
    seeded_stores_count: int = 0
    seeded_products_count: int = 0
    seeded_batches_count: int = 0
    seeded_orders_count: int = 0
    message: str
