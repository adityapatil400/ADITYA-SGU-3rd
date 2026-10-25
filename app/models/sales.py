"""
Sales Observations database model for PostgreSQL.
Captures store-level daily sales with stockout censoring as specified in FINAL_PRD.md.
"""

from datetime import date, datetime, timezone
from typing import TYPE_CHECKING
from sqlalchemy import (
    Integer, Date, DateTime, Boolean, ForeignKey, UniqueConstraint, CheckConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.facility import Store
    from app.models.product import Product


class SalesObservation(Base):
    """
    Daily sales observation for a given store and product.
    Includes stockout-censoring flag (is_stockout_day) to prevent stockout distortion in forecasts.
    """

    __tablename__ = "sales_observations"
    __table_args__ = (
        UniqueConstraint("store_id", "product_id", "observation_date", name="uq_store_product_date"),
        CheckConstraint("units_sold >= 0", name="chk_sales_units_non_negative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    store_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    product_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    observation_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    units_sold: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_stockout_day: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    # Relationships
    store: Mapped["Store"] = relationship("Store")
    product: Mapped["Product"] = relationship("Product")
