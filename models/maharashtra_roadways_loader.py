"""
Maharashtra Dynamic Delivery & Roadway Dataset Loader for FlowGrid
Inspects, loads, and joins the 16 relational CSV tables from:
FlowGrid_Maharashtra_Dataset/flowgrid_maharashtra_dynamic_delivery_dataset/data/

Tables managed:
1. locations.csv (33 Maharashtra district centres with coordinates & fuel prices)
2. warehouses.csv (4 regional DCs: Mumbai, Pune, Nagpur, Kolhapur)
3. route_options.csv (132 origin-destination corridor options)
4. road_corridor_references.csv (8 major National Highway & Expressway corridors)
5. vehicles.csv (230 synthetic delivery vehicles with capacities & km/L)
6. fuel_prices_daily.csv (2,970 daily city-level petrol & diesel prices)
7. road_conditions_daily.csv (11,880 route-day weather, ghat, and delay observations)
8. delivery_orders.csv (2,970 daily delivery shipments with fuel costs & mileage)
9. delivery_order_lines.csv (14,542 SKU line items with weight, volume, & price)
10. delivery_assignments.csv (2,970 baseline assignments with replan flags)
11. products_100sku.csv (100-SKU Flipkart companion product catalog)
12. operational_events.csv (359 synthetic disruption & weather events)
13. decision_guard_test_cases.csv (12 decision guard test fixtures)
"""

import os
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
import pandas as pd
import numpy as np


class MaharashtraRoadwaysLoader:
    def __init__(self, data_dir: Optional[str] = None):
        if data_dir:
            self.data_dir = Path(data_dir)
        else:
            candidates = [
                Path("FlowGrid_Maharashtra_Dataset/flowgrid_maharashtra_dynamic_delivery_dataset/data"),
                Path("../FlowGrid_Maharashtra_Dataset/flowgrid_maharashtra_dynamic_delivery_dataset/data"),
                Path("data"),
            ]
            self.data_dir = candidates[0]
            for c in candidates:
                if (c / "locations.csv").exists() and (c / "road_conditions_daily.csv").exists():
                    self.data_dir = c
                    break

        if not (self.data_dir / "locations.csv").exists():
            raise FileNotFoundError(
                f"Maharashtra dataset not found at {self.data_dir}. "
                "Ensure FlowGrid_Maharashtra_Dataset is extracted."
            )

    def get_path(self, filename: str) -> Path:
        return self.data_dir / filename

    def load_locations(self) -> pd.DataFrame:
        """33 Maharashtra district-centre locations with coordinates, terrain, and diesel prices."""
        return pd.read_csv(self.get_path("locations.csv"))

    def load_warehouses(self) -> pd.DataFrame:
        """4 regional distribution centres (Mumbai, Pune, Nagpur, Kolhapur)."""
        return pd.read_csv(self.get_path("warehouses.csv"))

    def load_corridors(self) -> pd.DataFrame:
        """8 reference road corridors (NH-48, NH-65, NH-60, NH-160, NH-66, NH-61, Samruddhi, Local)."""
        return pd.read_csv(self.get_path("road_corridor_references.csv"))

    def load_route_options(self) -> pd.DataFrame:
        """132 route options linking 4 warehouses to 33 destination districts."""
        return pd.read_csv(self.get_path("route_options.csv"))

    def load_vehicles(self) -> pd.DataFrame:
        """230 fleet vehicles with payload capacity (kg), fuel consumption (L/100km), and mileage (km/L)."""
        return pd.read_csv(self.get_path("vehicles.csv"))

    def load_fuel_prices_daily(self) -> pd.DataFrame:
        """2,970 daily city-level diesel and petrol prices."""
        df = pd.read_csv(self.get_path("fuel_prices_daily.csv"))
        df["date"] = pd.to_datetime(df["date"])
        return df

    def load_road_conditions_daily(self) -> pd.DataFrame:
        """11,880 daily route-level road, weather, ghat, and delay observations."""
        df = pd.read_csv(self.get_path("road_conditions_daily.csv"))
        df["date"] = pd.to_datetime(df["date"])
        return df

    def load_delivery_orders(self) -> pd.DataFrame:
        """2,970 delivery shipments with road distance, diesel price, and fuel expense."""
        df = pd.read_csv(self.get_path("delivery_orders.csv"))
        df["order_date"] = pd.to_datetime(df["order_date"])
        if "requested_delivery_date" in df.columns:
            df["requested_delivery_date"] = pd.to_datetime(df["requested_delivery_date"])
        return df

    def load_delivery_order_lines(self) -> pd.DataFrame:
        """14,542 SKU-level delivery order line items."""
        return pd.read_csv(self.get_path("delivery_order_lines.csv"))

    def load_delivery_assignments(self) -> pd.DataFrame:
        """2,970 baseline assignments with needs_optimizer_replan flags."""
        return pd.read_csv(self.get_path("delivery_assignments.csv"))

    def load_products_100sku(self) -> pd.DataFrame:
        """100-SKU product catalog with weights, volumes, prices, and shelf-life."""
        return pd.read_csv(self.get_path("products_100sku.csv"))

    def load_operational_events(self) -> pd.DataFrame:
        """359 synthetic disruption, road weather, and fuel price events."""
        df = pd.read_csv(self.get_path("operational_events.csv"))
        df["event_timestamp"] = pd.to_datetime(df["event_timestamp"])
        return df

    def load_decision_guard_test_cases(self) -> pd.DataFrame:
        """12 decision guard test cases for route safety, fuel price shocks, and capacity."""
        return pd.read_csv(self.get_path("decision_guard_test_cases.csv"))

    def build_road_risk_training_features(self) -> pd.DataFrame:
        """
        Joins road_conditions_daily with route_options, corridors, and locations to produce
        a rich tabular dataset for ML models predicting transit delays and road surface risk.
        Total rows: 11,880.
        """
        cond = self.load_road_conditions_daily()
        routes = self.load_route_options()
        corridors = self.load_corridors()
        locations = self.load_locations()

        # Merge route metadata
        merged = cond.merge(
            routes[[
                "route_id", "road_distance_km", "representative_corridor_id",
                "baseline_drive_time_minutes_est"
            ]],
            on="route_id",
            how="left"
        )

        # Merge corridor designations
        merged = merged.merge(
            corridors[["corridor_id", "road_designation", "road_class"]],
            left_on="representative_corridor_id",
            right_on="corridor_id",
            how="left"
        )

        # Merge destination location fuel prices and coordinates
        loc_sub = locations[[
            "location_id", "latitude", "longitude",
            "diesel_inr_per_litre_2026_10_24"
        ]].rename(columns={"diesel_inr_per_litre_2026_10_24": "dest_diesel_price_inr"})

        merged = merged.merge(
            loc_sub,
            left_on="destination_location_id",
            right_on="location_id",
            how="left"
        )

        # Feature engineering
        merged["is_ghat_route"] = (merged["terrain_class"].str.contains("ghat", case=False, na=False)).astype(int)
        merged["is_wet_condition"] = (merged["road_condition_scenario"].str.contains("WET|RAIN|WATER", case=False, na=False)).astype(int)
        merged["waterlogging_flag_int"] = merged["waterlogging_flag_scenario"].astype(int)
        merged["day_of_week"] = merged["date"].dt.day_of_week
        merged["month"] = merged["date"].dt.month

        # Impute missing distances with Haversine or safe median
        if "road_distance_km" in merged.columns:
            merged["road_distance_km"] = merged["road_distance_km"].fillna(merged["road_distance_km"].median())

        return merged

    def build_delivery_dispatch_training_features(self) -> pd.DataFrame:
        """
        Joins delivery_order_lines with delivery_orders, route_options, and products
        to produce line-level dispatch dataset for neural demand and freight forecasting.
        Total rows: 14,542.
        """
        orders = self.load_delivery_orders()
        lines = self.load_delivery_order_lines()
        routes = self.load_route_options()
        locations = self.load_locations()

        # Merge lines with orders
        merged = lines.merge(
            orders[[
                "delivery_order_id", "order_date", "destination_location_id",
                "destination_name", "destination_region", "warehouse_id",
                "vehicle_id", "vehicle_type", "urgency", "route_id",
                "road_distance_km_current", "vehicle_mileage_km_per_litre_assumed",
                "road_condition_scenario", "traffic_congestion_index_scenario",
                "route_delay_minutes_scenario", "origin_diesel_price_inr_per_litre",
                "fuel_cost_inr_est_one_way"
            ]],
            on="delivery_order_id",
            how="left"
        )

        # Merge route corridors
        merged = merged.merge(
            routes[["route_id", "representative_corridor_id", "baseline_drive_time_minutes_est"]],
            on="route_id",
            how="left"
        )

        # Merge location terrain
        merged = merged.merge(
            locations[["location_id", "terrain_class"]],
            left_on="destination_location_id",
            right_on="location_id",
            how="left"
        )

        # Encode temporal and interaction features
        merged["day_of_week"] = merged["order_date"].dt.day_of_week
        merged["is_urgent"] = (merged["urgency"] == "HIGH").astype(int)
        merged["is_ghat"] = (merged["terrain_class"].str.contains("ghat", case=False, na=False)).astype(int)
        merged["representative_corridor_id"] = merged["representative_corridor_id"].fillna("COR-LOCAL")

        return merged

    def get_network_topology(self) -> Dict[str, Any]:
        """
        Returns unified network topology for the OR-Tools optimization engine:
        - warehouses: ['W-MUM', 'W-PUN', 'W-NGP', 'W-KOL']
        - locations: 33 destination districts
        - distance_matrix: (warehouse_id, location_id) -> road_distance_km
        - fuel_rates: warehouse_id -> origin diesel price INR/L
        - corridors: (warehouse_id, location_id) -> corridor_id
        """
        routes = self.load_route_options()
        warehouses = self.load_warehouses()
        locations = self.load_locations()

        dist_matrix: Dict[Tuple[str, str], float] = {}
        corridor_matrix: Dict[Tuple[str, str], str] = {}
        drive_times: Dict[Tuple[str, str], float] = {}

        for _, row in routes.iterrows():
            w = str(row["warehouse_id"])
            loc = str(row["destination_location_id"])
            dist = float(row.get("road_distance_km", row.get("road_distance_km_initial_estimate", 100.0)))
            corr = str(row.get("representative_corridor_id", "COR-LOCAL"))
            time_est = float(row.get("baseline_drive_time_minutes_est", 120.0))

            dist_matrix[(w, loc)] = dist
            corridor_matrix[(w, loc)] = corr
            drive_times[(w, loc)] = time_est

        # Fuel prices by warehouse origin location
        wh_fuel: Dict[str, float] = {
            "W-MUM": 97.83,
            "W-PUN": 98.68,
            "W-NGP": 98.48,
            "W-KOL": 99.23
        }

        return {
            "warehouses": warehouses["warehouse_id"].tolist(),
            "locations": locations["location_id"].tolist(),
            "location_names": dict(zip(locations["location_id"], locations["location_name"])),
            "distance_matrix": dist_matrix,
            "corridor_matrix": corridor_matrix,
            "drive_time_matrix": drive_times,
            "origin_diesel_prices": wh_fuel,
            "num_warehouses": len(warehouses),
            "num_locations": len(locations),
            "num_routes": len(routes)
        }
