"""Generate a complete, null-free FlowGrid synthetic dataset and model-ready features.

Default scale: 30 stores x 100 Flipkart-listed SKUs x 365 days = 1,095,000 sales rows.
Public product-listing snapshots are mixed with clearly labelled synthetic demand and logistics data.
All synthetic operational values are labelled and must not be represented as real data.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
from generate_flowgrid_base import parse_args, generate  # noqa: E402
from prepare_model_features import build_features  # noqa: E402


def make_geojson_line(origin_lon: float, origin_lat: float, dest_lon: float, dest_lat: float) -> str:
    # This is a straight-line visualization only, not a road route or ETA geometry.
    geom = {
        "type": "LineString",
        "coordinates": [
            [round(float(origin_lon), 6), round(float(origin_lat), 6)],
            [round(float(dest_lon), 6), round(float(dest_lat), 6)],
        ],
    }
    return json.dumps(geom, separators=(",", ":"))


def enrich_data(data: dict[str, pd.DataFrame], args: Any) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(args.seed + 707)
    as_of_date = pd.Timestamp(args.as_of_date).normalize()
    snapshot = pd.Timestamp(f"{as_of_date.date().isoformat()}T08:00:00Z")

    # Ensure product economics are coherent: retail prices are at least 10% over unit cost.
    skus = data["skus.csv"].copy()
    skus["unit_price"] = [
        round(max(float(price), float(cost) * 1.10), 2)
        for price, cost in zip(skus["unit_price"], skus["unit_cost"])
    ]
    data["skus.csv"] = skus
    sku_prices = skus.set_index("sku_id")["unit_price"].to_dict()
    sales = data["synthetic_sales_daily.csv"].copy()
    sales["unit_price"] = sales["sku_id"].map(sku_prices).astype(float)
    sales["promo_feature_available"] = 1
    sales["unit_price_imputed"] = 0
    sales["anomaly_label_available"] = 1
    sales["data_origin"] = "synthetic_demo"
    data["synthetic_sales_daily.csv"] = sales

    # Consistent stock semantics: on-hand = reserved + available-to-promise.
    inventory = data["inventory_batches.csv"].copy()
    on_hand = pd.to_numeric(inventory["quantity_available"], errors="raise").astype(int).to_numpy()
    reservations = np.array([
        min(int(q) - 1, (i % 7) + 1) if i % 4 == 0 and int(q) > 1 else 0
        for i, q in enumerate(on_hand)
    ], dtype=int)
    inventory["quantity_on_hand"] = on_hand
    inventory["quantity_reserved"] = reservations
    inventory["quantity_available"] = on_hand - reservations
    inventory["data_origin"] = "synthetic_demo"
    data["inventory_batches.csv"] = inventory

    # Fill every route geometry with valid JSON; identify its limitation explicitly.
    warehouses = data["warehouses.csv"].set_index("warehouse_id").to_dict("index")
    stores = data["stores.csv"].set_index("store_id").to_dict("index")
    routes = data["route_estimates.csv"].copy()
    geometries = []
    for row in routes.itertuples(index=False):
        wh = warehouses[row.origin_warehouse_id]
        st = stores[row.destination_store_id]
        geometries.append(make_geojson_line(wh["longitude"], wh["latitude"], st["longitude"], st["latitude"]))
    routes["route_geometry"] = geometries
    routes["geometry_source"] = "SYNTHETIC_STRAIGHT_LINE"
    routes["data_origin"] = "synthetic_demo"
    data["route_estimates.csv"] = routes

    # Produce a coherent assignment table and align order, agent, and vehicle status.
    orders = data["orders.csv"].copy().reset_index(drop=True)
    order_lines = data["order_lines.csv"].copy()
    agents = data["agents.csv"].copy().reset_index(drop=True)
    vehicles = data["vehicles.csv"].copy().reset_index(drop=True)
    route_lookup = data["route_estimates.csv"].copy()
    route_lookup = route_lookup.sort_values(["travel_time_min", "estimated_cost"])

    # The simulated snapshot is 08:00 UTC. Orders were created before the snapshot;
    # deadlines remain in the future so the sample is viable for dispatch testing.
    created_times, deadlines = [], []
    for idx, row in orders.iterrows():
        created = snapshot - pd.Timedelta(minutes=int(10 + (idx % 5) * 18))
        urgency = str(row["urgency"])
        deadline = snapshot + pd.Timedelta(minutes=180 if urgency == "URGENT" else 480)
        created_times.append(created.strftime("%Y-%m-%dT%H:%M:%SZ"))
        deadlines.append(deadline.strftime("%Y-%m-%dT%H:%M:%SZ"))
    orders["created_at"] = created_times
    orders["deadline"] = deadlines
    orders["order_status"] = "PENDING"
    orders["data_origin"] = "synthetic_demo"

    # Set clean, semantically explicit defaults for agents before applying assignments.
    agents["current_assignment_id"] = "NONE"
    agents["availability_status"] = "AVAILABLE"
    agents["location_status"] = "FRESH"
    agents["last_seen_at"] = snapshot.strftime("%Y-%m-%dT%H:%M:%SZ")
    agents["location_accuracy_m"] = pd.to_numeric(agents["location_accuracy_m"], errors="coerce").fillna(15).clip(lower=1).astype(int)
    # Keep two agents intentionally unavailable for emergency-reassignment tests.
    unavailable_ids = {agents.iloc[i - 1]["agent_id"] for i in (6, 11) if len(agents) >= i}
    agents.loc[agents["agent_id"].isin(unavailable_ids), "availability_status"] = "UNAVAILABLE"
    agents.loc[agents["agent_id"].isin(unavailable_ids), "location_status"] = "STALE"

    vehicles["availability_status"] = "AVAILABLE"
    assignments: list[dict[str, Any]] = []
    assignment_count = min(5, len(orders), len(agents), len(vehicles))
    for i in range(assignment_count):
        order = orders.iloc[i]
        agent = agents.iloc[i]
        vehicle = vehicles.iloc[i]
        candidate_routes = route_lookup[route_lookup["destination_store_id"] == order["store_id"]]
        if candidate_routes.empty:
            raise ValueError(f"No route estimate for store {order['store_id']}")
        chosen_route = candidate_routes.iloc[0]
        assignment_id = f"ASN-{i + 1:03d}"
        arrival = snapshot + pd.Timedelta(minutes=int(chosen_route["travel_time_min"]))
        # If the first five orders include any urgent order, 180 minutes is ample
        # for this small synthetic region; check it rather than assuming silently.
        deadline = pd.to_datetime(order["deadline"], utc=True)
        if arrival > deadline:
            raise ValueError(f"Seed assignment {assignment_id} would miss its synthetic deadline.")
        assignments.append({
            "assignment_id": assignment_id,
            "order_id": order["order_id"],
            "agent_id": agent["agent_id"],
            "vehicle_id": vehicle["vehicle_id"],
            "origin_warehouse_id": chosen_route["origin_warehouse_id"],
            "destination_store_id": order["store_id"],
            "route_estimate_id": chosen_route["route_estimate_id"],
            "assignment_status": "ASSIGNED",
            "assigned_at": snapshot.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "estimated_arrival": arrival.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "assignment_source": "SEEDED_SCENARIO",
            "data_origin": "synthetic_demo",
        })
        orders.loc[orders["order_id"] == order["order_id"], "order_status"] = "ASSIGNED"
        agents.loc[agents["agent_id"] == agent["agent_id"], "availability_status"] = "BUSY"
        agents.loc[agents["agent_id"] == agent["agent_id"], "current_assignment_id"] = assignment_id
        agents.loc[agents["agent_id"] == agent["agent_id"], "location_status"] = "FRESH"
        vehicles.loc[vehicles["vehicle_id"] == vehicle["vehicle_id"], "availability_status"] = "BUSY"

    # Do not allow the unavailable agents to have active assignments.
    assigned_agent_ids = {a["agent_id"] for a in assignments}
    if assigned_agent_ids & unavailable_ids:
        raise ValueError("Unavailable agent must not have an active assignment.")
    data["orders.csv"] = orders
    data["order_lines.csv"] = order_lines
    data["agents.csv"] = agents.sort_values("agent_id").reset_index(drop=True)
    data["vehicles.csv"] = vehicles.sort_values("vehicle_id").reset_index(drop=True)
    data["assignments.csv"] = pd.DataFrame(assignments, columns=[
        "assignment_id", "order_id", "agent_id", "vehicle_id", "origin_warehouse_id",
        "destination_store_id", "route_estimate_id", "assignment_status", "assigned_at",
        "estimated_arrival", "assignment_source", "data_origin",
    ])

    # Every output cell must be populated. Optional states use explicit domain values,
    # never empty strings/NaN; geometries are real JSON strings even though simulated.
    for name, df in data.items():
        for col in df.columns:
            if pd.api.types.is_object_dtype(df[col]) or pd.api.types.is_string_dtype(df[col]):
                df[col] = df[col].replace(r"^\s*$", np.nan, regex=True)
                if df[col].isna().any():
                    raise ValueError(f"Unexpected blank/null values in {name}.{col}; fix generation at source.")
        if df.isna().any().any():
            cols = df.isna().sum()
            raise ValueError(f"Unexpected null values in {name}: {cols[cols > 0].to_dict()}")
        data[name] = df

    return data


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    if args.days < 90:
        raise SystemExit("For model-ready train/validation/test exports, --days must be at least 90.")
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_data = generate(args)
    data = enrich_data(raw_data, args)
    expected_sales_rows = args.base_stores * args.scale_factor * args.skus * args.days
    sales = data["synthetic_sales_daily.csv"]
    if len(sales) != expected_sales_rows:
        raise SystemExit(f"Expected {expected_sales_rows:,} sales rows but generated {len(sales):,}.")

    for filename, frame in data.items():
        frame.to_csv(output_dir / filename, index=False, na_rep="")

    model_dir = output_dir / "model_ready"
    model_counts = build_features(output_dir / "synthetic_sales_daily.csv", model_dir)

    manifest = {
        "dataset_name": "FlowGrid 100-SKU Flipkart Listing Snapshot + Synthetic Logistics Development Dataset",
        "product_catalog_source": "Public Flipkart category listing pages inspected on 2026-10-24; visible prices may differ by seller, PIN code, and time.",
        "product_catalog_note": "Names, brands, listing pack descriptions, and displayed listing-price snapshots are sourced from public category pages. Warehouse stock, unit cost, logistics weight/volume estimates, demand histories, expiry dates, coordinates, routes and order activity are synthetic or estimated.",
        "data_origin": "mixed_public_product_listing_snapshot_and_synthetic_operations",
        "scale_factor": args.scale_factor,
        "seed": args.seed,
        "as_of_date": args.as_of_date,
        "snapshot_at_utc": f"{args.as_of_date}T08:00:00Z",
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
        "product_catalog_rows": int(len(data["flipkart_product_catalog.csv"])),
        "product_catalog_categories": {
            str(category): int(count)
            for category, count in data["flipkart_product_catalog.csv"]["category_id"].value_counts().sort_index().items()
        },
        "product_catalog_source_document": "docs/flipkart_product_data_sources.md",
        "null_counts_by_file": {filename: int(frame.isna().sum().sum()) for filename, frame in data.items()},
        "model_ready_row_counts": model_counts,
        "model_ready_null_counts": {filename: 0 for filename in model_counts},
        "null_policy": "Every exported CSV is required to contain zero null/blank values. Optional state uses explicit sentinels such as NONE; synthetic geometry is populated GeoJSON and explicitly labelled.",
        "model_protocol": {
            "feature_warmup_days": 28,
            "split": "Chronological shared date split: final 28 dates=test; preceding 28 dates=validation; earlier dates=train.",
            "evaluation": "Rolling-origin one-step-ahead; target-day features must be known at prediction time. synthetic_anomaly_flag is diagnostic metadata and excluded from predictors.",
        },
        "limitations": [
            "The product master contains observed product-name/brand/pack/price snapshots from public Flipkart category listings; price availability can vary by seller, PIN code, and time.",
            "All generated demand, warehouse/stock, cost, logistics dimensions, batch expiry, coordinate, route, agent and order events are synthetic or estimates for development/testing only.",
            "Coordinates and route geometry are synthetic; route_geometry is a straight line, not a road network path.",
            "Route distance, travel time, and costs are simulated, not live Google Maps results.",
            "Synthetic sales are not evidence of real-world forecasting performance.",
            "SKU unit_price uses a listing-price snapshot; SKU unit_cost is synthetic and must not be treated as real procurement cost.",
        ],
    }
    (output_dir / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("\nGenerated FlowGrid model-ready dataset")
    print(f"Stores: {args.base_stores * args.scale_factor} | SKUs: {args.skus} | Days: {args.days}")
    print(f"Raw sales rows: {expected_sales_rows:,}")
    print("\nRaw tables:")
    for filename, frame in sorted(data.items()):
        print(f"  {filename:<30} {len(frame):>10,} rows | nulls={int(frame.isna().sum().sum())}")
    print("\nModel-ready tables:")
    for filename, count in model_counts.items():
        print(f"  {filename:<30} {count:>10,} rows")
    print(f"\nOutput: {output_dir}")


if __name__ == "__main__":
    main()
