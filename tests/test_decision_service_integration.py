from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict
import httpx
import pytest
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.core.exceptions import (
    DecisionServiceTimeoutException,
    MalformedDecisionCandidateException,
    InvalidStateTransitionException,
    AuthoritativeRuleViolationException,
)
from app.core.security import hash_password, create_access_token
from app.models.enums import UserRole, PlanStatus, ValidationVerdict, ScenarioType
from app.models.facility import Warehouse, Store
from app.models.product import Product
from app.models.inventory import InventoryBatch
from app.models.plan import DistributionPlan
from app.models.scenario import SeededScenario
from app.models.user import User
from app.services.decision_service_client import DecisionServiceClient
from app.services.plan_lifecycle_service import PlanLifecycleService


# ==============================================================================
# Test Fixtures & Setup Helpers
# ==============================================================================

@pytest.fixture
def auth_users(db_session: Session):
    """Creates authenticated planner, approver, and viewer users with JWT tokens."""
    planner = User(
        email="lead_planner@flowgrid.io",
        hashed_password=hash_password("PlannerPass123!"),
        full_name="Lead Planner",
        role=UserRole.PLANNER,
        is_active=True,
    )
    approver = User(
        email="chief_approver@flowgrid.io",
        hashed_password=hash_password("ApproverPass123!"),
        full_name="Chief Approver",
        role=UserRole.APPROVER,
        is_active=True,
    )
    viewer = User(
        email="viewer@flowgrid.io",
        hashed_password=hash_password("ViewerPass123!"),
        full_name="Auditor Viewer",
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
        "tokens": tokens,
        "headers": {
            "planner": {"Authorization": f"Bearer {tokens['planner']}"},
            "approver": {"Authorization": f"Bearer {tokens['approver']}"},
            "viewer": {"Authorization": f"Bearer {tokens['viewer']}"},
        },
    }


@pytest.fixture
def seeded_scenario(db_session: Session):
    """Creates a seeded distribution scenario with deterministic network topology."""
    scenario = SeededScenario(
        scenario_code="SCENARIO-DIWALI-PEAK-2026",
        name="Diwali Festival Surge",
        scenario_type=ScenarioType.DEMAND_SURGE,
        description="Demand shock for consumer electronics across North region.",
        parameters={"affected_skus": ["SKU-SMARTPHONE-PRO"], "surge_percentage": 25},
        is_active=True,
    )
    wh = Warehouse(
        code="WH-NORTH-HUB",
        name="North Regional DC",
        region="North",
        capacity_limit=200000,
        current_inventory_version=1,
    )
    store = Store(
        code="STORE-DELHI-01",
        name="Delhi Flagship Retail",
        region="North",
        priority_tier=1,
    )
    prod = Product(
        sku="SKU-SMARTPHONE-PRO",
        name="Pro Smartphone 2026",
        category="Electronics",
        shelf_life_days=730,
    )
    db_session.add_all([scenario, wh, store, prod])
    db_session.commit()

    batch = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-PHONE-PRO-01",
        on_hand_quantity=500,
        reserved_quantity=0,
        committed_quantity=0,
        expiry_date=date.today() + timedelta(days=365),
        version=1,
    )
    db_session.add(batch)
    db_session.commit()

    return {
        "scenario": scenario,
        "warehouse": wh,
        "store": store,
        "product": prod,
        "batch": batch,
    }


# ==============================================================================
# 1. Complete End-to-End Workflow:
#    Scenario -> Snapshot -> Decision Service -> Validate -> Approve -> Commit
# ==============================================================================

def test_complete_decision_service_and_plan_lifecycle(
    client: TestClient,
    db_session: Session,
    auth_users: Dict[str, Any],
    seeded_scenario: Dict[str, Any],
):
    scenario = seeded_scenario["scenario"]
    batch = seeded_scenario["batch"]
    wh = seeded_scenario["warehouse"]
    headers = auth_users["headers"]

    # 1. Planner submits request to generate plan from scenario
    # POST /api/v1/scenarios/{scenario_id}/plans
    resp = client.post(
        f"/api/v1/scenarios/{scenario.id}/plans",
        headers=headers["planner"],
    )
    assert resp.status_code == 201
    plan_data = resp.json()
    plan_id = plan_data["id"]
    plan_code = plan_data["plan_code"]

    assert plan_data["scenario_id"] == scenario.id
    assert plan_data["target_warehouse_id"] == wh.id
    assert plan_data["input_inventory_version"] == 1
    # Auto-validated candidate:
    assert plan_data["status"] == "VALIDATED"
    assert plan_data["is_validated"] is True
    assert len(plan_data["candidate_allocations"]) > 0
    assert "model_version" in plan_data["decision_service_metadata"]
    assert len(plan_data["events"]) >= 3  # SNAPSHOT_TAKEN, CANDIDATE_GENERATED, VALIDATION_PASSED

    # 2. Viewer inspects plan details
    # GET /api/v1/plans/{plan_id}
    get_resp = client.get(f"/api/v1/plans/{plan_id}", headers=headers["viewer"])
    assert get_resp.status_code == 200
    detail = get_resp.json()
    assert detail["plan_code"] == plan_code
    assert detail["status"] == "VALIDATED"

    # Also test retrieval by plan_code
    code_resp = client.get(f"/api/v1/plans/{plan_code}", headers=headers["viewer"])
    assert code_resp.status_code == 200
    assert code_resp.json()["id"] == plan_id

    # 3. Separation of duties check:
    # Creator (Planner) cannot approve their own plan (403 Forbidden)
    self_approve = client.post(f"/api/v1/plans/{plan_id}/approve", headers=headers["planner"])
    assert self_approve.status_code == 403

    # 4. Independent Approver reviews and authorizes the plan
    # POST /api/v1/plans/{plan_id}/approve
    approve_resp = client.post(f"/api/v1/plans/{plan_id}/approve", headers=headers["approver"])
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "APPROVED"
    assert approve_resp.json()["approved_by_user_id"] == auth_users["approver"].id

    # Verify status changed to APPROVED
    detail_after_approve = client.get(f"/api/v1/plans/{plan_id}", headers=headers["viewer"]).json()
    assert detail_after_approve["status"] == "APPROVED"
    assert detail_after_approve["approved_by_user_id"] == auth_users["approver"].id

    # 5. Creator cannot commit the plan
    self_commit = client.post(
        f"/api/v1/plans/{plan_id}/commit",
        json={"idempotency_key": "IDEM-COMMIT-E2E-01"},
        headers=headers["planner"],
    )
    assert self_commit.status_code == 403

    # 6. Approver commits the plan transactionally
    # POST /api/v1/plans/{plan_id}/commit
    commit_resp = client.post(
        f"/api/v1/plans/{plan_id}/commit",
        json={"idempotency_key": "IDEM-COMMIT-E2E-01", "commit_reason": "Diwali operational release"},
        headers=headers["approver"],
    )
    assert commit_resp.status_code == 200
    commit_data = commit_resp.json()
    assert commit_data["status"] == "COMMITTED"
    assert commit_data["committed_movements_count"] > 0

    # Invariant check: Physical inventory updated in authoritative database
    db_session.refresh(batch)
    db_session.refresh(wh)
    assert batch.committed_quantity > 0
    assert batch.available_quantity == 500 - batch.committed_quantity
    assert wh.current_inventory_version == 2


# ==============================================================================
# 2. Decision Service Failures & Safe Handling:
#    Timeouts, Malformed Data, and Unreachable Errors
# ==============================================================================

def test_decision_service_timeout_fails_safely(
    client: TestClient,
    db_session: Session,
    auth_users: Dict[str, Any],
    seeded_scenario: Dict[str, Any],
):
    scenario = seeded_scenario["scenario"]
    headers = auth_users["headers"]

    # Mock decision service timeout
    def timeout_mock(payload):
        raise httpx.TimeoutException("Decision service calculation timed out.")

    DecisionServiceClient.set_mock_handler(timeout_mock)
    try:
        resp = client.post(
            f"/api/v1/scenarios/{scenario.id}/plans",
            headers=headers["planner"],
        )
        assert resp.status_code == 504
        data = resp.json()
        assert "timed out" in data["message"].lower()

        # Invariant: Failed candidate is stamped FAILED in DB and never committed
        failed_plan = db_session.query(DistributionPlan).filter_by(scenario_id=scenario.id).first()
        assert failed_plan is not None
        assert failed_plan.status == PlanStatus.FAILED
    finally:
        DecisionServiceClient.set_mock_handler(None)


def test_decision_service_malformed_response_fails_safely(
    client: TestClient,
    db_session: Session,
    auth_users: Dict[str, Any],
    seeded_scenario: Dict[str, Any],
):
    scenario = seeded_scenario["scenario"]
    headers = auth_users["headers"]

    # Mock decision service returning malformed JSON
    def malformed_mock(payload):
        return {"garbage": True, "not_a_valid_proposal": 123}

    DecisionServiceClient.set_mock_handler(malformed_mock)
    try:
        resp = client.post(
            f"/api/v1/scenarios/{scenario.id}/plans",
            headers=headers["planner"],
        )
        assert resp.status_code == 502
        assert "malformed" in resp.json()["message"].lower()

        # Invariant: Plan is stamped FAILED, never committed
        failed_plan = db_session.query(DistributionPlan).filter_by(scenario_id=scenario.id).first()
        assert failed_plan is not None
        assert failed_plan.status == PlanStatus.FAILED
    finally:
        DecisionServiceClient.set_mock_handler(None)


# ==============================================================================
# 3. Infeasible Candidate Plan Handling (Authoritative Rule Checks Reject)
# ==============================================================================

def test_infeasible_candidate_rejected_by_authoritative_validation(
    client: TestClient,
    db_session: Session,
    auth_users: Dict[str, Any],
    seeded_scenario: Dict[str, Any],
):
    scenario = seeded_scenario["scenario"]
    wh = seeded_scenario["warehouse"]
    headers = auth_users["headers"]

    # Decision service returns an infeasible candidate requesting 1,000,000 units (far exceeding stock of 500)
    def infeasible_mock(payload):
        return {
            "candidate_id": "CAND-INFEASIBLE-01",
            "scenario_code": scenario.scenario_code,
            "model_version": "flowgrid-opt-v1.0",
            "confidence_score": 0.40,
            "proposed_movements": [
                {
                    "sku": "SKU-SMARTPHONE-PRO",
                    "from_node_code": wh.code,
                    "to_node_code": "STORE-DELHI-01",
                    "quantity": 1000000,  # Infeasible!
                    "priority": "HIGH",
                    "estimated_transit_hours": 24,
                }
            ],
        }

    DecisionServiceClient.set_mock_handler(infeasible_mock)
    try:
        resp = client.post(
            f"/api/v1/scenarios/{scenario.id}/plans",
            headers=headers["planner"],
        )
        assert resp.status_code == 201
        plan_data = resp.json()
        plan_id = plan_data["id"]

        # Invariant: Backend authoritative validation rejected the candidate
        assert plan_data["status"] == "REJECTED"
        assert plan_data["is_validated"] is False
        assert len(plan_data["validation_results"]["violations"]) > 0

        # Attempting to approve an infeasible/rejected plan must be rejected!
        approve_resp = client.post(f"/api/v1/plans/{plan_id}/approve", headers=headers["approver"])
        assert approve_resp.status_code == 422
        assert "Invalid plan state transition" in approve_resp.json()["message"]

        # Attempting to commit an infeasible/rejected plan must be rejected!
        commit_resp = client.post(f"/api/v1/plans/{plan_id}/commit", headers=headers["approver"])
        assert commit_resp.status_code == 422
    finally:
        DecisionServiceClient.set_mock_handler(None)


# ==============================================================================
# 4. State Machine & Invalid State Transitions
# ==============================================================================

def test_plan_lifecycle_invalid_transitions_rejected(
    client: TestClient,
    db_session: Session,
    auth_users: Dict[str, Any],
    seeded_scenario: Dict[str, Any],
):
    scenario = seeded_scenario["scenario"]
    headers = auth_users["headers"]

    # Create a plan in DRAFT status manually
    plan = DistributionPlan(
        plan_code="PLAN-TRANSITION-TEST",
        scenario_id=scenario.id,
        target_warehouse_id=seeded_scenario["warehouse"].id,
        input_inventory_version=1,
        status=PlanStatus.DRAFT,
        created_by_user_id=auth_users["planner"].id,
    )
    db_session.add(plan)
    db_session.commit()

    # Rule: Cannot approve a DRAFT plan directly without generation and validation
    resp = client.post(f"/api/v1/plans/{plan.id}/approve", headers=headers["approver"])
    assert resp.status_code == 422
    assert "Invalid plan state transition" in resp.json()["message"]

    # Rule: Cannot commit a DRAFT plan
    resp = client.post(f"/api/v1/plans/{plan.id}/commit", headers=headers["approver"])
    assert resp.status_code == 422

    # Set plan to VALIDATED
    plan.status = PlanStatus.VALIDATED
    plan.is_validated = True
    db_session.commit()

    # Cancel the plan
    # POST /api/v1/plans/{plan_id}/cancel
    cancel_resp = client.post(
        f"/api/v1/plans/{plan.id}/cancel",
        json={"reason": "Scenario cancelled by user"},
        headers=headers["planner"],
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "CANCELLED"

    # Rule: Cannot approve or commit a CANCELLED plan
    approve_cancelled = client.post(f"/api/v1/plans/{plan.id}/approve", headers=headers["approver"])
    assert approve_cancelled.status_code == 422

    commit_cancelled = client.post(f"/api/v1/plans/{plan.id}/commit", headers=headers["approver"])
    assert commit_cancelled.status_code == 422


def test_direct_root_routes_alias(
    client: TestClient,
    seeded_scenario: Dict[str, Any],
    auth_users: Dict[str, Any],
):
    """Verifies that routes without the /api/v1 prefix resolve identically."""
    scenario = seeded_scenario["scenario"]
    headers = auth_users["headers"]

    # Direct POST /scenarios/{id}/plans
    resp = client.post(f"/scenarios/{scenario.id}/plans", headers=headers["planner"])
    assert resp.status_code == 201
    plan_id = resp.json()["id"]

    # Direct GET /plans/{id}
    get_resp = client.get(f"/plans/{plan_id}", headers=headers["viewer"])
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == plan_id

    # Direct POST /plans/{id}/approve
    appr_resp = client.post(f"/plans/{plan_id}/approve", headers=headers["approver"])
    assert appr_resp.status_code == 200
    assert appr_resp.json()["status"] == "APPROVED"

    # Direct POST /plans/{id}/commit
    commit_resp = client.post(f"/plans/{plan_id}/commit", headers=headers["approver"])
    assert commit_resp.status_code == 200
    assert commit_resp.json()["status"] == "COMMITTED"
