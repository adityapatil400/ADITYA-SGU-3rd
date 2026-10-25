from datetime import date, datetime, timedelta, timezone
import pytest
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.core.exceptions import (
    InventoryVersionMismatchException,
    InsufficientInventoryException,
    ExpiredBatchException,
    PlanAlreadyCommittedException,
    AuthoritativeRuleViolationException,
)
from app.core.security import hash_password, create_access_token
from app.models.enums import UserRole, PlanStatus, ReservationStatus, AllocationStatus
from app.models.facility import Warehouse, Store
from app.models.product import Product
from app.models.inventory import InventoryBatch, InventoryReservation
from app.models.plan import DistributionPlan, PlanAllocation
from app.models.user import User
from app.services.inventory_service import InventoryService


# ==============================================================================
# Helper Fixtures & Setup
# ==============================================================================

def setup_warehouse_and_product(db: Session, on_hand: int = 100, expiry_days: int = 365):
    """Sets up a warehouse, product, and physical batch with specified parameters."""
    wh = Warehouse(
        code="WH-INV-TEST",
        name="Inventory Test WH",
        region="North",
        capacity_limit=50000,
        current_inventory_version=1,
    )
    store = Store(
        code="STORE-INV-01",
        name="Retail Test Store",
        region="North",
        priority_tier=1,
    )
    prod = Product(
        sku="SKU-CHIP-99",
        name="AI Acceleration Chip",
        category="Electronics",
        shelf_life_days=730,
    )
    db.add_all([wh, store, prod])
    db.commit()

    batch = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-2026-CHIP-A",
        on_hand_quantity=on_hand,
        reserved_quantity=0,
        committed_quantity=0,
        expiry_date=date.today() + timedelta(days=expiry_days),
        version=1,
    )
    db.add(batch)
    db.commit()
    db.refresh(wh)
    db.refresh(batch)
    return wh, store, prod, batch


# ==============================================================================
# 1. Two Plans Competing for the Same Inventory
# ==============================================================================

def test_competing_plans_for_same_inventory(db_session: Session):
    wh, store, prod, batch = setup_warehouse_and_product(db_session, on_hand=100)

    # Plan A wants 70 units
    plan_a = DistributionPlan(
        plan_code="PLAN-COMPETE-A",
        target_warehouse_id=wh.id,
        input_inventory_version=wh.current_inventory_version,  # 1
        status=PlanStatus.PROPOSED,
        candidate_allocations=[{"sku": prod.sku, "quantity": 70}],
        created_by_user_id=1,
    )
    # Plan B wants 50 units (70 + 50 = 120 > 100 on-hand)
    plan_b = DistributionPlan(
        plan_code="PLAN-COMPETE-B",
        target_warehouse_id=wh.id,
        input_inventory_version=wh.current_inventory_version,  # 1
        status=PlanStatus.PROPOSED,
        candidate_allocations=[{"sku": prod.sku, "quantity": 50}],
        created_by_user_id=2,
    )
    db_session.add_all([plan_a, plan_b])
    db_session.commit()

    # Plan A commits first: succeeds!
    commit_a = InventoryService.validate_and_commit_plan(
        db=db_session,
        plan_code=plan_a.plan_code,
        committed_by_user_id=99,
    )
    assert commit_a.status == PlanStatus.COMMITTED
    db_session.refresh(batch)
    db_session.refresh(wh)
    assert batch.committed_quantity == 70
    assert batch.available_quantity == 30
    assert wh.current_inventory_version == 2

    # Plan B attempts to commit:
    # Warehouse version is now 2, but Plan B assumed version 1 -> Stale plan rejection!
    with pytest.raises(InventoryVersionMismatchException) as exc_info:
        InventoryService.validate_and_commit_plan(
            db=db_session,
            plan_code=plan_b.plan_code,
            committed_by_user_id=99,
        )
    assert "Inventory version mismatch" in str(exc_info.value)

    db_session.refresh(plan_b)
    assert plan_b.status == PlanStatus.REJECTED

    # If Plan B is updated to acknowledge version 2, it still fails due to insufficient stock (50 > 30)
    plan_b.status = PlanStatus.PROPOSED
    plan_b.input_inventory_version = wh.current_inventory_version  # 2
    db_session.commit()

    with pytest.raises(InsufficientInventoryException) as exc_info2:
        InventoryService.validate_and_commit_plan(
            db=db_session,
            plan_code=plan_b.plan_code,
            committed_by_user_id=99,
        )
    assert "Insufficient available stock" in str(exc_info2.value)


# ==============================================================================
# 2. Insufficient Stock Rejection
# ==============================================================================

def test_insufficient_stock_rejection(db_session: Session):
    wh, store, prod, batch = setup_warehouse_and_product(db_session, on_hand=30)

    plan = DistributionPlan(
        plan_code="PLAN-INSUFFICIENT-STOCK",
        target_warehouse_id=wh.id,
        input_inventory_version=wh.current_inventory_version,
        status=PlanStatus.PROPOSED,
        candidate_allocations=[{"sku": prod.sku, "quantity": 100}],  # 100 requested, only 30 available
        created_by_user_id=1,
    )
    db_session.add(plan)
    db_session.commit()

    with pytest.raises(InsufficientInventoryException) as exc_info:
        InventoryService.validate_and_commit_plan(
            db=db_session,
            plan_code=plan.plan_code,
            committed_by_user_id=99,
        )

    assert "Insufficient available stock" in str(exc_info.value)

    # Invariant: Batch quantities are completely untouched
    db_session.refresh(batch)
    assert batch.on_hand_quantity == 30
    assert batch.reserved_quantity == 0
    assert batch.committed_quantity == 0
    assert batch.available_quantity == 30

    db_session.refresh(plan)
    assert plan.status == PlanStatus.REJECTED


# ==============================================================================
# 3. Expired Stock Rejection
# ==============================================================================

def test_expired_stock_rejection(db_session: Session):
    # Setup batch expired 10 days ago
    wh, store, prod, batch = setup_warehouse_and_product(db_session, on_hand=100, expiry_days=-10)

    plan = DistributionPlan(
        plan_code="PLAN-EXPIRED-BATCH",
        target_warehouse_id=wh.id,
        input_inventory_version=wh.current_inventory_version,
        status=PlanStatus.PROPOSED,
        candidate_allocations=[{"sku": prod.sku, "quantity": 10}],
        created_by_user_id=1,
    )
    db_session.add(plan)
    db_session.commit()

    # 1. Reservation must reject expired stock
    with pytest.raises(ExpiredBatchException) as exc_info:
        InventoryService.reserve_stock_for_plan(
            db=db_session,
            plan_code=plan.plan_code,
        )
    assert "expired" in str(exc_info.value).lower()

    # Reset plan for direct commit test
    plan.status = PlanStatus.PROPOSED
    db_session.commit()

    # 2. Direct commit must reject expired stock
    with pytest.raises(ExpiredBatchException) as exc_info2:
        InventoryService.validate_and_commit_plan(
            db=db_session,
            plan_code=plan.plan_code,
            committed_by_user_id=99,
        )
    assert "expired" in str(exc_info2.value).lower()

    db_session.refresh(batch)
    assert batch.committed_quantity == 0
    assert batch.reserved_quantity == 0


# ==============================================================================
# 4. Inventory Version Mismatch (Stale Plan Rejection)
# ==============================================================================

def test_inventory_version_mismatch_rejects_stale_plan(db_session: Session):
    wh, store, prod, batch = setup_warehouse_and_product(db_session, on_hand=100)

    # Candidate plan was prepared based on version 1
    plan = DistributionPlan(
        plan_code="PLAN-STALE-VERSION",
        target_warehouse_id=wh.id,
        input_inventory_version=1,
        status=PlanStatus.PROPOSED,
        candidate_allocations=[{"sku": prod.sku, "quantity": 15}],
        created_by_user_id=1,
    )
    db_session.add(plan)
    db_session.commit()

    # Meanwhile, an unrelated inventory movement bumps warehouse version to 2
    wh.current_inventory_version = 2
    db_session.commit()

    with pytest.raises(InventoryVersionMismatchException) as exc_info:
        InventoryService.validate_and_commit_plan(
            db=db_session,
            plan_code=plan.plan_code,
            committed_by_user_id=99,
        )

    assert exc_info.value.details["expected_version"] == 1
    assert exc_info.value.details["current_version"] == 2

    db_session.refresh(plan)
    assert plan.status == PlanStatus.REJECTED


# ==============================================================================
# 5. Duplicate Commit Requests & Idempotency Safeguards
# ==============================================================================

def test_duplicate_commit_requests_and_idempotency(db_session: Session):
    wh, store, prod, batch = setup_warehouse_and_product(db_session, on_hand=100)

    plan = DistributionPlan(
        plan_code="PLAN-IDEMPOTENCY-TEST",
        target_warehouse_id=wh.id,
        input_inventory_version=wh.current_inventory_version,
        status=PlanStatus.PROPOSED,
        candidate_allocations=[{"sku": prod.sku, "quantity": 25}],
        created_by_user_id=1,
    )
    db_session.add(plan)
    db_session.commit()

    idempotency_key = "IDEM-KEY-2026-X1"

    # Request 1: Initial commit
    res1 = InventoryService.validate_and_commit_plan(
        db=db_session,
        plan_code=plan.plan_code,
        committed_by_user_id=99,
        idempotency_key=idempotency_key,
    )
    assert res1.status == PlanStatus.COMMITTED
    db_session.refresh(batch)
    db_session.refresh(wh)
    assert batch.committed_quantity == 25
    assert batch.available_quantity == 75
    version_after_first_commit = wh.current_inventory_version

    # Request 2: Duplicate request with the SAME idempotency key
    res2 = InventoryService.validate_and_commit_plan(
        db=db_session,
        plan_code=plan.plan_code,
        committed_by_user_id=99,
        idempotency_key=idempotency_key,
    )
    # Returns successfully (idempotent)
    assert res2.status == PlanStatus.COMMITTED
    assert "idempotent" in res2.message.lower()

    # Invariant: Physical stock was NOT deducted twice
    db_session.refresh(batch)
    db_session.refresh(wh)
    assert batch.committed_quantity == 25
    assert batch.available_quantity == 75
    assert wh.current_inventory_version == version_after_first_commit

    # Request 3: Re-committing without matching idempotency key raises Conflict
    with pytest.raises(PlanAlreadyCommittedException):
        InventoryService.validate_and_commit_plan(
            db=db_session,
            plan_code=plan.plan_code,
            committed_by_user_id=99,
            idempotency_key="DIFFERENT-KEY-999",
        )


# ==============================================================================
# 6. Transaction Rollback Prevents Partial Updates
# ==============================================================================

def test_transaction_rollback_prevents_partial_updates(db_session: Session):
    wh, store, prod, batch = setup_warehouse_and_product(db_session, on_hand=100)

    # Create a second batch with zero inventory
    batch_empty = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-EMPTY-02",
        on_hand_quantity=0,
        reserved_quantity=0,
        committed_quantity=0,
        expiry_date=date.today() + timedelta(days=300),
        version=1,
    )
    db_session.add(batch_empty)
    db_session.commit()

    # Plan requests 20 from valid batch, and 50 from empty batch
    plan = DistributionPlan(
        plan_code="PLAN-ROLLBACK-TEST",
        target_warehouse_id=wh.id,
        input_inventory_version=wh.current_inventory_version,
        status=PlanStatus.PROPOSED,
        candidate_allocations=[
            {"batch_id": batch.id, "quantity": 20},
            {"batch_id": batch_empty.id, "quantity": 50},  # Will fail!
        ],
        created_by_user_id=1,
    )
    db_session.add(plan)
    db_session.commit()

    initial_version = wh.current_inventory_version

    # Attempting to reserve stock fails on the second item
    with pytest.raises(InsufficientInventoryException):
        InventoryService.reserve_stock_for_plan(
            db=db_session,
            plan_code=plan.plan_code,
        )

    # Invariant: Complete rollback. Batch 1 was NOT partially reserved!
    db_session.refresh(batch)
    db_session.refresh(batch_empty)
    db_session.refresh(wh)
    assert batch.reserved_quantity == 0
    assert batch.committed_quantity == 0
    assert batch.available_quantity == 100
    assert batch_empty.reserved_quantity == 0
    # No reservations were persisted
    reservations = db_session.query(InventoryReservation).filter_by(plan_id=plan.id).all()
    assert len(reservations) == 0


# ==============================================================================
# 7. Stock Reservation and Release Lifecycle
# ==============================================================================

def test_reservation_and_release_lifecycle(db_session: Session):
    wh, store, prod, batch = setup_warehouse_and_product(db_session, on_hand=100)

    plan = DistributionPlan(
        plan_code="PLAN-RESERVATION-LIFECYCLE",
        target_warehouse_id=wh.id,
        input_inventory_version=wh.current_inventory_version,
        status=PlanStatus.PROPOSED,
        candidate_allocations=[{"sku": prod.sku, "quantity": 40}],
        created_by_user_id=1,
    )
    db_session.add(plan)
    db_session.commit()

    # Step 1: Reserve stock
    res = InventoryService.reserve_stock_for_plan(
        db=db_session,
        plan_code=plan.plan_code,
        ttl_minutes=45,
    )
    assert len(res) == 1
    assert res[0].status == ReservationStatus.ACTIVE
    assert res[0].quantity == 40

    db_session.refresh(batch)
    assert batch.reserved_quantity == 40
    assert batch.available_quantity == 60

    # Step 2: Release reservations
    released = InventoryService.release_reservations_for_plan(
        db=db_session,
        plan_code=plan.plan_code,
        reason="Testing reservation cancellation",
    )
    assert released == 1

    db_session.refresh(batch)
    assert batch.reserved_quantity == 0
    assert batch.available_quantity == 100


# ==============================================================================
# 8. API End-to-End: Reserve, Commit, and Separation of Duties via HTTP
# ==============================================================================

def test_api_reserve_commit_and_separation_of_duties(client: TestClient, db_session: Session):
    wh, store, prod, batch = setup_warehouse_and_product(db_session, on_hand=100)

    # Create planner and approver users
    planner = User(
        email="planner_inv@flowgrid.io",
        hashed_password=hash_password("Pass123!"),
        full_name="Planner User",
        role=UserRole.PLANNER,
        is_active=True,
    )
    approver = User(
        email="approver_inv@flowgrid.io",
        hashed_password=hash_password("Pass123!"),
        full_name="Approver User",
        role=UserRole.APPROVER,
        is_active=True,
    )
    db_session.add_all([planner, approver])
    db_session.commit()

    planner_token = create_access_token(data={"sub": planner.email, "user_id": planner.id, "role": planner.role.value})
    approver_token = create_access_token(data={"sub": approver.email, "user_id": approver.id, "role": approver.role.value})

    planner_headers = {"Authorization": f"Bearer {planner_token}"}
    approver_headers = {"Authorization": f"Bearer {approver_token}"}

    # 1. Planner creates candidate plan
    create_resp = client.post(
        "/api/v1/plans",
        json={
            "plan_code": "PLAN-HTTP-E2E-01",
            "target_warehouse_id": wh.id,
            "candidate_allocations": [{"sku": prod.sku, "quantity": 35}],
        },
        headers=planner_headers,
    )
    assert create_resp.status_code == 201

    # 2. Planner reserves stock
    reserve_resp = client.post(
        "/api/v1/plans/PLAN-HTTP-E2E-01/reserve",
        json={"ttl_minutes": 60},
        headers=planner_headers,
    )
    assert reserve_resp.status_code == 200
    assert reserve_resp.json()["reserved_items_count"] == 1

    # 3. Creator (Planner) attempts to commit: Separation of duties violation (HTTP 403)
    self_commit = client.post(
        "/api/v1/plans/PLAN-HTTP-E2E-01/commit",
        json={"idempotency_key": "KEY-E2E-01"},
        headers=planner_headers,
    )
    assert self_commit.status_code == 403

    # 4. Independent Approver commits the plan: Succeeds (HTTP 200)
    approve_commit = client.post(
        "/api/v1/plans/PLAN-HTTP-E2E-01/commit",
        json={"idempotency_key": "KEY-E2E-01"},
        headers=approver_headers,
    )
    assert approve_commit.status_code == 200
    commit_data = approve_commit.json()
    assert commit_data["status"] == "COMMITTED"
    assert commit_data["committed_movements_count"] == 1

    # Invariant: Physical balance updated
    db_session.refresh(batch)
    assert batch.reserved_quantity == 0
    assert batch.committed_quantity == 35
    assert batch.available_quantity == 65
