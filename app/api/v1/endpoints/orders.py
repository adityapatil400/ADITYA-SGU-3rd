from typing import Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.exceptions import EntityNotFoundException, ConflictException
from app.core.security import require_roles, AuthenticatedUser, PLANNER_ROLES
from app.models.enums import UserRole, OrderStatus
from app.models.facility import Store, Warehouse
from app.models.product import Product
from app.models.order import DistributionOrder, DistributionOrderItem
from app.schemas.order import (
    DistributionOrderCreate,
    DistributionOrderRead,
    DistributionOrderItemRead,
)
from app.schemas.pagination import PaginationParams, PaginatedResponse

router = APIRouter(prefix="/orders", tags=["Distribution Orders"])


@router.post(
    "",
    response_model=DistributionOrderRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new distribution order",
    description="Submits a demand order with line items for a destination store. Requires ADMIN or PLANNER role."
)
def create_order(
    payload: DistributionOrderCreate,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(PLANNER_ROLES)),
) -> DistributionOrderRead:
    store = db.query(Store).filter_by(id=payload.destination_store_id).first()
    if not store:
        raise EntityNotFoundException("Store", payload.destination_store_id)

    if payload.source_warehouse_id:
        wh = db.query(Warehouse).filter_by(id=payload.source_warehouse_id).first()
        if not wh:
            raise EntityNotFoundException("Warehouse", payload.source_warehouse_id)

    existing = db.query(DistributionOrder).filter_by(order_number=payload.order_number).first()
    if existing:
        raise ConflictException(f"Order with order number '{payload.order_number}' already exists.")

    # Validate that all requested products exist
    product_ids = [item.product_id for item in payload.items]
    found_products = db.query(Product).filter(Product.id.in_(product_ids)).all()
    found_map = {p.id: p for p in found_products}

    missing_ids = set(product_ids) - set(found_map.keys())
    if missing_ids:
        raise EntityNotFoundException("Product", list(missing_ids))

    # Transaction: Create order and its items atomically
    order = DistributionOrder(
        order_number=payload.order_number,
        destination_store_id=payload.destination_store_id,
        source_warehouse_id=payload.source_warehouse_id,
        status=OrderStatus.PENDING,
        priority=payload.priority,
        required_delivery_date=payload.required_delivery_date,
    )
    db.add(order)
    db.flush()  # Generate order.id

    order_items = []
    for item_in in payload.items:
        prod = found_map[item_in.product_id]
        oi = DistributionOrderItem(
            order_id=order.id,
            product_id=item_in.product_id,
            requested_quantity=item_in.requested_quantity,
            fulfilled_quantity=0,
        )
        db.add(oi)
        db.flush()
        order_items.append(
            DistributionOrderItemRead(
                id=oi.id,
                order_id=order.id,
                product_id=prod.id,
                sku=prod.sku,
                product_name=prod.name,
                requested_quantity=oi.requested_quantity,
                fulfilled_quantity=oi.fulfilled_quantity,
            )
        )

    db.commit()
    db.refresh(order)

    return DistributionOrderRead(
        id=order.id,
        order_number=order.order_number,
        destination_store_id=order.destination_store_id,
        destination_store_code=store.code,
        source_warehouse_id=order.source_warehouse_id,
        status=order.status,
        priority=order.priority,
        required_delivery_date=order.required_delivery_date,
        items=order_items,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


@router.get(
    "",
    response_model=PaginatedResponse[DistributionOrderRead],
    summary="List distribution orders",
    description="Returns paginated list of distribution demand orders with status and destination store filtering."
)
def list_orders(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    status_filter: Optional[OrderStatus] = Query(None, alias="status", description="Filter by order status"),
    destination_store_id: Optional[int] = Query(None, description="Filter by destination store ID"),
    db: Session = Depends(get_db),
) -> PaginatedResponse[DistributionOrderRead]:
    params = PaginationParams(page=page, page_size=page_size)
    query = db.query(DistributionOrder).join(Store)

    if status_filter:
        query = query.filter(DistributionOrder.status == status_filter)
    if destination_store_id:
        query = query.filter(DistributionOrder.destination_store_id == destination_store_id)

    total = query.count()
    orders = query.order_by(DistributionOrder.id.desc()).offset(params.offset).limit(params.page_size).all()

    items = []
    for order in orders:
        item_reads = [
            DistributionOrderItemRead(
                id=oi.id,
                order_id=oi.order_id,
                product_id=oi.product_id,
                sku=oi.product.sku if oi.product else None,
                product_name=oi.product.name if oi.product else None,
                requested_quantity=oi.requested_quantity,
                fulfilled_quantity=oi.fulfilled_quantity,
            )
            for oi in order.items
        ]
        items.append(
            DistributionOrderRead(
                id=order.id,
                order_number=order.order_number,
                destination_store_id=order.destination_store_id,
                destination_store_code=order.destination_store.code if order.destination_store else None,
                source_warehouse_id=order.source_warehouse_id,
                status=order.status,
                priority=order.priority,
                required_delivery_date=order.required_delivery_date,
                items=item_reads,
                created_at=order.created_at,
                updated_at=order.updated_at,
            )
        )

    return PaginatedResponse.create(items=items, total=total, params=params)


@router.get(
    "/{order_id}",
    response_model=DistributionOrderRead,
    summary="Retrieve a distribution order by ID",
    description="Returns order fulfillment state and line item quantities."
)
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
) -> DistributionOrderRead:
    order = db.query(DistributionOrder).filter_by(id=order_id).first()
    if not order:
        raise EntityNotFoundException("DistributionOrder", order_id)

    item_reads = [
        DistributionOrderItemRead(
            id=oi.id,
            order_id=oi.order_id,
            product_id=oi.product_id,
            sku=oi.product.sku if oi.product else None,
            product_name=oi.product.name if oi.product else None,
            requested_quantity=oi.requested_quantity,
            fulfilled_quantity=oi.fulfilled_quantity,
        )
        for oi in order.items
    ]

    return DistributionOrderRead(
        id=order.id,
        order_number=order.order_number,
        destination_store_id=order.destination_store_id,
        destination_store_code=order.destination_store.code if order.destination_store else None,
        source_warehouse_id=order.source_warehouse_id,
        status=order.status,
        priority=order.priority,
        required_delivery_date=order.required_delivery_date,
        items=item_reads,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )
