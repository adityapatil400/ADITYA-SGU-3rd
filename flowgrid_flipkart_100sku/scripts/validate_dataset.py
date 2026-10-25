"""Validate every FlowGrid CSV for nulls, schema, integrity, and ML readiness.

Exit code 0 means all configured structural/data checks passed. This does not
prove that synthetic data represents real demand or that a model is accurate.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_COLUMNS = {
    "synthetic_sales_daily.csv": ["date", "store_id", "sku_id", "units_sold", "unit_price", "weekday", "day_of_week", "month", "is_weekend", "synthetic_promo_flag", "synthetic_anomaly_flag", "promo_feature_available", "unit_price_imputed", "anomaly_label_available", "data_origin"],
    "warehouses.csv": ["warehouse_id", "warehouse_name", "latitude", "longitude", "storage_capacity_kg", "daily_dispatch_capacity_kg", "operating_status", "data_origin"],
    "stores.csv": ["store_id", "store_name", "source_store_id", "latitude", "longitude", "receiving_window_start", "receiving_window_end", "priority_weight", "service_zone", "operating_status", "location_data_status", "data_origin"],
    "flipkart_product_catalog.csv": ["sku_id", "product_name", "brand", "category_id", "pack_size_text", "listing_price_inr", "source_listing_url", "source_capture_date", "source_platform", "price_snapshot_status", "price_note", "logistics_weight_kg", "logistics_volume_l", "weight_basis", "logistics_specs_source", "estimated_total_shelf_life_days", "min_remaining_shelf_life_days", "storage_class", "catalog_data_status", "data_origin"],
    "skus.csv": ["sku_id", "source_item_id", "product_name", "brand", "category_id", "pack_size_text", "unit_of_measure", "unit_weight_kg", "unit_volume_l", "storage_class", "min_remaining_shelf_life_days", "estimated_total_shelf_life_days", "unit_cost", "unit_price", "listing_price_inr", "source_listing_url", "source_capture_date", "source_platform", "price_snapshot_status", "price_note", "logistics_specs_source", "unit_cost_source", "data_origin"],
    "inventory_batches.csv": ["batch_id", "warehouse_id", "sku_id", "lot_id", "quantity_on_hand", "quantity_available", "quantity_reserved", "unit_cost", "received_at", "expiry_date", "quality_status", "verification_status", "last_updated_at", "data_origin"],
    "vehicles.csv": ["vehicle_id", "vehicle_type", "capacity_kg", "capacity_volume_l", "fixed_cost_per_trip", "cost_per_km", "fuel_l_per_100km", "availability_status", "data_origin"],
    "agents.csv": ["agent_id", "display_name", "availability_status", "shift_start", "shift_end", "assigned_vehicle_id", "current_assignment_id", "last_seen_at", "location_accuracy_m", "location_status", "data_origin"],
    "route_estimates.csv": ["route_estimate_id", "origin_warehouse_id", "destination_store_id", "distance_km", "travel_time_min", "estimated_cost", "provider", "retrieved_at", "freshness_status", "route_geometry", "geometry_source", "data_origin"],
    "orders.csv": ["order_id", "store_id", "created_at", "deadline", "urgency", "order_status", "source", "idempotency_key", "data_origin"],
    "order_lines.csv": ["order_line_id", "order_id", "sku_id", "requested_quantity", "fulfilled_quantity", "unit_of_measure", "line_status", "data_origin"],
    "assignments.csv": ["assignment_id", "order_id", "agent_id", "vehicle_id", "origin_warehouse_id", "destination_store_id", "route_estimate_id", "assignment_status", "assigned_at", "estimated_arrival", "assignment_source", "data_origin"],
}
PKS = {
    "warehouses.csv": ["warehouse_id"], "stores.csv": ["store_id"], "flipkart_product_catalog.csv": ["sku_id"], "skus.csv": ["sku_id"],
    "inventory_batches.csv": ["batch_id"], "vehicles.csv": ["vehicle_id"], "agents.csv": ["agent_id"],
    "route_estimates.csv": ["route_estimate_id"], "orders.csv": ["order_id"],
    "order_lines.csv": ["order_line_id"], "assignments.csv": ["assignment_id"],
}
NUMERIC_NONNEGATIVE = {
    "flipkart_product_catalog.csv": ["listing_price_inr", "logistics_weight_kg", "logistics_volume_l", "estimated_total_shelf_life_days", "min_remaining_shelf_life_days"],
    "synthetic_sales_daily.csv": ["units_sold", "unit_price", "day_of_week", "month", "is_weekend", "synthetic_promo_flag", "synthetic_anomaly_flag"],
    "warehouses.csv": ["latitude", "longitude", "storage_capacity_kg", "daily_dispatch_capacity_kg"],
    "stores.csv": ["latitude", "longitude", "priority_weight"],
    "skus.csv": ["unit_weight_kg", "unit_volume_l", "min_remaining_shelf_life_days", "unit_cost", "unit_price"],
    "inventory_batches.csv": ["quantity_on_hand", "quantity_available", "quantity_reserved", "unit_cost"],
    "vehicles.csv": ["capacity_kg", "capacity_volume_l", "fixed_cost_per_trip", "cost_per_km", "fuel_l_per_100km"],
    "agents.csv": ["location_accuracy_m"],
    "route_estimates.csv": ["distance_km", "travel_time_min", "estimated_cost"],
    "order_lines.csv": ["requested_quantity", "fulfilled_quantity"],
}
POSITIVE = {
    "flipkart_product_catalog.csv": ["listing_price_inr", "logistics_weight_kg", "logistics_volume_l", "estimated_total_shelf_life_days"],
    "warehouses.csv": ["storage_capacity_kg", "daily_dispatch_capacity_kg"],
    "skus.csv": ["unit_weight_kg", "unit_volume_l", "unit_price"],
    "vehicles.csv": ["capacity_kg", "capacity_volume_l"],
    "route_estimates.csv": ["distance_km", "travel_time_min"],
    "order_lines.csv": ["requested_quantity"],
}
FK = [
    ("skus.csv", "sku_id", "flipkart_product_catalog.csv", "sku_id"),
    ("synthetic_sales_daily.csv", "store_id", "stores.csv", "store_id"),
    ("synthetic_sales_daily.csv", "sku_id", "skus.csv", "sku_id"),
    ("inventory_batches.csv", "warehouse_id", "warehouses.csv", "warehouse_id"),
    ("inventory_batches.csv", "sku_id", "skus.csv", "sku_id"),
    ("route_estimates.csv", "origin_warehouse_id", "warehouses.csv", "warehouse_id"),
    ("route_estimates.csv", "destination_store_id", "stores.csv", "store_id"),
    ("orders.csv", "store_id", "stores.csv", "store_id"),
    ("order_lines.csv", "order_id", "orders.csv", "order_id"),
    ("order_lines.csv", "sku_id", "skus.csv", "sku_id"),
    ("agents.csv", "assigned_vehicle_id", "vehicles.csv", "vehicle_id"),
    ("assignments.csv", "order_id", "orders.csv", "order_id"),
    ("assignments.csv", "agent_id", "agents.csv", "agent_id"),
    ("assignments.csv", "vehicle_id", "vehicles.csv", "vehicle_id"),
    ("assignments.csv", "origin_warehouse_id", "warehouses.csv", "warehouse_id"),
    ("assignments.csv", "destination_store_id", "stores.csv", "store_id"),
    ("assignments.csv", "route_estimate_id", "route_estimates.csv", "route_estimate_id"),
]


def validate(data_dir: Path) -> tuple[list[str], list[str], dict[str, int]]:
    errors: list[str] = []
    warnings: list[str] = []
    counts: dict[str, int] = {}
    frames: dict[str, pd.DataFrame] = {}

    manifest_path = data_dir / "dataset_manifest.json"
    if not manifest_path.exists():
        errors.append("dataset_manifest.json is missing.")
        manifest = {}
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"dataset_manifest.json is invalid JSON: {exc}")
            manifest = {}

    for name, required in REQUIRED_COLUMNS.items():
        path = data_dir / name
        if not path.exists():
            errors.append(f"Missing required file: {name}")
            continue
        try:
            df = pd.read_csv(path, keep_default_na=True)
        except Exception as exc:
            errors.append(f"Cannot read {name}: {exc}")
            continue
        frames[name] = df
        counts[name] = len(df)
        miss = sorted(set(required) - set(df.columns))
        if miss:
            errors.append(f"{name} missing required columns: {miss}")
            continue
        # Requirement: no nulls anywhere in exported CSVs, not just model columns.
        nulls = df.isna().sum()
        if (nulls > 0).any():
            errors.append(f"{name}: null values: {nulls[nulls > 0].to_dict()}")
        for col in df.select_dtypes(include=["object", "string"]).columns:
            blanks = df[col].astype(str).str.strip().eq("")
            if blanks.any():
                errors.append(f"{name}.{col}: {int(blanks.sum())} blank string value(s).")
                break
        for col in PKS.get(name, []):
            if col in df:
                if df[col].isna().any():
                    errors.append(f"{name}.{col}: primary key has nulls.")
                if df[col].duplicated().any():
                    errors.append(f"{name}.{col}: duplicate primary keys found.")

    if len(frames) != len(REQUIRED_COLUMNS):
        return errors, warnings, counts

    # Ensure every exported raw CSV, including any future extra CSV, has no nulls.
    for path in data_dir.rglob("*.csv"):
        try:
            df = pd.read_csv(path, keep_default_na=True)
        except Exception as exc:
            errors.append(f"Cannot read CSV {path.relative_to(data_dir)}: {exc}")
            continue
        nulls = df.isna().sum()
        if (nulls > 0).any():
            errors.append(f"{path.relative_to(data_dir)}: null values: {nulls[nulls > 0].to_dict()}")
        if any(df[col].astype(str).str.strip().eq("").any() for col in df.select_dtypes(include=["object", "string"]).columns):
            errors.append(f"{path.relative_to(data_dir)}: blank string values found.")

    # Numeric parsing, finite values and ranges.
    for file_name, cols in NUMERIC_NONNEGATIVE.items():
        df = frames[file_name]
        for col in cols:
            val = pd.to_numeric(df[col], errors="coerce")
            if val.isna().any() or not np.isfinite(val.to_numpy(dtype=float)).all():
                errors.append(f"{file_name}.{col}: nonnumeric, null, or nonfinite value.")
            elif (val < 0).any():
                errors.append(f"{file_name}.{col}: negative value found.")
    for file_name, cols in POSITIVE.items():
        for col in cols:
            if (pd.to_numeric(frames[file_name][col], errors="coerce") <= 0).any():
                errors.append(f"{file_name}.{col}: value must be > 0.")

    # Unique keys and foreign keys.
    for child_file, child_col, parent_file, parent_col in FK:
        child_vals = set(frames[child_file][child_col].astype(str))
        parent_vals = set(frames[parent_file][parent_col].astype(str))
        missing = sorted(child_vals - parent_vals)
        if missing:
            errors.append(f"Invalid FK {child_file}.{child_col} -> {parent_file}.{parent_col}: {missing[:5]}")

    # Sales panel completeness and calendar integrity.
    sales = frames["synthetic_sales_daily.csv"].copy()
    sales_dates = pd.to_datetime(sales["date"], errors="coerce")
    if sales_dates.isna().any():
        errors.append("Sales data contains invalid dates.")
    if sales.duplicated(["date", "store_id", "sku_id"]).any():
        errors.append("Duplicate date/store/SKU sales observations found.")
    if sales_dates.notna().all():
        expected = sales_dates.nunique() * sales["store_id"].nunique() * sales["sku_id"].nunique()
        if len(sales) != expected:
            errors.append(f"Incomplete sales panel: expected {expected:,} rows, found {len(sales):,}.")
        if not (sales["weekday"].astype(str).to_numpy() == sales_dates.dt.day_name().to_numpy()).all():
            errors.append("Sales weekday does not match date.")
        if not (pd.to_numeric(sales["day_of_week"]).to_numpy() == sales_dates.dt.dayofweek.to_numpy()).all():
            errors.append("Sales day_of_week does not match date.")
        if not (pd.to_numeric(sales["month"]).to_numpy() == sales_dates.dt.month.to_numpy()).all():
            errors.append("Sales month does not match date.")
        weekend = (sales_dates.dt.dayofweek >= 5).astype(int).to_numpy()
        if not (pd.to_numeric(sales["is_weekend"]).to_numpy() == weekend).all():
            errors.append("Sales is_weekend does not match date.")

    # Inventory logic.
    inventory = frames["inventory_batches.csv"]
    onhand = pd.to_numeric(inventory["quantity_on_hand"])
    reserved = pd.to_numeric(inventory["quantity_reserved"])
    available = pd.to_numeric(inventory["quantity_available"])
    if not (onhand == reserved + available).all():
        errors.append("Inventory quantity rule failed: quantity_on_hand must equal quantity_reserved + quantity_available.")
    if (reserved > onhand).any():
        errors.append("Inventory has reserved quantity greater than quantity_on_hand.")
    received = pd.to_datetime(inventory["received_at"], errors="coerce")
    expiry = pd.to_datetime(inventory["expiry_date"], errors="coerce")
    if (expiry <= received).any():
        errors.append("Inventory expiry must be later than received_at.")
    as_of = pd.to_datetime(manifest.get("as_of_date", "2026-10-24"))
    if (expiry <= as_of).any():
        warnings.append("Some inventory batches are expired at the dataset as_of_date; optimizer must exclude them or handle them explicitly.")

    # Product economics and coordinates.
    skus = frames["skus.csv"]
    if (pd.to_numeric(skus["unit_price"]) < pd.to_numeric(skus["unit_cost"])).any():
        warnings.append("Some SKU unit prices are below unit costs; inspect margins.")
    for f in ("warehouses.csv", "stores.csv"):
        df = frames[f]
        if ((pd.to_numeric(df["latitude"]) < -90) | (pd.to_numeric(df["latitude"]) > 90)).any():
            errors.append(f"{f}: invalid latitude.")
        if ((pd.to_numeric(df["longitude"]) < -180) | (pd.to_numeric(df["longitude"]) > 180)).any():
            errors.append(f"{f}: invalid longitude.")

    # Route geometry must be parseable GeoJSON, clearly labelled as synthetic.
    for row in frames["route_estimates.csv"].itertuples(index=False):
        try:
            geometry = json.loads(row.route_geometry)
            assert geometry.get("type") == "LineString"
            assert len(geometry.get("coordinates", [])) >= 2
            assert row.geometry_source == "SYNTHETIC_STRAIGHT_LINE"
        except Exception:
            errors.append(f"Invalid route geometry for {row.route_estimate_id}.")
            break

    # Order/assignment consistency.
    orders = frames["orders.csv"].copy()
    created = pd.to_datetime(orders["created_at"], errors="coerce", utc=True)
    deadline = pd.to_datetime(orders["deadline"], errors="coerce", utc=True)
    if created.isna().any() or deadline.isna().any() or (deadline <= created).any():
        errors.append("Orders have invalid timestamps or deadline <= created_at.")
    if orders["idempotency_key"].duplicated().any():
        errors.append("Orders contain duplicate idempotency keys.")
    lines = frames["order_lines.csv"]
    if (pd.to_numeric(lines["fulfilled_quantity"]) > pd.to_numeric(lines["requested_quantity"])).any():
        errors.append("Order line fulfilled_quantity exceeds requested_quantity.")
    assignments = frames["assignments.csv"]
    if assignments["order_id"].duplicated().any():
        errors.append("An order has more than one active assignment in assignments.csv.")
    if assignments["agent_id"].duplicated().any():
        errors.append("An agent has more than one active assignment in assignments.csv.")
    if assignments["vehicle_id"].duplicated().any():
        errors.append("A vehicle has more than one active assignment in assignments.csv.")
    assign_map = assignments.set_index("assignment_id")
    agents = frames["agents.csv"]
    for row in agents.itertuples(index=False):
        assignment_id = str(row.current_assignment_id)
        if assignment_id != "NONE":
            if assignment_id not in assign_map.index:
                errors.append(f"Agent {row.agent_id} points to missing assignment {assignment_id}.")
            elif assign_map.loc[assignment_id, "agent_id"] != row.agent_id:
                errors.append(f"Agent {row.agent_id} current_assignment_id points to another agent's assignment.")
            if row.availability_status != "BUSY":
                errors.append(f"Agent {row.agent_id} has an assignment but status is {row.availability_status}.")
        elif row.availability_status == "BUSY":
            errors.append(f"Agent {row.agent_id} is BUSY but current_assignment_id is NONE.")
    vehicle_status = frames["vehicles.csv"].set_index("vehicle_id")["availability_status"].to_dict()
    for row in assignments.itertuples(index=False):
        order_status = orders.loc[orders["order_id"] == row.order_id, "order_status"].iloc[0]
        if order_status != "ASSIGNED":
            errors.append(f"Assigned order {row.order_id} has status {order_status}, expected ASSIGNED.")
        if vehicle_status.get(row.vehicle_id) != "BUSY":
            errors.append(f"Assigned vehicle {row.vehicle_id} is not BUSY.")
        if pd.to_datetime(row.estimated_arrival, utc=True) > pd.to_datetime(orders.loc[orders["order_id"] == row.order_id, "deadline"].iloc[0], utc=True):
            errors.append(f"Assignment {row.assignment_id} arrives after order deadline.")

    # Public catalog facts are sourced from Flipkart listings; operational values are synthetic.
    for name, df in frames.items():
        if "data_origin" not in df:
            continue
        origin = df["data_origin"].astype(str)
        if name == "flipkart_product_catalog.csv":
            if not origin.eq("flipkart_public_listing_snapshot").all():
                errors.append("flipkart_product_catalog.csv: data_origin must be flipkart_public_listing_snapshot.")
            if not df["source_listing_url"].astype(str).str.contains(r"https?://(?:www\.)?flipkart\.com/", regex=True).all():
                errors.append("flipkart_product_catalog.csv: every source_listing_url must be a Flipkart URL.")
            if not df["product_name"].astype(str).str.strip().ne("").all():
                errors.append("flipkart_product_catalog.csv: product names cannot be blank.")
        elif not origin.str.contains("synthetic", case=False).all():
            errors.append(f"{name}: synthetic operational data must explicitly label data_origin as synthetic.")

    # Manifest counts.
    expected_counts = manifest.get("file_row_counts", {})
    for name, count in counts.items():
        if name in expected_counts and int(expected_counts[name]) != count:
            errors.append(f"Manifest row count mismatch for {name}: expected {expected_counts[name]}, got {count}.")

    # Model-ready feature exports must have no nulls and consistent split files.
    model_dir = data_dir / "model_ready"
    required_model_files = ["sales_features.csv", "train.csv", "validation.csv", "test.csv"]
    model_frames = {}
    for name in required_model_files:
        p = model_dir / name
        if not p.exists():
            errors.append(f"Missing model-ready file: model_ready/{name}")
            continue
        try:
            df = pd.read_csv(p)
            model_frames[name] = df
            if df.isna().any().any():
                cols = df.isna().sum()
                errors.append(f"model_ready/{name}: nulls {cols[cols > 0].to_dict()}")
            if df.empty:
                errors.append(f"model_ready/{name} is empty.")
            if "units_sold" not in df or "split" not in df:
                errors.append(f"model_ready/{name} needs units_sold and split columns.")
            if "date" in df:
                pd.to_datetime(df["date"], errors="raise")
        except Exception as exc:
            errors.append(f"Cannot read model_ready/{name}: {exc}")
    if len(model_frames) == len(required_model_files):
        features = model_frames["sales_features.csv"]
        if features.duplicated(["date", "store_id", "sku_id"]).any():
            errors.append("Model-ready features have duplicate date/store/SKU rows.")
        splitsets = {s: features.loc[features["split"] == s] for s in ("train", "validation", "test")}
        for split, filename in (("train", "train.csv"), ("validation", "validation.csv"), ("test", "test.csv")):
            expected_subset = splitsets[split].reset_index(drop=True)
            actual_subset = model_frames[filename].reset_index(drop=True)
            if len(expected_subset) != len(actual_subset) or set(expected_subset.columns) != set(actual_subset.columns):
                errors.append(f"model_ready/{filename} does not match split={split} in sales_features.csv.")
            elif len(expected_subset) and not expected_subset["date"].astype(str).equals(actual_subset["date"].astype(str)):
                errors.append(f"model_ready/{filename} dates do not match sales_features.csv split={split}.")
        if features["split"].nunique() != 3:
            errors.append("Model-ready features must contain train, validation and test splits.")
        numeric_cols = features.select_dtypes(include=[np.number]).columns
        if not np.isfinite(features[numeric_cols].to_numpy(dtype=float)).all():
            errors.append("Model-ready data contains non-finite numeric values.")
        if "synthetic_anomaly_flag" in features.columns:
            warnings.append("synthetic_anomaly_flag is present as a diagnostic column; exclude it from model predictors to avoid target-event leakage.")

    # Manifest model-ready counts if present.
    model_counts = manifest.get("model_ready_row_counts", {})
    for name, df in model_frames.items():
        if name in model_counts and int(model_counts[name]) != len(df):
            errors.append(f"Manifest model-ready row count mismatch for {name}.")

    return errors, warnings, counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data", help="Directory containing generated CSVs and dataset_manifest.json")
    args = parser.parse_args()
    directory = args.data_dir.resolve()
    if not directory.exists():
        raise SystemExit(f"Data directory does not exist: {directory}")
    errors, warnings, counts = validate(directory)
    print(f"FlowGrid model-ready dataset validation: {directory}")
    print("\nRaw table row counts:")
    for name, count in counts.items():
        print(f"  {name:<32} {count:>10,}")
    if warnings:
        print("\nWARNINGS:")
        for warning in sorted(set(warnings)):
            print(f"  - {warning}")
    if errors:
        print("\nVALIDATION FAILED:")
        for error in errors:
            print(f"  - {error}")
        raise SystemExit(1)
    print("\nVALIDATION PASSED: required tables and model-ready exports are structurally valid, non-null, and cross-table checks passed.")
    print("NOTE: structural validation does not prove model quality or real-world representativeness.")


if __name__ == "__main__":
    main()
