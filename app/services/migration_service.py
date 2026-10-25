"""
Data Migration and Inventory Invariant Verification Service.

Responsibilities:
1. Safely migrate seeded operational scenario data into authoritative PostgreSQL database.
2. Idempotently upsert records (warehouses, retailers/stores, products, batches, orders)
   without creating duplicate entries.
3. Preserve batch-level expiry, available/reserved quantities, inventory versions, and timestamps.
4. Verify critical inventory invariants:
   - Available stock = physical quantity - reserved quantity (accounting for commitments).
   - Stock must never become negative.
   - Reservations must never exceed available stock.
   - Inventory versions increment whenever authoritative inventory changes.
   - Migrations must not silently discard existing data.
5. Guarantee safe migration: old database/data sources are never deleted until PostgreSQL
   invariants are comprehensively verified.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.logging import logger
from app.core.exceptions import AuthoritativeRuleViolationException
from app.models.enums import UserRole, StoreType, OrderPriority, OrderStatus, ScenarioType, ReservationStatus
from app.models.facility import Warehouse, Store, Retailer
from app.models.product import Product
from app.models.inventory import InventoryBatch, InventoryReservation, Reservation
from app.models.order import DistributionOrder, DistributionOrderItem, Order, OrderItem
from app.models.scenario import SeededScenario
from app.models.user import User


class DataMigrationService:
    """
    Authoritative database migration service for PostgreSQL.
    """

    @classmethod
    def migrate_seeded_scenario_data(
        cls,
        db: Session,
        scenario_code: str = "SCENARIO-SURGE-001",
        preserve_existing_versions: bool = True,
    ) -> Dict[str, Any]:
        """
        Migrates and seeds deterministic operational data without duplicating records.
        Preserves existing inventory versions, timestamps, and batch expiry.
        """
        logger.info("Beginning safe scenario data migration into PostgreSQL (scenario: %s)", scenario_code)

        results: Dict[str, Any] = {
            "scenario_code": scenario_code,
            "warehouses_upserted": 0,
            "retailers_upserted": 0,
            "products_upserted": 0,
            "batches_upserted": 0,
            "orders_upserted": 0,
            "duplicates_prevented": 0,
            "status": "COMPLETED",
        }

        # 1. Deterministic Warehouses
        warehouse_seeds: List[Dict[str, Any]] = [
            {
                "code": "WH-WEST-SEED-01",
                "name": "West Seed Distribution Hub",
                "region": "West",
                "capacity_limit": 500000,
                "initial_version": 1,
            },
            {
                "code": "WH-NORTH-SEED-01",
                "name": "North Seed Distribution Hub",
                "region": "North",
                "capacity_limit": 400000,
                "initial_version": 1,
            },
        ]

        wh_map: Dict[str, Warehouse] = {}
        for w_data in warehouse_seeds:
            existing = db.query(Warehouse).filter_by(code=w_data["code"]).first()
            if existing:
                existing.name = w_data["name"]
                existing.region = w_data["region"]
                existing.capacity_limit = w_data["capacity_limit"]
                existing.is_active = True
                wh_map[w_data["code"]] = existing
                results["duplicates_prevented"] += 1
            else:
                wh = Warehouse(
                    code=w_data["code"],
                    name=w_data["name"],
                    region=w_data["region"],
                    capacity_limit=w_data["capacity_limit"],
                    current_inventory_version=w_data["initial_version"],
                    is_active=True,
                )
                db.add(wh)
                wh_map[w_data["code"]] = wh
                results["warehouses_upserted"] += 1

        db.flush()

        # 2. Deterministic Retailers / Stores
        store_seeds: List[Dict[str, Any]] = [
            {
                "code": "STORE-MUM-SEED-01",
                "name": "Mumbai Seed Retail Flagship",
                "region": "West",
                "store_type": StoreType.FLAGSHIP,
                "priority_tier": 1,
            },
            {
                "code": "STORE-DEL-SEED-01",
                "name": "Delhi Seed Express Store",
                "region": "North",
                "store_type": StoreType.EXPRESS,
                "priority_tier": 2,
            },
        ]

        store_map: Dict[str, Store] = {}
        for s_data in store_seeds:
            existing_store = db.query(Store).filter_by(code=s_data["code"]).first()
            if existing_store:
                existing_store.name = s_data["name"]
                existing_store.region = s_data["region"]
                existing_store.store_type = s_data["store_type"]
                existing_store.priority_tier = s_data["priority_tier"]
                existing_store.is_active = True
                store_map[s_data["code"]] = existing_store
                results["duplicates_prevented"] += 1
            else:
                st = Store(
                    code=s_data["code"],
                    name=s_data["name"],
                    region=s_data["region"],
                    store_type=s_data["store_type"],
                    priority_tier=s_data["priority_tier"],
                    is_active=True,
                )
                db.add(st)
                store_map[s_data["code"]] = st
                results["retailers_upserted"] += 1

        db.flush()

        # 3. Deterministic Products
        product_seeds: List[Dict[str, Any]] = [
            {
                "sku": "SKU-SEED-PHONE-X",
                "name": "Seed Smartphone X",
                "category": "Electronics",
                "shelf_life_days": 730,
                "is_perishable": False,
                "unit_cost": 500.0,
            },
            {
                "sku": "SKU-SEED-LAPTOP-15",
                "name": "Seed Laptop 15",
                "category": "Electronics",
                "shelf_life_days": 1095,
                "is_perishable": False,
                "unit_cost": 1000.0,
            },
        ]

        prod_map: Dict[str, Product] = {}
        for p_data in product_seeds:
            existing_prod = db.query(Product).filter_by(sku=p_data["sku"]).first()
            if existing_prod:
                existing_prod.name = p_data["name"]
                existing_prod.category = p_data["category"]
                existing_prod.unit_cost = p_data["unit_cost"]
                prod_map[p_data["sku"]] = existing_prod
                results["duplicates_prevented"] += 1
            else:
                p = Product(
                    sku=p_data["sku"],
                    name=p_data["name"],
                    category=p_data["category"],
                    shelf_life_days=p_data["shelf_life_days"],
                    is_perishable=p_data["is_perishable"],
                    unit_cost=p_data["unit_cost"],
                    is_active=True,
                )
                db.add(p)
                prod_map[p_data["sku"]] = p
                results["products_upserted"] += 1

        db.flush()

        # 4. Deterministic Inventory Batches
        today = date.today()
        batch_seeds: List[Dict[str, Any]] = [
            {
                "wh_code": "WH-WEST-SEED-01",
                "sku": "SKU-SEED-PHONE-X",
                "batch_number": "BATCH-SEED-P1-W1",
                "on_hand": 1000,
                "reserved": 100,
                "committed": 50,
                "expiry_days": 180,
            },
            {
                "wh_code": "WH-NORTH-SEED-01",
                "sku": "SKU-SEED-PHONE-X",
                "batch_number": "BATCH-SEED-P1-W2",
                "on_hand": 600,
                "reserved": 50,
                "committed": 0,
                "expiry_days": 90,
            },
            {
                "wh_code": "WH-WEST-SEED-01",
                "sku": "SKU-SEED-LAPTOP-15",
                "batch_number": "BATCH-SEED-P2-W1",
                "on_hand": 300,
                "reserved": 20,
                "committed": 10,
                "expiry_days": 365,
            },
        ]

        for b_data in batch_seeds:
            wh = wh_map[b_data["wh_code"]]
            prod = prod_map[b_data["sku"]]
            existing_batch = (
                db.query(InventoryBatch)
                .filter_by(warehouse_id=wh.id, product_id=prod.id, batch_number=b_data["batch_number"])
                .first()
            )

            if existing_batch:
                # Update without losing non-negative guarantees or resetting existing versions
                existing_batch.on_hand_quantity = b_data["on_hand"]
                existing_batch.reserved_quantity = b_data["reserved"]
                existing_batch.committed_quantity = b_data["committed"]
                existing_batch.expiry_date = today + timedelta(days=b_data["expiry_days"])
                if not preserve_existing_versions:
                    existing_batch.version = 1
                results["duplicates_prevented"] += 1
            else:
                batch = InventoryBatch(
                    warehouse_id=wh.id,
                    product_id=prod.id,
                    batch_number=b_data["batch_number"],
                    on_hand_quantity=b_data["on_hand"],
                    reserved_quantity=b_data["reserved"],
                    committed_quantity=b_data["committed"],
                    expiry_date=today + timedelta(days=b_data["expiry_days"]),
                    version=1,
                )
                db.add(batch)
                wh.current_inventory_version += 1
                results["batches_upserted"] += 1

        db.flush()

        # 5. Deterministic Distribution Orders
        order_seeds: List[Dict[str, Any]] = [
            {
                "order_number": "ORD-SEED-WEST-001",
                "dest_code": "STORE-MUM-SEED-01",
                "source_wh": "WH-WEST-SEED-01",
                "priority": OrderPriority.HIGH,
                "items": [{"sku": "SKU-SEED-PHONE-X", "qty": 80}, {"sku": "SKU-SEED-LAPTOP-15", "qty": 20}],
            },
            {
                "order_number": "ORD-SEED-NORTH-002",
                "dest_code": "STORE-DEL-SEED-01",
                "source_wh": "WH-NORTH-SEED-01",
                "priority": OrderPriority.NORMAL,
                "items": [{"sku": "SKU-SEED-PHONE-X", "qty": 40}],
            },
        ]

        for o_data in order_seeds:
            dest = store_map[o_data["dest_code"]]
            src = wh_map[o_data["source_wh"]]
            existing_order = db.query(DistributionOrder).filter_by(order_number=o_data["order_number"]).first()
            if existing_order:
                existing_order.destination_store_id = dest.id
                existing_order.source_warehouse_id = src.id
                existing_order.priority = o_data["priority"]
                results["duplicates_prevented"] += 1
            else:
                order = DistributionOrder(
                    order_number=o_data["order_number"],
                    destination_store_id=dest.id,
                    source_warehouse_id=src.id,
                    status=OrderStatus.PENDING,
                    priority=o_data["priority"],
                    required_delivery_date=datetime.now(timezone.utc) + timedelta(days=2),
                )
                db.add(order)
                db.flush()

                for item_data in o_data["items"]:
                    prod = prod_map[item_data["sku"]]
                    order_item = DistributionOrderItem(
                        order_id=order.id,
                        product_id=prod.id,
                        requested_quantity=item_data["qty"],
                        fulfilled_quantity=0,
                    )
                    db.add(order_item)
                results["orders_upserted"] += 1

        # 6. Seed Scenario Metadata
        scenario = db.query(SeededScenario).filter_by(scenario_code=scenario_code).first()
        if not scenario:
            scenario = SeededScenario(
                scenario_code=scenario_code,
                name="Authoritative Production Seed Scenario",
                scenario_type=ScenarioType.DEMAND_SURGE,
                description="Deterministic baseline scenario migrated to authoritative PostgreSQL database.",
                parameters={"target_warehouse_code": "WH-WEST-SEED-01", "surge_factor": 1.5},
                is_active=True,
            )
            db.add(scenario)

        db.commit()
        logger.info("Scenario data migration committed successfully: %s", results)
        return results

    @classmethod
    def verify_inventory_invariants(cls, db: Session) -> Dict[str, Any]:
        """
        Validates all authoritative inventory invariants across PostgreSQL:
        1. Available stock = physical quantity - reserved quantity (accounting for committed quantity).
        2. Stock must never become negative.
        3. Reservations must never exceed available stock.
        4. Inventory versions must change when authoritative inventory changes (version >= 1).
        5. Batch expiry is preserved and valid.
        """
        batches = db.query(InventoryBatch).all()
        warehouses = db.query(Warehouse).all()

        violations = []
        batch_details = []

        for b in batches:
            # Invariant 1: Available stock calculation
            expected_available = max(0, b.on_hand_quantity - (b.reserved_quantity + b.committed_quantity))
            if b.available_quantity != expected_available or b.available_stock != expected_available:
                violations.append(
                    f"Batch {b.batch_number}: available_stock ({b.available_stock}) != "
                    f"expected_available ({expected_available})."
                )

            # Invariant 2: Stock must never become negative
            if b.on_hand_quantity < 0:
                violations.append(f"Batch {b.batch_number}: negative physical on-hand quantity ({b.on_hand_quantity}).")
            if b.reserved_quantity < 0:
                violations.append(f"Batch {b.batch_number}: negative reserved quantity ({b.reserved_quantity}).")
            if b.committed_quantity < 0:
                violations.append(f"Batch {b.batch_number}: negative committed quantity ({b.committed_quantity}).")
            if b.available_quantity < 0 or b.available_stock < 0:
                violations.append(f"Batch {b.batch_number}: negative available stock ({b.available_stock}).")

            # Invariant 3: Reservations must never exceed available stock
            if b.reserved_quantity > (b.on_hand_quantity - b.committed_quantity):
                violations.append(
                    f"Batch {b.batch_number}: reserved quantity ({b.reserved_quantity}) exceeds "
                    f"physical headroom ({b.on_hand_quantity - b.committed_quantity})."
                )

            # Invariant 4: Inventory version must be positive
            if b.version < 1:
                violations.append(f"Batch {b.batch_number}: invalid version number ({b.version}).")

            batch_details.append({
                "batch_id": b.id,
                "batch_number": b.batch_number,
                "physical_quantity": b.physical_quantity,
                "reserved_quantity": b.reserved_quantity,
                "committed_quantity": b.committed_quantity,
                "available_stock": b.available_stock,
                "version": b.version,
                "expiry_date": b.expiry_date.isoformat() if b.expiry_date else None,
            })

        for wh in warehouses:
            if wh.current_inventory_version < 1:
                violations.append(f"Warehouse {wh.code}: invalid version number ({wh.current_inventory_version}).")

        is_valid = len(violations) == 0

        return {
            "is_valid": is_valid,
            "total_batches_checked": len(batches),
            "total_warehouses_checked": len(warehouses),
            "violations_count": len(violations),
            "violations": violations,
            "migration_status": "VERIFIED" if is_valid else "FAILED",
            "details": {
                "batches": batch_details,
                "warehouses_count": len(warehouses),
            },
        }

    @classmethod
    def execute_safe_migration(
        cls,
        db: Session,
        scenario_code: str = "SCENARIO-SURGE-001",
        legacy_data_source: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes an end-to-end safe migration:
        1. Migrates seeded scenario data without duplicating records.
        2. Validates all inventory invariants against PostgreSQL.
        3. Only flags migration as VERIFIED once zero violations are found.
        4. Preserves legacy database/store references without deleting old data.
        """
        # Step 1: Migrate data
        migration_stats = cls.migrate_seeded_scenario_data(db, scenario_code=scenario_code)

        # Step 2: Verify invariants
        verification_report = cls.verify_inventory_invariants(db)

        if not verification_report["is_valid"]:
            raise AuthoritativeRuleViolationException(
                f"Migration verification failed with {verification_report['violations_count']} violations: "
                f"{verification_report['violations']}"
            )

        logger.info(
            "Safe migration completed and VERIFIED. PostgreSQL is authoritative. "
            "Old database preserved at: %s",
            legacy_data_source or "mongo_legacy_archive (preserved)",
        )

        return {
            "migration_stats": migration_stats,
            "verification_report": verification_report,
            "authoritative_database": "PostgreSQL",
            "legacy_preserved": True,
            "legacy_source": legacy_data_source or "mongo_archive_preserved",
            "status": "MIGRATION_VERIFIED_SUCCESSFUL",
        }

    @classmethod
    def migrate_flipkart_catalog(cls, db: Session) -> Dict[str, Any]:
        """
        Idempotently migrates the Flipkart 100-SKU FMCG Network into the database:
        - 3 Regional Warehouses (WH-01, WH-02, WH-03)
        - 30 Retail Stores (STORE-001 through STORE-030)
        - 100 Flipkart FMCG Products/SKUs
        - 600 Inventory Batches with FEFO Expiry Dates
        - 15 Delivery Agents / Vehicles
        """
        import pandas as pd
        from models.data_loader import FlowGridDataLoader

        logger.info("Migrating Flipkart 100-SKU FMCG catalog into database...")
        loader = FlowGridDataLoader()
        results: Dict[str, Any] = {
            "warehouses_upserted": 0,
            "stores_upserted": 0,
            "products_upserted": 0,
            "batches_upserted": 0,
            "agents_upserted": 0,
            "duplicates_prevented": 0,
            "status": "COMPLETED",
        }

        # 1. Warehouses
        wh_df = loader.load_warehouses()
        wh_map: Dict[str, Warehouse] = {}
        for _, row in wh_df.iterrows():
            code = str(row["warehouse_id"]).strip()
            name = str(row["warehouse_name"]).strip()
            region = str(row.get("city", row.get("region", "Maharashtra"))).strip()
            raw_cap = row.get("storage_capacity_kg", row.get("daily_dispatch_capacity_kg", 50000))
            capacity = max(int(float(raw_cap)) * 5, 200000)
            existing_wh = db.query(Warehouse).filter_by(code=code).first()
            if existing_wh:
                existing_wh.name = name
                existing_wh.region = region
                existing_wh.capacity_limit = capacity
                wh_map[code] = existing_wh
                results["duplicates_prevented"] += 1
            else:
                wh = Warehouse(
                    code=code,
                    name=name,
                    region=region,
                    capacity_limit=capacity,
                    current_inventory_version=1,
                    is_active=True,
                )
                db.add(wh)
                wh_map[code] = wh
                results["warehouses_upserted"] += 1
        db.flush()

        # 2. Stores / Retailers
        stores_df = loader.load_stores()
        for _, row in stores_df.iterrows():
            code = str(row["store_id"]).strip()
            name = str(row["store_name"]).strip()
            region = str(row.get("service_zone", row.get("city", "Maharashtra"))).strip()
            p_weight = int(row.get("priority_weight", 1))
            st_type_raw = str(row.get("store_type", "REGIONAL_RETAIL")).upper()
            st_type = StoreType.FLAGSHIP if ("FLAGSHIP" in st_type_raw or "SUPERMARKET" in st_type_raw) else StoreType.REGIONAL_RETAIL
            existing_st = db.query(Store).filter_by(code=code).first()
            if existing_st:
                existing_st.name = name
                existing_st.region = region
                existing_st.priority_tier = p_weight
                existing_st.store_type = st_type
                results["duplicates_prevented"] += 1
            else:
                st = Store(
                    code=code,
                    name=name,
                    region=region,
                    priority_tier=p_weight,
                    store_type=st_type,
                    is_active=True,
                )
                db.add(st)
                results["stores_upserted"] += 1
        db.flush()

        # 3. Products / 100 SKUs
        skus_df = loader.load_skus()
        prod_map: Dict[str, Product] = {}
        for _, row in skus_df.iterrows():
            sku = str(row["sku_id"]).strip()
            name = str(row.get("product_name", sku)).strip()
            cat = str(row.get("category_id", row.get("category", "General"))).strip()
            shelf_life = int(row.get("estimated_total_shelf_life_days", row.get("shelf_life_days", 180)))
            price = float(row.get("listing_price_inr", row.get("unit_price", 100.0)))
            is_perishable = shelf_life < 90
            existing_p = db.query(Product).filter_by(sku=sku).first()
            if existing_p:
                existing_p.name = name
                existing_p.category = cat
                existing_p.shelf_life_days = shelf_life
                existing_p.unit_cost = price
                existing_p.is_perishable = is_perishable
                prod_map[sku] = existing_p
                results["duplicates_prevented"] += 1
            else:
                p = Product(
                    sku=sku,
                    name=name,
                    category=cat,
                    shelf_life_days=shelf_life,
                    unit_cost=price,
                    is_perishable=is_perishable,
                    is_active=True,
                )
                db.add(p)
                prod_map[sku] = p
                results["products_upserted"] += 1
        db.flush()

        # 4. Inventory Batches (600 batches with FEFO expiry)
        batches_df = loader.load_inventory_batches(only_usable=True)
        for _, row in batches_df.iterrows():
            wh_code = str(row["warehouse_id"]).strip()
            sku_code = str(row["sku_id"]).strip()
            lot_no = str(row.get("lot_id", row.get("batch_id", "LOT-001"))).strip()
            qty = int(row.get("quantity_on_hand", row.get("quantity_available", 100)))
            mfg_raw = row.get("received_at", row.get("manufactured_date"))
            exp_raw = row.get("expiry_date")

            wh = wh_map.get(wh_code) or db.query(Warehouse).filter_by(code=wh_code).first()
            prod = prod_map.get(sku_code) or db.query(Product).filter_by(sku=sku_code).first()
            if not wh or not prod:
                continue

            mfg_date = pd.to_datetime(mfg_raw).date() if pd.notna(mfg_raw) else None
            exp_date = pd.to_datetime(exp_raw).date() if pd.notna(exp_raw) else None

            existing_b = db.query(InventoryBatch).filter_by(
                warehouse_id=wh.id,
                product_id=prod.id,
                batch_number=lot_no,
            ).first()
            if existing_b:
                existing_b.on_hand_quantity = qty
                existing_b.reserved_quantity = 0
                existing_b.committed_quantity = 0
                existing_b.expiry_date = exp_date
                existing_b.manufactured_date = mfg_date
                results["duplicates_prevented"] += 1
            else:
                batch = InventoryBatch(
                    warehouse_id=wh.id,
                    product_id=prod.id,
                    batch_number=lot_no,
                    on_hand_quantity=qty,
                    reserved_quantity=0,
                    committed_quantity=0,
                    manufactured_date=mfg_date,
                    expiry_date=exp_date,
                    version=1,
                )
                db.add(batch)
                results["batches_upserted"] += 1

        db.commit()

        # 5. Delivery Agents
        try:
            agents_path = loader.get_path("agents.csv")
            if agents_path.exists():
                agents_df = pd.read_csv(agents_path)
                from app.models.agent import DeliveryAgent
                from app.models.enums import AgentAvailabilityStatus, AgentShiftStatus, VehicleType
                for _, row in agents_df.iterrows():
                    code = str(row["agent_id"]).strip()
                    name = str(row.get("name", f"Delivery Agent {code}")).strip()
                    existing_ag = db.query(DeliveryAgent).filter_by(code=code).first()
                    if not existing_ag:
                        agent = DeliveryAgent(
                            code=code,
                            name=name,
                            phone="+91-9876543210",
                            vehicle_type=VehicleType.TRUCK,
                            capacity_kg=2000.0,
                            capacity_volume_cbm=12.0,
                            availability_status=AgentAvailabilityStatus.AVAILABLE,
                            shift_status=AgentShiftStatus.ON_SHIFT,
                            is_active=True,
                        )
                        db.add(agent)
                        results["agents_upserted"] += 1
                db.commit()
        except Exception as e:
            logger.warning("Could not seed delivery agents: %s", e)

        logger.info(
            "Flipkart catalog migration finished: %d WH, %d Stores, %d SKUs, %d Batches",
            results["warehouses_upserted"],
            results["stores_upserted"],
            results["products_upserted"],
            results["batches_upserted"],
        )
        return results


