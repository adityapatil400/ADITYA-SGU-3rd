from datetime import datetime
from typing import Optional, TYPE_CHECKING
from sqlalchemy import (
    String, Integer, DateTime, ForeignKey, CheckConstraint, Enum as SQLEnum
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
from app.models.enums import OrderStatus, OrderPriority

if TYPE_CHECKING:
    from app.models.facility import Store
    from app.models.product import Product
    from app.models.plan import PlanAllocation


class DistributionOrder(Base):
    """
    Distribution demand order requested by a destination store/retailer.
    """

    __tablename__ = "distribution_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    order_number: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    destination_store_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    source_warehouse_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("warehouses.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    status: Mapped[OrderStatus] = mapped_column(
        SQLEnum(OrderStatus, name="order_status_enum"),
        default=OrderStatus.PENDING,
        nullable=False,
        index=True
    )
    priority: Mapped[OrderPriority] = mapped_column(
        SQLEnum(OrderPriority, name="order_priority_enum"),
        default=OrderPriority.NORMAL,
        nullable=False
    )
    required_delivery_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    destination_store: Mapped["Store"] = relationship("Store", back_populates="orders")
    items: Mapped[list["DistributionOrderItem"]] = relationship(
        "DistributionOrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
    )


class DistributionOrderItem(Base):
    """
    Individual line item in a distribution demand order.
    """

    __tablename__ = "distribution_order_items"
    __table_args__ = (
        CheckConstraint("requested_quantity > 0", name="chk_order_item_req_qty_positive"),
        CheckConstraint("fulfilled_quantity >= 0", name="chk_order_item_ful_qty_non_negative"),
        CheckConstraint(
            "fulfilled_quantity <= requested_quantity",
            name="chk_order_item_ful_lte_req"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    order_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("distribution_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    product_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    requested_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    fulfilled_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    order: Mapped["DistributionOrder"] = relationship("DistributionOrder", back_populates="items")
    product: Mapped["Product"] = relationship("Product", back_populates="order_items")
    allocations: Mapped[list["PlanAllocation"]] = relationship(
        "PlanAllocation",
        back_populates="order_item",
    )


# Backward-compatible model aliases
Order = DistributionOrder
OrderItem = DistributionOrderItem

