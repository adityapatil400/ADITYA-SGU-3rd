import pytest
from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models.enums import (
    StoreType,
    PlanStatus,
    OrderPriority,
    PlanEventType,
)
from app.models.facility import Warehouse, Store
from app.models.product import Product
from app.models.inventory import InventoryBatch
from app.models.scenario import SeededScenario
from app.models.plan import DistributionPlan, PlanAllocation, PlanEvent


def test_warehouse_creation(db_session: Session):
    wh = Warehouse(
        code="WH-WEST-01",
        name="Western Regional Distribution Center",
        region="West",
        capacity_limit=500000,
        current_inventory_version=1,
    )
    db_session.add(wh)
    db_session.commit()
    db_session.refresh(wh)

    assert wh.id is not None
    assert wh.code == "WH-WEST-01"
    assert wh.is_active is True
    assert wh.current_inventory_version == 1


def test_inventory_batch_properties_and_constraints(db_session: Session):
    wh = Warehouse(code="WH-NORTH-01", name="Northern Central Hub", region="North")
    prod = Product(sku="SKU-PHONE-X", name="Smart Phone X")
    db_session.add_all([wh, prod])
    db_session.commit()

    batch = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-PHONE-001",
        on_hand_quantity=1000,
        reserved_quantity=200,
        committed_quantity=150,
        version=1,
    )
    db_session.add(batch)
    db_session.commit()
    db_session.refresh(batch)

    # 1000 on hand - (200 reserved + 150 committed) = 650 available
    assert batch.available_quantity == 650
    assert batch.warehouse.code == "WH-NORTH-01"
    assert batch.product.sku == "SKU-PHONE-X"


def test_inventory_batch_duplicate_raises_integrity_error(db_session: Session):
    wh = Warehouse(code="WH-SOUTH-01", name="Southern Regional DC", region="South")
    prod = Product(sku="SKU-LAPTOP-15", name="Laptop 15 inch")
    db_session.add_all([wh, prod])
    db_session.commit()

    b1 = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-LP-01",
        on_hand_quantity=100,
    )
    db_session.add(b1)
    db_session.commit()

    # Attempt to insert same batch_number for same warehouse and product
    b2 = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-LP-01",
        on_hand_quantity=50,
    )
    db_session.add(b2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_distribution_plan_and_scenario_lifecycle(db_session: Session):
    wh = Warehouse(code="WH-CENTRAL", name="Central DC", region="Central")
    store = Store(code="STORE-MUM-01", name="Mumbai Retail Hub", region="West", store_type=StoreType.FLAGSHIP)
    prod = Product(sku="SKU-PHONE-X", name="Smart Phone X")
    db_session.add_all([wh, store, prod])
    db_session.commit()

    batch = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-PH-01",
        on_hand_quantity=500,
    )
    db_session.add(batch)
    db_session.commit()

    # Create Seeded Scenario
    scenario = SeededScenario(
        scenario_code="SCENARIO-DIWALI-PEAK-2026",
        name="Diwali Electronics Demand Surge",
        description="Surge in smartphone demand in Western region",
        parameters={"affected_sku": "SKU-PHONE-X", "surge_factor": 1.5},
    )
    db_session.add(scenario)
    db_session.commit()

    # Propose Candidate Plan (starts in PROPOSED status)
    plan = DistributionPlan(
        plan_code="PLAN-DIWALI-001",
        scenario_id=scenario.id,
        target_warehouse_id=wh.id,
        input_inventory_version=wh.current_inventory_version,
        status=PlanStatus.PROPOSED,
        candidate_allocations=[{"sku": prod.sku, "quantity": 300}],
        decision_service_metadata={"model": "flowgrid-opt-v1", "candidate_id": "CAND-AI-9021"},
        validation_results={},
        is_validated=False,
    )
    db_session.add(plan)
    db_session.commit()

    # Add planned allocation item
    alloc = PlanAllocation(
        plan_id=plan.id,
        source_warehouse_id=wh.id,
        destination_store_id=store.id,
        product_id=prod.id,
        batch_id=batch.id,
        allocated_quantity=300,
        estimated_transit_hours=12,
    )
    db_session.add(alloc)

    # Add audit event entry
    audit = PlanEvent(
        plan_id=plan.id,
        trace_id=plan.trace_id,
        event_type=PlanEventType.CANDIDATE_PROPOSED,
        actor_type="decision_service",
        actor_id="flowgrid-ai-optimizer-v1",
        new_status=PlanStatus.PROPOSED.value,
        payload={"candidate_id": "CAND-AI-9021", "proposed_quantity": 300},
    )
    db_session.add(audit)
    db_session.commit()

    # Verify query and relationships
    db_session.refresh(plan)
    assert plan.status == PlanStatus.PROPOSED
    assert len(plan.allocations) == 1
    assert plan.allocations[0].allocated_quantity == 300
    assert len(plan.events) == 1
    assert plan.events[0].event_type == PlanEventType.CANDIDATE_PROPOSED
    assert plan.is_validated is False
    assert plan.committed_at is None
    assert plan.scenario is not None
    assert plan.scenario.scenario_code == "SCENARIO-DIWALI-PEAK-2026"
