from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Union
import uuid
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.core.exceptions import (
    EntityNotFoundException,
    AuthoritativeRuleViolationException,
    InvalidStateTransitionException,
    ConflictException,
)
from app.core.security import verify_separation_of_duties
from app.models.enums import PlanStatus, PlanEventType, ValidationVerdict, OrderStatus
from app.models.facility import Warehouse, Store
from app.models.product import Product
from app.models.inventory import InventoryBatch
from app.models.plan import DistributionPlan, PlanEvent
from app.models.scenario import SeededScenario
from app.schemas.validation import ValidationResult
from app.schemas.plan import PlanCommitResult
from app.services.decision_service_client import DecisionServiceClient
from app.services.validation_service import ValidationService
from app.services.inventory_service import InventoryService


ALLOWED_TRANSITIONS: Dict[PlanStatus, Set[PlanStatus]] = {
    PlanStatus.DRAFT: {PlanStatus.GENERATED, PlanStatus.FAILED, PlanStatus.CANCELLED},
    PlanStatus.GENERATED: {PlanStatus.VALIDATING, PlanStatus.VALIDATED, PlanStatus.REJECTED, PlanStatus.CANCELLED, PlanStatus.FAILED},
    PlanStatus.PROPOSED: {PlanStatus.VALIDATING, PlanStatus.VALIDATED, PlanStatus.APPROVED, PlanStatus.COMMITTED, PlanStatus.REJECTED, PlanStatus.CANCELLED, PlanStatus.FAILED},
    PlanStatus.VALIDATING: {PlanStatus.VALIDATED, PlanStatus.REJECTED, PlanStatus.FAILED, PlanStatus.CANCELLED},
    PlanStatus.VALIDATED: {PlanStatus.APPROVED, PlanStatus.COMMITTED, PlanStatus.REJECTED, PlanStatus.CANCELLED, PlanStatus.VALIDATING},
    PlanStatus.APPROVED: {PlanStatus.COMMITTED, PlanStatus.REJECTED, PlanStatus.CANCELLED},
    PlanStatus.COMMITTED: set(),
    PlanStatus.REJECTED: set(),
    PlanStatus.CANCELLED: set(),
    PlanStatus.FAILED: set(),
}


class PlanLifecycleService:
    """
    Manages the authoritative lifecycle of distribution plans:
    DRAFT -> GENERATED -> VALIDATED -> APPROVED -> COMMITTED
    
    Guarantees:
    - Rejects invalid state transitions.
    - Prevents self-approval / self-commitment (Separation of Duties).
    - Stores immutable audit trail for every state change.
    - Integrates safely with decision service proposals without granting execution authority.
    """

    @classmethod
    def check_transition(cls, current_status: PlanStatus, target_status: PlanStatus) -> None:
        """Validates that a transition from current_status to target_status is permitted."""
        allowed = ALLOWED_TRANSITIONS.get(current_status, set())
        if target_status not in allowed and target_status != current_status:
            logger.warning("Rejected invalid plan transition from '%s' to '%s'", current_status.value, target_status.value)
            raise InvalidStateTransitionException(current_status.value, target_status.value)

    @classmethod
    def create_plan_from_scenario(
        cls,
        db: Session,
        scenario_id: int,
        created_by_user_id: int,
        auto_validate: bool = True,
    ) -> DistributionPlan:
        """
        Executes end-to-end scenario pipeline:
        1. Loads scenario and builds authoritative inventory snapshot.
        2. Creates initial DRAFT plan.
        3. Calls Decision Service for candidate proposals.
        4. Transitions to GENERATED.
        5. If auto_validate is True, evaluates against authoritative inventory and sets VALIDATED or REJECTED.
        """
        scenario = db.query(SeededScenario).filter_by(id=scenario_id).first()
        if not scenario:
            raise EntityNotFoundException("SeededScenario", scenario_id)

        # 1. Build authoritative inventory snapshot
        inventory_snapshot = cls._build_inventory_snapshot(db, scenario)
        snapshot_id = inventory_snapshot.get("snapshot_id")
        warehouses = inventory_snapshot.get("warehouses", [])
        if not warehouses:
            raise AuthoritativeRuleViolationException("No active warehouses found to fulfill scenario.")

        primary_wh_id = warehouses[0]["id"]
        primary_wh_version = warehouses[0]["current_inventory_version"]

        # 2. Initialize plan in DRAFT status
        plan_code = f"PLAN-{scenario.scenario_code[:16]}-{uuid.uuid4().hex[:6].upper()}"
        plan = DistributionPlan(
            plan_code=plan_code,
            scenario_id=scenario.id,
            target_warehouse_id=primary_wh_id,
            input_inventory_version=primary_wh_version,
            status=PlanStatus.DRAFT,
            created_by_user_id=created_by_user_id,
            candidate_allocations=[],
            decision_service_metadata={
                "snapshot_id": snapshot_id,
                "scenario_snapshot": inventory_snapshot,
                "decision_service_status": "PENDING",
            },
            validation_results={},
            is_validated=False,
        )
        db.add(plan)
        db.flush()

        # Audit event: Snapshot taken
        event_snapshot = PlanEvent(
            plan_id=plan.id,
            trace_id=plan.trace_id,
            event_type=PlanEventType.SCENARIO_SNAPSHOT_TAKEN,
            actor_type="backend_orchestrator",
            actor_id=str(created_by_user_id),
            new_status=PlanStatus.DRAFT.value,
            payload={
                "scenario_code": scenario.scenario_code,
                "snapshot_id": snapshot_id,
                "input_inventory_version": primary_wh_version,
                "stock_versions": inventory_snapshot.get("stock_versions", {}),
            },
        )
        db.add(event_snapshot)
        db.commit()
        db.refresh(plan)

        # 3. Call Decision Service for candidate proposals
        try:
            candidate_proposal = DecisionServiceClient.generate_candidate_plan(
                scenario_code=scenario.scenario_code,
                scenario_type=scenario.scenario_type.value,
                parameters=scenario.parameters,
                inventory_snapshot=inventory_snapshot,
            )

            # 4. Transition to GENERATED
            cls.check_transition(plan.status, PlanStatus.GENERATED)
            plan.status = PlanStatus.GENERATED
            plan.candidate_allocations = [m.model_dump() for m in candidate_proposal.proposed_movements]
            plan.decision_service_metadata = {
                "snapshot_id": snapshot_id,
                "scenario_snapshot": inventory_snapshot,
                "candidate_id": candidate_proposal.candidate_id,
                "model_version": candidate_proposal.model_version,
                "confidence_score": candidate_proposal.confidence_score,
                "decision_service_status": "COMPLETED",
            }

            event_generated = PlanEvent(
                plan_id=plan.id,
                trace_id=plan.trace_id,
                event_type=PlanEventType.CANDIDATE_GENERATED,
                actor_type="decision_service",
                actor_id=candidate_proposal.model_version,
                previous_status=PlanStatus.DRAFT.value,
                new_status=PlanStatus.GENERATED.value,
                payload={
                    "candidate_id": candidate_proposal.candidate_id,
                    "movements_count": len(candidate_proposal.proposed_movements),
                    "snapshot_id": snapshot_id,
                },
            )
            db.add(event_generated)
            db.commit()
            db.refresh(plan)

        except Exception as exc:
            logger.error("Decision service proposal failed for scenario %s: %s", scenario.scenario_code, exc)
            cls.check_transition(plan.status, PlanStatus.FAILED)
            plan.status = PlanStatus.FAILED
            meta = dict(plan.decision_service_metadata or {})
            meta["decision_service_status"] = "FAILED"
            meta["error"] = str(exc)
            plan.decision_service_metadata = meta

            event_failed = PlanEvent(
                plan_id=plan.id,
                trace_id=plan.trace_id,
                event_type=PlanEventType.PLAN_FAILED,
                actor_type="decision_service",
                actor_id="error_handler",
                previous_status=PlanStatus.DRAFT.value,
                new_status=PlanStatus.FAILED.value,
                payload={"error": str(exc), "snapshot_id": snapshot_id},
            )
            db.add(event_failed)
            db.commit()
            raise

        # 5. Authoritative validation
        if auto_validate:
            cls.validate_plan(db, plan.id, actor_id=created_by_user_id)
            db.refresh(plan)

        return plan

    @classmethod
    def validate_plan(
        cls,
        db: Session,
        plan_id_or_code: Union[int, str],
        actor_id: int,
    ) -> ValidationResult:
        """
        Validates candidate plan allocations against authoritative inventory.
        Transitions plan to VALIDATED or REJECTED.
        """
        plan = cls._resolve_plan(db, plan_id_or_code)

        # Transition check
        cls.check_transition(plan.status, PlanStatus.VALIDATING)
        prev_status = plan.status
        plan.status = PlanStatus.VALIDATING

        # Run authoritative validation checks
        val_result = ValidationService.validate_plan_allocations(db, plan)

        target_status = PlanStatus.VALIDATED if val_result.is_valid else PlanStatus.REJECTED
        cls.check_transition(plan.status, target_status)

        plan.status = target_status
        plan.is_validated = val_result.is_valid
        plan.validated_at = datetime.now(timezone.utc)
        val_dict = val_result.model_dump(mode="json")
        val_dict["rejection_reasons"] = [v["message"] for v in val_dict.get("violations", [])]
        val_dict["validation_status"] = "VALIDATED" if val_result.is_valid else "REJECTED"
        plan.validation_results = val_dict

        event_type = PlanEventType.VALIDATION_PASSED if val_result.is_valid else PlanEventType.VALIDATION_FAILED
        event = PlanEvent(
            plan_id=plan.id,
            trace_id=plan.trace_id,
            event_type=event_type,
            actor_type="backend_validation_engine",
            actor_id=str(actor_id),
            previous_status=prev_status.value,
            new_status=target_status.value,
            payload={
                "is_valid": val_result.is_valid,
                "violations_count": len(val_result.violations),
                "warnings_count": len(val_result.warnings),
                "rejection_reasons": val_dict["rejection_reasons"],
            },
        )
        db.add(event)
        db.commit()
        db.refresh(plan)

        return val_result

    @classmethod
    def approve_plan(
        cls,
        db: Session,
        plan_id_or_code: Union[int, str],
        approver_user_id: int,
    ) -> DistributionPlan:
        """
        Authorizes a validated plan for commitment.
        Enforces separation of duties (creator cannot approve their own plan).
        """
        plan = cls._resolve_plan(db, plan_id_or_code)

        # Separation of duties (Four-Eyes Principle)
        verify_separation_of_duties(plan.created_by_user_id, approver_user_id)

        # Transition validation
        cls.check_transition(plan.status, PlanStatus.APPROVED)

        prev_status = plan.status
        plan.status = PlanStatus.APPROVED
        plan.approved_by_user_id = approver_user_id
        plan.approved_at = datetime.now(timezone.utc)

        event = PlanEvent(
            plan_id=plan.id,
            trace_id=plan.trace_id,
            event_type=PlanEventType.PLAN_APPROVED,
            actor_type="approver",
            actor_id=str(approver_user_id),
            previous_status=prev_status.value,
            new_status=PlanStatus.APPROVED.value,
            payload={"approved_by_user_id": approver_user_id},
        )
        db.add(event)
        db.commit()
        db.refresh(plan)

        return plan

    @classmethod
    def commit_plan(
        cls,
        db: Session,
        plan_id_or_code: Union[int, str],
        approver_user_id: int,
        idempotency_key: Optional[str] = None,
        commit_reason: Optional[str] = "Authoritative commitment",
    ) -> PlanCommitResult:
        """
        Commits plan allocations to physical stock via InventoryService.
        Enforces separation of duties, version checks, and idempotency.
        """
        plan = cls._resolve_plan(db, plan_id_or_code)

        # Separation of duties
        verify_separation_of_duties(plan.created_by_user_id, approver_user_id)

        # Ensure plan was approved, validated, or proposed
        if plan.status not in (PlanStatus.APPROVED, PlanStatus.VALIDATED, PlanStatus.PROPOSED, PlanStatus.COMMITTED):
            cls.check_transition(plan.status, PlanStatus.COMMITTED)

        return InventoryService.validate_and_commit_plan(
            db=db,
            plan_code=plan.plan_code,
            committed_by_user_id=approver_user_id,
            idempotency_key=idempotency_key,
            commit_reason=commit_reason,
        )

    @classmethod
    def cancel_plan(
        cls,
        db: Session,
        plan_id_or_code: Union[int, str],
        user_id: int,
        reason: str = "Plan cancelled by operator",
    ) -> DistributionPlan:
        """
        Cancels a plan and releases any held inventory reservations.
        """
        plan = cls._resolve_plan(db, plan_id_or_code)

        cls.check_transition(plan.status, PlanStatus.CANCELLED)

        # Release any active inventory reservations
        InventoryService.release_reservations_for_plan(db, plan.plan_code, reason=reason)

        prev_status = plan.status
        plan.status = PlanStatus.CANCELLED

        event = PlanEvent(
            plan_id=plan.id,
            trace_id=plan.trace_id,
            event_type=PlanEventType.PLAN_CANCELLED,
            actor_type="operator",
            actor_id=str(user_id),
            previous_status=prev_status.value,
            new_status=PlanStatus.CANCELLED.value,
            payload={"reason": reason},
        )
        db.add(event)
        db.commit()
        db.refresh(plan)

        return plan

    # --------------------------------------------------------------------------
    # Helper Resolution Methods
    # --------------------------------------------------------------------------

    @classmethod
    def _resolve_plan(cls, db: Session, plan_id_or_code: Union[int, str]) -> DistributionPlan:
        """Resolves plan by integer id or string plan_code."""
        plan = None
        if isinstance(plan_id_or_code, int) or (isinstance(plan_id_or_code, str) and plan_id_or_code.isdigit()):
            plan = db.query(DistributionPlan).filter_by(id=int(plan_id_or_code)).first()
        if not plan and isinstance(plan_id_or_code, str):
            plan = db.query(DistributionPlan).filter_by(plan_code=plan_id_or_code).first()

        if not plan:
            raise EntityNotFoundException("DistributionPlan", plan_id_or_code)
        return plan

    @classmethod
    def _build_inventory_snapshot(cls, db: Session, scenario: SeededScenario) -> Dict[str, Any]:
        """
        Builds an authoritative, frozen snapshot of:
        - Active warehouses and live inventory batches (with version numbers and expiry).
        - Authoritative stock versions across warehouses.
        - Destination stores/retailers.
        - Pending distribution orders and item demand lines.
        - Transit routes between network nodes.
        Assigned a unique traceable snapshot identifier (e.g. SNAP-...).
        """
        snapshot_id = f"SNAP-{uuid.uuid4().hex[:12].upper()}"
        frozen_at = datetime.now(timezone.utc).isoformat()

        warehouses = db.query(Warehouse).filter_by(is_active=True).all()
        stores = db.query(Store).filter_by(is_active=True).all()

        stock_versions: Dict[str, int] = {}
        wh_list = []
        for wh in warehouses:
            stock_versions[wh.code] = wh.current_inventory_version
            batches = db.query(InventoryBatch).filter_by(warehouse_id=wh.id).all()
            b_list = [
                {
                    "batch_id": b.id,
                    "batch_number": b.batch_number,
                    "sku": b.product.sku if b.product else "",
                    "product_id": b.product_id,
                    "on_hand_quantity": b.on_hand_quantity,
                    "reserved_quantity": b.reserved_quantity,
                    "committed_quantity": b.committed_quantity,
                    "available_quantity": b.available_quantity,
                    "expiry_date": b.expiry_date.isoformat() if b.expiry_date else None,
                    "version": b.version,
                }
                for b in batches
            ]
            wh_list.append(
                {
                    "id": wh.id,
                    "code": wh.code,
                    "name": wh.name,
                    "region": wh.region,
                    "current_inventory_version": wh.current_inventory_version,
                    "capacity_limit": wh.capacity_limit,
                    "batches": b_list,
                }
            )

        store_list = [
            {
                "id": s.id,
                "code": s.code,
                "name": s.name,
                "region": s.region,
                "store_type": s.store_type.value if hasattr(s.store_type, "value") else str(s.store_type),
                "priority_tier": s.priority_tier,
            }
            for s in stores
        ]

        # Authoritative distribution orders and demand lines
        from app.models.order import DistributionOrder
        orders = (
            db.query(DistributionOrder)
            .filter(DistributionOrder.status.in_([OrderStatus.PENDING, OrderStatus.PLANNED]))
            .all()
        )
        order_list = []
        demand_list = []
        for o in orders:
            o_items = []
            for item in o.items:
                sku = item.product.sku if item.product else ""
                o_items.append({
                    "item_id": item.id,
                    "sku": sku,
                    "product_id": item.product_id,
                    "requested_quantity": item.requested_quantity,
                    "fulfilled_quantity": item.fulfilled_quantity,
                    "remaining_quantity": max(0, item.requested_quantity - item.fulfilled_quantity),
                })
                demand_list.append({
                    "order_number": o.order_number,
                    "destination_store_code": o.destination_store.code if o.destination_store else "",
                    "sku": sku,
                    "requested_quantity": item.requested_quantity,
                    "priority": o.priority.value if hasattr(o.priority, "value") else str(o.priority),
                })
            order_list.append({
                "order_id": o.id,
                "order_number": o.order_number,
                "destination_store_id": o.destination_store_id,
                "status": o.status.value if hasattr(o.status, "value") else str(o.status),
                "priority": o.priority.value if hasattr(o.priority, "value") else str(o.priority),
                "items": o_items,
            })

        # Transit routes between warehouses and stores
        routes = []
        for wh in warehouses:
            for s in stores:
                transit_hours = 12 if wh.region == s.region else 24
                routes.append({
                    "origin_node_code": wh.code,
                    "destination_node_code": s.code,
                    "transit_hours": transit_hours,
                    "mode": "ROAD",
                })

        return {
            "snapshot_id": snapshot_id,
            "frozen_at": frozen_at,
            "scenario_id": scenario.id,
            "scenario_code": scenario.scenario_code,
            "scenario_type": scenario.scenario_type.value if hasattr(scenario.scenario_type, "value") else str(scenario.scenario_type),
            "parameters": scenario.parameters or {},
            "stock_versions": stock_versions,
            "warehouses": wh_list,
            "stores": store_list,
            "orders": order_list,
            "demand": demand_list,
            "routes": routes,
        }
