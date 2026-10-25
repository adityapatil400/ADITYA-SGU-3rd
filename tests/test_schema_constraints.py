from datetime import date, datetime, timezone, timedelta
import pytest
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models.enums import (
    UserRole,
    StoreType,
    OrderStatus,
    OrderPriority,
    ReservationStatus,
    PlanStatus,
    AllocationStatus,
    PlanEventType,
)
from app.models.user import User
from app.models.facility import Warehouse, Store
from app.models.product import Product
from app.models.inventory import InventoryBatch, InventoryReservation
from app.models.order import DistributionOrder, DistributionOrderItem
from app.models.plan import DistributionPlan, PlanAllocation, PlanEvent


def test_user_creation_and_email_uniqueness(db_session: Session):
    user1 = User(
        email="operator1@flowgrid.io",
        hashed_password="hashed_secure_password_1",
        full_name="Sarah Operator",
        role=UserRole.OPERATOR,
    )
    db_session.add(user1)
    db_session.commit()
    db_session.refresh(user1)

    assert user1.id is not None
    assert user1.role == UserRole.OPERATOR

    # Attempt to insert duplicate email
    user2 = User(
        email="operator1@flowgrid.io",
        hashed_password="hashed_secure_password_2",
        full_name="Duplicate User",
        role=UserRole.PLANNER,
    )
    db_session.add(user2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_warehouse_initial_inventory_version(db_session: Session):
    wh = Warehouse(
        code="WH-MUMBAI-01",
        name="Western Central Distribution Hub",
        region="West",
        capacity_limit=250000,
        current_inventory_version=1,
    )
    db_session.add(wh)
    db_session.commit()
    db_session.refresh(wh)

    assert wh.current_inventory_version == 1
    assert wh.code == "WH-MUMBAI-01"


def test_inventory_batch_available_quantity_and_negative_prevention(db_session: Session):
    wh = Warehouse(code="WH-DELHI-01", name="Northern DC", region="North")
    prod = Product(
        sku="SKU-DAIRY-MILK-1L",
        name="Fresh Whole Milk 1L",
        category="Dairy",
        shelf_life_days=14,
        is_perishable=True,
    )
    db_session.add_all([wh, prod])
    db_session.commit()

    batch = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-MILK-2026-10-A",
        expiry_date=date(2026, 11, 5),
        on_hand_quantity=1000,
        reserved_quantity=200,
        committed_quantity=100,
        version=1,
    )
    db_session.add(batch)
    db_session.commit()
    db_session.refresh(batch)

    # 1000 on-hand - (200 reserved + 100 committed) = 700 available
    assert batch.available_quantity == 700

    # Test database constraint: prevent reserved + committed > on_hand (negative available)
    batch.reserved_quantity = 800
    batch.committed_quantity = 300  # Total 1100 > 1000
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_inventory_batch_unique_constraint(db_session: Session):
    wh = Warehouse(code="WH-BLR-01", name="Bengaluru DC", region="South")
    prod = Product(sku="SKU-PHARMA-INSULIN", name="Insulin 100IU", is_perishable=True)
    db_session.add_all([wh, prod])
    db_session.commit()

    b1 = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-INS-99",
        on_hand_quantity=100,
    )
    db_session.add(b1)
    db_session.commit()

    # Same warehouse, product, and batch_number must trigger IntegrityError
    b2 = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-INS-99",
        on_hand_quantity=50,
    )
    db_session.add(b2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_batch_fefo_expiry_query(db_session: Session):
    wh = Warehouse(code="WH-HYD-01", name="Hyderabad DC", region="South")
    prod = Product(sku="SKU-YOGURT-500G", name="Greek Yogurt 500g", is_perishable=True)
    db_session.add_all([wh, prod])
    db_session.commit()

    batch_late = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-YOG-LATE",
        expiry_date=date(2026, 12, 1),
        on_hand_quantity=150,
    )
    batch_early = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-YOG-EARLY",
        expiry_date=date(2026, 11, 1),
        on_hand_quantity=100,
    )
    db_session.add_all([batch_late, batch_early])
    db_session.commit()

    # Query using FEFO (First Expired First Out)
    batches = (
        db_session.query(InventoryBatch)
        .filter_by(product_id=prod.id)
        .order_by(InventoryBatch.expiry_date.asc())
        .all()
    )
    assert batches[0].batch_number == "BATCH-YOG-EARLY"
    assert batches[1].batch_number == "BATCH-YOG-LATE"


def test_inventory_version_increment_on_stock_change(db_session: Session):
    wh = Warehouse(code="WH-CHN-01", name="Chennai DC", region="South", current_inventory_version=1)
    prod = Product(sku="SKU-CHIP-A1", name="Logic Board A1")
    db_session.add_all([wh, prod])
    db_session.commit()

    batch = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-CHIP-01",
        on_hand_quantity=500,
        version=1,
    )
    db_session.add(batch)
    db_session.commit()

    # Simulate inventory change (e.g., reservation) -> warehouse version increments
    batch.reserved_quantity += 50
    batch.version += 1
    wh.current_inventory_version += 1
    db_session.commit()

    db_session.refresh(wh)
    db_session.refresh(batch)
    assert wh.current_inventory_version == 2
    assert batch.version == 2
    assert batch.available_quantity == 450


def test_distribution_plan_optimistic_inventory_version_concurrency(db_session: Session):
    wh = Warehouse(code="WH-KOL-01", name="Kolkata DC", region="East", current_inventory_version=3)
    db_session.add(wh)
    db_session.commit()

    # Decision service generates candidate plan based on warehouse version 3
    plan = DistributionPlan(
        plan_code="PLAN-OPT-CONCURRENCY-001",
        target_warehouse_id=wh.id,
        input_inventory_version=3,
        status=PlanStatus.PROPOSED,
        candidate_allocations=[{"sku": "SKU-TEST", "quantity": 100}],
        decision_service_metadata={"model": "flowgrid-opt-v1.4", "solver_time_ms": 42},
        validation_results={},
    )
    db_session.add(plan)
    db_session.commit()

    # Pre-condition: plan matches warehouse inventory version
    assert plan.input_inventory_version == wh.current_inventory_version

    # Simulate concurrent inventory change occurring before plan commitment
    wh.current_inventory_version += 1
    db_session.commit()

    db_session.refresh(wh)
    db_session.refresh(plan)
    # Backend detects mismatch: plan was calculated on stale inventory version 3, now at version 4!
    is_version_fresh = (plan.input_inventory_version == wh.current_inventory_version)
    assert is_version_fresh is False


def test_append_only_plan_events_audit_trail(db_session: Session):
    wh = Warehouse(code="WH-PUN-01", name="Pune Hub", region="West")
    store = Store(code="STORE-PUN-01", name="Pune Retail Express", region="West", store_type=StoreType.EXPRESS)
    prod = Product(sku="SKU-COFFEE-250G", name="Single Origin Coffee 250g")
    db_session.add_all([wh, store, prod])
    db_session.commit()

    batch = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-COF-01",
        on_hand_quantity=300,
    )
    db_session.add(batch)
    db_session.commit()

    # 1. Propose Candidate Plan
    plan = DistributionPlan(
        plan_code="PLAN-AUDIT-TRACE-2026",
        target_warehouse_id=wh.id,
        input_inventory_version=1,
        status=PlanStatus.PROPOSED,
        candidate_allocations=[{"sku": prod.sku, "quantity": 50}],
        decision_service_metadata={"solver": "lp_simplex", "confidence": 0.96},
        validation_results={},
    )
    db_session.add(plan)
    db_session.commit()

    # Event 1: Candidate Proposed
    e1 = PlanEvent(
        plan_id=plan.id,
        trace_id=plan.trace_id,
        event_type=PlanEventType.CANDIDATE_PROPOSED,
        actor_type="decision_service",
        actor_id="flowgrid-ai-v1",
        new_status=PlanStatus.PROPOSED.value,
        payload={"proposed_allocations_count": 1},
    )
    db_session.add(e1)

    # Event 2: Validation Passed
    plan.status = PlanStatus.VALIDATED
    plan.is_validated = True
    plan.validated_at = datetime.now(timezone.utc)
    plan.validation_results = {"verdict": "PASS", "violations": [], "warnings": []}

    e2 = PlanEvent(
        plan_id=plan.id,
        trace_id=plan.trace_id,
        event_type=PlanEventType.VALIDATION_PASSED,
        actor_type="validation_engine",
        actor_id="backend-authoritative-rules",
        previous_status=PlanStatus.PROPOSED.value,
        new_status=PlanStatus.VALIDATED.value,
        payload={"verdict": "PASS"},
    )
    db_session.add(e2)

    # Event 3: Authoritative Commitment
    user = User(
        email="lead_operator@flowgrid.io",
        hashed_password="pw",
        full_name="Lead Operator",
        role=UserRole.ADMIN,
    )
    db_session.add(user)
    db_session.commit()

    plan.status = PlanStatus.COMMITTED
    plan.committed_at = datetime.now(timezone.utc)
    plan.committed_by_user_id = user.id

    e3 = PlanEvent(
        plan_id=plan.id,
        trace_id=plan.trace_id,
        event_type=PlanEventType.PLAN_COMMITTED,
        actor_type="operator",
        actor_id=str(user.id),
        previous_status=PlanStatus.VALIDATED.value,
        new_status=PlanStatus.COMMITTED.value,
        payload={"committed_by": user.email},
    )
    db_session.add(e3)
    db_session.commit()

    # Verify audit event history in chronological order
    events = (
        db_session.query(PlanEvent)
        .filter_by(plan_id=plan.id)
        .order_by(PlanEvent.id.asc())
        .all()
    )
    assert len(events) == 3
    assert events[0].event_type == PlanEventType.CANDIDATE_PROPOSED
    assert events[1].event_type == PlanEventType.VALIDATION_PASSED
    assert events[2].event_type == PlanEventType.PLAN_COMMITTED
    assert events[0].trace_id == plan.trace_id
    assert events[2].trace_id == plan.trace_id
