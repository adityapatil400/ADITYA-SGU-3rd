from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.models.enums import PlanStatus, AllocationPriority, ValidationVerdict
from app.schemas.validation import ValidationResult


class AllocationItemRead(BaseModel):
    id: int
    plan_id: int
    sku: str
    from_node_id: int
    to_node_id: int
    quantity: int
    priority: AllocationPriority
    estimated_transit_hours: int
    item_status: str
    
    model_config = ConfigDict(from_attributes=True)


class PlanAuditTrailRead(BaseModel):
    id: int
    trace_id: str
    event_type: str
    actor_type: str = "system"
    actor_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class AllocationPlanResponse(BaseModel):
    id: int
    plan_code: str
    scenario_id: int
    status: PlanStatus
    trace_id: str
    decision_service_candidate_id: Optional[str]
    proposed_by: str
    is_validated: bool
    validation_verdict: Optional[ValidationVerdict]
    validation_details: Dict[str, Any]
    committed_at: Optional[datetime]
    committed_by: Optional[str]
    items: List[AllocationItemRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class PlanCommitRequest(BaseModel):
    """Authoritative request to commit a previously validated plan."""
    plan_code: Optional[str] = None
    commit_reason: Optional[str] = "Approved by automated operational orchestrator"
    operator_id: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, description="Idempotency key to safely deduplicate repeated commit requests")


class PlanCommitResult(BaseModel):
    """Traceable outcome of an authoritative commitment."""
    plan_code: str
    trace_id: str
    status: PlanStatus
    committed_at: datetime
    committed_by: Optional[str] = "system"
    message: str
    committed_movements_count: int
    total_allocated_units: Optional[int] = None
    plan_id: Optional[int] = None
    scenario_id: Optional[int] = None
    inventory_version: Optional[int] = None
    idempotency_key: Optional[str] = None
    inventory_snapshot: Dict[str, Any] = Field(default_factory=dict)
    candidate_allocations: List[Dict[str, Any]] = Field(default_factory=list)
    validation_status: Optional[str] = "VALIDATED"
    rejection_reasons: List[str] = Field(default_factory=list)
    decision_service_status: Optional[str] = "COMPLETED"
    approval_status: Optional[str] = "APPROVED"
    commitment_status: Optional[str] = "COMMITTED"
    audit_event_ids: List[int] = Field(default_factory=list)


class PlanReservationRequest(BaseModel):
    """Request to create stock reservations for a candidate plan."""
    ttl_minutes: int = Field(30, ge=1, le=1440, description="Reservation time-to-live in minutes")


class PlanReservationItemRead(BaseModel):
    id: int
    reservation_code: str
    warehouse_id: int
    product_id: int
    batch_id: int
    quantity: int
    status: str
    expires_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class PlanReservationResponse(BaseModel):
    plan_code: str
    reserved_items_count: int
    reservations: List[PlanReservationItemRead]
    message: str


class PlanReleaseResponse(BaseModel):
    plan_code: str
    released_count: int
    message: str


class PlanDetailResponse(BaseModel):
    id: int
    plan_id: Optional[int] = None
    plan_code: str
    scenario_id: Optional[int] = None
    target_warehouse_id: Optional[int] = None
    input_inventory_version: int
    inventory_version: Optional[int] = None
    inventory_snapshot: Dict[str, Any] = Field(default_factory=dict)
    status: PlanStatus
    validation_status: Optional[str] = "PENDING"
    rejection_reasons: List[str] = Field(default_factory=list)
    decision_service_status: Optional[str] = "PENDING"
    approval_status: Optional[str] = "PENDING"
    commitment_status: Optional[str] = "UNCOMMITTED"
    audit_event_ids: List[int] = Field(default_factory=list)
    created_by_user_id: Optional[int] = None
    approved_by_user_id: Optional[int] = None
    approved_at: Optional[datetime] = None
    committed_by_user_id: Optional[int] = None
    committed_at: Optional[datetime] = None
    is_validated: bool
    trace_id: str
    candidate_allocations: List[Dict[str, Any]] = Field(default_factory=list)
    decision_service_metadata: Dict[str, Any] = Field(default_factory=dict)
    validation_results: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    events: List[PlanAuditTrailRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def populate_traceable_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Derive plan_id and inventory_version
            if data.get("plan_id") is None and data.get("id") is not None:
                data["plan_id"] = data["id"]
            if data.get("inventory_version") is None and data.get("input_inventory_version") is not None:
                data["inventory_version"] = data["input_inventory_version"]

            meta = data.get("decision_service_metadata") or {}
            if not data.get("inventory_snapshot"):
                data["inventory_snapshot"] = meta.get("scenario_snapshot") or {}

            if not data.get("decision_service_status"):
                data["decision_service_status"] = meta.get("decision_service_status") or "COMPLETED"

            val_res = data.get("validation_results") or {}
            st = data.get("status")
            st_val = getattr(st, "value", None) or (str(st) if st is not None else "")
            if not data.get("validation_status"):
                if st_val == "VALIDATED":
                    data["validation_status"] = "VALIDATED"
                elif st_val == "REJECTED":
                    data["validation_status"] = "REJECTED"
                elif data.get("is_validated"):
                    data["validation_status"] = "VALIDATED"
                else:
                    data["validation_status"] = val_res.get("validation_status") or "PENDING"

            if not data.get("rejection_reasons"):
                reasons = val_res.get("rejection_reasons")
                if not reasons and "rejection_reason" in val_res:
                    reasons = [val_res["rejection_reason"]]
                if not reasons and "violations" in val_res:
                    reasons = [
                        v.get("message")
                        for v in val_res["violations"]
                        if isinstance(v, dict) and "message" in v
                    ]
                data["rejection_reasons"] = reasons or []

            if not data.get("approval_status"):
                if data.get("approved_by_user_id") is not None or st_val in ("APPROVED", "COMMITTED"):
                    data["approval_status"] = "APPROVED"
                elif st_val == "REJECTED":
                    data["approval_status"] = "REJECTED"
                else:
                    data["approval_status"] = "PENDING"

            if not data.get("commitment_status"):
                if st_val == "COMMITTED":
                    data["commitment_status"] = "COMMITTED"
                elif st_val == "REJECTED":
                    data["commitment_status"] = "REJECTED"
                else:
                    data["commitment_status"] = "UNCOMMITTED"

            if not data.get("audit_event_ids"):
                events = data.get("events") or []
                audit_ids: list[int] = []
                for e in events:
                    if isinstance(e, dict) and "id" in e:
                        audit_ids.append(e["id"])
                    elif hasattr(e, "id"):
                        audit_ids.append(getattr(e, "id"))
                data["audit_event_ids"] = audit_ids
        return data
