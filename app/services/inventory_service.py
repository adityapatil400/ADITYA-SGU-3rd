from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import uuid
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.logging import logger
from app.core.exceptions import (
    EntityNotFoundException,
    AuthoritativeRuleViolationException,
    InventoryVersionMismatchException,
    InsufficientInventoryException,
    ExpiredBatchException,
    PlanAlreadyCommittedException,
    ConflictException,
)
from app.models.enums import (
    PlanStatus,
    PlanEventType,
    ReservationStatus,
    AllocationStatus,
)
from app.models.facility import Warehouse, Store
from app.models.product import Product
from app.models.inventory import InventoryBatch, InventoryReservation
from app.models.plan import DistributionPlan, PlanAllocation, PlanEvent
from app.schemas.plan import PlanCommitResult


class InventoryService:
    """
    Authoritative service governing all inventory reservations and commitments.
    
    The backend is the sole authority for evaluating, allocating, reserving,
    and committing physical stock. External decision services propose candidate
    plans, but cannot directly alter stock or bypass these authoritative guards.
    """

    @classmethod
    def reserve_stock_for_plan(
        cls,
        db: Session,
        plan_code: str,
        ttl_minutes: int = 30,
    ) -> List[InventoryReservation]:
        """
        Transactional stock reservation for a candidate plan.
        
        Guarantees:
        1. Row locks on target warehouse and referenced inventory batches.
        2. Validates plan assumptions against authoritative warehouse inventory version.
        3. Validates that requested quantities do not exceed available stock.
        4. Rejects expired stock batches.
        5. Atomically increments batch reserved_quantity and warehouse current_inventory_version.
        6. Rolls back completely on any validation error (no partial reservations).
        """
        # 1. Fetch and lock plan
        plan = (
            db.query(DistributionPlan)
            .filter_by(plan_code=plan_code)
            .with_for_update()
            .first()
        )
        if not plan:
            raise EntityNotFoundException("DistributionPlan", plan_code)

        if plan.status in (PlanStatus.COMMITTED, PlanStatus.CANCELLED, PlanStatus.REJECTED):
            raise AuthoritativeRuleViolationException(
                f"Cannot reserve stock for plan '{plan_code}' with status '{plan.status}'."
            )

        if not plan.target_warehouse_id:
            raise AuthoritativeRuleViolationException("Plan does not specify a target warehouse.")

        # 2. Lock target warehouse
        warehouse = (
            db.query(Warehouse)
            .filter_by(id=plan.target_warehouse_id)
            .with_for_update()
            .first()
        )
        if not warehouse:
            raise EntityNotFoundException("Warehouse", plan.target_warehouse_id)

        # 3. Optimistic version check: reject stale plans
        if plan.input_inventory_version != warehouse.current_inventory_version:
            logger.warning(
                "Stale plan reservation attempt: plan '%s' input_version=%d, warehouse '%s' version=%d",
                plan_code,
                plan.input_inventory_version,
                warehouse.code,
                warehouse.current_inventory_version,
            )
            cls._reject_plan(
                db=db,
                plan=plan,
                reason=(
                    f"Inventory version mismatch: plan was calculated using version "
                    f"{plan.input_inventory_version}, but warehouse '{warehouse.code}' is at version "
                    f"{warehouse.current_inventory_version}."
                ),
            )
            raise InventoryVersionMismatchException(
                expected_version=plan.input_inventory_version,
                current_version=warehouse.current_inventory_version,
            )

        # 4. Check for existing active reservations for this plan
        existing_active = (
            db.query(InventoryReservation)
            .filter_by(plan_id=plan.id, status=ReservationStatus.ACTIVE)
            .all()
        )
        if existing_active:
            # Active reservations already held for this plan
            return existing_active

        # 5. Evaluate candidate allocations
        if not plan.candidate_allocations:
            raise AuthoritativeRuleViolationException("Plan has no candidate allocations to reserve.")

        today = date.today()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        created_reservations: List[InventoryReservation] = []

        # Phase 1: Validate all items and lock batches without modifying balances
        items_to_reserve = []
        for item in plan.candidate_allocations:
            req_qty = item.get("quantity", 0)
            if req_qty <= 0:
                raise AuthoritativeRuleViolationException(f"Invalid allocation quantity: {req_qty}")

            product = cls._resolve_product(db, item)
            batch = cls._resolve_and_lock_batch(db, warehouse.id, product.id, item)

            if batch.expiry_date and batch.expiry_date < today:
                error_msg = f"Batch '{batch.batch_number}' expired on {batch.expiry_date} and cannot be reserved."
                logger.warning(error_msg)
                cls._rollback_and_reject_plan(db, plan, error_msg)
                raise ExpiredBatchException(error_msg)

            if batch.available_quantity < req_qty:
                error_msg = (
                    f"Insufficient available stock for product '{product.sku}' in batch '{batch.batch_number}'. "
                    f"Requested: {req_qty}, Available: {batch.available_quantity}."
                )
                logger.warning(error_msg)
                cls._rollback_and_reject_plan(db, plan, error_msg)
                raise InsufficientInventoryException(error_msg)

            items_to_reserve.append((product, batch, req_qty))

        # Phase 2: Apply mutations atomically once all items are confirmed valid
        for product, batch, req_qty in items_to_reserve:
            batch.reserved_quantity += req_qty
            batch.version += 1

            reservation = InventoryReservation(
                reservation_code=f"RES-{uuid.uuid4().hex[:12].upper()}",
                warehouse_id=warehouse.id,
                product_id=product.id,
                batch_id=batch.id,
                plan_id=plan.id,
                quantity=req_qty,
                status=ReservationStatus.ACTIVE,
                expires_at=expires_at,
            )
            db.add(reservation)
            created_reservations.append(reservation)

        # Atomically increment warehouse inventory version
        warehouse.current_inventory_version += 1

        # Record audit event
        event = PlanEvent(
            plan_id=plan.id,
            trace_id=plan.trace_id,
            event_type=PlanEventType.RESERVATION_CREATED,
            actor_type="backend_inventory_authority",
            actor_id="reservation_service",
            new_status=plan.status.value,
            payload={
                "reservation_count": len(created_reservations),
                "expires_at": expires_at.isoformat(),
                "warehouse_version_after": warehouse.current_inventory_version,
            },
        )
        db.add(event)
        db.commit()

        logger.info(
            "Successfully reserved %d inventory allocations for plan '%s' (expires %s)",
            len(created_reservations),
            plan_code,
            expires_at.isoformat(),
        )
        return created_reservations

    @classmethod
    def release_reservations_for_plan(
        cls,
        db: Session,
        plan_code: str,
        reason: str = "Plan cancelled, rejected, or expired",
    ) -> int:
        """
        Releases all active inventory reservations associated with a plan.
        Decrements reserved_quantity on batches and updates warehouse inventory version.
        """
        plan = (
            db.query(DistributionPlan)
            .filter_by(plan_code=plan_code)
            .with_for_update()
            .first()
        )
        if not plan:
            raise EntityNotFoundException("DistributionPlan", plan_code)

        active_reservations = (
            db.query(InventoryReservation)
            .filter_by(plan_id=plan.id, status=ReservationStatus.ACTIVE)
            .with_for_update()
            .all()
        )

        if not active_reservations:
            return 0

        released_count = 0
        warehouse_id = plan.target_warehouse_id

        for res in active_reservations:
            batch = (
                db.query(InventoryBatch)
                .filter_by(id=res.batch_id)
                .with_for_update()
                .first()
            )
            if batch:
                batch.reserved_quantity = max(0, batch.reserved_quantity - res.quantity)
                batch.version += 1
            res.status = ReservationStatus.RELEASED
            released_count += 1

        if warehouse_id:
            warehouse = (
                db.query(Warehouse)
                .filter_by(id=warehouse_id)
                .with_for_update()
                .first()
            )
            if warehouse:
                warehouse.current_inventory_version += 1

        event = PlanEvent(
            plan_id=plan.id,
            trace_id=plan.trace_id,
            event_type=PlanEventType.RESERVATION_RELEASED,
            actor_type="backend_inventory_authority",
            actor_id="reservation_service",
            new_status=plan.status.value,
            payload={"released_count": released_count, "reason": reason},
        )
        db.add(event)
        db.commit()

        logger.info("Released %d reservations for plan '%s': %s", released_count, plan_code, reason)
        return released_count

    @classmethod
    def validate_and_commit_plan(
        cls,
        db: Session,
        plan_code: str,
        committed_by_user_id: int,
        idempotency_key: Optional[str] = None,
        commit_reason: Optional[str] = "Authoritative plan commitment",
    ) -> PlanCommitResult:
        """
        Authoritatively validates and commits candidate plan allocations to physical stock.
        
        Guarantees:
        1. Idempotency: Duplicate commits with the same idempotency key return the committed plan safely.
        2. Row-level locking: SELECT ... FOR UPDATE prevents concurrent transactions from double-allocating stock.
        3. Optimistic version check: Rejects stale plans whose input_inventory_version != warehouse current version.
        4. Expiry & Capacity constraints: Validates batch expiry and warehouse capacity.
        5. Atomicity: In case of any validation or rule failure, the transaction rolls back cleanly.
        """
        # 1. Fetch and lock plan
        plan = (
            db.query(DistributionPlan)
            .filter_by(plan_code=plan_code)
            .with_for_update()
            .first()
        )
        if not plan:
            raise EntityNotFoundException("DistributionPlan", plan_code)

        # 2. Idempotency safeguard
        if plan.status == PlanStatus.COMMITTED:
            existing_results = plan.validation_results or {}
            saved_key = existing_results.get("idempotency_key")
            if idempotency_key and saved_key == idempotency_key:
                logger.info("Idempotent commit request recognized for plan '%s' with key '%s'", plan_code, idempotency_key)
                return PlanCommitResult(
                    plan_code=plan.plan_code,
                    trace_id=plan.trace_id,
                    status=plan.status,
                    committed_at=plan.committed_at or datetime.now(timezone.utc),
                    committed_by=str(plan.committed_by_user_id),
                    message="Plan was previously committed (idempotent request).",
                    committed_movements_count=len(plan.allocations),
                    total_allocated_units=sum(a.allocated_quantity for a in plan.allocations),
                    plan_id=plan.id,
                    scenario_id=plan.scenario_id,
                    inventory_version=plan.input_inventory_version,
                    idempotency_key=saved_key,
                    inventory_snapshot=plan.inventory_snapshot,
                    candidate_allocations=plan.candidate_allocations,
                    validation_status="VALIDATED",
                    rejection_reasons=[],
                    decision_service_status=plan.decision_service_status,
                    approval_status="APPROVED",
                    commitment_status="COMMITTED",
                    audit_event_ids=plan.audit_event_ids,
                )
            # Re-commitment without matching idempotency key is rejected
            raise PlanAlreadyCommittedException(plan_code)

        if plan.status in (PlanStatus.CANCELLED, PlanStatus.REJECTED):
            raise AuthoritativeRuleViolationException(
                f"Cannot commit plan '{plan_code}' with status '{plan.status}'."
            )

        if not plan.target_warehouse_id:
            raise AuthoritativeRuleViolationException("Plan does not specify a target warehouse.")

        # 3. Lock target warehouse
        warehouse = (
            db.query(Warehouse)
            .filter_by(id=plan.target_warehouse_id)
            .with_for_update()
            .first()
        )
        if not warehouse:
            raise EntityNotFoundException("Warehouse", plan.target_warehouse_id)

        # 4. Authoritative check: Inventory version freshness
        # Notice: If reservations were held for this plan, the warehouse version incremented during reservation.
        # So we check against plan.input_inventory_version or the expected active reservation state.
        active_reservations = (
            db.query(InventoryReservation)
            .filter_by(plan_id=plan.id, status=ReservationStatus.ACTIVE)
            .with_for_update()
            .all()
        )

        expected_version = plan.input_inventory_version
        if active_reservations:
            # If this plan reserved inventory, warehouse version incremented by 1 during reservation.
            expected_version += 1

        if warehouse.current_inventory_version != expected_version:
            error_msg = (
                f"Inventory version mismatch: plan was calculated on version {plan.input_inventory_version}, "
                f"but warehouse '{warehouse.code}' is currently at version {warehouse.current_inventory_version}."
            )
            logger.warning(error_msg)
            cls.release_reservations_for_plan(db, plan_code, reason="Stale inventory version mismatch")
            cls._reject_plan(db, plan, error_msg)
            raise InventoryVersionMismatchException(
                expected_version=expected_version,
                current_version=warehouse.current_inventory_version,
            )

        # 5. Authoritative check: Warehouse capacity limits
        total_on_hand = (
            db.query(func.sum(InventoryBatch.on_hand_quantity))
            .filter(InventoryBatch.warehouse_id == warehouse.id)
            .scalar()
            or 0
        )
        if total_on_hand > warehouse.capacity_limit:
            error_msg = f"Warehouse '{warehouse.code}' exceeds total capacity limit ({warehouse.capacity_limit})."
            cls.release_reservations_for_plan(db, plan_code, reason="Warehouse capacity violation")
            cls._reject_plan(db, plan, error_msg)
            raise AuthoritativeRuleViolationException(error_msg)

        today = date.today()
        movements_count = 0

        # Resolve or prepare destination store for PlanAllocation records
        destination_store = cls._resolve_or_create_destination_store(db)

        # 6. Authoritative check & Commitment Execution
        validated_reservations: list[tuple[InventoryReservation, InventoryBatch]] = []
        items_to_commit: list[tuple[Product, InventoryBatch, int, dict[str, Any]]] = []

        if active_reservations:
            # Case A: Commit from active reservations
            # Phase 1: Validate all reservations before mutating
            for res in active_reservations:
                batch = (
                    db.query(InventoryBatch)
                    .filter_by(id=res.batch_id)
                    .with_for_update()
                    .first()
                )
                if not batch:
                    raise EntityNotFoundException("InventoryBatch", res.batch_id)

                if batch.expiry_date and batch.expiry_date < today:
                    error_msg = f"Batch '{batch.batch_number}' expired on {batch.expiry_date} before commitment."
                    cls.release_reservations_for_plan(db, plan_code, reason="Expired stock detected at commit")
                    cls._rollback_and_reject_plan(db, plan, error_msg)
                    raise ExpiredBatchException(error_msg)

                if batch.reserved_quantity < res.quantity:
                    error_msg = f"Batch '{batch.batch_number}' reservation balance compromised."
                    cls.release_reservations_for_plan(db, plan_code, reason="Reservation balance compromised")
                    cls._rollback_and_reject_plan(db, plan, error_msg)
                    raise InsufficientInventoryException(error_msg)

                validated_reservations.append((res, batch))
        else:
            # Case B: Direct commitment from candidate allocations (without prior reservation)
            if not plan.candidate_allocations:
                raise AuthoritativeRuleViolationException("Plan has no candidate allocations to commit.")

            # Phase 1: Validate all candidate items
            items_to_commit = []
            allocated_in_run: Dict[int, int] = {}
            for item in plan.candidate_allocations:
                req_qty = item.get("quantity", 0)
                if req_qty <= 0:
                    raise AuthoritativeRuleViolationException(f"Invalid allocation quantity: {req_qty}")

                target_wh_id = warehouse.id
                if item.get("from_warehouse"):
                    source_wh = db.query(Warehouse).filter_by(code=item["from_warehouse"]).first()
                    if source_wh:
                        target_wh_id = source_wh.id

                product = cls._resolve_product(db, item)
                if "batch_id" in item or "batch_number" in item:
                    batch = cls._resolve_and_lock_batch(db, target_wh_id, product.id, item, allocated_in_run)
                    if batch.expiry_date and batch.expiry_date < today:
                        error_msg = f"Batch '{batch.batch_number}' expired on {batch.expiry_date}."
                        cls._rollback_and_reject_plan(db, plan, error_msg)
                        raise ExpiredBatchException(error_msg)
                    rem = batch.available_quantity - allocated_in_run.get(batch.id, 0)
                    if rem < req_qty:
                        error_msg = (
                            f"Insufficient available stock for product '{product.sku}' in batch '{batch.batch_number}'. "
                            f"Requested: {req_qty}, Available: {rem}."
                        )
                        cls._rollback_and_reject_plan(db, plan, error_msg)
                        raise InsufficientInventoryException(error_msg)
                    allocated_in_run[batch.id] = allocated_in_run.get(batch.id, 0) + req_qty
                    items_to_commit.append((product, batch, req_qty, item))
                else:
                    needed = req_qty
                    eligible_batches = (
                        db.query(InventoryBatch)
                        .filter_by(warehouse_id=target_wh_id, product_id=product.id)
                        .order_by(InventoryBatch.expiry_date.asc().nulls_last(), InventoryBatch.id.asc())
                        .with_for_update()
                        .all()
                    )
                    allocated_for_item = 0
                    expired_batches_found = []
                    for b in eligible_batches:
                        if b.expiry_date and b.expiry_date < today:
                            expired_batches_found.append(b)
                            continue
                        available = b.available_quantity - allocated_in_run.get(b.id, 0)
                        if available <= 0:
                            continue
                        take = min(needed, available)
                        allocated_in_run[b.id] = allocated_in_run.get(b.id, 0) + take
                        items_to_commit.append((product, b, take, item))
                        needed -= take
                        allocated_for_item += take
                        if needed == 0:
                            break
                    if needed > 0:
                        if expired_batches_found and allocated_for_item == 0:
                            b_exp = expired_batches_found[0]
                            error_msg = f"Batch '{b_exp.batch_number}' expired on {b_exp.expiry_date}."
                            cls._rollback_and_reject_plan(db, plan, error_msg)
                            raise ExpiredBatchException(error_msg)
                        error_msg = (
                            f"Insufficient available stock for product '{product.sku}' in warehouse. "
                            f"Requested: {req_qty}, Available: {allocated_for_item}."
                        )
                        cls._rollback_and_reject_plan(db, plan, error_msg)
                        raise InsufficientInventoryException(error_msg)

        # Phase 2: Transactional Mutations and Audit Logging
        try:
            if active_reservations:
                # Phase 2: Convert reservations into physical commitments
                for res, batch in validated_reservations:
                    batch.reserved_quantity -= res.quantity
                    batch.committed_quantity += res.quantity
                    batch.version += 1
                    res.status = ReservationStatus.COMMITTED

                    allocation = PlanAllocation(
                        plan_id=plan.id,
                        source_warehouse_id=warehouse.id,
                        destination_store_id=destination_store.id,
                        product_id=batch.product_id,
                        batch_id=batch.id,
                        allocated_quantity=res.quantity,
                        allocation_status=AllocationStatus.COMMITTED,
                        estimated_transit_hours=24,
                    )
                    db.add(allocation)
                    movements_count += 1
            else:
                # Phase 2: Mutate stock and create allocation records
                for product, batch, req_qty, item in items_to_commit:
                    batch.committed_quantity += req_qty
                    batch.version += 1

                    allocation = PlanAllocation(
                        plan_id=plan.id,
                        source_warehouse_id=batch.warehouse_id,
                        destination_store_id=item.get("destination_store_id", destination_store.id),
                        product_id=product.id,
                        batch_id=batch.id,
                        allocated_quantity=req_qty,
                        allocation_status=AllocationStatus.COMMITTED,
                        estimated_transit_hours=item.get("estimated_transit_hours", 24),
                    )
                    db.add(allocation)
                    movements_count += 1

            # 7. Finalize Plan Status & Metadata
            now_dt = datetime.now(timezone.utc)
            plan.status = PlanStatus.COMMITTED
            plan.committed_at = now_dt
            plan.committed_by_user_id = committed_by_user_id

            val_results = dict(plan.validation_results or {})
            val_results["idempotency_key"] = idempotency_key
            val_results["commit_reason"] = commit_reason
            val_results["committed_at"] = now_dt.isoformat()
            plan.validation_results = val_results

            # Increment warehouse inventory version
            warehouse.current_inventory_version += 1

            # 8. Record Immutable Plan Audit Event
            event = PlanEvent(
                plan_id=plan.id,
                trace_id=plan.trace_id,
                event_type=PlanEventType.PLAN_COMMITTED,
                actor_type="backend_inventory_authority",
                actor_id=str(committed_by_user_id),
                previous_status=plan.status.value,
                new_status=PlanStatus.COMMITTED.value,
                payload={
                    "commit_reason": commit_reason,
                    "idempotency_key": idempotency_key,
                    "movements_count": movements_count,
                    "warehouse_version_after": warehouse.current_inventory_version,
                },
            )
            db.add(event)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error("Transaction failed during plan commitment '%s': %s", plan_code, str(exc))
            raise

        logger.info(
            "Plan '%s' authoritatively committed by user %d (%d allocations committed).",
            plan_code,
            committed_by_user_id,
            movements_count,
        )

        return PlanCommitResult(
            plan_code=plan.plan_code,
            trace_id=plan.trace_id,
            status=plan.status,
            committed_at=now_dt,
            committed_by=str(committed_by_user_id),
            message="Plan authoritatively validated and committed.",
            committed_movements_count=movements_count,
            total_allocated_units=sum(a.allocated_quantity for a in plan.allocations),
            plan_id=plan.id,
            scenario_id=plan.scenario_id,
            inventory_version=warehouse.current_inventory_version,
            idempotency_key=idempotency_key,
            inventory_snapshot=plan.inventory_snapshot,
            candidate_allocations=plan.candidate_allocations,
            validation_status="VALIDATED",
            rejection_reasons=[],
            decision_service_status=plan.decision_service_status,
            approval_status="APPROVED",
            commitment_status="COMMITTED",
            audit_event_ids=plan.audit_event_ids,
        )

    # --------------------------------------------------------------------------
    # Helper & Resolution Methods
    # --------------------------------------------------------------------------

    @classmethod
    def _resolve_product(cls, db: Session, item: Dict[str, Any]) -> Product:
        """Resolves product by sku, product_id, batch_id, or name pattern."""
        import re
        prod = None
        if item.get("product_id"):
            prod = db.query(Product).filter_by(id=item["product_id"]).first()
        elif item.get("sku"):
            prod = db.query(Product).filter_by(sku=item["sku"]).first()
        elif item.get("batch_id"):
            batch = db.query(InventoryBatch).filter_by(id=item["batch_id"]).first()
            if batch:
                prod = db.query(Product).filter_by(id=batch.product_id).first()
        
        if not prod and item.get("product"):
            match = re.search(r"(SKU-\d+)", str(item["product"]))
            if match:
                prod = db.query(Product).filter_by(sku=match.group(1)).first()

        if not prod:
            identifier = item.get("sku") or item.get("product_id") or item.get("batch_id") or item.get("product")
            raise EntityNotFoundException("Product", identifier)
        return prod

    @classmethod
    def _resolve_and_lock_batch(
        cls,
        db: Session,
        warehouse_id: int,
        product_id: int,
        item: Dict[str, Any],
        allocated_in_run: Optional[Dict[int, int]] = None,
    ) -> InventoryBatch:
        """Resolves target batch by batch_id, batch_number, or FEFO unexpired selection."""
        allocated = allocated_in_run or {}
        if "batch_id" in item:
            batch = (
                db.query(InventoryBatch)
                .filter_by(id=item["batch_id"])
                .with_for_update()
                .first()
            )
            if not batch:
                raise EntityNotFoundException("InventoryBatch", item["batch_id"])
            return batch

        if "batch_number" in item:
            batch = (
                db.query(InventoryBatch)
                .filter_by(
                    warehouse_id=warehouse_id,
                    product_id=product_id,
                    batch_number=item["batch_number"],
                )
                .with_for_update()
                .first()
            )
            if not batch:
                raise EntityNotFoundException("InventoryBatch", item["batch_number"])
            return batch

        # FEFO (First Expired First Out) fallback
        batches = (
            db.query(InventoryBatch)
            .filter_by(warehouse_id=warehouse_id, product_id=product_id)
            .order_by(InventoryBatch.expiry_date.asc().nulls_last(), InventoryBatch.id.asc())
            .with_for_update()
            .all()
        )
        if not batches:
            raise EntityNotFoundException("InventoryBatch", f"wh={warehouse_id}, prod={product_id}")

        # Choose the best unexpired batch with sufficient inventory (FEFO) after running allocations
        today = date.today()
        req_qty = item.get("quantity", 1)
        for b in batches:
            rem = b.available_quantity - allocated.get(b.id, 0)
            if (not b.expiry_date or b.expiry_date >= today) and rem >= req_qty:
                return b

        for b in batches:
            rem = b.available_quantity - allocated.get(b.id, 0)
            if (not b.expiry_date or b.expiry_date >= today) and rem > 0:
                return b

        return batches[0]

    @classmethod
    def _resolve_or_create_destination_store(cls, db: Session) -> Store:
        """Ensures at least one valid destination store exists for foreign key constraints."""
        store = db.query(Store).filter_by(is_active=True).first()
        if not store:
            store = Store(
                code="STORE-AUTO-01",
                name="Default Retail Store",
                region="Central",
                priority_tier=1,
                is_active=True,
            )
            db.add(store)
            db.flush()
        return store

    @classmethod
    def _reject_plan(cls, db: Session, plan: DistributionPlan, reason: str) -> None:
        """Marks a plan as REJECTED and records an audit trail event."""
        plan.status = PlanStatus.REJECTED
        val_results = dict(plan.validation_results or {})
        val_results["rejection_reason"] = reason
        val_results["rejection_reasons"] = [reason]
        val_results["validation_status"] = "REJECTED"
        plan.validation_results = val_results

        event = PlanEvent(
            plan_id=plan.id,
            trace_id=plan.trace_id,
            event_type=PlanEventType.PLAN_REJECTED,
            actor_type="backend_inventory_authority",
            actor_id="inventory_service",
            new_status=PlanStatus.REJECTED.value,
            payload={"reason": reason},
        )
        db.add(event)
        db.commit()

    @classmethod
    def _rollback_and_reject_plan(cls, db: Session, plan: DistributionPlan, reason: str) -> None:
        """Rolls back any dirty session changes, then cleanly marks plan REJECTED."""
        plan_id = plan.id
        trace_id = plan.trace_id
        db.rollback()

        # Re-fetch plan in clean session
        p = db.query(DistributionPlan).filter_by(id=plan_id).first()
        if p:
            p.status = PlanStatus.REJECTED
            val_results = dict(p.validation_results or {})
            val_results["rejection_reason"] = reason
            val_results["rejection_reasons"] = [reason]
            val_results["validation_status"] = "REJECTED"
            p.validation_results = val_results

            event = PlanEvent(
                plan_id=p.id,
                trace_id=trace_id,
                event_type=PlanEventType.PLAN_REJECTED,
                actor_type="backend_inventory_authority",
                actor_id="inventory_service",
                new_status=PlanStatus.REJECTED.value,
                payload={"reason": reason},
            )
            db.add(event)
            db.commit()

    @classmethod
    def adjust_batch_inventory(
        cls,
        db: Session,
        batch_id: int,
        quantity_delta: int,
        reason: str,
        user_id: Optional[int] = None,
    ) -> InventoryBatch:
        """
        Transactionally adjusts on-hand physical stock for an existing batch with concurrency protection.
        Guarantees:
        1. Row locks batch and warehouse with SELECT ... FOR UPDATE.
        2. Invariant: on_hand_quantity + quantity_delta >= reserved_quantity + committed_quantity.
        3. Stock must never become negative.
        4. Reservations must never exceed available stock.
        5. Atomically increments batch.version and warehouse.current_inventory_version.
        """
        batch = (
            db.query(InventoryBatch)
            .filter_by(id=batch_id)
            .with_for_update()
            .first()
        )
        if not batch:
            raise EntityNotFoundException("InventoryBatch", batch_id)

        warehouse = (
            db.query(Warehouse)
            .filter_by(id=batch.warehouse_id)
            .with_for_update()
            .first()
        )
        if not warehouse:
            raise EntityNotFoundException("Warehouse", batch.warehouse_id)

        new_on_hand = batch.on_hand_quantity + quantity_delta
        min_required = batch.reserved_quantity + batch.committed_quantity

        if new_on_hand < 0:
            raise AuthoritativeRuleViolationException(
                f"Adjustment of {quantity_delta} would make physical stock negative ({new_on_hand}) "
                f"for batch '{batch.batch_number}'."
            )

        if new_on_hand < min_required:
            raise AuthoritativeRuleViolationException(
                f"Adjustment of {quantity_delta} breaches active reservations and commitments "
                f"(required: {min_required}, resulting physical: {new_on_hand}) for batch '{batch.batch_number}'."
            )

        batch.on_hand_quantity = new_on_hand
        batch.version += 1
        warehouse.current_inventory_version += 1

        db.commit()
        db.refresh(batch)
        logger.info(
            "Batch '%s' inventory adjusted by %d units. Reason: %s. Batch version: %d, Warehouse version: %d",
            batch.batch_number,
            quantity_delta,
            reason,
            batch.version,
            warehouse.current_inventory_version,
        )
        return batch

