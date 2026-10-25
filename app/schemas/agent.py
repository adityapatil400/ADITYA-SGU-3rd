from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import AgentAvailabilityStatus, AgentShiftStatus, VehicleType


class AgentBase(BaseModel):
    code: str = Field(..., max_length=50, examples=["AGENT-MUM-01"])
    name: str = Field(..., max_length=150, examples=["Suresh Kumar"])
    phone: str = Field(default="", max_length=50, examples=["+91-9876543210"])
    vehicle_type: VehicleType = Field(default=VehicleType.VAN)
    capacity_kg: float = Field(default=500.0, gt=0, examples=[500.0])
    capacity_volume_cbm: float = Field(default=5.0, gt=0, examples=[5.0])
    availability_status: AgentAvailabilityStatus = Field(default=AgentAvailabilityStatus.AVAILABLE)
    shift_status: AgentShiftStatus = Field(default=AgentShiftStatus.ON_SHIFT)


class AgentCreate(AgentBase):
    pass


class AgentUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=150)
    phone: Optional[str] = Field(default=None, max_length=50)
    vehicle_type: Optional[VehicleType] = None
    capacity_kg: Optional[float] = Field(default=None, gt=0)
    capacity_volume_cbm: Optional[float] = Field(default=None, gt=0)
    availability_status: Optional[AgentAvailabilityStatus] = None
    shift_status: Optional[AgentShiftStatus] = None
    is_active: Optional[bool] = None


class AgentHeartbeatRequest(BaseModel):
    latitude: Optional[float] = Field(default=None, ge=-90.0, le=90.0, examples=[19.0760])
    longitude: Optional[float] = Field(default=None, ge=-180.0, le=180.0, examples=[72.8777])
    location_accuracy_m: Optional[float] = Field(default=10.0, ge=0.0, examples=[10.0])
    shift_status: Optional[AgentShiftStatus] = None
    recorded_at: Optional[datetime] = None


class AgentAvailabilityRequest(BaseModel):
    availability_status: AgentAvailabilityStatus = Field(
        ...,
        description="Declared availability status (e.g. UNAVAILABLE or AVAILABLE)",
        examples=[AgentAvailabilityStatus.UNAVAILABLE],
    )
    reason: Optional[str] = Field(default="Driver declared unavailable", max_length=255)
    idempotency_key: Optional[str] = Field(default=None, description="Idempotency key for offline sync")


class AgentRead(AgentBase):
    id: int
    current_assignment_id: Optional[int] = None
    last_seen_at: datetime
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_accuracy_m: Optional[float] = None
    unavailability_reason: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AgentReassignmentRecommendation(BaseModel):
    unavailable_agent_id: int
    unavailable_agent_code: str
    reassigned_orders_count: int
    affected_order_numbers: List[str] = Field(default_factory=list)
    recommended_replacement_agent_id: Optional[int] = None
    recommended_replacement_agent_code: Optional[str] = None
    reason: str
