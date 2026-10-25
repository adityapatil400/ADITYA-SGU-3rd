from typing import Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db.session import get_db
from app.core.exceptions import EntityNotFoundException, ConflictException
from app.core.security import require_roles, AuthenticatedUser, PLANNER_ROLES
from app.models.enums import UserRole
from app.models.facility import Warehouse
from app.schemas.facility import WarehouseCreate, WarehouseUpdate, WarehouseRead
from app.schemas.pagination import PaginationParams, PaginatedResponse

router = APIRouter(prefix="/warehouses", tags=["Warehouses"])


@router.post(
    "",
    response_model=WarehouseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new distribution warehouse",
    description="Registers an authoritative warehouse facility. Requires ADMIN or PLANNER role."
)
def create_warehouse(
    payload: WarehouseCreate,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(PLANNER_ROLES)),
) -> Warehouse:
    existing = db.query(Warehouse).filter_by(code=payload.code).first()
    if existing:
        raise ConflictException(f"Warehouse with code '{payload.code}' already exists.")

    wh = Warehouse(
        code=payload.code,
        name=payload.name,
        region=payload.region,
        address=payload.address,
        capacity_limit=payload.capacity_limit,
        is_active=payload.is_active,
        current_inventory_version=1,
    )
    db.add(wh)
    db.commit()
    db.refresh(wh)
    return wh


@router.get(
    "",
    response_model=PaginatedResponse[WarehouseRead],
    summary="List distribution warehouses",
    description="Returns paginated list of warehouses with optional region and active status filtering."
)
def list_warehouses(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    region: Optional[str] = Query(None, description="Filter by operational region"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    db: Session = Depends(get_db),
) -> PaginatedResponse[WarehouseRead]:
    params = PaginationParams(page=page, page_size=page_size)
    query = db.query(Warehouse)

    if region:
        query = query.filter(Warehouse.region.ilike(f"%{region}%"))
    if is_active is not None:
        query = query.filter(Warehouse.is_active == is_active)

    total = query.count()
    items = query.order_by(Warehouse.id.asc()).offset(params.offset).limit(params.page_size).all()

    return PaginatedResponse.create(items=items, total=total, params=params)


@router.get(
    "/{warehouse_id}",
    response_model=WarehouseRead,
    summary="Retrieve a warehouse by ID",
    description="Returns full warehouse details including its current authoritative inventory version."
)
def get_warehouse(
    warehouse_id: int,
    db: Session = Depends(get_db),
) -> Warehouse:
    wh = db.query(Warehouse).filter_by(id=warehouse_id).first()
    if not wh:
        raise EntityNotFoundException("Warehouse", warehouse_id)
    return wh


@router.put(
    "/{warehouse_id}",
    response_model=WarehouseRead,
    summary="Update warehouse details",
    description="Updates capacity, region, or status of an existing warehouse. Requires ADMIN or OPERATOR role."
)
def update_warehouse(
    warehouse_id: int,
    payload: WarehouseUpdate,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(PLANNER_ROLES)),
) -> Warehouse:
    wh = db.query(Warehouse).filter_by(id=warehouse_id).first()
    if not wh:
        raise EntityNotFoundException("Warehouse", warehouse_id)

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(wh, field, value)

    db.commit()
    db.refresh(wh)
    return wh
