"""
FlowGrid Final Acceptance & End-to-End Integration Suite.

Demonstrates:
1. Complete deterministic 9-step plan lifecycle:
   Seed Scenario -> Submit -> Decision Service -> Candidate Plan -> Authoritative Validation ->
   Role-based Approval -> Transactional Commit -> Audit Trail Retrieval -> Inventory Verification.

Failure Scenarios:
1. Decision service unavailable (fails safely, no unvalidated plan committed).
2. Candidate allocation exceeds available inventory.
3. Expired batch allocation.
4. Concurrent plans competing for limited stock (prevents double-allocation & negative stock).
5. Inventory changes after plan generation (stale plan version mismatch rejection).
6. Unauthorized approval and commitment (RBAC and Separation of Duties).
7. Duplicate commitment requests (idempotency safety and conflict rejection).
8. Database transaction failure (atomic rollback, no partial inventory mutations).

Acceptance Criteria:
- A valid plan can be committed exactly once.
- An invalid or stale plan cannot be committed.
- Inventory cannot become negative under any concurrent or invalid load.
- Every commitment and rejection has a traceable audit record.
- The API returns understandable validation errors.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict
from unittest.mock import patch
import httpx
import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.core.exceptions import (
    DecisionServiceTimeoutException,
    DecisionServiceException,
    InventoryVersionMismatchException,
    InsufficientInventoryException,
    ExpiredBatchException,
    PlanAlreadyCommittedException,
    AuthoritativeRuleViolationException,
)
from app.core.security import hash_password, create_access_token
from app.models.enums import UserRole, PlanStatus, PlanEventType, ScenarioType
from app.models.facility import Warehouse, Store
from app.models.product import Product
from app.models.inventory import InventoryBatch
from app.models.plan import DistributionPlan, PlanEvent, PlanAllocation
from app.models.scenario import SeededScenario
from app.models.user import User
from app.services.decision_service_client import DecisionServiceClient
from app.services.inventory_service import InventoryService
from app.services.plan_lifecycle_service import PlanLifecycleService


# ==============================================================================
# Acceptance Fixtures
# ==============================================================================

@pytest.fixture
def acceptance_users(db_session: Session):
    """Sets up an authoritative role hierarchy with JWT credentials."""
    planner = User(
        email="acceptance_planner@flowgrid.io",
        hashed_password=hash_password("PlannerSecret123!"),
        full_name="Alex Rivera (Lead Planner)",
        role=UserRole.PLANNER,
        is_active=True,
    )
    approver = User(
        email="acceptance_approver@flowgrid.io",
        hashed_password=hash_password("ApproverSecret123!"),
        full_name="Morgan Vance (Inventory Controller)",
        role=UserRole.APPROVER,
        is_active=True,
    )
    viewer = User(
        email="acceptance_viewer@flowgrid.io",
        hashed_password=hash_password("ViewerSecret123!"),
        full_name="Sam Lee (Compliance Auditor)",
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
def deterministic_scenario(db_session: Session):
    """Creates a deterministic distribution scenario with authoritative network nodes and batches."""
    wh = Warehouse(
        code="WH-MUMBAI-DC01",
        name="Mumbai Central Distribution Center",
        region="West",
        capacity_limit=100000,
        current_inventory_version=10,
    )
    store = Store(
        code="STORE-PUNE-01",
        name="Pune High Street Retail",
        region="West",
        priority_tier=1,
    )
    prod = Product(
        sku="SKU-FLOW-5G-ROUTER",
        name="FlowGrid Industrial 5G Router",
        category="Networking",
        shelf_life_days=365,
    )
    db_session.add_all([wh, store, prod])
    db_session.commit()

    batch_valid = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-ROUTER-2026-A",
        on_hand_quantity=1000,
        reserved_quantity=0,
        committed_quantity=0,
        expiry_date=date.today() + timedelta(days=365),
        version=1,
    )
    db_session.add(batch_valid)
    db_session.commit()

    scenario = SeededScenario(
        scenario_code="SCENARIO-SURGE-Q4-2026",
        name="Q4 Critical Network Surge",
        scenario_type=ScenarioType.DEMAND_SURGE,
        description="Priority telecom equipment distribution for Pune region.",
        parameters={
            "affected_skus": ["SKU-FLOW-5G-ROUTER"],
            "requested_quantity": 250,
            "target_warehouse_code": wh.code,
            "target_store_code": store.code,
        },
        is_active=True,
    )
    db_session.add(scenario)
    db_session.commit()

    return {
        "scenario": scenario,
        "warehouse": wh,
        "store": store,
        "product": prod,
        "batch": batch_valid,
    }


# ==============================================================================
# Demonstration 1: Complete 9-Step Lifecycle (Golden Path)
# ==============================================================================

def test_demonstration_complete_9_step_lifecycle(
    client: TestClient,
    db_session: Session,
    acceptance_users: Dict[str, Any],
    deterministic_scenario: Dict[str, Any],
):
    """
    Demonstrates the complete end-to-end workflow:
    1. Load deterministic seeded scenario.
    2. Submit scenario to backend (POST /scenarios/{id}/plans).
    3. Backend takes inventory snapshot and calls decision service.
    4. Decision service proposes candidate allocation.
    5. Authoritative backend validates against inventory & business rules.
    6. Approver reviews and authorizes the plan (POST /plans/{id}/approve).
    7. Approver commits plan transactionally (POST /plans/{id}/commit).
    8. Retrieve final plan and full audit trail (GET /plans/{id}).
    9. Verify physical inventory quantities and version increments.
    """
    scenario = deterministic_scenario["scenario"]
    wh = deterministic_scenario["warehouse"]
    batch = deterministic_scenario["batch"]
    headers = acceptance_users["headers"]

    # 1. & 2. Submit scenario to backend
    submit_resp = client.post(
        f"/scenarios/{scenario.id}/plans",
        headers=headers["planner"],
    )
    assert submit_resp.status_code == 201, submit_resp.text
    plan_data = submit_resp.json()
    plan_id = plan_data["id"]
    plan_code = plan_data["plan_code"]

    # 3. & 4. Candidate generated and recorded with decision-service metadata
    assert plan_data["status"] == "VALIDATED"
    assert plan_data["input_inventory_version"] == 10
    assert "model_version" in plan_data["decision_service_metadata"]
    assert len(plan_data["candidate_allocations"]) > 0

    # 5. Authoritative validation passed
    assert plan_data["is_validated"] is True
    assert len(plan_data["validation_results"]["violations"]) == 0

    # 6. Separation of duties check:
    # Planner (creator) cannot approve their own plan (HTTP 403)
    self_appr = client.post(f"/plans/{plan_id}/approve", headers=headers["planner"])
    assert self_appr.status_code == 403

    # Authorized Approver reviews and approves the plan
    appr_resp = client.post(f"/plans/{plan_id}/approve", headers=headers["approver"])
    assert appr_resp.status_code == 200
    assert appr_resp.json()["status"] == "APPROVED"
    assert appr_resp.json()["approved_by_user_id"] == acceptance_users["approver"].id

    # 7. Approver commits the plan transactionally
    commit_resp = client.post(
        f"/plans/{plan_id}/commit",
        json={"idempotency_key": "IDEM-GOLDEN-PATH-001", "commit_reason": "Authoritative Q4 distribution release"},
        headers=headers["approver"],
    )
    assert commit_resp.status_code == 200
    commit_data = commit_resp.json()
    assert commit_data["status"] == "COMMITTED"
    assert commit_data["committed_movements_count"] > 0

    # 8. Retrieve final plan and inspect immutable audit trail
    audit_resp = client.get(f"/plans/{plan_id}", headers=headers["viewer"])
    assert audit_resp.status_code == 200
    final_plan = audit_resp.json()

    assert final_plan["status"] == "COMMITTED"
    assert final_plan["approved_by_user_id"] == acceptance_users["approver"].id
    assert final_plan["committed_by_user_id"] == acceptance_users["approver"].id
    assert final_plan["approved_at"] is not None
    assert final_plan["committed_at"] is not None

    # Verify audit event chain:
    event_types = [ev["event_type"] for ev in final_plan["events"]]
    assert "SCENARIO_SNAPSHOT_TAKEN" in event_types
    assert "CANDIDATE_GENERATED" in event_types
    assert "VALIDATION_PASSED" in event_types
    assert "PLAN_APPROVED" in event_types
    assert "PLAN_COMMITTED" in event_types

    # 9. Verify that inventory quantities and versions updated authoritatively
    db_session.refresh(batch)
    db_session.refresh(wh)
    allocated_qty = final_plan["candidate_allocations"][0]["quantity"]
    assert batch.committed_quantity == allocated_qty
    assert batch.available_quantity == 1000 - allocated_qty
    assert batch.reserved_quantity == 0
    assert wh.current_inventory_version == 11  # Incremented from 10 to 11


# ==============================================================================
# Failure Scenario 1: Decision Service Unavailable
# ==============================================================================

def test_failure_decision_service_unavailable(
    client: TestClient,
    db_session: Session,
    acceptance_users: Dict[str, Any],
    deterministic_scenario: Dict[str, Any],
):
    """
    If the decision service is unreachable or times out, the backend fails safely:
    - Returns HTTP 504 (Timeout) or 502 (Bad Gateway).
    - Plan record is created and marked FAILED.
    - No candidate is committed or approved.
    """
    scenario = deterministic_scenario["scenario"]
    headers = acceptance_users["headers"]

    def timeout_mock(payload):
        raise httpx.TimeoutException("Decision service connection timed out after 30s")

    DecisionServiceClient.set_mock_handler(timeout_mock)
    try:
        resp = client.post(f"/scenarios/{scenario.id}/plans", headers=headers["planner"])
        assert resp.status_code == 504
        assert "timed out" in resp.json()["message"].lower()

        # Database audit verification
        failed_plan = db_session.query(DistributionPlan).filter_by(scenario_id=scenario.id).first()
        assert failed_plan is not None
        assert failed_plan.status == PlanStatus.FAILED

        # Rejection of approval on a failed plan
        appr_resp = client.post(f"/plans/{failed_plan.id}/approve", headers=headers["approver"])
        assert appr_resp.status_code == 422
    finally:
        DecisionServiceClient.set_mock_handler(None)


# ==============================================================================
# Failure Scenario 2: Candidate Allocation Exceeds Inventory
# ==============================================================================

def test_failure_candidate_allocation_exceeds_inventory(
    client: TestClient,
    db_session: Session,
    acceptance_users: Dict[str, Any],
    deterministic_scenario: Dict[str, Any],
):
    """
    If the candidate plan proposes allocating more units than available on hand:
    - Backend authoritative validation rejects candidate (status: REJECTED).
    - Validation error details report the specific SKU, requested, and available quantities.
    - Cannot transition to APPROVED or COMMITTED.
    """
    scenario = deterministic_scenario["scenario"]
    wh = deterministic_scenario["warehouse"]
    headers = acceptance_users["headers"]

    def excessive_mock(payload):
        return {
            "candidate_id": "CAND-EXCESSIVE-001",
            "scenario_code": scenario.scenario_code,
            "model_version": "v1.0",
            "confidence_score": 0.85,
            "proposed_movements": [
                {
                    "sku": "SKU-FLOW-5G-ROUTER",
                    "from_node_code": wh.code,
                    "to_node_code": "STORE-PUNE-01",
                    "quantity": 50000,  # Far exceeds 1000 on hand
                }
            ],
        }

    DecisionServiceClient.set_mock_handler(excessive_mock)
    try:
        resp = client.post(f"/scenarios/{scenario.id}/plans", headers=headers["planner"])
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "REJECTED"
        assert data["is_validated"] is False
        assert any(
            v["rule_name"] == "INSUFFICIENT_ON_HAND_STOCK"
            or "available unexpired units" in v["message"].lower()
            for v in data["validation_results"]["violations"]
        )

        # Attempt to approve rejected plan must be rejected (HTTP 422)
        appr_resp = client.post(f"/plans/{data['id']}/approve", headers=headers["approver"])
        assert appr_resp.status_code == 422
    finally:
        DecisionServiceClient.set_mock_handler(None)


# ==============================================================================
# Failure Scenario 3: Expired Batch Allocation
# ==============================================================================

def test_failure_expired_batch_allocation(
    client: TestClient,
    db_session: Session,
    acceptance_users: Dict[str, Any],
    deterministic_scenario: Dict[str, Any],
):
    """
    If a batch expires before plan generation or commitment, the authoritative backend
    rejects the plan to prevent distributing expired products.
    """
    wh = deterministic_scenario["warehouse"]
    prod = deterministic_scenario["product"]
    headers = acceptance_users["headers"]

    # Create an expired batch
    expired_batch = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-EXPIRED-99",
        on_hand_quantity=200,
        reserved_quantity=0,
        committed_quantity=0,
        expiry_date=date.today() - timedelta(days=5),
        version=1,
    )
    db_session.add(expired_batch)
    db_session.commit()

    # Plan explicitly targeting expired batch
    plan = DistributionPlan(
        plan_code="PLAN-EXPIRED-TEST-01",
        target_warehouse_id=wh.id,
        input_inventory_version=wh.current_inventory_version,
        status=PlanStatus.GENERATED,
        candidate_allocations=[{"batch_id": expired_batch.id, "quantity": 10}],
        created_by_user_id=acceptance_users["planner"].id,
    )
    db_session.add(plan)
    db_session.commit()

    # Authoritative validation detects expired batch
    val_resp = client.post(f"/plans/{plan.id}/validate", headers=headers["planner"])
    assert val_resp.status_code == 200
    val_data = val_resp.json()
    assert val_data["is_valid"] is False
    assert any("expired" in v["message"].lower() for v in val_data["violations"])

    db_session.refresh(plan)
    assert plan.status == PlanStatus.REJECTED


# ==============================================================================
# Failure Scenario 4: Concurrent Plans Competing for Limited Stock
# ==============================================================================

def test_failure_concurrent_plans_competing_for_stock(
    client: TestClient,
    db_session: Session,
    acceptance_users: Dict[str, Any],
    deterministic_scenario: Dict[str, Any],
):
    """
    When two plans are prepared against initial stock (e.g., 1000 units):
    - Plan A requests 700 units.
    - Plan B requests 600 units (700 + 600 = 1300 > 1000).
    - Plan A commits first: succeeds.
    - Plan B attempts commit: rejected authoritatively due to inventory version bump and insufficient stock.
    - Authoritative invariant: Stock never goes negative!
    """
    wh = deterministic_scenario["warehouse"]
    prod = deterministic_scenario["product"]
    batch = deterministic_scenario["batch"]
    headers = acceptance_users["headers"]

    v_initial = wh.current_inventory_version

    plan_a = DistributionPlan(
        plan_code="PLAN-CONCURRENT-A",
        target_warehouse_id=wh.id,
        input_inventory_version=v_initial,
        status=PlanStatus.APPROVED,
        candidate_allocations=[{"sku": prod.sku, "quantity": 700}],
        created_by_user_id=acceptance_users["planner"].id,
        approved_by_user_id=acceptance_users["approver"].id,
    )
    plan_b = DistributionPlan(
        plan_code="PLAN-CONCURRENT-B",
        target_warehouse_id=wh.id,
        input_inventory_version=v_initial,
        status=PlanStatus.APPROVED,
        candidate_allocations=[{"sku": prod.sku, "quantity": 600}],
        created_by_user_id=acceptance_users["planner"].id,
        approved_by_user_id=acceptance_users["approver"].id,
    )
    db_session.add_all([plan_a, plan_b])
    db_session.commit()

    # Plan A commits first -> Succeeds
    commit_a = client.post(f"/plans/{plan_a.id}/commit", headers=headers["approver"])
    assert commit_a.status_code == 200
    assert commit_a.json()["status"] == "COMMITTED"

    db_session.refresh(wh)
    assert wh.current_inventory_version == v_initial + 1

    # Plan B attempts commit -> Fails due to stale version mismatch!
    commit_b = client.post(f"/plans/{plan_b.id}/commit", headers=headers["approver"])
    assert commit_b.status_code == 409
    assert "version mismatch" in commit_b.json()["message"].lower()

    # Invariant: Available inventory cannot become negative
    db_session.refresh(batch)
    assert batch.committed_quantity == 700
    assert batch.available_quantity == 300
    assert batch.available_quantity >= 0


# ==============================================================================
# Failure Scenario 5: Inventory Changes After Plan Generation (Stale Version)
# ==============================================================================

def test_failure_inventory_changes_after_plan_generation(
    client: TestClient,
    db_session: Session,
    acceptance_users: Dict[str, Any],
    deterministic_scenario: Dict[str, Any],
):
    """
    If background inventory changes occur after a candidate plan was generated:
    - The warehouse version increments from V to V+1.
    - When the plan is submitted for commitment, the backend detects version mismatch.
    - Stale plan is rejected and recorded.
    """
    wh = deterministic_scenario["warehouse"]
    prod = deterministic_scenario["product"]
    headers = acceptance_users["headers"]

    v_start = wh.current_inventory_version

    # Plan created based on v_start
    plan = DistributionPlan(
        plan_code="PLAN-STALE-VERSION-01",
        target_warehouse_id=wh.id,
        input_inventory_version=v_start,
        status=PlanStatus.APPROVED,
        candidate_allocations=[{"sku": prod.sku, "quantity": 50}],
        created_by_user_id=acceptance_users["planner"].id,
        approved_by_user_id=acceptance_users["approver"].id,
    )
    db_session.add(plan)
    db_session.commit()

    # External operational event bumps warehouse version
    wh.current_inventory_version += 1
    db_session.commit()

    # Commit attempt is rejected
    commit_resp = client.post(f"/plans/{plan.id}/commit", headers=headers["approver"])
    assert commit_resp.status_code == 409
    err = commit_resp.json()
    assert "version mismatch" in err["message"].lower()
    assert err["details"]["expected_version"] == v_start
    assert err["details"]["current_version"] == v_start + 1


# ==============================================================================
# Failure Scenario 6: Unauthorized Approval or Commitment
# ==============================================================================

def test_failure_unauthorized_approval_and_commitment(
    client: TestClient,
    db_session: Session,
    acceptance_users: Dict[str, Any],
    deterministic_scenario: Dict[str, Any],
):
    """
    Tests security boundaries:
    1. Unauthenticated requests are rejected (HTTP 401).
    2. Read-only viewer role cannot approve or commit (HTTP 403).
    3. Separation of duties: creator cannot approve their own plan (HTTP 403).
    """
    wh = deterministic_scenario["warehouse"]
    prod = deterministic_scenario["product"]
    headers = acceptance_users["headers"]

    plan = DistributionPlan(
        plan_code="PLAN-AUTH-SECURITY-01",
        target_warehouse_id=wh.id,
        input_inventory_version=wh.current_inventory_version,
        status=PlanStatus.VALIDATED,
        is_validated=True,
        candidate_allocations=[{"sku": prod.sku, "quantity": 20}],
        created_by_user_id=acceptance_users["planner"].id,
    )
    db_session.add(plan)
    db_session.commit()

    # 1. Unauthenticated request -> 401
    unauth_resp = client.post(f"/plans/{plan.id}/approve")
    assert unauth_resp.status_code == 401

    # 2. Viewer role attempting approval -> 403 Forbidden (RBAC)
    viewer_resp = client.post(f"/plans/{plan.id}/approve", headers=headers["viewer"])
    assert viewer_resp.status_code == 403
    assert "permission denied" in (viewer_resp.json().get("detail") or "").lower()

    # 3. Planner role attempting approval -> 403 Forbidden (RBAC: requires APPROVER/ADMIN)
    planner_resp = client.post(f"/plans/{plan.id}/approve", headers=headers["planner"])
    assert planner_resp.status_code == 403
    assert "permission denied" in (planner_resp.json().get("detail") or "").lower()

    # 4. Separation of Duties: Approver who created a plan attempts to approve their OWN plan -> 403
    plan_self = DistributionPlan(
        plan_code="PLAN-SELF-APPROVAL-TEST",
        target_warehouse_id=wh.id,
        input_inventory_version=wh.current_inventory_version,
        status=PlanStatus.VALIDATED,
        is_validated=True,
        candidate_allocations=[{"sku": prod.sku, "quantity": 10}],
        created_by_user_id=acceptance_users["approver"].id,  # Created by the approver!
    )
    db_session.add(plan_self)
    db_session.commit()

    self_appr_resp = client.post(f"/plans/{plan_self.id}/approve", headers=headers["approver"])
    assert self_appr_resp.status_code == 403
    err_body = self_appr_resp.json()
    err_msg = err_body.get("detail") or err_body.get("message", "")
    assert "separation of duties" in err_msg.lower()


# ==============================================================================
# Failure Scenario 7: Duplicate Commitment Request & Idempotency
# ==============================================================================

def test_failure_duplicate_commitment_requests(
    client: TestClient,
    db_session: Session,
    acceptance_users: Dict[str, Any],
    deterministic_scenario: Dict[str, Any],
):
    """
    Ensures a valid plan can be committed exactly once:
    - First commit succeeds.
    - Duplicate request with identical idempotency key returns safe idempotent response.
    - Subsequent commit without or with different idempotency key returns HTTP 409 Conflict.
    - Physical stock is never deducted twice.
    """
    wh = deterministic_scenario["warehouse"]
    prod = deterministic_scenario["product"]
    batch = deterministic_scenario["batch"]
    headers = acceptance_users["headers"]

    plan = DistributionPlan(
        plan_code="PLAN-IDEMPOTENT-001",
        target_warehouse_id=wh.id,
        input_inventory_version=wh.current_inventory_version,
        status=PlanStatus.APPROVED,
        candidate_allocations=[{"sku": prod.sku, "quantity": 100}],
        created_by_user_id=acceptance_users["planner"].id,
        approved_by_user_id=acceptance_users["approver"].id,
    )
    db_session.add(plan)
    db_session.commit()

    key = "IDEM-KEY-UNIQUE-777"

    # First commitment -> 200 OK
    resp1 = client.post(
        f"/plans/{plan.id}/commit",
        json={"idempotency_key": key},
        headers=headers["approver"],
    )
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "COMMITTED"

    db_session.refresh(batch)
    db_session.refresh(wh)
    assert batch.committed_quantity == 100
    version_after_first = wh.current_inventory_version

    # Duplicate commitment with same key -> 200 OK (Idempotent replay)
    resp2 = client.post(
        f"/plans/{plan.id}/commit",
        json={"idempotency_key": key},
        headers=headers["approver"],
    )
    assert resp2.status_code == 200
    assert "idempotent" in resp2.json()["message"].lower()

    # Invariant: Stock was NOT deducted twice
    db_session.refresh(batch)
    db_session.refresh(wh)
    assert batch.committed_quantity == 100
    assert wh.current_inventory_version == version_after_first

    # Third commitment with different key -> 409 Conflict
    resp3 = client.post(
        f"/plans/{plan.id}/commit",
        json={"idempotency_key": "DIFFERENT-KEY-999"},
        headers=headers["approver"],
    )
    assert resp3.status_code == 409
    assert "already been committed" in resp3.json()["message"].lower()


# ==============================================================================
# Failure Scenario 8: Database Transaction Failure (Rollback Safety)
# ==============================================================================

def test_failure_database_transaction_failure_rolls_back(
    client: TestClient,
    db_session: Session,
    acceptance_users: Dict[str, Any],
    deterministic_scenario: Dict[str, Any],
):
    """
    Simulates a database failure during commitment:
    - Confirms that any physical mutations to inventory batches are completely rolled back.
    - Warehouse inventory version does not increment.
    - Plan status remains uncommitted.
    """
    wh = deterministic_scenario["warehouse"]
    prod = deterministic_scenario["product"]
    batch = deterministic_scenario["batch"]

    plan = DistributionPlan(
        plan_code="PLAN-DB-FAIL-SIM",
        target_warehouse_id=wh.id,
        input_inventory_version=wh.current_inventory_version,
        status=PlanStatus.APPROVED,
        candidate_allocations=[{"sku": prod.sku, "quantity": 150}],
        created_by_user_id=acceptance_users["planner"].id,
        approved_by_user_id=acceptance_users["approver"].id,
    )
    db_session.add(plan)
    db_session.commit()

    initial_on_hand = batch.on_hand_quantity
    initial_committed = batch.committed_quantity
    initial_version = wh.current_inventory_version

    # Mock db.commit during InventoryService.validate_and_commit_plan to raise OperationalError
    def broken_commit():
        raise OperationalError("Simulated database connection loss", {}, Exception("Connection reset"))

    with patch.object(db_session, "commit", side_effect=broken_commit):
        with pytest.raises(OperationalError):
            InventoryService.validate_and_commit_plan(
                db=db_session,
                plan_code=plan.plan_code,
                committed_by_user_id=acceptance_users["approver"].id,
            )

    # Invariant: Rollback occurred cleanly
    db_session.rollback()
    refreshed_batch = db_session.query(InventoryBatch).filter_by(id=batch.id).first()
    refreshed_wh = db_session.query(Warehouse).filter_by(id=wh.id).first()
    refreshed_plan = db_session.query(DistributionPlan).filter_by(id=plan.id).first()

    assert refreshed_batch is not None
    assert refreshed_wh is not None
    assert refreshed_plan is not None
    assert refreshed_batch.committed_quantity == initial_committed
    assert refreshed_batch.on_hand_quantity == initial_on_hand
    assert refreshed_wh.current_inventory_version == initial_version
    assert refreshed_plan.status == PlanStatus.APPROVED  # Not committed!
    # No allocations persisted
    persisted_allocations = db_session.query(PlanAllocation).filter_by(plan_id=plan.id).all()
    assert len(persisted_allocations) == 0
