from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
)
from app.models.enums import UserRole
from app.models.user import User
from app.models.facility import Warehouse


# ==============================================================================
# Password Hashing & Verification Tests
# ==============================================================================

def test_password_hashing_and_verification():
    raw_pw = "SuperSecurePassword2026!"
    hashed = hash_password(raw_pw)

    # Invariant: Plaintext password is never stored
    assert hashed != raw_pw
    assert hashed.startswith("$2b$") or hashed.startswith("$2a$")

    # Correct password succeeds
    assert verify_password(raw_pw, hashed) is True

    # Incorrect password fails
    assert verify_password("WrongPassword!", hashed) is False
    assert verify_password("", hashed) is False


# ==============================================================================
# User Bootstrap, Registration & Login Tests
# ==============================================================================

def test_initial_user_bootstrap_and_login_flow(client: TestClient):
    # 1. Bootstrap: First user is automatically granted ADMIN role
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "email": "superadmin@flowgrid.io",
            "password": "AdminPassword123!",
            "full_name": "Root Administrator",
            "role": "ADMIN",
        },
    )
    assert resp.status_code == 201
    admin_data = resp.json()
    assert admin_data["email"] == "superadmin@flowgrid.io"
    assert admin_data["role"] == "ADMIN"
    assert "password" not in admin_data
    assert "hashed_password" not in admin_data

    # 2. Login with valid credentials
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "superadmin@flowgrid.io", "password": "AdminPassword123!"},
    )
    assert login_resp.status_code == 200
    token_data = login_resp.json()
    assert "access_token" in token_data
    assert token_data["token_type"] == "bearer"
    assert token_data["role"] == "ADMIN"
    admin_token = token_data["access_token"]

    # 3. Access /auth/me with Bearer token
    me_resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "superadmin@flowgrid.io"

    # 4. Login with invalid password fails
    bad_login = client.post(
        "/api/v1/auth/login",
        json={"email": "superadmin@flowgrid.io", "password": "IncorrectPassword!"},
    )
    assert bad_login.status_code == 401
    assert "Invalid email or password" in bad_login.json()["detail"]


# ==============================================================================
# Token Expiration & Tampering Tests
# ==============================================================================

def test_token_expiration_and_malformed_tokens(client: TestClient):
    # 1. Expired token validation
    expired_token = create_access_token(
        data={"sub": "planner@flowgrid.io", "role": "PLANNER"},
        expires_delta=timedelta(seconds=-30),  # Expired in the past
    )
    resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()

    # 2. Tampered / malformed token
    resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer not.a.valid.jwt.token"},
    )
    assert resp.status_code == 401
    assert "Invalid authentication token" in resp.json()["detail"]


# ==============================================================================
# Role-Based Access Control (RBAC) Tests
# ==============================================================================

def test_rbac_permissions_by_role(client: TestClient, db_session: Session):
    # Setup test users for each role
    users = [
        ("admin@flowgrid.io", UserRole.ADMIN),
        ("planner@flowgrid.io", UserRole.PLANNER),
        ("approver@flowgrid.io", UserRole.APPROVER),
        ("viewer@flowgrid.io", UserRole.VIEWER),
    ]
    tokens = {}
    for email, role in users:
        u = User(
            email=email,
            hashed_password=hash_password("Password123!"),
            full_name=email.split("@")[0].capitalize(),
            role=role,
            is_active=True,
        )
        db_session.add(u)
        db_session.commit()
        db_session.refresh(u)
        tokens[role] = create_access_token(
            data={"sub": u.email, "user_id": u.id, "role": role.value}
        )

    # Operation 1: List users directory (ADMIN ONLY)
    admin_auth = {"Authorization": f"Bearer {tokens[UserRole.ADMIN]}"}
    planner_auth = {"Authorization": f"Bearer {tokens[UserRole.PLANNER]}"}
    viewer_auth = {"Authorization": f"Bearer {tokens[UserRole.VIEWER]}"}
    approver_auth = {"Authorization": f"Bearer {tokens[UserRole.APPROVER]}"}

    # Admin succeeds
    resp = client.get("/api/v1/auth/users", headers=admin_auth)
    assert resp.status_code == 200

    # Planner fails
    resp = client.get("/api/v1/auth/users", headers=planner_auth)
    assert resp.status_code == 403

    # Viewer fails
    resp = client.get("/api/v1/auth/users", headers=viewer_auth)
    assert resp.status_code == 403

    # Operation 2: Create Warehouse (PLANNER or ADMIN permitted)
    resp = client.post(
        "/api/v1/warehouses",
        json={"code": "WH-RBAC-01", "name": "RBAC Warehouse", "region": "Central"},
        headers=planner_auth,
    )
    assert resp.status_code == 201

    # Approver cannot create warehouse
    resp = client.post(
        "/api/v1/warehouses",
        json={"code": "WH-RBAC-02", "name": "Approver WH", "region": "Central"},
        headers=approver_auth,
    )
    assert resp.status_code == 403

    # Viewer cannot create warehouse
    resp = client.post(
        "/api/v1/warehouses",
        json={"code": "WH-RBAC-03", "name": "Viewer WH", "region": "Central"},
        headers=viewer_auth,
    )
    assert resp.status_code == 403


# ==============================================================================
# Separation of Duties (Four-Eyes Principle) Tests
# ==============================================================================

def test_separation_of_duties_prevents_self_approval(client: TestClient, db_session: Session):
    # Setup Warehouse
    wh = Warehouse(code="WH-SOD-01", name="SOD Warehouse", region="South", current_inventory_version=1)
    db_session.add(wh)
    db_session.commit()

    # Create Planner User
    planner = User(
        email="lead_planner@flowgrid.io",
        hashed_password=hash_password("PlannerPass123!"),
        full_name="Lead Planner",
        role=UserRole.ADMIN,  # Even with ADMIN role, separation of duties must apply to self-created plans!
        is_active=True,
    )
    # Create Independent Approver User
    approver = User(
        email="independent_approver@flowgrid.io",
        hashed_password=hash_password("ApproverPass123!"),
        full_name="Chief Approver",
        role=UserRole.APPROVER,
        is_active=True,
    )
    db_session.add_all([planner, approver])
    db_session.commit()

    planner_token = create_access_token(
        data={"sub": planner.email, "user_id": planner.id, "role": planner.role.value}
    )
    approver_token = create_access_token(
        data={"sub": approver.email, "user_id": approver.id, "role": approver.role.value}
    )

    # 1. Planner creates candidate plan
    create_resp = client.post(
        "/api/v1/plans",
        json={
            "plan_code": "PLAN-SOD-TEST-2026",
            "target_warehouse_id": wh.id,
            "candidate_allocations": [{"sku": "SKU-TEST", "quantity": 100}],
        },
        headers={"Authorization": f"Bearer {planner_token}"},
    )
    assert create_resp.status_code == 201
    plan_code = create_resp.json()["plan_code"]

    # 2. Separation of duties violation: Creator attempts to approve their own plan
    self_approve_resp = client.post(
        f"/api/v1/plans/{plan_code}/approve",
        headers={"Authorization": f"Bearer {planner_token}"},
    )
    assert self_approve_resp.status_code == 403
    assert "Separation of duties violation" in self_approve_resp.json()["detail"]

    # 3. Independent Approver authorizes the plan
    distinct_approve_resp = client.post(
        f"/api/v1/plans/{plan_code}/approve",
        headers={"Authorization": f"Bearer {approver_token}"},
    )
    assert distinct_approve_resp.status_code == 200
    assert distinct_approve_resp.json()["status"] in ("VALIDATED", "APPROVED")
    assert distinct_approve_resp.json()["approved_by_user_id"] == approver.id
