from datetime import date, datetime
from typing import Optional, TYPE_CHECKING
from sqlalchemy import (
    String, Integer, Date, DateTime, ForeignKey, UniqueConstraint, CheckConstraint, Enum as SQLEnum
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
from app.models.enums import ReservationStatus

if TYPE_CHECKING:
    from app.models.node import DistributionNode
    from app.models.facility import Warehouse
    from app.models.product import Product
    from app.models.plan import DistributionPlan, PlanAllocation


class AuthoritativeInventory(Base):
    """
    Authoritative aggregate inventory summary per warehouse and product (or SKU).
    Managed exclusively by the backend authority.
    """

    __tablename__ = "authoritative_inventories"
    __table_args__ = (
        UniqueConstraint("node_id", "sku", name="uq_node_sku"),
        CheckConstraint("on_hand_quantity >= 0", name="chk_auth_on_hand_non_negative"),
        CheckConstraint("reserved_quantity >= 0", name="chk_auth_reserved_non_negative"),
        CheckConstraint(
            "on_hand_quantity >= (reserved_quantity + committed_quantity)",
            name="chk_auth_available_non_negative"
        ),
        CheckConstraint("version >= 1", name="chk_auth_version_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    node_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("distribution_nodes.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )
    sku: Mapped[str] = mapped_column(String(100), index=True, nullable=False)

    on_hand_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reserved_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    committed_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    safety_stock: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Optimistic concurrency version
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Relationship to distribution node
    node: Mapped["DistributionNode"] = relationship("DistributionNode", back_populates="inventories")

    @property
    def available_quantity(self) -> int:
        """Available units after accounting for reservations and commitments."""
        return max(0, self.on_hand_quantity - (self.reserved_quantity + self.committed_quantity))

    @property
    def free_above_safety_stock(self) -> int:
        """Units available without breaching safety stock thresholds."""
        return max(0, self.available_quantity - self.safety_stock)


class InventoryBatch(Base):
    """
    Authoritative batch-level inventory.
    Tracks physical stock by product, warehouse, batch number, and expiry date.
    
    CRITICAL ARCHITECTURAL CONSTRAINTS:
    - on_hand_quantity >= (reserved_quantity + committed_quantity)
      strictly prevents negative available inventory at the database level.
    - Version counter supports batch-level optimistic concurrency.
    """

    __tablename__ = "inventory_batches"
    __table_args__ = (
        UniqueConstraint("warehouse_id", "product_id", "batch_number", name="uq_wh_product_batch"),
        CheckConstraint("on_hand_quantity >= 0", name="chk_batch_on_hand_non_negative"),
        CheckConstraint("reserved_quantity >= 0", name="chk_batch_reserved_non_negative"),
        CheckConstraint("committed_quantity >= 0", name="chk_batch_committed_non_negative"),
        CheckConstraint(
            "on_hand_quantity >= (reserved_quantity + committed_quantity)",
            name="chk_batch_available_non_negative"
        ),
        CheckConstraint("version >= 1", name="chk_batch_version_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    warehouse_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("warehouses.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    product_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    batch_number: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    
    # Expiry and manufacturing dates for FEFO (First Expired First Out) allocation
    manufactured_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[Optional[date]] = mapped_column(Date, index=True, nullable=True)

    # Authoritative balance states
    on_hand_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reserved_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    committed_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Optimistic locking version for this batch
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Relationships
    warehouse: Mapped["Warehouse"] = relationship("Warehouse", back_populates="batches")
    product: Mapped["Product"] = relationship("Product", back_populates="batches")
    reservations: Mapped[list["InventoryReservation"]] = relationship(
        "InventoryReservation",
        back_populates="batch",
        cascade="all, delete-orphan",
    )
    allocations: Mapped[list["PlanAllocation"]] = relationship(
        "PlanAllocation",
        back_populates="batch",
    )

    @property
    def physical_quantity(self) -> int:
        """Physical on-hand quantity."""
        return self.on_hand_quantity

    @property
    def available_stock(self) -> int:
        """
        Available stock = physical quantity - reserved quantity (accounting for commitments).
        Guaranteed to be non-negative by database check constraint chk_batch_available_non_negative.
        """
        return max(0, self.on_hand_quantity - (self.reserved_quantity + self.committed_quantity))

    @property
    def available_quantity(self) -> int:
        """
        Quantity free for new allocation proposals.
        Guaranteed to be non-negative by database check constraint chk_batch_available_non_negative.
        """
        return self.available_stock



class InventoryReservation(Base):
    """
    Temporary or validated inventory reservation held against a batch
    during plan validation before authoritative commitment.
    """

    __tablename__ = "inventory_reservations"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="chk_reservation_qty_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    reservation_code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    warehouse_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("warehouses.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    product_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    batch_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("inventory_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    plan_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("distribution_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ReservationStatus] = mapped_column(
        SQLEnum(ReservationStatus, name="reservation_status_enum"),
        default=ReservationStatus.ACTIVE,
        nullable=False,
        index=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    warehouse: Mapped["Warehouse"] = relationship("Warehouse", back_populates="reservations")
    batch: Mapped["InventoryBatch"] = relationship("InventoryBatch", back_populates="reservations")
    plan: Mapped[Optional["DistributionPlan"]] = relationship("DistributionPlan", back_populates="reservations")


# Backward-compatible model aliases
Reservation = InventoryReservation
