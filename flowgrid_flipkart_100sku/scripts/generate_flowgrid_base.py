"""Generate a reproducible, scalable synthetic FlowGrid dataset.

Default generation scales the original 10-store × 20-SKU prototype to 30 stores and expands the product catalog to 100 SKUs:
30 stores × 100 Flipkart-listed grocery SKUs × 365 days = 1,095,000 sales rows (15× the original 73,000-row smoke dataset).

All generated values are synthetic. They are suitable for development and
integration tests, not as evidence of real-world forecast or cost performance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "data"
DEFAULT_AS_OF_DATE = "2026-10-24"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DATA_DIR,
                        help="Directory where CSV files will be written (default: ./data)")
    parser.add_argument("--scale-factor", type=int, default=3,
                        help="Scale stores, agents, vehicles, and orders from the baseline. Default: 3")
    parser.add_argument("--base-stores", type=int, default=10,
                        help="Baseline store count. Default: 10")
    parser.add_argument("--skus", type=int, default=100,
                        help="Number of products to use from the product catalog. Default: 100")
    parser.add_argument("--product-catalog", type=Path, default=DEFAULT_DATA_DIR / "flipkart_product_catalog.csv",
                        help="CSV product catalog based on observed public Flipkart category listings; logistics fields are estimated.")
    parser.add_argument("--warehouses", type=int, default=3,
                        help="Warehouse count. Default: 3")
    parser.add_argument("--days", type=int, default=365,
                        help="Daily sales history length. Default: 365")
    parser.add_argument("--base-agents", type=int, default=5,
                        help="Baseline agent count, multiplied by scale factor. Default: 5")
    parser.add_argument("--base-vehicles", type=int, default=5,
                        help="Baseline vehicle count, multiplied by scale factor. Default: 5")
    parser.add_argument("--base-orders", type=int, default=20,
                        help="Baseline order count, multiplied by scale factor. Default: 20")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility. Default: 42")
    parser.add_argument("--as-of-date", default=DEFAULT_AS_OF_DATE,
                        help=f"Reference date in YYYY-MM-DD format. Default: {DEFAULT_AS_OF_DATE}")
    return parser.parse_args()


def make_id(prefix: str, number: int, width: int = 3) -> str:
    return f"{prefix}-{number:0{width}d}"


def generate(args: argparse.Namespace) -> dict[str, pd.DataFrame]:
    if min(args.scale_factor, args.base_stores, args.skus, args.warehouses,
           args.days, args.base_agents, args.base_vehicles, args.base_orders) < 1:
        raise ValueError("All counts and scale-factor must be positive integers.")
    try:
        as_of = pd.Timestamp(args.as_of_date).normalize()
    except Exception as exc:
        raise ValueError("--as-of-date must be a valid date in YYYY-MM-DD format.") from exc

    rng = np.random.default_rng(args.seed)
    store_count = args.base_stores * args.scale_factor
    agent_count = args.base_agents * args.scale_factor
    vehicle_count = args.base_vehicles * args.scale_factor
    order_count = args.base_orders * args.scale_factor
    output: dict[str, pd.DataFrame] = {}

    # Synthetic coordinates are generated for mapping/algorithm tests only.
    warehouses: list[dict[str, Any]] = []
    for i in range(1, args.warehouses + 1):
        warehouses.append({
            "warehouse_id": make_id("WH", i, 2),
            "warehouse_name": f"Demo Warehouse {i:02d}",
            "latitude": round(18.47 + i * 0.045, 6),
            "longitude": round(73.76 + i * 0.038, 6),
            "storage_capacity_kg": int(rng.integers(12000, 24001)),
            "daily_dispatch_capacity_kg": int(rng.integers(2500, 6001)),
            "operating_status": "ACTIVE",
            "data_origin": "synthetic_demo",
        })
    warehouses_df = pd.DataFrame(warehouses)
    output["warehouses.csv"] = warehouses_df

    stores: list[dict[str, Any]] = []
    for i in range(1, store_count + 1):
        # Spread synthetic coordinates on a grid around a fictional demo region.
        row, col = divmod(i - 1, 6)
        stores.append({
            "store_id": make_id("STORE", i),
            "store_name": f"Demo Store {i:03d}",
            "source_store_id": f"SYNTH-STORE-{i:03d}",
            "latitude": round(18.48 + row * 0.012 + float(rng.uniform(-0.003, 0.003)), 6),
            "longitude": round(73.78 + col * 0.014 + float(rng.uniform(-0.003, 0.003)), 6),
            "receiving_window_start": "08:00",
            "receiving_window_end": "18:00",
            "priority_weight": int(rng.integers(1, 4)),
            "service_zone": make_id("ZONE", ((i - 1) // 6) + 1, 2),
            "operating_status": "ACTIVE",
            "location_data_status": "SYNTHETIC",
            "data_origin": "synthetic_demo",
        })
    stores_df = pd.DataFrame(stores)
    output["stores.csv"] = stores_df

    # Product master comes from visible public Flipkart category-listing snapshots.
    # Listing price/name/pack description are observed catalog fields; transport
    # dimensions, procurement cost and inventory/sales behavior remain simulated.
    catalog_path = args.product_catalog.expanduser().resolve()
    if not catalog_path.exists():
        raise FileNotFoundError(f"Product catalog not found: {catalog_path}")
    catalog = pd.read_csv(catalog_path, keep_default_na=False)
    required_catalog = {
        "sku_id", "product_name", "brand", "category_id", "pack_size_text",
        "listing_price_inr", "source_listing_url", "source_capture_date",
        "logistics_weight_kg", "logistics_volume_l", "min_remaining_shelf_life_days",
        "estimated_total_shelf_life_days", "storage_class", "logistics_specs_source",
        "data_origin"
    }
    missing_catalog = sorted(required_catalog - set(catalog.columns))
    if missing_catalog:
        raise ValueError(f"Product catalog is missing columns: {missing_catalog}")
    if len(catalog) < args.skus:
        raise ValueError(f"Requested {args.skus} SKUs, but catalog contains only {len(catalog)} products.")
    catalog = catalog.head(args.skus).copy()
    source_catalog = catalog.copy()  # preserve public listing fields separately from synthetic procurement economics
    catalog["listing_price_inr"] = pd.to_numeric(catalog["listing_price_inr"], errors="raise")
    if catalog["sku_id"].duplicated().any() or catalog["product_name"].eq("").any():
        raise ValueError("Catalog must have unique SKU IDs and non-empty product names.")
    if (catalog["listing_price_inr"] <= 0).any():
        raise ValueError("Every product must have a positive listing-price snapshot.")
    catalog["unit_cost"] = [round(float(price) * float(rng.uniform(0.68, 0.88)), 2)
                             for price in catalog["listing_price_inr"]]
    catalog["unit_price"] = catalog["listing_price_inr"].round(2)
    catalog["source_item_id"] = catalog["sku_id"].map(lambda x: f"FLIPKART-{x}")
    catalog["unit_of_measure"] = "pack"
    catalog["unit_weight_kg"] = pd.to_numeric(catalog["logistics_weight_kg"], errors="raise").round(3)
    catalog["unit_volume_l"] = pd.to_numeric(catalog["logistics_volume_l"], errors="raise").round(3)
    catalog["category_id"] = catalog["category_id"].astype(str)
    catalog["min_remaining_shelf_life_days"] = pd.to_numeric(catalog["min_remaining_shelf_life_days"], errors="raise").astype(int)
    catalog["estimated_total_shelf_life_days"] = pd.to_numeric(catalog["estimated_total_shelf_life_days"], errors="raise").astype(int)
    # Keep the product catalog and SKU table linked by the same stable SKU IDs.
    output["flipkart_product_catalog.csv"] = source_catalog.copy()
    sku_fields = [
        "sku_id", "source_item_id", "product_name", "brand", "category_id",
        "pack_size_text", "unit_of_measure", "unit_weight_kg", "unit_volume_l",
        "storage_class", "min_remaining_shelf_life_days", "estimated_total_shelf_life_days",
        "unit_cost", "unit_price", "listing_price_inr", "source_listing_url",
        "source_capture_date", "source_platform", "price_snapshot_status", "price_note",
        "logistics_specs_source", "data_origin"
    ]
    for col in ("source_platform", "price_snapshot_status", "price_note"):
        if col not in catalog.columns:
            catalog[col] = "NOT_PROVIDED"
    skus_df = catalog[sku_fields].copy()
    skus_df["unit_cost_source"] = "SYNTHETIC_ESTIMATE"
    skus_df["data_origin"] = "flipkart_listing_snapshot_plus_synthetic_logistics"
    output["skus.csv"] = skus_df
    skus = skus_df.to_dict("records")

    # Two batches per warehouse/SKU. The generated dates are fictional test values.
    batches: list[dict[str, Any]] = []
    for wh in warehouses:
        for sku in skus:
            for lot in range(1, 3):
                received = as_of - pd.Timedelta(days=int(rng.integers(5, 120)))
                shelf_total = max(60, int(sku.get("estimated_total_shelf_life_days", 365)))
                min_remaining = max(1, int(sku.get("min_remaining_shelf_life_days", 14)))
                low = min(min_remaining, shelf_total)
                # Remaining shelf life is synthetic; the product catalog's pack facts
                # do not provide batch-level expiry dates.
                days_to_expiry = int(rng.integers(low, shelf_total + 1))
                expiry = as_of + pd.Timedelta(days=days_to_expiry)
                available = int(rng.integers(10, 180))
                batches.append({
                    "batch_id": f"{wh['warehouse_id']}-{sku['sku_id']}-LOT{lot}",
                    "warehouse_id": wh["warehouse_id"],
                    "sku_id": sku["sku_id"],
                    "lot_id": f"LOT-{lot}",
                    "quantity_available": available,
                    "quantity_reserved": 0,
                    "unit_cost": sku["unit_cost"],
                    "received_at": received.date().isoformat(),
                    "expiry_date": expiry.date().isoformat(),
                    "quality_status": "USABLE",
                    "verification_status": "SYNTHETIC",
                    "last_updated_at": as_of.strftime("%Y-%m-%dT08:00:00Z"),
                    "data_origin": "synthetic_demo",
                })
    output["inventory_batches.csv"] = pd.DataFrame(batches)

    vehicles: list[dict[str, Any]] = []
    for i in range(1, vehicle_count + 1):
        vehicles.append({
            "vehicle_id": make_id("VEH", i),
            "vehicle_type": "VAN" if i % 3 else "LIGHT_TRUCK",
            "capacity_kg": int(rng.choice([500, 750, 1000, 1500, 2000])),
            "capacity_volume_l": int(rng.choice([2500, 4000, 6000, 8000])),
            "fixed_cost_per_trip": round(float(rng.uniform(100, 250)), 2),
            "cost_per_km": round(float(rng.uniform(5, 12)), 2),
            "fuel_l_per_100km": round(float(rng.uniform(8, 14)), 2),
            "availability_status": "BUSY" if i % 7 == 0 else "AVAILABLE",
            "data_origin": "synthetic_demo",
        })
    output["vehicles.csv"] = pd.DataFrame(vehicles)

    agents: list[dict[str, Any]] = []
    for i in range(1, agent_count + 1):
        vehicle_id = make_id("VEH", ((i - 1) % vehicle_count) + 1)
        status = "UNAVAILABLE" if i % 11 == 0 else ("BUSY" if i % 5 == 0 else "AVAILABLE")
        agents.append({
            "agent_id": make_id("AGENT", i),
            "display_name": f"Demo Agent {i:03d}",
            "availability_status": status,
            "shift_start": "08:00",
            "shift_end": "18:00",
            "assigned_vehicle_id": vehicle_id,
            "current_assignment_id": "" if status == "AVAILABLE" else f"SIM-ASSIGN-{i:03d}",
            "last_seen_at": as_of.strftime("%Y-%m-%dT08:00:00Z"),
            "location_accuracy_m": int(rng.integers(5, 61)),
            "location_status": "FRESH" if i % 9 else "STALE",
            "data_origin": "synthetic_demo",
        })
    output["agents.csv"] = pd.DataFrame(agents)

    # All warehouse/store pairs have one synthetic route estimate.
    routes: list[dict[str, Any]] = []
    for wh in warehouses:
        for store in stores:
            lat_diff = (wh["latitude"] - store["latitude"]) * 111.0
            lon_diff = (wh["longitude"] - store["longitude"]) * 104.5
            straight_km = max(1.0, float(np.sqrt(lat_diff**2 + lon_diff**2)))
            road_factor = float(rng.uniform(1.15, 1.75))
            distance_km = round(straight_km * road_factor, 2)
            speed_kmh = float(rng.uniform(22, 38))
            travel_minutes = int(np.ceil(distance_km / speed_kmh * 60 + rng.integers(5, 21)))
            routes.append({
                "route_estimate_id": f"{wh['warehouse_id']}-{store['store_id']}",
                "origin_warehouse_id": wh["warehouse_id"],
                "destination_store_id": store["store_id"],
                "distance_km": distance_km,
                "travel_time_min": travel_minutes,
                "estimated_cost": round(100 + distance_km * 8.5, 2),
                "provider": "SYNTHETIC_ESTIMATE",
                "retrieved_at": as_of.strftime("%Y-%m-%dT08:00:00Z"),
                "freshness_status": "SIMULATED",
                "route_geometry": "",
                "data_origin": "synthetic_demo",
            })
    output["route_estimates.csv"] = pd.DataFrame(routes)

    orders: list[dict[str, Any]] = []
    order_lines: list[dict[str, Any]] = []
    for i in range(1, order_count + 1):
        store = stores[(i - 1) % len(stores)]
        created = as_of + pd.Timedelta(minutes=int(rng.integers(0, 360)))
        urgency = "URGENT" if i % 7 == 0 or i == 1 else "NORMAL"
        deadline = created + pd.Timedelta(hours=2 if urgency == "URGENT" else 8)
        order_id = make_id("ORD", i)
        orders.append({
            "order_id": order_id,
            "store_id": store["store_id"],
            "created_at": created.isoformat() + "Z",
            "deadline": deadline.isoformat() + "Z",
            "urgency": urgency,
            "order_status": "PENDING",
            "source": "synthetic_demo",
            "idempotency_key": f"order-create-{i:05d}",
            "data_origin": "synthetic_demo",
        })
        selected_skus = rng.choice(args.skus, size=min(2, args.skus), replace=False)
        for line_no, sku_idx in enumerate(sorted(int(x) for x in selected_skus), start=1):
            order_lines.append({
                "order_line_id": f"{order_id}-LINE-{line_no:02d}",
                "order_id": order_id,
                "sku_id": skus[sku_idx]["sku_id"],
                "requested_quantity": int(rng.integers(5, 46)),
                "fulfilled_quantity": 0,
                "unit_of_measure": "unit",
                "line_status": "OPEN",
                "data_origin": "synthetic_demo",
            })
    output["orders.csv"] = pd.DataFrame(orders)
    output["order_lines.csv"] = pd.DataFrame(order_lines)

    # Vectorized allocation of output columns keeps million-row generation practical
    # on a 16 GB laptop. Sales are entirely synthetic, even for observed product names.
    dates = pd.date_range(end=as_of - pd.Timedelta(days=1), periods=args.days, freq="D")
    date_strings = dates.strftime("%Y-%m-%d").to_numpy(dtype=object)
    series = [(store, sku) for store in stores for sku in skus]
    n_series = len(series)
    n_rows = n_series * args.days
    store_ids = np.repeat(np.array([st["store_id"] for st, _ in series], dtype=object), args.days)
    sku_ids = np.repeat(np.array([sku["sku_id"] for _, sku in series], dtype=object), args.days)
    date_values = np.tile(date_strings, n_series)
    units = np.empty(n_rows, dtype=np.int32)
    promos = np.empty(n_rows, dtype=np.int8)
    anomalies = np.empty(n_rows, dtype=np.int8)
    sku_prices = np.repeat(np.array([float(sku["unit_price"]) for _, sku in series], dtype=np.float32), args.days)
    weekday_multiplier = np.array([1.00, 0.96, 0.98, 1.02, 1.10, 1.22, 1.18], dtype=float)
    trend = 1.0 + 0.0005 * np.arange(args.days, dtype=float)
    store_scale = {st["store_id"]: float(rng.uniform(0.7, 1.5)) for st in stores}
    sku_scale: dict[str, float] = {}
    for series_idx, (store, sku) in enumerate(series):
        begin = series_idx * args.days
        stop = begin + args.days
        store_factor = store_scale.get(store["store_id"])
        if store_factor is None:
            store_factor = float(rng.uniform(0.7, 1.5))
            store_scale[store["store_id"]] = store_factor
        if sku["sku_id"] not in sku_scale:
            # Product-level consumption scale is simulated; price is the listing snapshot.
            category_factor = {
                "EDIBLE_OIL": 0.55, "PULSES": 0.8, "SALT": 0.6, "DAIRY_POWDER": 0.45,
                "BREAKFAST_CEREAL": 0.5, "RICE": 0.95, "TEA": 0.7,
                "NOODLES_SAUCES": 0.65, "HONEY": 0.25,
            }.get(str(sku["category_id"]), 0.6)
            price_factor = float(np.clip(240.0 / max(float(sku["unit_price"]), 1.0), 0.35, 1.8))
            sku_scale[sku["sku_id"]] = float(rng.uniform(2.0, 35.0)) * category_factor * price_factor
        base = sku_scale[sku["sku_id"]] * store_factor
        promo = (rng.random(args.days) < 0.06).astype(np.int8)
        lam = np.maximum(0.1, base * weekday_multiplier[dates.dayofweek] * trend * np.where(promo == 1, 1.25, 1.0))
        sold = rng.poisson(lam).astype(np.int32)
        anomaly = (rng.random(args.days) < 0.005).astype(np.int8)
        if anomaly.any():
            sold[anomaly == 1] = np.rint(sold[anomaly == 1] * rng.uniform(1.8, 3.0, int(anomaly.sum()))).astype(np.int32)
        units[begin:stop] = sold
        promos[begin:stop] = promo
        anomalies[begin:stop] = anomaly
    output["synthetic_sales_daily.csv"] = pd.DataFrame({
        "date": date_values,
        "store_id": store_ids,
        "sku_id": sku_ids,
        "units_sold": units,
        "unit_price": sku_prices,
        "weekday": np.tile(dates.day_name().to_numpy(dtype=object), n_series),
        "day_of_week": np.tile(dates.dayofweek.to_numpy(dtype=np.int8), n_series),
        "month": np.tile(dates.month.to_numpy(dtype=np.int8), n_series),
        "is_weekend": np.tile((dates.dayofweek >= 5).astype(np.int8), n_series),
        "synthetic_promo_flag": promos,
        "synthetic_anomaly_flag": anomalies,
        "data_origin": np.full(n_rows, "synthetic_sales_generated_for_flipkart_catalog", dtype=object),
    })
    return output


def validate_dataset(data: dict[str, pd.DataFrame], expected_sales_rows: int) -> list[str]:
    errors: list[str] = []
    sales = data["synthetic_sales_daily.csv"]
    if len(sales) != expected_sales_rows:
        errors.append(f"Expected {expected_sales_rows:,} sales rows, got {len(sales):,}.")
    if sales.duplicated(["date", "store_id", "sku_id"]).any():
        errors.append("Duplicate date/store_id/sku_id sales observations found.")
    if (sales["units_sold"] < 0).any():
        errors.append("Negative units_sold value found.")
    for child_name, parent_name, child_key, parent_key in [
        ("inventory_batches.csv", "warehouses.csv", "warehouse_id", "warehouse_id"),
        ("inventory_batches.csv", "skus.csv", "sku_id", "sku_id"),
        ("route_estimates.csv", "warehouses.csv", "origin_warehouse_id", "warehouse_id"),
        ("route_estimates.csv", "stores.csv", "destination_store_id", "store_id"),
        ("orders.csv", "stores.csv", "store_id", "store_id"),
        ("order_lines.csv", "orders.csv", "order_id", "order_id"),
        ("order_lines.csv", "skus.csv", "sku_id", "sku_id"),
        ("agents.csv", "vehicles.csv", "assigned_vehicle_id", "vehicle_id"),
    ]:
        child_values = set(data[child_name][child_key].astype(str))
        parent_values = set(data[parent_name][parent_key].astype(str))
        if not child_values.issubset(parent_values):
            errors.append(f"Foreign-key check failed: {child_name}.{child_key} -> {parent_name}.{parent_key}.")
    if (data["inventory_batches.csv"]["quantity_available"] < 0).any():
        errors.append("Negative available inventory found.")
    return errors


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    data = generate(args)
    expected_sales_rows = args.base_stores * args.scale_factor * args.skus * args.days
    errors = validate_dataset(data, expected_sales_rows)
    if errors:
        raise SystemExit("Dataset validation failed:\n- " + "\n- ".join(errors))
    for filename, frame in data.items():
        frame.to_csv(output_dir / filename, index=False)
    manifest = {
        "dataset_name": "FlowGrid 100-SKU Flipkart Listing Snapshot + Synthetic Logistics Development Dataset",
        "data_origin": "mixed_public_product_listing_snapshot_and_synthetic_operations",
        "scale_factor": args.scale_factor,
        "seed": args.seed,
        "as_of_date": args.as_of_date,
        "dimensions": {
            "stores": args.base_stores * args.scale_factor,
            "skus": args.skus,
            "warehouses": args.warehouses,
            "days": args.days,
            "agents": args.base_agents * args.scale_factor,
            "vehicles": args.base_vehicles * args.scale_factor,
            "orders": args.base_orders * args.scale_factor,
        },
        "expected_sales_rows": expected_sales_rows,
        "file_row_counts": {filename: int(len(frame)) for filename, frame in data.items()},
        "limitations": [
            "Synthetic values are for development and integration tests only.",
            "Synthetic route estimates are not live road routes or ETAs.",
            "Synthetic sales are not evidence of real-world forecasting performance."
        ]
    }
    (output_dir / "dataset_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print("\nGenerated and validated FlowGrid dataset")
    print(f"Scale factor: {args.scale_factor}x (stores scale; SKU count and history length remain configurable)")
    print(f"Stores: {args.base_stores * args.scale_factor} | SKUs: {args.skus} | Days: {args.days}")
    print(f"Sales rows: {expected_sales_rows:,}")
    print(f"Output directory: {output_dir}")
    print("\nCSV row counts:")
    for filename, frame in sorted(data.items()):
        print(f"  {filename:<30} {len(frame):>9,} rows")
    print("\nNOTE: All generated records are synthetic and are for development/testing only.")


if __name__ == "__main__":
    main()
