from typing import Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.exceptions import EntityNotFoundException, ConflictException
from app.core.security import require_roles, AuthenticatedUser, PLANNER_ROLES
from app.models.enums import UserRole
from app.models.product import Product
from app.schemas.product import ProductCreate, ProductUpdate, ProductRead
from app.schemas.pagination import PaginationParams, PaginatedResponse

router = APIRouter(prefix="/products", tags=["Products"])


@router.post(
    "",
    response_model=ProductRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new catalog product",
    description="Registers an inventory SKU tracked across the distribution network. Requires ADMIN or PLANNER role."
)
def create_product(
    payload: ProductCreate,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(PLANNER_ROLES)),
) -> Product:
    existing = db.query(Product).filter_by(sku=payload.sku).first()
    if existing:
        raise ConflictException(f"Product with SKU '{payload.sku}' already exists.")

    product = Product(
        sku=payload.sku,
        name=payload.name,
        category=payload.category,
        unit_of_measure=payload.unit_of_measure,
        shelf_life_days=payload.shelf_life_days,
        is_perishable=payload.is_perishable,
        unit_cost=payload.unit_cost,
        is_active=payload.is_active,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.get(
    "",
    response_model=PaginatedResponse[ProductRead],
    summary="List catalog products",
    description="Returns paginated list of products with optional category and perishable status filtering."
)
def list_products(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    category: Optional[str] = Query(None, description="Filter by category"),
    is_perishable: Optional[bool] = Query(None, description="Filter by perishable flag"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    db: Session = Depends(get_db),
) -> PaginatedResponse[ProductRead]:
    params = PaginationParams(page=page, page_size=page_size)
    query = db.query(Product)

    if category:
        query = query.filter(Product.category.ilike(f"%{category}%"))
    if is_perishable is not None:
        query = query.filter(Product.is_perishable == is_perishable)
    if is_active is not None:
        query = query.filter(Product.is_active == is_active)

    total = query.count()
    items = query.order_by(Product.id.asc()).offset(params.offset).limit(params.page_size).all()

    return PaginatedResponse.create(items=items, total=total, params=params)


@router.get(
    "/{product_id}",
    response_model=ProductRead,
    summary="Retrieve a product by ID",
    description="Returns product specifications and shelf-life metadata."
)
def get_product(
    product_id: int,
    db: Session = Depends(get_db),
) -> Product:
    product = db.query(Product).filter_by(id=product_id).first()
    if not product:
        raise EntityNotFoundException("Product", product_id)
    return product


@router.put(
    "/{product_id}",
    response_model=ProductRead,
    summary="Update product details",
    description="Updates name, category, unit cost, or shelf-life parameters. Requires ADMIN or OPERATOR role."
)
def update_product(
    product_id: int,
    payload: ProductUpdate,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(PLANNER_ROLES)),
) -> Product:
    product = db.query(Product).filter_by(id=product_id).first()
    if not product:
        raise EntityNotFoundException("Product", product_id)

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(product, field, value)

    db.commit()
    db.refresh(product)
    return product
