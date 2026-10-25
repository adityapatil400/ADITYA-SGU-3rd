from typing import Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.exceptions import EntityNotFoundException, ConflictException
from app.core.security import require_roles, AuthenticatedUser, PLANNER_ROLES
from app.models.enums import UserRole, StoreType
from app.models.facility import Store
from app.schemas.facility import StoreCreate, StoreUpdate, StoreRead
from app.schemas.pagination import PaginationParams, PaginatedResponse

router = APIRouter(prefix="/stores", tags=["Stores & Retailers"])


@router.post(
    "",
    response_model=StoreRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new store or retailer",
    description="Registers a destination store or retail fulfillment node. Requires ADMIN or PLANNER role."
)
def create_store(
    payload: StoreCreate,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(PLANNER_ROLES)),
) -> Store:
    existing = db.query(Store).filter_by(code=payload.code).first()
    if existing:
        raise ConflictException(f"Store with code '{payload.code}' already exists.")

    store = Store(
        code=payload.code,
        name=payload.name,
        region=payload.region,
        address=payload.address,
        store_type=payload.store_type,
        priority_tier=payload.priority_tier,
        is_active=payload.is_active,
    )
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


@router.get(
    "",
    response_model=PaginatedResponse[StoreRead],
    summary="List stores or retailers",
    description="Returns paginated list of stores with optional region and store type filtering."
)
def list_stores(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    region: Optional[str] = Query(None, description="Filter by geographic region"),
    store_type: Optional[StoreType] = Query(None, description="Filter by store type"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    db: Session = Depends(get_db),
) -> PaginatedResponse[StoreRead]:
    params = PaginationParams(page=page, page_size=page_size)
    query = db.query(Store)

    if region:
        query = query.filter(Store.region.ilike(f"%{region}%"))
    if store_type:
        query = query.filter(Store.store_type == store_type)
    if is_active is not None:
        query = query.filter(Store.is_active == is_active)

    total = query.count()
    items = query.order_by(Store.id.asc()).offset(params.offset).limit(params.page_size).all()

    return PaginatedResponse.create(items=items, total=total, params=params)


@router.get(
    "/{store_id}",
    response_model=StoreRead,
    summary="Retrieve a store by ID",
    description="Returns detailed profile of a retail fulfillment location."
)
def get_store(
    store_id: int,
    db: Session = Depends(get_db),
) -> Store:
    store = db.query(Store).filter_by(id=store_id).first()
    if not store:
        raise EntityNotFoundException("Store", store_id)
    return store


@router.put(
    "/{store_id}",
    response_model=StoreRead,
    summary="Update store details",
    description="Updates name, region, priority tier, or status of an existing store. Requires ADMIN or OPERATOR role."
)
def update_store(
    store_id: int,
    payload: StoreUpdate,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(PLANNER_ROLES)),
) -> Store:
    store = db.query(Store).filter_by(id=store_id).first()
    if not store:
        raise EntityNotFoundException("Store", store_id)

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(store, field, value)

    db.commit()
    db.refresh(store)
    return store


# Backward-compatible Retailer API router
retailer_router = APIRouter(prefix="/retailers", tags=["Stores & Retailers"])
retailer_router.add_api_route("", create_store, methods=["POST"], response_model=StoreRead, status_code=status.HTTP_201_CREATED, summary="Create a new retailer")
retailer_router.add_api_route("", list_stores, methods=["GET"], response_model=PaginatedResponse[StoreRead], summary="List retailers")
retailer_router.add_api_route("/{store_id}", get_store, methods=["GET"], response_model=StoreRead, summary="Retrieve a retailer by ID")
retailer_router.add_api_route("/{store_id}", update_store, methods=["PUT"], response_model=StoreRead, summary="Update retailer details")

