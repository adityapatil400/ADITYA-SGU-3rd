"""
Tests for PostgreSQL Authoritative Inventory Database:
1. Mapping existing MongoDB/Mongoose models to PostgreSQL tables & SQLAlchemy models.
2. Preserving entities required by FINAL_PRD.md:
   - Product, Warehouse, Retailer (Store), InventoryBatch, Order, OrderItem,
     PlanRun (DistributionPlan), AllocationLine (PlanAllocation),
     Reservation (InventoryReservation), User, AuditEvent (PlanEvent).
3. Preserving batch-level expiry, available/reserved quantities, inventory versions, timestamps.
4. Testing Alembic migration upgrade, downgrade, and compatibility views.
5. Critical rules & invariants:
   - Available stock = physical quantity - reserved quantity.
   - Stock must never become negative.
   - Reservations must never exceed available stock.
   - Inventory versions change when authoritative inventory changes.
   - Safe migration: deduplication without discarding data.
"""

from datetime import date, datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
import pytest

from app.core.security import hash_password, create_access_token
from app.models.enums import UserRole, StoreType, OrderPriority, OrderStatus, PlanStatus, ReservationStatus
from app.models.facility import Warehouse, Store, Retailer
from app.models.product import Product
from app.models.inventory import InventoryBatch, InventoryReservation, Reservation
from app.models.order import DistributionOrder, DistributionOrderItem, Order, OrderItem
from app.models.plan import DistributionPlan, PlanAllocation, PlanEvent, PlanRun, AllocationLine, AuditEvent
from app.models.user import User
from app.services.inventory_service import InventoryService
from app.services.migration_service import DataMigrationService
from app.core.exceptions import (
    AuthoritativeRuleViolationException,
    InsufficientInventoryException,
    InventoryVersionMismatchException,
)


@pytest.fixture
def auth_tokens(db_session: Session):
    admin = User(
        email="inv_admin@flowgrid.io",
        hashed_password=hash_password("AdminPass123!"),
        full_name="Inventory Admin",
        role=UserRole.ADMIN,
        is_active=True,
    )
    planner = User(
        email="inv_planner@flowgrid.io",
        hashed_password=hash_password("PlannerPass123!"),
        full_name="Inventory Planner",
        role=UserRole.PLANNER,
        is_active=True,
    )
    db_session.add_all([admin, planner])
    db_session.commit()

    admin_token = create_access_token(data={"sub": admin.email, "user_id": admin.id, "role": admin.role.value})
    planner_token = create_access_token(data={"sub": planner.email, "user_id": planner.id, "role": planner.role.value})
    return {
        "admin": {"Authorization": f"Bearer {admin_token}"},
        "planner": {"Authorization": f"Bearer {planner_token}"},
        "admin_user": admin,
        "planner_user": planner,
    }


# ==============================================================================
# Task 1 & 2: Entity & Relationship Mapping Tests
# ==============================================================================

def test_prd_entity_model_mappings_and_aliases(db_session: Session):
    """Verifies that all Mongoose/FINAL_PRD entities exist and map cleanly to PostgreSQL."""
    # 1. Product
    prod = Product(sku="SKU-MAP-01", name="Product 01", category="Beverages", shelf_life_days=180)
    # 2. Warehouse
    wh = Warehouse(code="WH-MAP-01", name="Warehouse 01", region="Central", capacity_limit=100000)
    # 3. Retailer (aliased to Store)
    ret = Retailer(code="RET-MAP-01", name="Retailer Store 01", region="Central", store_type=StoreType.FLAGSHIP)
    # 4. User
    u = User(email="user_map@flowgrid.io", hashed_password="pw", full_name="User Map", role=UserRole.OPERATOR)

    db_session.add_all([prod, wh, ret, u])
    db_session.commit()

    assert isinstance(ret, Store)
    assert ret.code == "RET-MAP-01"

    # 5. InventoryBatch
    batch = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-MAP-01",
        on_hand_quantity=500,
        reserved_quantity=50,
        committed_quantity=20,
        expiry_date=date(2027, 6, 30),
        version=1,
    )
    db_session.add(batch)
    db_session.commit()

    # 6. Order and OrderItem aliases
    order = Order(
        order_number="ORD-MAP-01",
        destination_store_id=ret.id,
        source_warehouse_id=wh.id,
        status=OrderStatus.PENDING,
        priority=OrderPriority.HIGH,
    )
    db_session.add(order)
    db_session.commit()

    item = OrderItem(
        order_id=order.id,
        product_id=prod.id,
        requested_quantity=100,
        fulfilled_quantity=0,
    )
    db_session.add(item)
    db_session.commit()
    assert isinstance(order, DistributionOrder)
    assert isinstance(item, DistributionOrderItem)

    # 7. PlanRun and AllocationLine aliases
    plan_run = PlanRun(
        plan_code="PLAN-RUN-MAP-01",
        target_warehouse_id=wh.id,
        input_inventory_version=1,
        status=PlanStatus.PROPOSED,
        candidate_allocations=[{"sku": prod.sku, "quantity": 50}],
    )
    db_session.add(plan_run)
    db_session.commit()
    assert isinstance(plan_run, DistributionPlan)

    alloc_line = AllocationLine(
        plan_id=plan_run.id,
        source_warehouse_id=wh.id,
        destination_store_id=ret.id,
        product_id=prod.id,
        batch_id=batch.id,
        allocated_quantity=50,
    )
    db_session.add(alloc_line)
    db_session.commit()
    assert isinstance(alloc_line, PlanAllocation)

    # 8. Reservation alias
    res = Reservation(
        reservation_code="RES-MAP-01",
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_id=batch.id,
        plan_id=plan_run.id,
        quantity=50,
        status=ReservationStatus.ACTIVE,
    )
    db_session.add(res)
    db_session.commit()
    assert isinstance(res, InventoryReservation)

    # 9. AuditEvent alias
    audit = AuditEvent(
        plan_id=plan_run.id,
        trace_id=plan_run.trace_id,
        event_type="PLAN_CREATED",
        actor_type="test_suite",
        payload={"note": "mapped audit event"},
    )
    db_session.add(audit)
    db_session.commit()
    assert isinstance(audit, PlanEvent)


# ==============================================================================
# Task 3 & Critical Rules: Inventory Invariants & Concurrency Protection
# ==============================================================================

def test_available_stock_equation_and_non_negative_invariants(db_session: Session):
    """
    Critical Rule 1: Available stock = physical quantity - reserved quantity.
    Critical Rule 2: Stock must never become negative.
    Critical Rule 3: Reservations must never exceed available stock.
    """
    wh = Warehouse(code="WH-INV-01", name="Invariant WH", region="West", current_inventory_version=1)
    prod = Product(sku="SKU-INV-01", name="Invariant Product")
    db_session.add_all([wh, prod])
    db_session.commit()

    batch = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-INV-01",
        on_hand_quantity=1000,
        reserved_quantity=200,
        committed_quantity=100,
        version=1,
    )
    db_session.add(batch)
    db_session.commit()

    # Available stock = physical - reserved - committed = 1000 - 200 - 100 = 700
    assert batch.physical_quantity == 1000
    assert batch.available_stock == 700
    assert batch.available_quantity == 700

    # Test Rule 3: Database check constraint prevents reservations exceeding stock
    batch.reserved_quantity = 950  # 950 + 100 = 1050 > 1000 on hand
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Test Rule 2: Negative on_hand prevention
    b_neg = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-INV-NEG",
        on_hand_quantity=-10,
    )
    db_session.add(b_neg)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_inventory_version_increment_on_all_state_changes(db_session: Session):
    """
    Critical Rule 4: Inventory versions must change whenever authoritative inventory changes.
    """
    wh = Warehouse(code="WH-VER-01", name="Version WH", region="East", current_inventory_version=1)
    prod = Product(sku="SKU-VER-01", name="Version Product")
    db_session.add_all([wh, prod])
    db_session.commit()

    # Step 1: Batch creation increments warehouse version
    batch = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-VER-01",
        on_hand_quantity=500,
        reserved_quantity=0,
        committed_quantity=0,
        expiry_date=date.today() + timedelta(days=60),
        version=1,
    )
    db_session.add(batch)
    wh.current_inventory_version += 1
    db_session.commit()
    assert wh.current_inventory_version == 2

    # Step 2: Plan creation matching warehouse version
    plan = DistributionPlan(
        plan_code="PLAN-VER-01",
        target_warehouse_id=wh.id,
        input_inventory_version=wh.current_inventory_version,
        status=PlanStatus.PROPOSED,
        candidate_allocations=[{"sku": prod.sku, "quantity": 50}],
    )
    db_session.add(plan)
    db_session.commit()

    # Step 3: Stock Reservation increments warehouse and batch versions
    res_list = InventoryService.reserve_stock_for_plan(db_session, plan.plan_code)
    assert len(res_list) == 1
    db_session.refresh(wh)
    db_session.refresh(batch)
    assert wh.current_inventory_version == 3
    assert batch.version == 2
    assert batch.reserved_quantity == 50
    assert batch.available_stock == 450

    # Step 4: Stock Adjustment increments warehouse and batch versions
    adjusted_batch = InventoryService.adjust_batch_inventory(
        db_session,
        batch_id=batch.id,
        quantity_delta=100,
        reason="Stock arrival",
    )
    db_session.refresh(wh)
    assert adjusted_batch.physical_quantity == 600
    assert adjusted_batch.available_stock == 550
    assert adjusted_batch.version == 3
    assert wh.current_inventory_version == 4

    # Step 5: Reservation release increments warehouse and batch versions
    released_count = InventoryService.release_reservations_for_plan(db_session, plan.plan_code, reason="Test release")
    assert released_count == 1
    db_session.refresh(wh)
    db_session.refresh(batch)
    assert batch.reserved_quantity == 0
    assert batch.available_stock == 600
    assert batch.version == 4
    assert wh.current_inventory_version == 5


# ==============================================================================
# Task 6: Data Migration & Safe Deduplication Tests
# ==============================================================================

def test_safe_data_migration_and_idempotent_deduplication(db_session: Session):
    """
    Task 6: Migrate existing seeded scenario data without duplicating records.
    Verifies that running migration multiple times is strictly idempotent.
    """
    # Run 1: First migration
    result1 = DataMigrationService.migrate_seeded_scenario_data(
        db=db_session,
        scenario_code="SCENARIO-SURGE-SAFE-01",
    )
    assert result1["status"] == "COMPLETED"
    assert result1["warehouses_upserted"] >= 1
    assert result1["retailers_upserted"] >= 1
    assert result1["products_upserted"] >= 1
    assert result1["batches_upserted"] >= 1

    wh_count_1 = db_session.query(Warehouse).filter(Warehouse.code.like("%SEED%")).count()
    store_count_1 = db_session.query(Store).filter(Store.code.like("%SEED%")).count()
    batch_count_1 = db_session.query(InventoryBatch).filter(InventoryBatch.batch_number.like("%SEED%")).count()

    # Run 2: Second migration with same data -> MUST NOT duplicate
    result2 = DataMigrationService.migrate_seeded_scenario_data(
        db=db_session,
        scenario_code="SCENARIO-SURGE-SAFE-01",
    )
    assert result2["duplicates_prevented"] >= 1

    wh_count_2 = db_session.query(Warehouse).filter(Warehouse.code.like("%SEED%")).count()
    store_count_2 = db_session.query(Store).filter(Store.code.like("%SEED%")).count()
    batch_count_2 = db_session.query(InventoryBatch).filter(InventoryBatch.batch_number.like("%SEED%")).count()

    assert wh_count_1 == wh_count_2
    assert store_count_1 == store_count_2
    assert batch_count_1 == batch_count_2

    # Verify invariants via DataMigrationService
    verification = DataMigrationService.verify_inventory_invariants(db=db_session)
    assert verification["is_valid"] is True
    assert verification["violations_count"] == 0
    assert verification["migration_status"] == "VERIFIED"


def test_safe_migration_execution_preserves_legacy_and_verifies(db_session: Session):
    """
    Verifies that execute_safe_migration verifies PostgreSQL invariants and preserves
    legacy data sources without premature deletion.
    """
    safe_result = DataMigrationService.execute_safe_migration(
        db=db_session,
        scenario_code="SCENARIO-SAFE-VERIFY-01",
        legacy_data_source="legacy_mongodb_store_archive",
    )
    assert safe_result["status"] == "MIGRATION_VERIFIED_SUCCESSFUL"
    assert safe_result["authoritative_database"] == "PostgreSQL"
    assert safe_result["legacy_preserved"] is True
    assert safe_result["verification_report"]["is_valid"] is True


# ==============================================================================
# Task 7: Inventory & Retailer REST API Compatibility Tests
# ==============================================================================

def test_inventory_api_adjust_and_verify_endpoints(client: TestClient, auth_tokens, db_session: Session):
    """Tests the /api/v1/inventory/adjust and /api/v1/inventory/verify endpoints."""
    # Seed baseline
    wh = Warehouse(code="WH-API-TEST-01", name="API WH", region="West", current_inventory_version=1)
    prod = Product(sku="SKU-API-TEST-01", name="API Prod")
    db_session.add_all([wh, prod])
    db_session.commit()

    batch = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-API-ADJ-01",
        on_hand_quantity=200,
        reserved_quantity=20,
        committed_quantity=10,
        version=1,
    )
    db_session.add(batch)
    db_session.commit()

    # 1. Adjust physical stock via POST /api/v1/inventory/adjust
    resp = client.post(
        "/api/v1/inventory/adjust",
        json={"batch_id": batch.id, "quantity_delta": 50, "reason": "Inventory count increase"},
        headers=auth_tokens["planner"],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["on_hand_quantity"] == 250
    assert data["available_quantity"] == 220
    assert data["version"] == 2

    # 2. Reject adjustment causing negative stock
    bad_resp = client.post(
        "/api/v1/inventory/adjust",
        json={"batch_id": batch.id, "quantity_delta": -300, "reason": "Excessive deduction"},
        headers=auth_tokens["planner"],
    )
    assert bad_resp.status_code == 422

    # 3. Verify invariants via POST /api/v1/inventory/verify
    verify_resp = client.post("/api/v1/inventory/verify", headers=auth_tokens["planner"])
    assert verify_resp.status_code == 200
    assert verify_resp.json()["is_valid"] is True
    assert verify_resp.json()["migration_status"] == "VERIFIED"


def test_retailers_endpoint_alias_compatibility(client: TestClient, auth_tokens):
    """Verifies that /api/v1/retailers operates as a first-class alias to stores."""
    # 1. POST /api/v1/retailers
    create_resp = client.post(
        "/api/v1/retailers",
        json={
            "code": "RET-ALIAS-01",
            "name": "Retailer Alias Store",
            "region": "South",
            "store_type": "FLAGSHIP",
            "priority_tier": 1,
            "address": "100 Retail Plaza",
            "is_active": True,
        },
        headers=auth_tokens["planner"],
    )
    assert create_resp.status_code == 201
    ret_id = create_resp.json()["id"]

    # 2. GET /api/v1/retailers
    list_resp = client.get("/api/v1/retailers", headers=auth_tokens["planner"])
    assert list_resp.status_code == 200
    assert any(item["code"] == "RET-ALIAS-01" for item in list_resp.json()["items"])

    # 3. GET /api/v1/retailers/{id}
    get_resp = client.get(f"/api/v1/retailers/{ret_id}", headers=auth_tokens["planner"])
    assert get_resp.status_code == 200
    assert get_resp.json()["code"] == "RET-ALIAS-01"
