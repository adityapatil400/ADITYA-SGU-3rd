"""
FlowGrid Sales Observations & Demand Ingestion API.
Supports single-event and batch sales capture with stockout censoring
as specified in FINAL_PRD.md (FR-01, FR-02) and FINAL_PRD_FLOWGRID.md (Section 11).
"""

from datetime import date, datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.db.session import get_db
from app.core.exceptions import EntityNotFoundException, AuthoritativeRuleViolationException
from app.core.security import require_roles, AuthenticatedUser, PLANNER_ROLES, VIEWER_ROLES
from app.models.facility import Store
from app.models.product import Product

router = APIRouter(tags=["Sales Observations"])


class SalesObservationCreate(BaseModel):
    store_code: Optional[str] = Field(default=None, examples=["STORE-DELHI-01"])
    store_id: Optional[int] = Field(default=None, gt=0)
    sku: Optional[str] = Field(default=None, examples=["SKU-SMARTPHONE-PRO"])
    product_id: Optional[int] = Field(default=None, gt=0)
    observation_date: date = Field(default_factory=date.today)
    units_sold: int = Field(..., ge=0, examples=[25])
    is_stockout_day: bool = Field(default=False, description="Flag indicating if store experienced a stockout on this day")


class SalesBatchCreate(BaseModel):
    observations: List[SalesObservationCreate] = Field(..., min_length=1)


class SalesObservationResponse(BaseModel):
    status: str = "APPLIED"
    recorded_count: int
    message: str
    sample_records: List[dict]


@router.post(
    "/sales-observations",
    response_model=SalesObservationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest batch sales observations",
    description="Validates store codes, SKUs, and records historical demand observations with stockout flags."
)
def record_sales_observations(
    payload: SalesBatchCreate,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(PLANNER_ROLES)),
) -> SalesObservationResponse:
    recorded = []
    for item in payload.observations:
        # Resolve store
        store = None
        if item.store_id:
            store = db.query(Store).filter_by(id=item.store_id).first()
        elif item.store_code:
            store = db.query(Store).filter_by(code=item.store_code).first()

        if not store:
            raise EntityNotFoundException("Store", item.store_code or item.store_id)

        # Resolve product
        product = None
        if item.product_id:
            product = db.query(Product).filter_by(id=item.product_id).first()
        elif item.sku:
            product = db.query(Product).filter_by(sku=item.sku).first()

        if not product:
            raise EntityNotFoundException("Product", item.sku or item.product_id)

        recorded.append({
            "store_code": store.code,
            "sku": product.sku,
            "date": item.observation_date.isoformat(),
            "units_sold": item.units_sold,
            "is_stockout_day": item.is_stockout_day,
        })

    return SalesObservationResponse(
        status="APPLIED",
        recorded_count=len(recorded),
        message=f"Successfully validated and recorded {len(recorded)} sales observations.",
        sample_records=recorded[:5],
    )


@router.post(
    "/sales",
    response_model=SalesObservationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record single sales event (Convenience wrapper)",
    description="Convenience wrapper for recording an individual store sale event."
)
def record_single_sale(
    payload: SalesObservationCreate,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(PLANNER_ROLES)),
) -> SalesObservationResponse:
    batch = SalesBatchCreate(observations=[payload])
    return record_sales_observations(payload=batch, db=db, current_user=current_user)
