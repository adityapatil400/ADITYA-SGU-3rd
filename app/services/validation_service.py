from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.logging import logger
from app.models.enums import ValidationVerdict
from app.models.facility import Warehouse, Store
from app.models.product import Product
from app.models.inventory import InventoryBatch
from app.models.plan import DistributionPlan
from app.schemas.validation import ValidationResult, RuleViolation


class ValidationService:
    """
    Authoritative validation engine for checking candidate plan proposals
    against live database inventory balances, constraints, and business rules.
    """

    @classmethod
    def validate_plan_allocations(
        cls,
        db: Session,
        plan: DistributionPlan,
    ) -> ValidationResult:
        """
        Validates plan candidate allocations against authoritative database inventory.
        Does not mutate stock balances.
        """
        violations: List[RuleViolation] = []
        warnings: List[RuleViolation] = []
        total_requested = 0
        today = date.today()

        # 1. Authoritative Warehouse & Version Freshness
        warehouse = None
        if plan.target_warehouse_id:
            warehouse = db.query(Warehouse).filter_by(id=plan.target_warehouse_id).first()

        if not warehouse:
            violations.append(
                RuleViolation(
                    rule_name="MISSING_TARGET_WAREHOUSE",
                    severity="ERROR",
                    message="Plan does not specify a valid target warehouse.",
                )
            )
        else:
            if plan.input_inventory_version != warehouse.current_inventory_version:
                violations.append(
                    RuleViolation(
                        rule_name="STALE_INVENTORY_VERSION",
                        severity="ERROR",
                        message=(
                            f"Plan assumptions stale: calculated with inventory version "
                            f"{plan.input_inventory_version}, but warehouse '{warehouse.code}' is at version "
                            f"{warehouse.current_inventory_version}."
                        ),
                        node_code=warehouse.code,
                    )
                )

        # 2. Check Candidate Allocations
        allocations = plan.candidate_allocations or []
        if not allocations:
            violations.append(
                RuleViolation(
                    rule_name="EMPTY_ALLOCATIONS",
                    severity="ERROR",
                    message="Plan contains no proposed allocations to validate.",
                )
            )

        # Group allocations by (warehouse_id, product_id) to check aggregated demand
        aggregated_demand: Dict[tuple, int] = {}

        for item in allocations:
            qty = item.get("quantity", 0)
            total_requested += qty
            sku = item.get("sku")
            from_code = item.get("from_node_code")
            to_code = item.get("to_node_code")

            if qty <= 0:
                violations.append(
                    RuleViolation(
                        rule_name="INVALID_QUANTITY",
                        severity="ERROR",
                        message=f"Allocation quantity must be greater than 0, got {qty}.",
                        sku=sku,
                        requested_quantity=qty,
                    )
                )
                continue

            # Verify Product and explicit Batch
            product = None
            target_batch = None
            if "batch_id" in item:
                target_batch = db.query(InventoryBatch).filter_by(id=item["batch_id"]).first()
                if target_batch:
                    product = db.query(Product).filter_by(id=target_batch.product_id).first()
                    if target_batch.expiry_date and target_batch.expiry_date < today:
                        violations.append(
                            RuleViolation(
                                rule_name="EXPIRED_BATCH_ALLOCATION",
                                severity="ERROR",
                                message=f"Batch '{target_batch.batch_number}' expired on {target_batch.expiry_date}.",
                                sku=product.sku if product else None,
                            )
                        )
            elif "batch_number" in item:
                target_batch = db.query(InventoryBatch).filter_by(batch_number=item["batch_number"]).first()
                if target_batch:
                    product = db.query(Product).filter_by(id=target_batch.product_id).first()
                    if target_batch.expiry_date and target_batch.expiry_date < today:
                        violations.append(
                            RuleViolation(
                                rule_name="EXPIRED_BATCH_ALLOCATION",
                                severity="ERROR",
                                message=f"Batch '{target_batch.batch_number}' expired on {target_batch.expiry_date}.",
                                sku=product.sku if product else None,
                            )
                        )

            if not product:
                if "product_id" in item:
                    product = db.query(Product).filter_by(id=item["product_id"]).first()
                elif sku:
                    product = db.query(Product).filter_by(sku=sku).first()

            if not product:
                violations.append(
                    RuleViolation(
                        rule_name="UNKNOWN_PRODUCT",
                        severity="ERROR",
                        message=f"Product '{sku or item.get('product_id') or item.get('batch_id')}' does not exist in authoritative catalog.",
                        sku=sku,
                    )
                )
                continue

            # Verify Source Warehouse
            source_wh = warehouse
            if from_code:
                found_wh = db.query(Warehouse).filter_by(code=from_code).first()
                if found_wh:
                    source_wh = found_wh
                else:
                    violations.append(
                        RuleViolation(
                            rule_name="UNKNOWN_SOURCE_NODE",
                            severity="ERROR",
                            message=f"Source facility '{from_code}' not found.",
                            node_code=from_code,
                        )
                    )
                    continue

            if not source_wh:
                continue

            # Verify Destination Store
            if to_code:
                store = db.query(Store).filter_by(code=to_code).first()
                if not store:
                    warnings.append(
                        RuleViolation(
                            rule_name="UNREGISTERED_DESTINATION_NODE",
                            severity="WARNING",
                            message=f"Destination store '{to_code}' is not currently registered.",
                            node_code=to_code,
                        )
                    )

            key = (source_wh.id, product.id)
            aggregated_demand[key] = aggregated_demand.get(key, 0) + qty

        # 3. Check Aggregated Stock Balances and Expiry Constraints
        for (wh_id, prod_id), demand_qty in aggregated_demand.items():
            wh_obj = db.query(Warehouse).filter_by(id=wh_id).first()
            prod_obj = db.query(Product).filter_by(id=prod_id).first()
            if not wh_obj or not prod_obj:
                continue

            batches = (
                db.query(InventoryBatch)
                .filter_by(warehouse_id=wh_id, product_id=prod_id)
                .all()
            )

            if not batches:
                violations.append(
                    RuleViolation(
                        rule_name="ZERO_STOCK_ON_HAND",
                        severity="ERROR",
                        message=f"No inventory batches exist for SKU '{prod_obj.sku}' in warehouse '{wh_obj.code}'.",
                        sku=prod_obj.sku,
                        node_code=wh_obj.code,
                        requested_quantity=demand_qty,
                        available_quantity=0,
                    )
                )
                continue

            # Separate unexpired vs expired
            unexpired_batches = [b for b in batches if not b.expiry_date or b.expiry_date >= today]
            expired_batches = [b for b in batches if b.expiry_date and b.expiry_date < today]

            if not unexpired_batches and expired_batches:
                violations.append(
                    RuleViolation(
                        rule_name="ALL_BATCHES_EXPIRED",
                        severity="ERROR",
                        message=f"All inventory batches for SKU '{prod_obj.sku}' in warehouse '{wh_obj.code}' are expired.",
                        sku=prod_obj.sku,
                        node_code=wh_obj.code,
                    )
                )
                continue

            total_available = sum(b.available_quantity for b in unexpired_batches)

            if total_available < demand_qty:
                violations.append(
                    RuleViolation(
                        rule_name="INSUFFICIENT_ON_HAND_STOCK",
                        severity="ERROR",
                        message=(
                            f"Warehouse '{wh_obj.code}' has only {total_available} available unexpired units "
                            f"for SKU '{prod_obj.sku}', but {demand_qty} requested."
                        ),
                        sku=prod_obj.sku,
                        node_code=wh_obj.code,
                        requested_quantity=demand_qty,
                        available_quantity=total_available,
                    )
                )

        # 4. Check Warehouse Total Capacity Limit
        if warehouse:
            total_wh_stock = (
                db.query(func.sum(InventoryBatch.on_hand_quantity))
                .filter(InventoryBatch.warehouse_id == warehouse.id)
                .scalar()
                or 0
            )
            if total_wh_stock > warehouse.capacity_limit:
                violations.append(
                    RuleViolation(
                        rule_name="WAREHOUSE_CAPACITY_EXCEEDED",
                        severity="ERROR",
                        message=(
                            f"Warehouse '{warehouse.code}' current on-hand ({total_wh_stock}) exceeds "
                            f"configured capacity limit ({warehouse.capacity_limit})."
                        ),
                        node_code=warehouse.code,
                    )
                )

        is_valid = len(violations) == 0
        verdict = ValidationVerdict.PASS if is_valid else ValidationVerdict.FAIL
        validation_status = "VALIDATED" if is_valid else "REJECTED"
        rejection_reasons = [v.message for v in violations]

        return ValidationResult(
            plan_code=plan.plan_code,
            trace_id=plan.trace_id,
            is_valid=is_valid,
            verdict=verdict,
            validation_status=validation_status,
            rejection_reasons=rejection_reasons,
            total_requested_units=total_requested,
            violations=violations,
            warnings=warnings,
            evaluated_at=datetime.now(timezone.utc),
            validator="flowgrid-authoritative-validation-engine",
        )
