import pytest
from fastapi.testclient import TestClient
from app.models.enums import UserRole, StoreType, OrderPriority


# Headers for RBAC testing
ADMIN_HEADERS = {"X-User-Email": "admin@flowgrid.io", "X-User-Role": "ADMIN"}
OPERATOR_HEADERS = {"X-User-Email": "operator@flowgrid.io", "X-User-Role": "OPERATOR"}
PLANNER_HEADERS = {"X-User-Email": "planner@flowgrid.io", "X-User-Role": "PLANNER"}
AUDITOR_HEADERS = {"X-User-Email": "auditor@flowgrid.io", "X-User-Role": "AUDITOR"}
VIEWER_HEADERS = {"X-User-Email": "viewer@flowgrid.io", "X-User-Role": "VIEWER"}


# ==============================================================================
# Warehouse Endpoint Tests
# ==============================================================================

def test_warehouse_crud_and_permissions(client: TestClient):
    # 1. Permission failure: Auditor cannot create warehouse
    resp = client.post(
        "/api/v1/warehouses",
        json={"code": "WH-TEST-01", "name": "Test DC", "region": "North"},
        headers=AUDITOR_HEADERS,
    )
    assert resp.status_code == 403
    assert "Permission denied" in resp.json()["detail"]

    # 2. Unauthorized: Missing credentials
    resp = client.post(
        "/api/v1/warehouses",
        json={"code": "WH-TEST-01", "name": "Test DC", "region": "North"},
    )
    assert resp.status_code == 401

    # 3. Invalid input: capacity_limit <= 0
    resp = client.post(
        "/api/v1/warehouses",
        json={"code": "WH-TEST-01", "name": "Test DC", "region": "North", "capacity_limit": -5},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 422

    # 4. Success: Create warehouse
    resp = client.post(
        "/api/v1/warehouses",
        json={"code": "WH-TEST-01", "name": "Northern DC", "region": "North", "capacity_limit": 300000},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 201
    wh = resp.json()
    assert wh["code"] == "WH-TEST-01"
    assert wh["current_inventory_version"] == 1
    wh_id = wh["id"]

    # 5. Conflict: Duplicate code
    resp = client.post(
        "/api/v1/warehouses",
        json={"code": "WH-TEST-01", "name": "Duplicate DC", "region": "North"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 409

    # 6. Retrieve warehouse
    resp = client.get(f"/api/v1/warehouses/{wh_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Northern DC"

    # 7. Missing resource
    resp = client.get("/api/v1/warehouses/99999")
    assert resp.status_code == 404

    # 8. Update warehouse
    resp = client.put(
        f"/api/v1/warehouses/{wh_id}",
        json={"name": "Northern Regional Central Hub", "capacity_limit": 450000},
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Northern Regional Central Hub"
    assert resp.json()["capacity_limit"] == 450000

    # 9. List warehouses with pagination
    resp = client.get("/api/v1/warehouses?page=1&page_size=10&region=North")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert data["page"] == 1
    assert len(data["items"]) >= 1


# ==============================================================================
# Store Endpoint Tests
# ==============================================================================

def test_store_crud_and_missing_resource(client: TestClient):
    # 1. Success: Create store
    resp = client.post(
        "/api/v1/stores",
        json={
            "code": "STORE-MUM-10",
            "name": "Mumbai Bandra Retail Store",
            "region": "West",
            "store_type": "FLAGSHIP",
            "priority_tier": 1,
        },
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 201
    store_id = resp.json()["id"]

    # 2. Retrieve store
    resp = client.get(f"/api/v1/stores/{store_id}")
    assert resp.status_code == 200
    assert resp.json()["store_type"] == "FLAGSHIP"

    # 3. Missing store
    resp = client.get("/api/v1/stores/88888")
    assert resp.status_code == 404

    # 4. Update store
    resp = client.put(
        f"/api/v1/stores/{store_id}",
        json={"priority_tier": 2},
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["priority_tier"] == 2

    # 5. List stores
    resp = client.get("/api/v1/stores?region=West")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


# ==============================================================================
# Product Endpoint Tests
# ==============================================================================

def test_product_crud_and_conflict(client: TestClient):
    # 1. Success: Create product
    resp = client.post(
        "/api/v1/products",
        json={
            "sku": "SKU-MONITOR-4K",
            "name": "4K Ultra HD 27 inch Monitor",
            "category": "Displays",
            "is_perishable": False,
            "unit_cost": 349.99,
        },
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 201
    prod_id = resp.json()["id"]

    # 2. Conflict: Duplicate SKU
    resp = client.post(
        "/api/v1/products",
        json={"sku": "SKU-MONITOR-4K", "name": "Duplicate Monitor"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 409

    # 3. Retrieve product
    resp = client.get(f"/api/v1/products/{prod_id}")
    assert resp.status_code == 200
    assert resp.json()["sku"] == "SKU-MONITOR-4K"

    # 4. Update product
    resp = client.put(
        f"/api/v1/products/{prod_id}",
        json={"unit_cost": 329.99},
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["unit_cost"] == 329.99

    # 5. List products
    resp = client.get("/api/v1/products?category=Displays")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


# ==============================================================================
# Inventory Endpoint Tests
# ==============================================================================

def test_inventory_batches_distinguish_quantities_and_increment_version(client: TestClient):
    # Setup warehouse and product
    wh_resp = client.post(
        "/api/v1/warehouses",
        json={"code": "WH-INV-01", "name": "Inventory Test WH", "region": "South"},
        headers=ADMIN_HEADERS,
    )
    wh_id = wh_resp.json()["id"]
    assert wh_resp.json()["current_inventory_version"] == 1

    prod_resp = client.post(
        "/api/v1/products",
        json={"sku": "SKU-INV-TABLET", "name": "Tablet 10 inch", "is_perishable": True, "shelf_life_days": 180},
        headers=ADMIN_HEADERS,
    )
    prod_id = prod_resp.json()["id"]

    # 1. Receive batch into warehouse
    resp = client.post(
        "/api/v1/inventory/batches",
        json={
            "warehouse_id": wh_id,
            "product_id": prod_id,
            "batch_number": "BATCH-TAB-01",
            "on_hand_quantity": 400,
            "expiry_date": "2027-01-15",
        },
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 201
    batch = resp.json()
    assert batch["batch_number"] == "BATCH-TAB-01"
    # Verify quantities are clearly distinguished
    assert batch["on_hand_quantity"] == 400
    assert batch["reserved_quantity"] == 0
    assert batch["committed_quantity"] == 0
    assert batch["available_quantity"] == 400

    # Verify warehouse version was incremented
    wh_after = client.get(f"/api/v1/warehouses/{wh_id}").json()
    assert wh_after["current_inventory_version"] == 2

    # 2. Query batches
    resp = client.get(f"/api/v1/inventory/batches?warehouse_id={wh_id}&sku=SKU-INV-TABLET")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["available_quantity"] == 400

    # 3. Query inventory summary
    resp = client.get(f"/api/v1/inventory/summary?warehouse_id={wh_id}&sku=SKU-INV-TABLET")
    assert resp.status_code == 200
    summary = resp.json()
    assert len(summary) == 1
    assert summary[0]["total_on_hand"] == 400
    assert summary[0]["total_available"] == 400


# ==============================================================================
# Distribution Order Endpoint Tests
# ==============================================================================

def test_order_creation_listing_and_validation(client: TestClient):
    # Setup store and products
    store_resp = client.post(
        "/api/v1/stores",
        json={"code": "STORE-ORD-01", "name": "Order Test Store", "region": "North"},
        headers=ADMIN_HEADERS,
    )
    store_id = store_resp.json()["id"]

    p1_resp = client.post(
        "/api/v1/products",
        json={"sku": "SKU-ORD-A", "name": "Item A"},
        headers=ADMIN_HEADERS,
    )
    p1_id = p1_resp.json()["id"]

    # 1. Invalid input: Non-existent store
    resp = client.post(
        "/api/v1/orders",
        json={
            "order_number": "ORD-INVALID-STORE",
            "destination_store_id": 99999,
            "items": [{"product_id": p1_id, "requested_quantity": 50}],
        },
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 404
    assert "Store" in resp.json()["message"]

    # 2. Invalid input: Non-existent product in line items
    resp = client.post(
        "/api/v1/orders",
        json={
            "order_number": "ORD-INVALID-PROD",
            "destination_store_id": store_id,
            "items": [{"product_id": 99999, "requested_quantity": 50}],
        },
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 404
    assert "Product" in resp.json()["message"]

    # 3. Success: Create distribution order
    resp = client.post(
        "/api/v1/orders",
        json={
            "order_number": "ORD-TEST-2026-001",
            "destination_store_id": store_id,
            "priority": "HIGH",
            "items": [{"product_id": p1_id, "requested_quantity": 75}],
        },
        headers=OPERATOR_HEADERS,
    )
    assert resp.status_code == 201
    order = resp.json()
    assert order["order_number"] == "ORD-TEST-2026-001"
    assert order["status"] == "PENDING"
    assert len(order["items"]) == 1
    assert order["items"][0]["requested_quantity"] == 75
    order_id = order["id"]

    # 4. Retrieve order
    resp = client.get(f"/api/v1/orders/{order_id}")
    assert resp.status_code == 200
    assert resp.json()["order_number"] == "ORD-TEST-2026-001"

    # 5. List orders
    resp = client.get(f"/api/v1/orders?destination_store_id={store_id}")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


# ==============================================================================
# Scenario Seeding Endpoint Tests
# ==============================================================================

def test_scenario_seeding_and_overwrite_protection(client: TestClient):
    seed_payload = {
        "scenario_code": "SCENARIO-AUTO-TEST-2026",
        "name": "Diwali Demand Surge Scenario",
        "scenario_type": "DEMAND_SURGE",
        "description": "Deterministic peak season demand surge for Western India",
        "parameters": {"surge_factor": 1.45, "affected_regions": ["West"]},
        "seed_network_topology": True,
        "overwrite": False,
    }

    # 1. Success: Seed scenario with deterministic topology
    resp = client.post(
        "/api/v1/scenarios/seed",
        json=seed_payload,
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 201
    result = resp.json()
    assert result["scenario"]["scenario_code"] == "SCENARIO-AUTO-TEST-2026"
    assert result["seeded_warehouses_count"] >= 1
    assert result["seeded_stores_count"] >= 1
    assert result["seeded_products_count"] >= 1
    assert result["seeded_batches_count"] >= 1

    # 2. Overwrite Protection: Attempting to seed same scenario code without overwrite=True triggers 409
    resp = client.post(
        "/api/v1/scenarios/seed",
        json=seed_payload,
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 409
    assert "already exists" in resp.json()["message"]

    # 3. Explicit overwrite: re-seed with overwrite=True succeeds
    seed_payload["overwrite"] = True
    seed_payload["description"] = "Updated description with overwrite flag"
    resp = client.post(
        "/api/v1/scenarios/seed",
        json=seed_payload,
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["scenario"]["description"] == "Updated description with overwrite flag"

    # 4. List and retrieve scenario
    sc_id = resp.json()["scenario"]["id"]
    get_resp = client.get(f"/api/v1/scenarios/{sc_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["scenario_code"] == "SCENARIO-AUTO-TEST-2026"


# ==============================================================================
# OpenAPI Schema Documentation Test
# ==============================================================================

def test_openapi_schema_documentation(client: TestClient):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    paths = schema["paths"]

    # Verify all required core domain paths are documented in OpenAPI
    assert "/api/v1/warehouses" in paths
    assert "/api/v1/stores" in paths
    assert "/api/v1/products" in paths
    assert "/api/v1/inventory/batches" in paths
    assert "/api/v1/inventory/summary" in paths
    assert "/api/v1/orders" in paths
    assert "/api/v1/scenarios/seed" in paths


def test_inventory_reservation_and_migration_endpoints(client: TestClient):
    # 1. Permission check: VIEWER cannot trigger migration
    unauth_resp = client.post(
        "/api/v1/inventory/migrate",
        json={"scenario_code": "SCENARIO-TEST-MIGRATE"},
        headers=VIEWER_HEADERS,
    )
    assert unauth_resp.status_code == 403

    # 2. Success: PLANNER can execute safe inventory migration
    mig_resp = client.post(
        "/api/v1/inventory/migrate",
        json={"scenario_code": "SCENARIO-TEST-MIGRATE"},
        headers=PLANNER_HEADERS,
    )
    assert mig_resp.status_code == 200
    mig_data = mig_resp.json()
    assert mig_data["status"] == "MIGRATION_VERIFIED_SUCCESSFUL"
    assert mig_data["migration_stats"]["scenario_code"] == "SCENARIO-TEST-MIGRATE"

    # 3. Query inventory reservations endpoint
    res_resp = client.get("/api/v1/inventory/reservations", headers=OPERATOR_HEADERS)
    assert res_resp.status_code == 200
    assert isinstance(res_resp.json(), list)

    # 4. Permission check: VIEWER cannot access reservations list
    res_unauth = client.get("/api/v1/inventory/reservations", headers=VIEWER_HEADERS)
    assert res_unauth.status_code == 403

    # 5. Release reservations for a non-existent plan returns 404 EntityNotFoundException
    rel_resp = client.post(
        "/api/v1/inventory/release",
        json={"plan_code": "PLAN-NON-EXISTENT", "reason": "Test release"},
        headers=PLANNER_HEADERS,
    )
    assert rel_resp.status_code == 404
    assert "DistributionPlan" in rel_resp.json()["message"]

    # 6. Verify inventory invariants
    verify_resp = client.post("/api/v1/inventory/verify", headers=OPERATOR_HEADERS)
    assert verify_resp.status_code == 200
    assert verify_resp.json()["is_valid"] is True
