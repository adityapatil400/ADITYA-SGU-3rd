from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, Integer, Float, Boolean, DateTime, func, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.models.enums import AgentAvailabilityStatus, AgentShiftStatus, VehicleType


class DeliveryAgent(Base):
    """
    Delivery Agent / Vehicle entity.
    Tracks availability, capacity, location freshness, and dispatch shift status
    as required by smartsystem.md / agent.md specification.
    """

    __tablename__ = "delivery_agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    phone: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    vehicle_type: Mapped[VehicleType] = mapped_column(
        SQLEnum(VehicleType, name="vehicle_type_enum"),
        default=VehicleType.VAN,
        nullable=False,
    )
    capacity_kg: Mapped[float] = mapped_column(Float, default=500.0, nullable=False)
    capacity_volume_cbm: Mapped[float] = mapped_column(Float, default=5.0, nullable=False)
    availability_status: Mapped[AgentAvailabilityStatus] = mapped_column(
        SQLEnum(AgentAvailabilityStatus, name="agent_availability_status_enum"),
        default=AgentAvailabilityStatus.AVAILABLE,
        nullable=False,
        index=True,
    )
    shift_status: Mapped[AgentShiftStatus] = mapped_column(
        SQLEnum(AgentShiftStatus, name="agent_shift_status_enum"),
        default=AgentShiftStatus.ON_SHIFT,
        nullable=False,
    )
    current_assignment_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    location_accuracy_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unavailability_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
