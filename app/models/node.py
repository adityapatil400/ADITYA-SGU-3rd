from typing import TYPE_CHECKING
from sqlalchemy import String, Integer, Boolean, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
from app.models.enums import NodeType

if TYPE_CHECKING:
    from app.models.inventory import AuthoritativeInventory


class DistributionNode(Base):
    """Represents a physical distribution center, hub, or fulfillment node."""
    
    __tablename__ = "distribution_nodes"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    node_type: Mapped[NodeType] = mapped_column(
        SQLEnum(NodeType, name="node_type_enum"),
        nullable=False,
        default=NodeType.REGIONAL_DC
    )
    region: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    capacity_limit: Mapped[int] = mapped_column(Integer, default=100000, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    
    # Relationships
    inventories: Mapped[list["AuthoritativeInventory"]] = relationship(
        "AuthoritativeInventory",
        back_populates="node",
        cascade="all, delete-orphan"
    )
