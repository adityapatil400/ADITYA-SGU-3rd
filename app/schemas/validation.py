from datetime import datetime, timezone
from typing import Any, List, Optional
from pydantic import BaseModel, Field, model_validator
from app.models.enums import ValidationVerdict


class RuleViolation(BaseModel):
    """Specific rule check failure or warning evaluated against authoritative inventory."""
    rule_name: str = Field(..., examples=["INSUFFICIENT_ON_HAND_STOCK"])
    severity: str = Field(..., examples=["ERROR", "WARNING"])
    message: str = Field(..., examples=["Node DC-CENTRAL-01 has only 100 available units for SKU-ELEC-402, but 250 requested."])
    sku: Optional[str] = None
    node_code: Optional[str] = None
    requested_quantity: Optional[int] = None
    available_quantity: Optional[int] = None


class ValidationResult(BaseModel):
    """
    Authoritative outcome of checking a candidate plan against real inventory and business rules.
    """
    plan_code: str
    trace_id: str
    is_valid: bool
    verdict: ValidationVerdict
    validation_status: Optional[str] = None
    rejection_reasons: List[str] = Field(default_factory=list)
    total_requested_units: int = Field(ge=0)
    violations: List[RuleViolation] = Field(default_factory=list)
    warnings: List[RuleViolation] = Field(default_factory=list)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    validator: str = Field(default="flowgrid-authoritative-backend-engine")

    @model_validator(mode="before")
    @classmethod
    def populate_validation_status_and_reasons(cls, data: Any) -> Any:
        if isinstance(data, dict):
            is_valid = data.get("is_valid", False)
            if not data.get("validation_status"):
                data["validation_status"] = "VALIDATED" if is_valid else "REJECTED"
            if not data.get("rejection_reasons"):
                violations = data.get("violations") or []
                data["rejection_reasons"] = [
                    v.get("message") if isinstance(v, dict) else v.message
                    for v in violations
                    if (isinstance(v, dict) and "message" in v) or hasattr(v, "message")
                ]
        return data
