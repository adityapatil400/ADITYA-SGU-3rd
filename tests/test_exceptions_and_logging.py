import logging
from fastapi.testclient import TestClient
from app.main import app
from app.core.exceptions import (
    EntityNotFoundException,
    AuthoritativeRuleViolationException,
    UnauthorizedCommitException,
)
from app.core.logging import SensitiveDataFilter


def test_exception_handler_entity_not_found():
    # Dynamically mount a temporary test route
    @app.get("/test-not-found")
    def raise_not_found():
        raise EntityNotFoundException("AuthoritativeInventory", "SKU-999")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/test-not-found")
    assert response.status_code == 404
    data = response.json()
    assert data["error"] == "EntityNotFoundException"
    assert "SKU-999" in data["message"]


def test_exception_handler_authoritative_rule_violation():
    @app.get("/test-rule-violation")
    def raise_rule_violation():
        raise AuthoritativeRuleViolationException(
            "Insufficient on-hand stock for allocation",
            violations=[{"rule": "INSUFFICIENT_STOCK", "sku": "SKU-100"}]
        )

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/test-rule-violation")
    assert response.status_code == 422
    data = response.json()
    assert data["error"] == "AuthoritativeRuleViolationException"
    assert len(data["details"]["violations"]) == 1


def test_exception_handler_unauthorized_commit():
    @app.get("/test-unauthorized-commit")
    def raise_unauthorized():
        raise UnauthorizedCommitException("Candidate plan cannot be committed without prior backend validation.")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/test-unauthorized-commit")
    assert response.status_code == 403
    data = response.json()
    assert data["error"] == "UnauthorizedCommitException"


def test_sensitive_data_filter_masks_credentials():
    log_filter = SensitiveDataFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Connecting to postgresql+psycopg2://db_user:super_secret@localhost:5432/flowgrid",
        args=(),
        exc_info=None,
    )
    result = log_filter.filter(record)
    assert result is True
    assert "super_secret" not in record.msg
    assert "db_user:***@localhost" in record.msg
