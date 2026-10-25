from datetime import date, datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.exceptions import EntityNotFoundException, ConflictException
from app.core.security import require_roles, AuthenticatedUser, PLANNER_ROLES
from app.models.enums import UserRole, StoreType, OrderPriority, OrderStatus
from app.models.facility import Warehouse, Store
from app.models.product import Product
from app.models.inventory import InventoryBatch
from app.models.order import DistributionOrder, DistributionOrderItem
from app.models.scenario import SeededScenario
from app.schemas.scenario import (
    ScenarioSeedRequest,
    ScenarioResponse,
    ScenarioSeedResult,
)
from app.schemas.pagination import PaginationParams, PaginatedResponse
from app.schemas.plan import PlanDetailResponse
from app.services.plan_lifecycle_service import PlanLifecycleService

router = APIRouter(prefix="/scenarios", tags=["Distribution Scenarios"])


@router.post(
    "/seed",
    response_model=ScenarioSeedResult,
    status_code=status.HTTP_201_CREATED,
    summary="Load a seeded distribution scenario",
    description=(
        "Seeds a deterministic operational scenario for optimization testing. "
        "Guards against silent overwrite of production data: requires 'overwrite=True' if scenario exists. "
        "Requires ADMIN or PLANNER role."
    )
)
def seed_scenario(
    payload: ScenarioSeedRequest,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(PLANNER_ROLES)),
) -> ScenarioSeedResult:
    existing = db.query(SeededScenario).filter_by(scenario_code=payload.scenario_code).first()
    if existing and not payload.overwrite:
        raise ConflictException(
            f"Scenario with code '{payload.scenario_code}' already exists. "
            "Set 'overwrite=True' to deliberately replace this test scenario."
        )

    if existing:
        # Update existing scenario record
        scenario = existing
        scenario.name = payload.name
        scenario.scenario_type = payload.scenario_type
        scenario.description = payload.description
        scenario.parameters = payload.parameters
        scenario.is_active = True
    else:
        # Create fresh scenario record
        scenario = SeededScenario(
            scenario_code=payload.scenario_code,
            name=payload.name,
            scenario_type=payload.scenario_type,
            description=payload.description,
            parameters=payload.parameters,
            is_active=True,
        )
        db.add(scenario)

    db.flush()

    seeded_wh_count = 0
    seeded_store_count = 0
    seeded_prod_count = 0
    seeded_batch_count = 0
    seeded_order_count = 0

    if payload.seed_network_topology:
        # 1. Deterministic Warehouses
        wh1 = db.query(Warehouse).filter_by(code="WH-WEST-SEED-01").first()
        if not wh1:
            wh1 = Warehouse(code="WH-WEST-SEED-01", name="West Seed Hub", region="West", capacity_limit=500000)
            db.add(wh1)
            seeded_wh_count += 1

        wh2 = db.query(Warehouse).filter_by(code="WH-NORTH-SEED-01").first()
        if not wh2:
            wh2 = Warehouse(code="WH-NORTH-SEED-01", name="North Seed Hub", region="North", capacity_limit=400000)
            db.add(wh2)
            seeded_wh_count += 1
        db.flush()

        # 2. Deterministic Stores
        s1 = db.query(Store).filter_by(code="STORE-MUM-SEED-01").first()
        if not s1:
            s1 = Store(code="STORE-MUM-SEED-01", name="Mumbai Seed Retail", region="West", store_type=StoreType.FLAGSHIP)
            db.add(s1)
            seeded_store_count += 1

        s2 = db.query(Store).filter_by(code="STORE-DEL-SEED-01").first()
        if not s2:
            s2 = Store(code="STORE-DEL-SEED-01", name="Delhi Seed Express", region="North", store_type=StoreType.EXPRESS)
            db.add(s2)
            seeded_store_count += 1
        db.flush()

        # 3. Deterministic Products
        p1 = db.query(Product).filter_by(sku="SKU-SEED-PHONE-X").first()
        if not p1:
            p1 = Product(sku="SKU-SEED-PHONE-X", name="Seed Smartphone X", category="Electronics", unit_cost=500.0)
            db.add(p1)
            seeded_prod_count += 1

        p2 = db.query(Product).filter_by(sku="SKU-SEED-LAPTOP-15").first()
        if not p2:
            p2 = Product(sku="SKU-SEED-LAPTOP-15", name="Seed Laptop 15", category="Electronics", unit_cost=1000.0)
            db.add(p2)
            seeded_prod_count += 1
        db.flush()

        # 4. Deterministic Inventory Batches
        b1 = db.query(InventoryBatch).filter_by(warehouse_id=wh1.id, product_id=p1.id, batch_number="BATCH-SEED-P1-W1").first()
        if not b1:
            b1 = InventoryBatch(
                warehouse_id=wh1.id,
                product_id=p1.id,
                batch_number="BATCH-SEED-P1-W1",
                on_hand_quantity=1000,
                reserved_quantity=100,
                committed_quantity=50,
                expiry_date=date.today() + timedelta(days=180),
                version=1,
            )
            db.add(b1)
            wh1.current_inventory_version += 1
            seeded_batch_count += 1

        b2 = db.query(InventoryBatch).filter_by(warehouse_id=wh2.id, product_id=p1.id, batch_number="BATCH-SEED-P1-W2").first()
        if not b2:
            b2 = InventoryBatch(
                warehouse_id=wh2.id,
                product_id=p1.id,
                batch_number="BATCH-SEED-P1-W2",
                on_hand_quantity=600,
                reserved_quantity=50,
                committed_quantity=0,
                expiry_date=date.today() + timedelta(days=90),
                version=1,
            )
            db.add(b2)
            wh2.current_inventory_version += 1
            seeded_batch_count += 1

        b3 = db.query(InventoryBatch).filter_by(warehouse_id=wh1.id, product_id=p2.id, batch_number="BATCH-SEED-P2-W1").first()
        if not b3:
            b3 = InventoryBatch(
                warehouse_id=wh1.id,
                product_id=p2.id,
                batch_number="BATCH-SEED-P2-W1",
                on_hand_quantity=300,
                reserved_quantity=0,
                committed_quantity=0,
                expiry_date=date.today() + timedelta(days=365),
                version=1,
            )
            db.add(b3)
            wh1.current_inventory_version += 1
            seeded_batch_count += 1
        db.flush()

        # 5. Deterministic Distribution Order
        ord_num = f"ORD-SEED-{scenario.scenario_code[:12]}"
        existing_ord = db.query(DistributionOrder).filter_by(order_number=ord_num).first()
        if not existing_ord:
            ord_seed = DistributionOrder(
                order_number=ord_num,
                destination_store_id=s1.id,
                source_warehouse_id=wh1.id,
                status=OrderStatus.PENDING,
                priority=OrderPriority.HIGH,
                required_delivery_date=datetime.now(timezone.utc) + timedelta(days=3),
            )
            db.add(ord_seed)
            db.flush()
            oi1 = DistributionOrderItem(order_id=ord_seed.id, product_id=p1.id, requested_quantity=200, fulfilled_quantity=0)
            db.add(oi1)
            seeded_order_count += 1

    db.commit()
    db.refresh(scenario)

    return ScenarioSeedResult(
        scenario=ScenarioResponse.model_validate(scenario),
        seeded_warehouses_count=seeded_wh_count,
        seeded_stores_count=seeded_store_count,
        seeded_products_count=seeded_prod_count,
        seeded_batches_count=seeded_batch_count,
        seeded_orders_count=seeded_order_count,
        message=f"Scenario '{scenario.scenario_code}' successfully loaded with deterministic test topology."
    )


@router.get(
    "",
    response_model=PaginatedResponse[ScenarioResponse],
    summary="List distribution scenarios",
    description="Returns paginated list of seeded operational scenarios."
)
def list_scenarios(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: Session = Depends(get_db),
) -> PaginatedResponse[ScenarioResponse]:
    params = PaginationParams(page=page, page_size=page_size)
    query = db.query(SeededScenario)

    total = query.count()
    scenarios = query.order_by(SeededScenario.id.desc()).offset(params.offset).limit(params.page_size).all()

    items = [ScenarioResponse.model_validate(s) for s in scenarios]
    return PaginatedResponse.create(items=items, total=total, params=params)


@router.get(
    "/{scenario_id}",
    response_model=ScenarioResponse,
    summary="Retrieve a distribution scenario by ID",
    description="Returns scenario parameters and demand shock definitions."
)
def get_scenario(
    scenario_id: int,
    db: Session = Depends(get_db),
) -> ScenarioResponse:
    scenario = db.query(SeededScenario).filter_by(id=scenario_id).first()
    if not scenario:
        raise EntityNotFoundException("SeededScenario", scenario_id)
    return ScenarioResponse.model_validate(scenario)


@router.post(
    "/{scenario_id}/plans",
    response_model=PlanDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate candidate distribution plan from scenario",
    description=(
        "Loads current authoritative inventory snapshot for scenario, calls decision service "
        "to generate candidate proposals, stores candidate plan, and validates it authoritatively. "
        "Requires PLANNER or ADMIN role."
    ),
)
def create_plan_from_scenario(
    scenario_id: int,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(PLANNER_ROLES)),
) -> PlanDetailResponse:
    plan = PlanLifecycleService.create_plan_from_scenario(
        db=db,
        scenario_id=scenario_id,
        created_by_user_id=current_user.id,
        auto_validate=True,
    )
    return PlanDetailResponse.model_validate(plan)


from pydantic import BaseModel as PydanticBaseModel


class ScenarioResetResult(PydanticBaseModel):
    scenario_id: int
    scenario_code: str
    status: str = "RESET"
    message: str
    batches_reset_count: int
    warehouse_version_reset: int


@router.post(
    "/{scenario_id}/reset",
    response_model=ScenarioResetResult,
    summary="Reset scenario to clean initial deterministic state",
    description=(
        "Resets warehouse inventory versions, batches, and active reservations for reproducible evaluation. "
        "Accepts either integer ID or scenario_code string. Requires PLANNER or ADMIN role."
    )
)
def reset_scenario(
    scenario_id: str,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles(PLANNER_ROLES)),
) -> ScenarioResetResult:
    # Resolve scenario by ID or code
    scenario = None
    if scenario_id.isdigit():
        scenario = db.query(SeededScenario).filter_by(id=int(scenario_id)).first()
    if not scenario:
        scenario = db.query(SeededScenario).filter_by(scenario_code=scenario_id).first()
    if not scenario:
        raise EntityNotFoundException("SeededScenario", scenario_id)

    params = scenario.parameters or {}
    wh_code = params.get("target_warehouse_code")
    wh = None
    if wh_code:
        wh = db.query(Warehouse).filter_by(code=wh_code).first()
    if not wh:
        wh = db.query(Warehouse).first()

    batches_reset_count = 0
    warehouse_version_reset = 1
    if wh:
        wh.current_inventory_version = 1
        warehouse_version_reset = 1
        batches = db.query(InventoryBatch).filter_by(warehouse_id=wh.id).all()
        for b in batches:
            b.committed_quantity = 0
            b.reserved_quantity = 0
            b.version = 1
            batches_reset_count += 1

    # Cleanly cancel uncommitted plans and release active reservations
    from app.models.enums import ReservationStatus, PlanStatus
    from app.models.inventory import InventoryReservation
    from app.models.plan import DistributionPlan

    plans = db.query(DistributionPlan).filter_by(scenario_id=scenario.id).all()
    for p in plans:
        if p.status not in (PlanStatus.COMMITTED, PlanStatus.CANCELLED):
            p.status = PlanStatus.CANCELLED
        active_res = db.query(InventoryReservation).filter_by(plan_id=p.id, status=ReservationStatus.ACTIVE).all()
        for r in active_res:
            r.status = ReservationStatus.RELEASED

    db.commit()

    return ScenarioResetResult(
        scenario_id=scenario.id,
        scenario_code=scenario.scenario_code,
        status="RESET",
        message=f"Scenario '{scenario.scenario_code}' successfully reset to initial deterministic state.",
        batches_reset_count=batches_reset_count,
        warehouse_version_reset=warehouse_version_reset,
    )
