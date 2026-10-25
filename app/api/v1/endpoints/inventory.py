from datetime import date
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, Query, status, Body
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel, Field

from app.db.session import get_db
from app.core.exceptions import EntityNotFoundException, ConflictException, AuthoritativeRuleViolationException
from app.core.security import require_roles, AuthenticatedUser, PLANNER_ROLES, OPERATOR_ROLES
from app.models.enums import UserRole, ReservationStatus
from app.models.facility import Warehouse
from app.models.product import Product
from app.models.inventory import InventoryBatch, InventoryReservation
from app.schemas.inventory import (
    InventoryBatchCreate,
    InventoryBatchRead,
    InventorySummaryRead,
    InventoryBatchAdjust,
    InventoryReservationRead,
    InventoryVerificationResult,
)
from app.schemas.pagination import PaginationParams, PaginatedResponse
from app.services.inventory_service import InventoryService
from app.services.migration_service import DataMigrationService

router = APIRouter(prefix="/inventory", tags=["Inventory"])



@router.post(
    "/batches",
    response_model=InventoryBatchRead,
    status_code=status.HTTP_201_CREATED,
    summary="Receive an inventory batch into warehouse",
    description=(
        "Records physical stock arrival for a product batch. "
        "Atomically increments the warehouse current_inventory_version. "
        "Requires ADMIN or PLANNER role."
    )
)
def receive_inventory_batch(
    payload: InventoryBatchCreate,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(PLANNER_ROLES)),
) -> InventoryBatchRead:
    wh = db.query(Warehouse).filter_by(id=payload.warehouse_id).first()
    if not wh:
        raise EntityNotFoundException("Warehouse", payload.warehouse_id)

    prod = db.query(Product).filter_by(id=payload.product_id).first()
    if not prod:
        raise EntityNotFoundException("Product", payload.product_id)

    existing = (
        db.query(InventoryBatch)
        .filter_by(
            warehouse_id=payload.warehouse_id,
            product_id=payload.product_id,
            batch_number=payload.batch_number,
        )
        .first()
    )
    if existing:
        raise ConflictException(
            f"Batch '{payload.batch_number}' for product '{prod.sku}' already exists in warehouse '{wh.code}'."
        )

    batch = InventoryBatch(
        warehouse_id=payload.warehouse_id,
        product_id=payload.product_id,
        batch_number=payload.batch_number,
        on_hand_quantity=payload.on_hand_quantity,
        manufactured_date=payload.manufactured_date,
        expiry_date=payload.expiry_date,
        version=1,
    )
    db.add(batch)

    # Atomically increment authoritative warehouse inventory version
    wh.current_inventory_version += 1
    db.commit()
    db.refresh(batch)

    return InventoryBatchRead(
        id=batch.id,
        warehouse_id=batch.warehouse_id,
        warehouse_code=wh.code,
        product_id=batch.product_id,
        sku=prod.sku,
        product_name=prod.name,
        batch_number=batch.batch_number,
        manufactured_date=batch.manufactured_date,
        expiry_date=batch.expiry_date,
        on_hand_quantity=batch.on_hand_quantity,
        physical_quantity=batch.on_hand_quantity,
        reserved_quantity=batch.reserved_quantity,
        committed_quantity=batch.committed_quantity,
        available_quantity=batch.available_quantity,
        available_stock=batch.available_quantity,
        version=batch.version,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
    )


@router.get(
    "/batches",
    response_model=PaginatedResponse[InventoryBatchRead],
    summary="Retrieve inventory batches by warehouse, product, batch, and expiry",
    description=(
        "Queries physical stock batches across the distribution network. "
        "Clearly distinguishes on-hand, reserved, committed, and available quantities. "
        "Supports filtering by warehouse, SKU, batch number, and expiry threshold."
    )
)
def list_inventory_batches(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    warehouse_id: Optional[int] = Query(None, description="Filter by warehouse ID"),
    product_id: Optional[int] = Query(None, description="Filter by product ID"),
    sku: Optional[str] = Query(None, description="Filter by SKU"),
    batch_number: Optional[str] = Query(None, description="Filter by batch number"),
    expiring_before: Optional[date] = Query(None, description="Filter batches expiring before date"),
    db: Session = Depends(get_db),
) -> PaginatedResponse[InventoryBatchRead]:
    params = PaginationParams(page=page, page_size=page_size)
    query = db.query(InventoryBatch).join(Warehouse).join(Product)

    if warehouse_id:
        query = query.filter(InventoryBatch.warehouse_id == warehouse_id)
    if product_id:
        query = query.filter(InventoryBatch.product_id == product_id)
    if sku:
        query = query.filter(Product.sku == sku)
    if batch_number:
        query = query.filter(InventoryBatch.batch_number.ilike(f"%{batch_number}%"))
    if expiring_before:
        query = query.filter(InventoryBatch.expiry_date <= expiring_before)

    total = query.count()
    # Order by expiry date ascending (FEFO) then batch ID
    batches = (
        query.order_by(InventoryBatch.expiry_date.asc().nulls_last(), InventoryBatch.id.asc())
        .offset(params.offset)
        .limit(params.page_size)
        .all()
    )

    items = [
        InventoryBatchRead(
            id=b.id,
            warehouse_id=b.warehouse_id,
            warehouse_code=b.warehouse.code if b.warehouse else None,
            product_id=b.product_id,
            sku=b.product.sku if b.product else None,
            product_name=b.product.name if b.product else None,
            batch_number=b.batch_number,
            manufactured_date=b.manufactured_date,
            expiry_date=b.expiry_date,
            on_hand_quantity=b.on_hand_quantity,
            physical_quantity=b.on_hand_quantity,
            reserved_quantity=b.reserved_quantity,
            committed_quantity=b.committed_quantity,
            available_quantity=b.available_quantity,
            available_stock=b.available_quantity,
            version=b.version,
            created_at=b.created_at,
            updated_at=b.updated_at,
        )
        for b in batches
    ]

    return PaginatedResponse.create(items=items, total=total, params=params)


@router.get(
    "/summary",
    response_model=List[InventorySummaryRead],
    summary="Retrieve aggregated inventory summary",
    description="Returns total on-hand, reserved, committed, and available quantities grouped by warehouse and SKU."
)
def get_inventory_summary(
    warehouse_id: Optional[int] = Query(None, description="Filter by warehouse ID"),
    sku: Optional[str] = Query(None, description="Filter by product SKU"),
    db: Session = Depends(get_db),
) -> List[InventorySummaryRead]:
    query = (
        db.query(
            Warehouse.id.label("warehouse_id"),
            Warehouse.code.label("warehouse_code"),
            Product.id.label("product_id"),
            Product.sku.label("sku"),
            Product.name.label("product_name"),
            func.sum(InventoryBatch.on_hand_quantity).label("total_on_hand"),
            func.sum(InventoryBatch.reserved_quantity).label("total_reserved"),
            func.sum(InventoryBatch.committed_quantity).label("total_committed"),
            func.count(InventoryBatch.id).label("batch_count"),
            func.min(InventoryBatch.expiry_date).label("earliest_expiry_date"),
        )
        .join(InventoryBatch, InventoryBatch.warehouse_id == Warehouse.id)
        .join(Product, InventoryBatch.product_id == Product.id)
        .group_by(Warehouse.id, Warehouse.code, Product.id, Product.sku, Product.name)
    )

    if warehouse_id:
        query = query.filter(Warehouse.id == warehouse_id)
    if sku:
        query = query.filter(Product.sku == sku)

    rows = query.all()
    results = []
    for r in rows:
        on_hand = r.total_on_hand or 0
        reserved = r.total_reserved or 0
        committed = r.total_committed or 0
        available = max(0, on_hand - (reserved + committed))
        results.append(
            InventorySummaryRead(
                warehouse_id=r.warehouse_id,
                warehouse_code=r.warehouse_code,
                product_id=r.product_id,
                sku=r.sku,
                product_name=r.product_name,
                total_on_hand=on_hand,
                total_reserved=reserved,
                total_committed=committed,
                total_available=available,
                batch_count=r.batch_count or 0,
                earliest_expiry_date=r.earliest_expiry_date,
            )
        )
    return results


class PlanReservationRequest(BaseModel):
    plan_code: str = Field(..., description="Plan code to reserve stock for", examples=["PLAN-2026-001"])
    ttl_minutes: int = Field(default=30, ge=1, le=1440, description="Reservation TTL in minutes")


class PlanReleaseRequest(BaseModel):
    plan_code: str = Field(..., description="Plan code to release stock reservations for", examples=["PLAN-2026-001"])
    reason: str = Field(default="Manual operator release", description="Reason for releasing reservations")


class ScenarioMigrationRequest(BaseModel):
    scenario_code: str = Field(default="SCENARIO-SURGE-001", description="Scenario code to migrate")
    preserve_existing_versions: bool = Field(default=True, description="Preserve existing versions and timestamps")


@router.post(
    "/adjust",
    response_model=InventoryBatchRead,
    status_code=status.HTTP_200_OK,
    summary="Adjust physical stock on a batch with concurrency protection",
    description=(
        "Safely adjusts on-hand quantity for a batch. "
        "Enforces non-negative stock invariant and ensures reservations do not exceed physical stock. "
        "Atomically increments batch version and warehouse inventory version."
    )
)
@router.post(
    "/batches/{batch_id}/adjust",
    response_model=InventoryBatchRead,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
def adjust_batch_inventory(
    payload: InventoryBatchAdjust,
    batch_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(PLANNER_ROLES)),
) -> InventoryBatchRead:
    target_id = batch_id or payload.batch_id
    if not target_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="batch_id must be provided in URL path or request body")
    batch = InventoryService.adjust_batch_inventory(
        db=db,
        batch_id=target_id,
        quantity_delta=payload.quantity_delta,
        reason=payload.reason,
        user_id=current_user.id,
    )
    return InventoryBatchRead(
        id=batch.id,
        warehouse_id=batch.warehouse_id,
        warehouse_code=batch.warehouse.code if batch.warehouse else None,
        product_id=batch.product_id,
        sku=batch.product.sku if batch.product else None,
        product_name=batch.product.name if batch.product else None,
        batch_number=batch.batch_number,
        manufactured_date=batch.manufactured_date,
        expiry_date=batch.expiry_date,
        on_hand_quantity=batch.on_hand_quantity,
        physical_quantity=batch.physical_quantity,
        reserved_quantity=batch.reserved_quantity,
        committed_quantity=batch.committed_quantity,
        available_quantity=batch.available_quantity,
        available_stock=batch.available_stock,
        version=batch.version,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
    )


@router.post(
    "/reserve",
    response_model=List[InventoryReservationRead],
    status_code=status.HTTP_201_CREATED,
    summary="Reserve stock for a distribution plan",
    description="Transactionally reserves inventory batches for a plan, updating versions and enforcing invariants."
)
def reserve_inventory_for_plan(
    payload: PlanReservationRequest,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(PLANNER_ROLES)),
) -> List[InventoryReservationRead]:
    reservations = InventoryService.reserve_stock_for_plan(
        db=db,
        plan_code=payload.plan_code,
        ttl_minutes=payload.ttl_minutes,
    )
    return [
        InventoryReservationRead(
            id=r.id,
            reservation_code=r.reservation_code,
            warehouse_id=r.warehouse_id,
            product_id=r.product_id,
            batch_id=r.batch_id,
            plan_id=r.plan_id,
            quantity=r.quantity,
            status=r.status.value if hasattr(r.status, "value") else str(r.status),
            expires_at=r.expires_at,
            created_at=r.created_at,
        )
        for r in reservations
    ]


@router.post(
    "/release",
    status_code=status.HTTP_200_OK,
    summary="Release stock reservations for a plan",
    description="Releases active stock reservations, restoring available inventory and updating versions."
)
def release_inventory_reservations(
    payload: PlanReleaseRequest,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(PLANNER_ROLES)),
) -> Dict[str, Any]:
    count = InventoryService.release_reservations_for_plan(
        db=db,
        plan_code=payload.plan_code,
        reason=payload.reason,
    )
    return {"plan_code": payload.plan_code, "released_reservations_count": count, "status": "RELEASED"}


@router.get(
    "/reservations",
    response_model=List[InventoryReservationRead],
    summary="List inventory reservations",
    description="Queries inventory reservations with optional warehouse and status filtering."
)
def list_inventory_reservations(
    warehouse_id: Optional[int] = Query(None, description="Filter by warehouse ID"),
    plan_id: Optional[int] = Query(None, description="Filter by plan ID"),
    status: Optional[str] = Query(None, description="Filter by status (ACTIVE, COMMITTED, RELEASED, EXPIRED)"),
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(OPERATOR_ROLES)),
) -> List[InventoryReservationRead]:
    query = db.query(InventoryReservation)
    if warehouse_id:
        query = query.filter(InventoryReservation.warehouse_id == warehouse_id)
    if plan_id:
        query = query.filter(InventoryReservation.plan_id == plan_id)
    if status:
        query = query.filter(InventoryReservation.status == status)

    reservations = query.order_by(InventoryReservation.id.desc()).all()
    return [
        InventoryReservationRead(
            id=r.id,
            reservation_code=r.reservation_code,
            warehouse_id=r.warehouse_id,
            product_id=r.product_id,
            batch_id=r.batch_id,
            plan_id=r.plan_id,
            quantity=r.quantity,
            status=r.status.value if hasattr(r.status, "value") else str(r.status),
            expires_at=r.expires_at,
            created_at=r.created_at,
        )
        for r in reservations
    ]


@router.post(
    "/verify",
    response_model=InventoryVerificationResult,
    status_code=status.HTTP_200_OK,
    summary="Verify inventory invariants against PostgreSQL",
    description="Runs authoritative invariant audit: non-negative stock, available calculation, reservation limits, and versioning."
)
def verify_inventory_invariants(
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(OPERATOR_ROLES)),
) -> InventoryVerificationResult:
    report = DataMigrationService.verify_inventory_invariants(db=db)
    return InventoryVerificationResult(**report)


@router.post(
    "/migrate",
    status_code=status.HTTP_200_OK,
    summary="Migrate seeded scenario data into PostgreSQL without duplicates",
    description="Safely migrates seeded scenario data into authoritative PostgreSQL database and verifies invariants."
)
def execute_safe_inventory_migration(
    payload: Optional[ScenarioMigrationRequest] = None,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(PLANNER_ROLES)),
) -> Dict[str, Any]:
    code = payload.scenario_code if payload else "SCENARIO-SURGE-001"
    return DataMigrationService.execute_safe_migration(db=db, scenario_code=code)


@router.post(
    "/seed-flipkart",
    status_code=status.HTTP_200_OK,
    summary="Migrate complete Flipkart 100-SKU network into database",
    description="Loads 3 warehouses, 30 stores, 100 FMCG SKUs, 600 FEFO inventory batches, and delivery agents into authoritative database."
)
def seed_flipkart_network_data(
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(PLANNER_ROLES)),
) -> Dict[str, Any]:
    return DataMigrationService.migrate_flipkart_catalog(db=db)


