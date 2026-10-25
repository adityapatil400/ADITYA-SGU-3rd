import pytest
from pydantic import ValidationError

from app.models.enums import AllocationPriority, ValidationVerdict, PlanStatus
from app.schemas.decision import CandidatePlanProposal, ProposedMovement
from app.schemas.validation import ValidationResult, RuleViolation
from app.schemas.plan import PlanCommitRequest, PlanCommitResult
from datetime import datetime, timezone


def test_candidate_plan_proposal_valid():
    proposal = CandidatePlanProposal(
        candidate_id="CAND-TEST-001",
        scenario_code="SCENARIO-SURGE-01",
        model_version="ai-allocator-v2",
        confidence_score=0.92,
        proposed_movements=[
            ProposedMovement(
                sku="SKU-TV-55",
                from_node_code="DC-NORTH-01",
                to_node_code="STORE-DELHI-02",
                quantity=40,
                priority=AllocationPriority.HIGH,
                estimated_transit_hours=18,
                rationale="Predicted stockout within 48 hours"
            )
        ]
    )

    assert proposal.candidate_id == "CAND-TEST-001"
    assert len(proposal.proposed_movements) == 1
    assert proposal.proposed_movements[0].quantity == 40
    assert proposal.confidence_score == 0.92


def test_candidate_movement_rejects_non_positive_quantity():
    with pytest.raises(ValidationError):
        ProposedMovement(
            sku="SKU-TV-55",
            from_node_code="DC-NORTH-01",
            to_node_code="STORE-DELHI-02",
            quantity=0,  # Invalid: must be gt 0
        )


def test_validation_result_schema_encapsulation():
    violation = RuleViolation(
        rule_name="SAFETY_STOCK_VIOLATION",
        severity="WARNING",
        message="Allocation reduces DC-NORTH stock to 20 units below safety stock threshold.",
        sku="SKU-TV-55",
        node_code="DC-NORTH-01",
        requested_quantity=40,
        available_quantity=50
    )

    result = ValidationResult(
        plan_code="PLAN-20261024-001",
        trace_id="trace-abc-123",
        is_valid=True,
        verdict=ValidationVerdict.PASS,
        total_requested_units=40,
        violations=[],
        warnings=[violation],
    )

    assert result.is_valid is True
    assert result.verdict == ValidationVerdict.PASS
    assert len(result.warnings) == 1
    assert result.warnings[0].rule_name == "SAFETY_STOCK_VIOLATION"


def test_plan_commit_contract():
    request = PlanCommitRequest(
        plan_code="PLAN-20261024-001",
        commit_reason="Verified by backend rules engine",
        operator_id="backend-orchestrator-job"
    )
    assert request.plan_code == "PLAN-20261024-001"

    now = datetime.now(timezone.utc)
    commit_result = PlanCommitResult(
        plan_code="PLAN-20261024-001",
        trace_id="trace-abc-123",
        status=PlanStatus.COMMITTED,
        committed_at=now,
        committed_by=request.operator_id or "system",
        message="Authoritative inventory balances successfully deducted and reserved.",
        committed_movements_count=1
    )
    assert commit_result.status == PlanStatus.COMMITTED
    assert commit_result.committed_movements_count == 1
