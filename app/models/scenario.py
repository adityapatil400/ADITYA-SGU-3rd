from typing import Any, Dict, TYPE_CHECKING
from sqlalchemy import String, Integer, Boolean, JSON, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
from app.models.enums import ScenarioType

if TYPE_CHECKING:
    from app.models.plan import DistributionPlan


class SeededScenario(Base):
    """
    Seeded distribution scenario representing an operational event
    (e.g., regional demand surge, warehouse outage, inventory rebalancing).
    """
    
    __tablename__ = "seeded_scenarios"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    scenario_code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    scenario_type: Mapped[ScenarioType] = mapped_column(
        SQLEnum(ScenarioType, name="scenario_type_enum"),
        nullable=False,
        default=ScenarioType.DEMAND_SURGE
    )
    description: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    
    # Dynamic operational parameters (target nodes, affected SKUs, surge percentage, constraints)
    parameters: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    
    # Plans generated under this scenario
    plans: Mapped[list["DistributionPlan"]] = relationship(
        "DistributionPlan",
        back_populates="scenario",
        cascade="all, delete-orphan"
    )
