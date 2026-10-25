"""
Tests for FlowGrid Backend Decision Guard & Authoritative Plan Lifecycle.

Verifies:
1. Valid plan lifecycle: Seeded scenario -> Snapshot -> Decision service -> Authoritative validation ->
   Approval -> Commit-time revalidation -> Transactional commit -> Fully traceable result.
2. Infeasible plan rejection: Decision service returns excessive/expired allocation -> Authoritative validation
   rejects candidate -> Stores rejection reasons -> Rejects invalid approval/commitment transitions.
3. Stale plan commitment rejection: Warehouse version advances prior to commitment -> Commit-time validation
   detects mismatch -> Rejects commitment with 409 -> Releases reservations and logs rejection event.
4. Duplicate commitment safeguards: Idempotent replay with same key succeeds without double-deduction;
   conflicting key returns 409.
5. Decision guard constraints: Decision service proposals cannot alter DB directly; invalid lifecycle transitions
   (DRAFT/CANCELLED/REJECTED -> COMMITTED) are strictly rejected; separation of duties is enforced.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict
import pytest
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.core.security import hash_password, create_access_token
from app.models.enums import UserRole, PlanStatus, PlanEventType, ScenarioType, OrderStatus, OrderPriority
from app.models.facility import Warehouse, Store
from app.models.product import Product
from app.models.inventory import InventoryBatch
from app.models.order import DistributionOrder, DistributionOrderItem
from app.models.plan import DistributionPlan, PlanEvent
from app.models.scenario import SeededScenario
from app.models.user import User
from app.services.decision_service_client import DecisionServiceClient
from app.services.plan_lifecycle_service import PlanLifecycleService


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def guard_users(db_session: Session):
    """Creates distinct planner and approver users to enforce separation of duties."""
    planner = User(
        email="guard_planner@flowgrid.io",
        hashed_password=hash_password("PlannerPass123!"),
        full_name="Planner Guard",
        role=UserRole.PLANNER,
        is_active=True,
    )
    approver = User(
        email="guard_approver@flowgrid.io",
        hashed_password=hash_password("ApproverPass123!"),
        full_name="Approver Guard",
        role=UserRole.APPROVER,
        is_active=True,
    )
    viewer = User(
        email="guard_viewer@flowgrid.io",
        hashed_password=hash_password("ViewerPass123!"),
        full_name="Viewer Guard",
        role=UserRole.VIEWER,
        is_active=True,
    )
    db_session.add_all([planner, approver, viewer])
    db_session.commit()

    tokens = {
        "planner": create_access_token(data={"sub": planner.email, "user_id": planner.id, "role": planner.role.value}),
        "approver": create_access_token(data={"sub": approver.email, "user_id": approver.id, "role": approver.role.value}),
        "viewer": create_access_token(data={"sub": viewer.email, "user_id": viewer.id, "role": viewer.role.value}),
    }
    return {
        "planner": planner,
        "approver": approver,
        "viewer": viewer,
        "headers": {
            "planner": {"Authorization": f"Bearer {tokens['planner']}"},
            "approver": {"Authorization": f"Bearer {tokens['approver']}"},
            "viewer": {"Authorization": f"Bearer {tokens['viewer']}"},
        },
    }


@pytest.fixture
def guard_scenario(db_session: Session):
    """Creates a seeded distribution network with warehouses, stores, products, batches, and demand orders."""
    scenario = SeededScenario(
        scenario_code="SCENARIO-GUARD-TEST-01",
        name="Decision Guard Network Scenario",
        scenario_type=ScenarioType.DEMAND_SURGE,
        description="Authoritative scenario for testing backend decision guard.",
        parameters={"affected_skus": ["SKU-GUARD-5G-PHONE"], "surge_percentage": 20},
        is_active=True,
    )
    wh = Warehouse(
        code="WH-GUARD-WEST",
        name="West DC Guard Hub",
        region="West",
        capacity_limit=100000,
        current_inventory_version=1,
    )
    store = Store(
        code="STORE-GUARD-PUNE",
        name="Pune Retail Guard",
        region="West",
        priority_tier=1,
    )
    prod = Product(
        sku="SKU-GUARD-5G-PHONE",
        name="Guard 5G Smartphone",
        category="Mobile",
        shelf_life_days=365,
    )
    db_session.add_all([scenario, wh, store, prod])
    db_session.commit()

    batch = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-GUARD-001",
        on_hand_quantity=1000,
        reserved_quantity=0,
        committed_quantity=0,
        expiry_date=date.today() + timedelta(days=200),
        version=1,
    )
    db_session.add(batch)
    db_session.commit()

    # Seed an authoritative distribution order to populate demand in the snapshot
    order = DistributionOrder(
        order_number="ORD-GUARD-DEMAND-01",
        destination_store_id=store.id,
        source_warehouse_id=wh.id,
        status=OrderStatus.PENDING,
        priority=OrderPriority.HIGH,
    )
    db_session.add(order)
    db_session.flush()

    order_item = DistributionOrderItem(
        order_id=order.id,
        product_id=prod.id,
        requested_quantity=150,
        fulfilled_quantity=0,
    )
    db_session.add(order_item)
    db_session.commit()

    return {
        "scenario": scenario,
        "warehouse": wh,
        "store": store,
        "product": prod,
        "batch": batch,
        "order": order,
    }


# ==============================================================================
# 1. Valid Lifecycle & Traceable Result
# ==============================================================================

def test_valid_plan_end_to_end_traceable_lifecycle(
    client: TestClient,
    db_session: Session,
    guard_users: Dict[str, Any],
    guard_scenario: Dict[str, Any],
):
    """
    Demonstrates the complete authoritative 10-step lifecycle:
    1. Seeded scenario submitted -> 2. Inventory loaded -> 3. Frozen snapshot taken ->
    4. Decision service called -> 5. Candidate proposal returned -> 6. Validated by backend ->
    7. Stored with traceable metadata -> 8. Approved by authorized user ->
    9. Revalidated at commit -> 10. Committed transactionally.
    """
    scenario = guard_scenario["scenario"]
    wh = guard_scenario["warehouse"]
    batch = guard_scenario["batch"]
    headers = guard_users["headers"]

    # 1-7: Generate plan from scenario
    resp = client.post(
        f"/api/v1/scenarios/{scenario.id}/plans",
        headers=headers["planner"],
    )
    assert resp.status_code == 201
    plan_data = resp.json()

    # Verify all traceable result fields are present and correctly populated
    assert plan_data["scenario_id"] == scenario.id
    assert plan_data["plan_id"] == plan_data["id"]
    assert plan_data["inventory_version"] == 1
    assert plan_data["input_inventory_version"] == 1
    assert isinstance(plan_data["candidate_allocations"], list)
    assert len(plan_data["candidate_allocations"]) > 0

    # Snapshot traceability
    snapshot = plan_data["inventory_snapshot"]
    assert snapshot is not None
    assert "snapshot_id" in snapshot
    assert snapshot["snapshot_id"].startswith("SNAP-")
    assert "frozen_at" in snapshot
    assert "stock_versions" in snapshot
    assert snapshot["stock_versions"].get(wh.code) == 1
    assert len(snapshot.get("warehouses", [])) > 0
    assert len(snapshot.get("stores", [])) > 0
    assert len(snapshot.get("orders", [])) > 0
    assert len(snapshot.get("demand", [])) > 0
    assert len(snapshot.get("routes", [])) > 0

    # Decision service & validation statuses
    assert plan_data["decision_service_status"] == "COMPLETED"
    assert plan_data["validation_status"] == "VALIDATED"
    assert plan_data["rejection_reasons"] == []
    assert plan_data["status"] == "VALIDATED"
    assert plan_data["is_validated"] is True
    assert plan_data["approval_status"] == "PENDING"
    assert plan_data["commitment_status"] == "UNCOMMITTED"
    assert len(plan_data["audit_event_ids"]) >= 3

    plan_id = plan_data["plan_id"]

    # 8. An authorized user (Approver) approves the plan
    approve_resp = client.post(
        f"/api/v1/plans/{plan_id}/approve",
        headers=headers["approver"],
    )
    assert approve_resp.status_code == 200
    approval_data = approve_resp.json()
    assert approval_data["status"] == "APPROVED"
    assert approval_data["approval_status"] == "APPROVED"
    assert approval_data["approved_by_user_id"] == guard_users["approver"].id
    assert len(approval_data["audit_event_ids"]) >= 4

    # Verify plan inspection reflects approved status
    inspect_resp = client.get(f"/api/v1/plans/{plan_id}", headers=headers["viewer"])
    assert inspect_resp.status_code == 200
    inspect_data = inspect_resp.json()
    assert inspect_data["status"] == "APPROVED"
    assert inspect_data["approval_status"] == "APPROVED"
    assert inspect_data["commitment_status"] == "UNCOMMITTED"

    # 9-10. Commit plan transactionally (revalidating inventory versions and stock)
    commit_resp = client.post(
        f"/api/v1/plans/{plan_id}/commit",
        json={"idempotency_key": "IDEM-VALID-E2E-001", "commit_reason": "Authoritative release"},
        headers=headers["approver"],
    )
    assert commit_resp.status_code == 200
    commit_data = commit_resp.json()

    # Verify traceable commitment outcome
    assert commit_data["status"] == "COMMITTED"
    assert commit_data["commitment_status"] == "COMMITTED"
    assert commit_data["validation_status"] == "VALIDATED"
    assert commit_data["approval_status"] == "APPROVED"
    assert commit_data["plan_id"] == plan_id
    assert commit_data["scenario_id"] == scenario.id
    assert commit_data["inventory_version"] == 2
    assert commit_data["committed_movements_count"] > 0
    assert len(commit_data["audit_event_ids"]) >= 5

    # Authoritative stock verification
    db_session.refresh(batch)
    db_session.refresh(wh)
    assert batch.committed_quantity > 0
    assert batch.available_quantity == 1000 - batch.committed_quantity
    assert wh.current_inventory_version == 2


# ==============================================================================
# 2. Infeasible Plan Workflow & Rejection
# ==============================================================================

def test_infeasible_plan_workflow_and_rejection(
    client: TestClient,
    db_session: Session,
    guard_users: Dict[str, Any],
    guard_scenario: Dict[str, Any],
):
    """
    Verifies that an infeasible candidate (excessive quantity or expired batch)
    is rejected by FastAPI authoritative validation, stores rejection reasons,
    and rejects approval or commitment.
    """
    scenario = guard_scenario["scenario"]
    headers = guard_users["headers"]

    # Mock decision service returning an infeasible proposal (allocating 999,999 units)
    def excessive_proposal_mock(payload):
        return {
            "candidate_id": "CAND-INFEASIBLE-01",
            "scenario_code": scenario.scenario_code,
            "model_version": "test-mock-optimizer-v1",
            "confidence_score": 0.40,
            "proposed_movements": [
                {
                    "sku": guard_scenario["product"].sku,
                    "from_node_code": guard_scenario["warehouse"].code,
                    "to_node_code": guard_scenario["store"].code,
                    "quantity": 999999,  # Far exceeds available 1000 units
                    "priority": "HIGH",
                    "estimated_transit_hours": 24,
                    "rationale": "Excessive allocation test",
                }
            ],
        }

    DecisionServiceClient.set_mock_handler(excessive_proposal_mock)
    try:
        resp = client.post(
            f"/api/v1/scenarios/{scenario.id}/plans",
            headers=headers["planner"],
        )
        assert resp.status_code == 201
        plan_data = resp.json()
        plan_id = plan_data["id"]

        # Backend decision guard caught the violation:
        assert plan_data["status"] == "REJECTED"
        assert plan_data["is_validated"] is False
        assert plan_data["validation_status"] == "REJECTED"
        assert len(plan_data["rejection_reasons"]) > 0
        assert "INSUFFICIENT_ON_HAND_STOCK" in str(plan_data["validation_results"])

        # Attempting to approve an infeasible plan must be rejected (HTTP 422)
        approve_resp = client.post(
            f"/api/v1/plans/{plan_id}/approve",
            headers=headers["approver"],
        )
        assert approve_resp.status_code == 422
        assert "Invalid plan state transition" in approve_resp.json()["message"]

        # Attempting to commit an infeasible plan must be rejected (HTTP 422)
        commit_resp = client.post(
            f"/api/v1/plans/{plan_id}/commit",
            headers=headers["approver"],
        )
        assert commit_resp.status_code == 422
        assert "Invalid plan state transition" in commit_resp.json()["message"]

        # Invariant: Physical stock never changed
        wh = guard_scenario["warehouse"]
        batch = guard_scenario["batch"]
        db_session.refresh(wh)
        db_session.refresh(batch)
        assert batch.committed_quantity == 0
        assert batch.available_quantity == 1000
        assert wh.current_inventory_version == 1
    finally:
        DecisionServiceClient.set_mock_handler(None)


# ==============================================================================
# 3. Stale Plan Rejection at Commitment Time
# ==============================================================================

def test_stale_plan_commitment_rejection(
    client: TestClient,
    db_session: Session,
    guard_users: Dict[str, Any],
    guard_scenario: Dict[str, Any],
):
    """
    Verifies that if authoritative inventory version advances between plan generation
    and commitment, FastAPI revalidation rejects the commitment with HTTP 409 Conflict.
    """
    scenario = guard_scenario["scenario"]
    wh = guard_scenario["warehouse"]
    batch = guard_scenario["batch"]
    headers = guard_users["headers"]

    # 1. Generate plan
    resp = client.post(
        f"/api/v1/scenarios/{scenario.id}/plans",
        headers=headers["planner"],
    )
    assert resp.status_code == 201
    plan_id = resp.json()["id"]

    # 2. Approver approves the plan
    approve_resp = client.post(f"/api/v1/plans/{plan_id}/approve", headers=headers["approver"])
    assert approve_resp.status_code == 200

    # 3. Simulate concurrent background inventory modification:
    # Warehouse current_inventory_version advances from 1 -> 2
    wh.current_inventory_version = 2
    db_session.commit()

    # 4. Approver attempts to commit the stale plan
    commit_resp = client.post(
        f"/api/v1/plans/{plan_id}/commit",
        json={"idempotency_key": "IDEM-STALE-COMMIT"},
        headers=headers["approver"],
    )
    # Must reject with 409 Conflict due to stale assumptions
    assert commit_resp.status_code == 409
    assert "version mismatch" in commit_resp.json()["message"].lower()

    # Verify plan was marked REJECTED in database
    plan = db_session.query(DistributionPlan).filter_by(id=plan_id).first()
    assert plan is not None
    assert plan.status == PlanStatus.REJECTED
    assert plan.rejection_reasons is not None
    assert len(plan.rejection_reasons) > 0


# ==============================================================================
# 4. Duplicate Commitment & Idempotency Safeguards
# ==============================================================================

def test_duplicate_commitment_idempotency_and_conflict(
    client: TestClient,
    db_session: Session,
    guard_users: Dict[str, Any],
    guard_scenario: Dict[str, Any],
):
    """
    Verifies that:
    - Same idempotency key returns 200 without double-deduction.
    - Conflicting idempotency key returns 409 Conflict.
    """
    scenario = guard_scenario["scenario"]
    wh = guard_scenario["warehouse"]
    batch = guard_scenario["batch"]
    headers = guard_users["headers"]

    resp = client.post(f"/api/v1/scenarios/{scenario.id}/plans", headers=headers["planner"])
    assert resp.status_code == 201
    plan_id = resp.json()["id"]

    # Approve
    client.post(f"/api/v1/plans/{plan_id}/approve", headers=headers["approver"])

    # Commit 1 (First attempt with key KEY-DUPLICATE-01)
    commit1 = client.post(
        f"/api/v1/plans/{plan_id}/commit",
        json={"idempotency_key": "KEY-DUPLICATE-01"},
        headers=headers["approver"],
    )
    assert commit1.status_code == 200
    assert commit1.json()["status"] == "COMMITTED"

    db_session.refresh(batch)
    committed_qty_after_1 = batch.committed_quantity
    assert committed_qty_after_1 > 0

    # Commit 2 (Idempotent replay with IDENTICAL key KEY-DUPLICATE-01)
    commit2 = client.post(
        f"/api/v1/plans/{plan_id}/commit",
        json={"idempotency_key": "KEY-DUPLICATE-01"},
        headers=headers["approver"],
    )
    assert commit2.status_code == 200
    assert "previously committed" in commit2.json()["message"].lower()

    # Invariant check: Stock was NOT deducted a second time
    db_session.refresh(batch)
    assert batch.committed_quantity == committed_qty_after_1

    # Commit 3 (Conflicting repeat attempt with DIFFERENT key KEY-DUPLICATE-02)
    commit3 = client.post(
        f"/api/v1/plans/{plan_id}/commit",
        json={"idempotency_key": "KEY-DUPLICATE-02"},
        headers=headers["approver"],
    )
    assert commit3.status_code == 409
    assert "already been committed" in commit3.json()["message"].lower()


# ==============================================================================
# 5. Backend Decision Guard Invariants
# ==============================================================================

def test_decision_service_cannot_bypass_backend_guard(
    client: TestClient,
    db_session: Session,
    guard_users: Dict[str, Any],
    guard_scenario: Dict[str, Any],
):
    """
    Ensures that the decision service cannot approve, reserve, or commit directly:
    - Creator cannot approve or commit (Separation of duties).
    - Unvalidated or DRAFT plans cannot be committed (State machine guard).
    - Cancelled plans cannot be committed.
    """
    scenario = guard_scenario["scenario"]
    headers = guard_users["headers"]

    # Create raw DRAFT plan
    draft_plan = DistributionPlan(
        plan_code="PLAN-RAW-DRAFT-01",
        scenario_id=scenario.id,
        target_warehouse_id=guard_scenario["warehouse"].id,
        input_inventory_version=1,
        status=PlanStatus.DRAFT,
        created_by_user_id=guard_users["planner"].id,
    )
    db_session.add(draft_plan)
    db_session.commit()

    # Guard: Cannot commit a DRAFT plan directly
    draft_commit = client.post(f"/api/v1/plans/{draft_plan.id}/commit", headers=headers["approver"])
    assert draft_commit.status_code == 422

    # Guard: Cannot approve a DRAFT plan directly
    draft_approve = client.post(f"/api/v1/plans/{draft_plan.id}/approve", headers=headers["approver"])
    assert draft_approve.status_code == 422

    # Guard: Creator (Planner) cannot approve their own plan
    draft_plan.status = PlanStatus.VALIDATED
    draft_plan.is_validated = True
    db_session.commit()

    self_approve = client.post(f"/api/v1/plans/{draft_plan.id}/approve", headers=headers["planner"])
    assert self_approve.status_code == 403

    # Guard: Creator (Planner) cannot commit their own plan
    self_commit = client.post(f"/api/v1/plans/{draft_plan.id}/commit", headers=headers["planner"])
    assert self_commit.status_code == 403

    # Guard: Cancelled plan cannot be committed
    draft_plan.status = PlanStatus.CANCELLED
    db_session.commit()
    cancel_commit = client.post(f"/api/v1/plans/{draft_plan.id}/commit", headers=headers["approver"])
    assert cancel_commit.status_code == 422
