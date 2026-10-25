from typing import TYPE_CHECKING
from sqlalchemy import String, Integer, Boolean, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.inventory import InventoryBatch
    from app.models.order import DistributionOrderItem
    from app.models.plan import PlanAllocation


class Product(Base):
    """
    Catalog product tracked across warehouses, batches, and allocation plans.
    """

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    sku: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(100), index=True, default="General", nullable=False)
    unit_of_measure: Mapped[str] = mapped_column(String(20), default="EACH", nullable=False)
    
    # Perishable & Expiry tracking metadata
    shelf_life_days: Mapped[int] = mapped_column(Integer, default=365, nullable=False)
    is_perishable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    unit_cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    batches: Mapped[list["InventoryBatch"]] = relationship(
        "InventoryBatch",
        back_populates="product",
        cascade="all, delete-orphan",
    )
    order_items: Mapped[list["DistributionOrderItem"]] = relationship(
        "DistributionOrderItem",
        back_populates="product",
    )
    allocations: Mapped[list["PlanAllocation"]] = relationship(
        "PlanAllocation",
        back_populates="product",
    )
