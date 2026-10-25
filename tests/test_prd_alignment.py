"""
Tests for PRD-aligned endpoints in FastAPI backend:
1. Scenario Reset (POST /scenarios/{id}/reset)
2. Allocation Plans PRD alias (POST & GET /allocation-plans)
3. Sales Observations Ingestion (POST /sales-observations & POST /sales)
4. Demand Forecasting & Baseline WAPE Evaluation (POST & GET /forecasts)
5. Three-Way Policy Benchmark Comparison (GET /comparison/{id} & GET /plans/{id}/comparison)
6. Stale Line Re-planning (POST /plans/{id}/replan)
"""

from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import pytest

from app.core.security import hash_password, create_access_token
from app.models.enums import UserRole, ScenarioType
from app.models.facility import Warehouse, Store
from app.models.product import Product
from app.models.inventory import InventoryBatch
from app.models.scenario import SeededScenario
from app.models.user import User


@pytest.fixture
def prd_auth_client(db_session: Session, client: TestClient):
    planner = User(
        email="prd_planner@flowgrid.io",
        hashed_password=hash_password("Pass123!"),
        full_name="PRD Planner",
        role=UserRole.PLANNER,
        is_active=True,
    )
    approver = User(
        email="prd_approver@flowgrid.io",
        hashed_password=hash_password("Pass123!"),
        full_name="PRD Approver",
        role=UserRole.APPROVER,
        is_active=True,
    )
    viewer = User(
        email="prd_viewer@flowgrid.io",
        hashed_password=hash_password("Pass123!"),
        full_name="PRD Viewer",
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
    headers = {
        "planner": {"Authorization": f"Bearer {tokens['planner']}"},
        "approver": {"Authorization": f"Bearer {tokens['approver']}"},
        "viewer": {"Authorization": f"Bearer {tokens['viewer']}"},
    }

    # Setup baseline warehouse, store, product, batch
    wh = Warehouse(code="WH-PRD-01", name="PRD Warehouse", region="West", capacity_limit=50000, current_inventory_version=3)
    st = Store(code="STORE-PRD-01", name="PRD Retail", region="West", priority_tier=1)
    prod = Product(sku="SKU-PRD-SNACK", name="Snack Pack 100g", category="Food", shelf_life_days=180)
    db_session.add_all([wh, st, prod])
    db_session.commit()

    batch = InventoryBatch(
        warehouse_id=wh.id,
        product_id=prod.id,
        batch_number="BATCH-SNACK-001",
        on_hand_quantity=500,
        reserved_quantity=50,
        committed_quantity=100,
        expiry_date=date(2027, 1, 1),
        version=2,
    )
    scenario = SeededScenario(
        scenario_code="SCENARIO-PRD-TEST",
        name="PRD Evaluation Scenario",
        scenario_type=ScenarioType.DEMAND_SURGE,
        parameters={"target_warehouse_code": wh.code, "affected_skus": [prod.sku]},
    )
    db_session.add_all([batch, scenario])
    db_session.commit()

    return {
        "headers": headers,
        "warehouse": wh,
        "store": st,
        "product": prod,
        "batch": batch,
        "scenario": scenario,
        "client": client,
    }


def test_scenario_reset_endpoint(prd_auth_client, db_session):
    client = prd_auth_client["client"]
    headers = prd_auth_client["headers"]
    scenario = prd_auth_client["scenario"]
    wh = prd_auth_client["warehouse"]
    batch = prd_auth_client["batch"]

    # Call POST /scenarios/{id}/reset
    resp = client.post(f"/scenarios/{scenario.id}/reset", headers=headers["planner"])
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "RESET"
    assert data["scenario_code"] == scenario.scenario_code
    assert data["warehouse_version_reset"] == 1
    assert data["batches_reset_count"] >= 1

    # Invariant: warehouse version reset to 1, batch quantities cleared
    db_session.refresh(wh)
    db_session.refresh(batch)
    assert wh.current_inventory_version == 1
    assert batch.committed_quantity == 0
    assert batch.reserved_quantity == 0


def test_allocation_plans_prd_alias_endpoints(prd_auth_client):
    client = prd_auth_client["client"]
    headers = prd_auth_client["headers"]
    wh = prd_auth_client["warehouse"]
    prod = prd_auth_client["product"]

    # 1. POST /api/v1/allocation-plans
    create_resp = client.post(
        "/api/v1/allocation-plans",
        json={
            "plan_code": "PLAN-PRD-ALIAS-01",
            "target_warehouse_id": wh.id,
            "candidate_allocations": [{"sku": prod.sku, "quantity": 30}],
        },
        headers=headers["planner"],
    )
    assert create_resp.status_code == 201
    plan_id = create_resp.json()["id"]

    # 2. GET /api/v1/allocation-plans/{id}
    get_resp = client.get(f"/api/v1/allocation-plans/{plan_id}", headers=headers["viewer"])
    assert get_resp.status_code == 200
    assert get_resp.json()["plan_code"] == "PLAN-PRD-ALIAS-01"


def test_sales_observations_ingestion(prd_auth_client):
    client = prd_auth_client["client"]
    headers = prd_auth_client["headers"]
    st = prd_auth_client["store"]
    prod = prd_auth_client["product"]

    # Batch sales ingestion: POST /sales-observations
    batch_resp = client.post(
        "/sales-observations",
        json={
            "observations": [
                {
                    "store_code": st.code,
                    "sku": prod.sku,
                    "observation_date": "2026-10-20",
                    "units_sold": 45,
                    "is_stockout_day": False,
                },
                {
                    "store_code": st.code,
                    "sku": prod.sku,
                    "observation_date": "2026-10-21",
                    "units_sold": 0,
                    "is_stockout_day": True,
                },
            ]
        },
        headers=headers["planner"],
    )
    assert batch_resp.status_code == 201
    assert batch_resp.json()["recorded_count"] == 2

    # Single sale ingestion: POST /sales
    single_resp = client.post(
        "/sales",
        json={
            "store_code": st.code,
            "sku": prod.sku,
            "observation_date": "2026-10-22",
            "units_sold": 60,
            "is_stockout_day": False,
        },
        headers=headers["planner"],
    )
    assert single_resp.status_code == 201
    assert single_resp.json()["recorded_count"] == 1


def test_demand_forecast_and_baseline_evaluation(prd_auth_client):
    client = prd_auth_client["client"]
    headers = prd_auth_client["headers"]

    # POST /forecasts
    resp = client.post(
        "/forecasts",
        json={"planning_horizon_days": 7, "confidence_level": 0.90},
        headers=headers["planner"],
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "COMPLETED"
    assert "wape" in data["accuracy"]
    assert len(data["series"]) > 0

    run_id = data["forecast_run_id"]

    # GET /forecasts/{id}
    get_resp = client.get(f"/forecasts/{run_id}", headers=headers["viewer"])
    assert get_resp.status_code == 200
    assert get_resp.json()["forecast_run_id"] == run_id


def test_three_way_policy_comparison(prd_auth_client):
    client = prd_auth_client["client"]
    headers = prd_auth_client["headers"]
    scenario = prd_auth_client["scenario"]

    # Generate a plan from scenario
    plan_resp = client.post(f"/scenarios/{scenario.id}/plans", headers=headers["planner"])
    assert plan_resp.status_code == 201
    plan_id = plan_resp.json()["id"]

    # GET /plans/{plan_id}/comparison
    comp_resp = client.get(f"/plans/{plan_id}/comparison", headers=headers["viewer"])
    assert comp_resp.status_code == 200
    comp_data = comp_resp.json()
    assert "current_practice" in comp_data["metrics"]
    assert "strong_baseline" in comp_data["metrics"]
    assert "flowgrid_optimization" in comp_data["metrics"]
    assert "stockout_reduction_vs_current" in comp_data["relative_improvements"]

    # Direct route alias: GET /comparison/{plan_id}
    direct_resp = client.get(f"/comparison/{plan_id}", headers=headers["viewer"])
    assert direct_resp.status_code == 200
    assert direct_resp.json()["plan_id"] == plan_id


def test_replan_stale_plan(prd_auth_client):
    client = prd_auth_client["client"]
    headers = prd_auth_client["headers"]
    scenario = prd_auth_client["scenario"]

    # Generate initial plan
    plan_resp = client.post(f"/scenarios/{scenario.id}/plans", headers=headers["planner"])
    assert plan_resp.status_code == 201
    plan_id = plan_resp.json()["id"]

    # POST /plans/{plan_id}/replan
    replan_resp = client.post(f"/plans/{plan_id}/replan", headers=headers["planner"])
    assert replan_resp.status_code == 201
    replan_data = replan_resp.json()
    assert replan_data["id"] != plan_id
    assert replan_data["scenario_id"] == scenario.id
    assert replan_data["status"] in ("VALIDATED", "GENERATED")
