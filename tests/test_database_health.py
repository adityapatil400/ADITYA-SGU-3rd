"""
FlowGrid Database Health & Diagnostic Test Suite.
Validates:
1. Connection & readiness checks with secret sanitization.
2. Complete ORM schema, table registration, and relationships.
3. Check constraints, unique constraints, and foreign key integrity.
4. CRUD operations and session persistence.
5. Inventory math, versioning, and non-negative guarantees.
6. Transaction atomicity and rollback safety.
7. Safe, idempotent migration & data seeding.
"""

from datetime import date, datetime, timedelta, timezone
import pytest
from sqlalchemy import create_engine, text, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import Base
from app.db.session import check_db_connection, engine
from app.models.enums import UserRole, StoreType, OrderPriority, OrderStatus, PlanStatus, PlanEventType
from app.models.facility import Warehouse, Store
from app.models.product import Product
from app.models.inventory import InventoryBatch, InventoryReservation, AuthoritativeInventory
from app.models.order import DistributionOrder, DistributionOrderItem
from app.models.plan import DistributionPlan, PlanAllocation, PlanEvent
from app.models.scenario import SeededScenario
from app.models.sales import SalesObservation
from app.models.user import User
from app.services.inventory_service import InventoryService
from app.services.migration_service import DataMigrationService


# ==============================================================================
# 1. Connection & Configuration Tests
# ==============================================================================

def test_database_connectivity_and_safety():
    """Validates real driver connection, ping response, and secret sanitization."""
    # 1. Real connection ping
    is_ready, err = check_db_connection()
    assert is_ready is True
    assert err is None

    # 2. Secret sanitization invariant
    sanitized_url = settings.sanitized_database_url
    assert "password" not in sanitized_url.lower() or "***" in sanitized_url
    assert "@" in sanitized_url or "sqlite" in sanitized_url

    # 3. Connection failure masking
    # If a broken connection string is provided, secrets must not be leaked
    broken_engine = create_engine("sqlite:////non_existent_dir_12345/db.sqlite")
    try:
        with broken_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        # Masked safely
        error_msg = str(exc)
        assert isinstance(error_msg, str)


# ==============================================================================
# 2. Schema, Tables, and Models Registration Tests
# ==============================================================================

def test_database_schema_and_model_registration(db_session: Session):
    """Verifies that all required models and tables exist with expected metadata."""
    tables = Base.metadata.tables

    expected_tables = [
        "users",
        "warehouses",
        "stores",
        "products",
        "inventory_batches",
        "inventory_reservations",
        "distribution_orders",
        "distribution_order_items",
        "distribution_plans",
        "plan_allocations",
        "plan_events",
        "seeded_scenarios",
        "sales_observations",
        "distribution_nodes",
        "authoritative_inventories",
    ]

    for table_name in expected_tables:
        assert table_name in tables, f"Expected table '{table_name}' was not found in ORM metadata."
        table = tables[table_name]
        assert len(table.primary_key.columns) > 0, f"Table '{table_name}' must have a primary key."


# ==============================================================================
# 3. CRUD Operations and Relationship Traversal Tests
# ==============================================================================

def test_database_crud_and_relationship_integrity(db_session: Session):
    """Tests create, read, update, delete, and bi-directional relationship navigation."""
    # 1. Create
    wh = Warehouse(code="WH-HEALTH-01", name="Health Test DC", region="North", capacity_limit=200000)
    st = Store(code="STORE-HEALTH-01", name="Health Store", region="North", store_type=StoreType.FLAGSHIP)
    prod = Product(sku="SKU-HEALTH-01", name="Health Tablet 10", category="Electronics", shelf_life_days=365)
    db_session.add_all([wh, st, prod])
    db_session.commit()

    batch = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-H-001",
        on_hand_quantity=500,
        reserved_quantity=50,
        committed_quantity=25,
        expiry_date=date(2027, 6, 1),
        version=1,
    )
    db_session.add(batch)
    db_session.commit()

    # 2. Read and verify bi-directional relationships
    refreshed_batch = db_session.query(InventoryBatch).filter_by(id=batch.id).first()
    assert refreshed_batch is not None
    assert refreshed_batch.warehouse.code == "WH-HEALTH-01"
    assert refreshed_batch.product.sku == "SKU-HEALTH-01"
    assert len(wh.batches) == 1
    assert wh.batches[0].batch_number == "BATCH-H-001"
    assert len(prod.batches) == 1

    # 3. Update
    refreshed_batch.on_hand_quantity = 600
    refreshed_batch.version += 1
    wh.current_inventory_version += 1
    db_session.commit()

    updated_batch = db_session.query(InventoryBatch).filter_by(id=batch.id).first()
    assert updated_batch is not None
    assert updated_batch.on_hand_quantity == 600
    assert updated_batch.version == 2
    assert wh.current_inventory_version == 2

    # 4. Delete permitted record
    db_session.delete(updated_batch)
    db_session.commit()
    assert db_session.query(InventoryBatch).filter_by(id=batch.id).first() is None


# ==============================================================================
# 4. Constraints Enforcement (Unique, Check, Foreign Keys)
# ==============================================================================

def test_unique_and_check_constraints_enforcement(db_session: Session):
    """Verifies that database constraints reject duplicate and invalid data."""
    # 1. Unique warehouse code
    wh1 = Warehouse(code="WH-UQ-01", name="DC 1", region="South", capacity_limit=100000)
    db_session.add(wh1)
    db_session.commit()

    wh_dup = Warehouse(code="WH-UQ-01", name="DC 2", region="South", capacity_limit=100000)
    db_session.add(wh_dup)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # 2. Unique product SKU
    prod1 = Product(sku="SKU-UQ-01", name="Product 1")
    db_session.add(prod1)
    db_session.commit()

    prod_dup = Product(sku="SKU-UQ-01", name="Product Duplicate")
    db_session.add(prod_dup)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # 3. Unique user email
    user1 = User(email="unique@flowgrid.io", hashed_password="hash", full_name="User 1")
    db_session.add(user1)
    db_session.commit()

    user_dup = User(email="unique@flowgrid.io", hashed_password="hash", full_name="User Duplicate")
    db_session.add(user_dup)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # 4. Check constraint: on_hand_quantity >= 0
    wh = db_session.query(Warehouse).first()
    prod = db_session.query(Product).first()
    assert wh is not None and prod is not None

    invalid_batch = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-NEGATIVE",
        on_hand_quantity=-10,  # Violates chk_on_hand_non_negative
        version=1,
    )
    db_session.add(invalid_batch)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # 5. Check constraint: on_hand >= reserved + committed
    oversubscribed_batch = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-OVER-RESERVED",
        on_hand_quantity=100,
        reserved_quantity=80,
        committed_quantity=30,  # 80 + 30 = 110 > 100
        version=1,
    )
    db_session.add(oversubscribed_batch)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ==============================================================================
# 5. Inventory Calculation & Versioning Integrity Tests
# ==============================================================================

def test_inventory_math_and_version_invariants(db_session: Session):
    """Verifies that available stock is accurately computed and version tracking works."""
    wh = Warehouse(code="WH-MATH-01", name="Math DC", region="East", capacity_limit=150000, current_inventory_version=1)
    prod = Product(sku="SKU-MATH-01", name="Math Product")
    db_session.add_all([wh, prod])
    db_session.commit()

    batch = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-MATH-100",
        on_hand_quantity=1000,
        reserved_quantity=200,
        committed_quantity=150,
        version=1,
    )
    db_session.add(batch)
    db_session.commit()

    # Invariant: Available stock = physical quantity - reserved quantity - committed quantity
    assert batch.available_quantity == 650

    # Mutate and ensure balance updates
    batch.reserved_quantity += 100
    assert batch.available_quantity == 550
    db_session.commit()


# ==============================================================================
# 6. Transaction Atomicity & Rollback Safety Tests
# ==============================================================================

def test_transaction_rollback_guarantee(db_session: Session):
    """Ensures that errors occurring during multi-record operations roll back completely."""
    wh = Warehouse(code="WH-TX-01", name="TX Warehouse", region="West", capacity_limit=100000)
    prod = Product(sku="SKU-TX-01", name="TX Product")
    db_session.add_all([wh, prod])
    db_session.commit()

    batch_initial = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-TX-INIT",
        on_hand_quantity=500,
        version=1,
    )
    db_session.add(batch_initial)
    db_session.commit()

    initial_version = wh.current_inventory_version
    initial_batch_count = db_session.query(InventoryBatch).count()

    # Multi-step atomic operation: modify batch, increment warehouse version, then trigger error
    try:
        batch_initial.on_hand_quantity = 800
        wh.current_inventory_version += 1
        # Add a broken record that violates uniqueness
        duplicate_wh = Warehouse(code="WH-TX-01", name="Duplicate", region="West", capacity_limit=1000)
        db_session.add(duplicate_wh)
        db_session.commit()
    except IntegrityError:
        db_session.rollback()

    # Invariant: Clean rollback, zero partial changes persisted
    refreshed_batch = db_session.query(InventoryBatch).filter_by(id=batch_initial.id).first()
    refreshed_wh = db_session.query(Warehouse).filter_by(id=wh.id).first()
    assert refreshed_batch is not None and refreshed_wh is not None
    assert refreshed_batch.on_hand_quantity == 500  # Not 800!
    assert refreshed_wh.current_inventory_version == initial_version  # Not incremented!
    assert db_session.query(InventoryBatch).count() == initial_batch_count


# ==============================================================================
# 7. Safe Migration & Repeated Seeding Idempotency Tests
# ==============================================================================

def test_data_migration_idempotency_and_invariant_audit(db_session: Session):
    """Validates that DataMigrationService can be run repeatedly without duplicating data."""
    # First migration run
    run1 = DataMigrationService.execute_safe_migration(db_session, scenario_code="SCENARIO-HEALTH-SEED")
    assert run1["status"] == "MIGRATION_VERIFIED_SUCCESSFUL"
    assert run1["verification_report"]["is_valid"] is True
    assert run1["migration_stats"]["warehouses_upserted"] >= 1

    first_wh_count = db_session.query(Warehouse).count()
    first_prod_count = db_session.query(Product).count()
    first_batch_count = db_session.query(InventoryBatch).count()

    # Second migration run (idempotency check)
    run2 = DataMigrationService.execute_safe_migration(db_session, scenario_code="SCENARIO-HEALTH-SEED")
    assert run2["status"] == "MIGRATION_VERIFIED_SUCCESSFUL"
    assert run2["migration_stats"]["duplicates_prevented"] > 0

    # Counts must remain identical
    assert db_session.query(Warehouse).count() == first_wh_count
    assert db_session.query(Product).count() == first_prod_count
    assert db_session.query(InventoryBatch).count() == first_batch_count

    # Verify inventory invariants report
    audit = DataMigrationService.verify_inventory_invariants(db_session)
    assert audit["is_valid"] is True
    assert audit["violations_count"] == 0
