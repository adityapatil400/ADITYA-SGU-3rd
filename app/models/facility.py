from typing import TYPE_CHECKING
from sqlalchemy import String, Integer, Boolean, CheckConstraint, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
from app.models.enums import StoreType

if TYPE_CHECKING:
    from app.models.inventory import InventoryBatch, InventoryReservation
    from app.models.plan import PlanAllocation
    from app.models.order import DistributionOrder


class Warehouse(Base):
    """
    Authoritative distribution warehouse managing physical inventory batches.
    Maintains current_inventory_version to guarantee optimistic concurrency
    during candidate plan validation and commitment.
    """

    __tablename__ = "warehouses"
    __table_args__ = (
        CheckConstraint("capacity_limit > 0", name="chk_wh_capacity_positive"),
        CheckConstraint("current_inventory_version >= 1", name="chk_wh_version_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    region: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    address: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    capacity_limit: Mapped[int] = mapped_column(Integer, default=500000, nullable=False)

    # Authoritative inventory version: incremented atomically whenever batch quantities change
    current_inventory_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    batches: Mapped[list["InventoryBatch"]] = relationship(
        "InventoryBatch",
        back_populates="warehouse",
        cascade="all, delete-orphan",
    )
    reservations: Mapped[list["InventoryReservation"]] = relationship(
        "InventoryReservation",
        back_populates="warehouse",
        cascade="all, delete-orphan",
    )
    allocations_out: Mapped[list["PlanAllocation"]] = relationship(
        "PlanAllocation",
        back_populates="source_warehouse",
        foreign_keys="PlanAllocation.source_warehouse_id",
    )


class Store(Base):
    """
    Destination store or retail fulfillment location demanding inventory.
    """

    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    region: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    address: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    store_type: Mapped[StoreType] = mapped_column(
        SQLEnum(StoreType, name="store_type_enum"),
        default=StoreType.REGIONAL_RETAIL,
        nullable=False,
    )
    priority_tier: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    orders: Mapped[list["DistributionOrder"]] = relationship(
        "DistributionOrder",
        back_populates="destination_store",
        cascade="all, delete-orphan",
    )
    allocations_in: Mapped[list["PlanAllocation"]] = relationship(
        "PlanAllocation",
        back_populates="destination_store",
        foreign_keys="PlanAllocation.destination_store_id",
    )


# Backward-compatible model aliases
Retailer = Store

