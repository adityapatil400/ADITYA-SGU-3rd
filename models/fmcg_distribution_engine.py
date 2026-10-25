"""
FlowGrid FMCG Distribution Engine: Master AI Intelligence Orchestrator
Problem Statement 2 (SGU-TRIWIZATHON)

Unifies the 4 Core Concepts:
1. Demand Forecasting: Estimates future demand using trained ML / DeepDemandNet models.
2. Inventory Optimization: Calculates dynamic safety stocks & net replenishment requirements.
3. Distribution Optimization: Google OR-Tools CP-SAT multi-warehouse constrained dispatch.
4. Shelf-Life and Expiry Management: FEFO (First-Expired, First-Out) shelf-life optimization.

Delivers the 4 Hackathon Modules:
1. Inventory Dashboard (Warehouse stock levels & Store inventory positions)
2. Demand Prediction (Store-level forecasts)
3. Distribution Recommendation Engine (Product, From, To, Quantity, Estimated Cost, Reason)
4. Optimization Dashboard (Before vs After: Stockout Risk, Transport Cost, Expiry Wastage)
"""

import os
from typing import Dict, List, Tuple, Any, Optional
import pandas as pd
import numpy as np

from .data_loader import FlowGridDataLoader
from .inventory_optimizer import InventoryOptimizer
from .stock_optimizer import DistributionOptimizer
from .weekly_quantile_forecaster import WeeklyQuantileForecaster
from .ollama_intelligence import OllamaIntelligenceAdapter


class FMCGDistributionEngine:
    def __init__(
        self,
        data_loader: Optional[FlowGridDataLoader] = None,
        models_dir: str = "saved_models",
        ollama_model: str = "llama3"
    ):
        self.loader = data_loader or FlowGridDataLoader()
        self.models_dir = models_dir
        self.inventory_optimizer = InventoryOptimizer(service_level_z=1.65)
        self.distribution_optimizer = DistributionOptimizer(
            transport_cost_per_km=1.8,
            shortage_penalty_per_unit=150.0,
            vehicle_capacity_kg=2000.0,
            time_limit_seconds=6.0
        )
        self.ollama = OllamaIntelligenceAdapter(model=ollama_model)
        self.weekly_forecast_report = None

    def run_distribution_intelligence(
        self,
        top_n_skus: int = 25,
        num_stores: int = 15,
        use_net_replenishment: bool = True,
        simulated_stock_seed: int = 42
    ) -> Dict[str, Any]:
        """
        Executes the complete 4-step AI Supply Chain pipeline:
        Step 1: Ingest Relational Network (Warehouses, Stores, SKUs, Batches, Routes)
        Step 2: Generate Demand Predictions (Module 2)
        Step 3: Calculate Store Net Replenishment & Inventory Health (Module 1)
        Step 4: Solve Constrained FEFO Allocation & Generate Recommended Plan (Modules 3 & 4)
        """
        # Step 1: Ingest Network Topology
        net = self.loader.build_network_inputs(top_n_skus=top_n_skus)
        warehouses = net["warehouses"]
        stores = net["stores"][:num_stores]
        skus = net["skus"]
        inventory = net["inventory"]
        distances = net["distances"]
        sku_weights = net["sku_weights"]
        sku_volumes = net["sku_volumes"]
        sku_names = net["sku_names"]
        sku_categories = net["sku_categories"]
        store_priorities = net["store_priorities"]

        # Ingest usable inventory batches with expiry dates for FEFO
        batches_df = self.loader.load_inventory_batches(only_usable=True)
        batches_records = batches_df[
            (batches_df["warehouse_id"].isin(warehouses)) &
            (batches_df["sku_id"].isin(skus))
        ].to_dict(orient="records")

        # Step 2: Forecast Demand per (Store, SKU)
        forecasted_demand = self._generate_demand_forecast(stores, skus)

        # Step 3: Inventory Optimization (Net Replenishment & Safety Stock)
        # Simulate / Ingest current store on-hand stock
        store_current_stocks = self.inventory_optimizer.simulate_store_current_stocks(
            forecasted_demand=forecasted_demand,
            stockout_prob=0.35,
            overstock_prob=0.20,
            seed=simulated_stock_seed
        )

        inventory_analysis = self.inventory_optimizer.calculate_net_replenishment(
            forecasted_demand=forecasted_demand,
            current_store_stock=store_current_stocks
        )

        # Determine effective demand for the distribution solver
        if use_net_replenishment:
            solver_demand = inventory_analysis.get("net_replenishment_dict", forecasted_demand)
        else:
            solver_demand = forecasted_demand

        # Step 4: Distribution Optimization & FEFO Batch Allocation
        opt_solution = self.distribution_optimizer.solve_allocation(
            warehouses=warehouses,
            stores=stores,
            skus=skus,
            inventory=inventory,
            demand=solver_demand,
            distance_matrix=distances,
            sku_weights=sku_weights,
            sku_volumes=sku_volumes,
            sku_names=sku_names,
            sku_categories=sku_categories,
            store_priorities=store_priorities,
            inventory_batches=batches_records,
            store_current_stocks=store_current_stocks
        )

        # Synthesize the 4 Core Modules
        inventory_dashboard = self._build_inventory_dashboard(
            inventory_analysis=inventory_analysis,
            warehouse_inventory=inventory,
            sku_names=sku_names,
            warehouses=warehouses
        )

        demand_prediction_summary = {
            "total_forecasted_units": sum(forecasted_demand.values()),
            "total_net_replenishment_units": sum(solver_demand.values()),
            "store_sku_forecasts_count": len(forecasted_demand),
            "forecasting_model_used": "PRD weekly quantile forecaster (q50; 7-day demand)",
            "forecast_cycle_start": str(pd.Timestamp((self.weekly_forecast_report or {}).get("history_end")).date() + pd.Timedelta(days=1)) if self.weekly_forecast_report else None,
            "forecast_holdout_accuracy": (self.weekly_forecast_report or {}).get("accuracy", {}),
            "forecasts": [
                {"retailerId": store, "sku": sku, **self.weekly_forecast_by_pair[(str(store), str(sku))]}
                for store, sku in forecasted_demand
                if (str(store), str(sku)) in self.weekly_forecast_by_pair
            ]
        }

        recommendations_table = opt_solution.get("distribution_recommendations", [])
        impact_dashboard = opt_solution.get("impact_metrics", {})

        # Step 5: Ollama Output Optimization & Key Features Extraction
        ollama_health = self.ollama.check_health()
        ollama_output = self.ollama.optimize_distribution_output(
            distribution_recommendations=recommendations_table,
            impact_metrics=impact_dashboard
        )
        optimized_recommendations = ollama_output.get("optimized_recommendations", recommendations_table)

        # Extract Key Causal Drivers for critical understocked stores
        key_feature_insights = []
        if inventory_analysis.get("store_sku_positions"):
            critical_positions = [
                p for p in inventory_analysis["store_sku_positions"]
                if p["stock_status"] in ("CRITICAL_STOCKOUT_RISK", "LOW_STOCK")
            ][:3]
            for cp in critical_positions:
                feat = self.ollama.extract_key_features(
                    store_id=cp["store_id"],
                    sku_id=cp["sku_id"],
                    product_name=sku_names.get(cp["sku_id"], cp["sku_id"]),
                    feature_data={
                        "units_sold": cp["forecasted_demand"],
                        "current_stock": cp["current_stock"],
                        "days_to_expiry": 35
                    }
                )
                key_feature_insights.append(feat)

        return {
            "engine_status": "SUCCESS",
            "module_1_inventory_dashboard": inventory_dashboard,
            "module_2_demand_prediction": demand_prediction_summary,
            "module_3_distribution_recommendations": optimized_recommendations,
            "module_4_optimization_dashboard": impact_dashboard,
            "ollama_intelligence": {
                "health": ollama_health,
                "engine_status": ollama_output.get("llm_engine"),
                "executive_briefing": ollama_output.get("executive_dispatch_briefing"),
                "key_feature_insights": key_feature_insights
            },
            "solver_summary": {
                "status": opt_solution["solver_status"],
                "total_demand_units": opt_solution["total_demand_units"],
                "total_allocated_units": opt_solution["total_allocated_units"],
                "total_unmet_units": opt_solution["total_unmet_units"],
                "fulfillment_rate_pct": opt_solution["fulfillment_rate_pct"],
                "modeled_transport_cost": opt_solution["modeled_transport_cost"],
                "wall_time_seconds": opt_solution["wall_time_seconds"],
                "violations_detected": opt_solution["violations_detected"]
            }
        }

    def _generate_demand_forecast(
        self,
        stores: List[str],
        skus: List[str]
    ) -> Dict[Tuple[str, str], int]:
        """Uses the PRD model's median seven-day total for each retailer/SKU."""
        import json
        from pathlib import Path

        if self.weekly_forecast_report is None:
            sales = self.loader.load_sales_history()
            report_path = Path(self.models_dir) / "weekly_forecast_report.json"
            report = None
            if report_path.exists():
                try:
                    cached = json.loads(report_path.read_text(encoding="utf-8"))
                    if (cached.get("model_version") == "weekly-quantile-v1"
                            and cached.get("history_end") == str(pd.to_datetime(sales["date"]).max().date())):
                        report = cached
                except (OSError, ValueError, TypeError):
                    report = None
            if report is None:
                report = WeeklyQuantileForecaster().forecast(sales)
                WeeklyQuantileForecaster.save(report, str(report_path))
            self.weekly_forecast_report = report

        by_pair = {
            (str(row["retailerId"]), str(row["sku"])): row
            for row in self.weekly_forecast_report.get("forecasts", [])
        }
        self.weekly_forecast_by_pair = by_pair
        return {
            (store, sku): int(by_pair.get((str(store), str(sku)), {}).get("q50", 0))
            for store in stores for sku in skus
        }

    def _build_inventory_dashboard(
        self,
        inventory_analysis: Dict[str, Any],
        warehouse_inventory: Dict[Tuple[str, str], int],
        sku_names: Dict[str, str],
        warehouses: List[str]
    ) -> Dict[str, Any]:
        """Constructs Module 1: Inventory Dashboard."""
        positions = inventory_analysis["store_sku_positions"]
        summary = inventory_analysis["summary"]

        warehouse_totals = {}
        for (w, k), qty in warehouse_inventory.items():
            warehouse_totals[w] = warehouse_totals.get(w, 0) + qty

        critical_stores = [
            p for p in positions if p["stock_status"] == "CRITICAL_STOCKOUT_RISK"
        ]
        overstocked_stores = [
            p for p in positions if p["stock_status"] == "OVERSTOCKED_EXCESS"
        ]

        return {
            "warehouse_stocks": warehouse_totals,
            "total_warehouse_stock": sum(warehouse_totals.values()),
            "store_summary": summary,
            "critical_stockout_alerts": [
                {
                    "store_id": p["store_id"],
                    "sku_id": p["sku_id"],
                    "product": sku_names.get(p["sku_id"], p["sku_id"]),
                    "current_stock": p["current_stock"],
                    "forecasted_demand": p["forecasted_demand"],
                    "safety_stock": p["safety_stock"],
                    "net_needed": p["net_replenishment_needed"],
                    "days_of_supply": p["days_of_supply"]
                }
                for p in critical_stores[:10]
            ],
            "overstock_alerts": [
                {
                    "store_id": p["store_id"],
                    "sku_id": p["sku_id"],
                    "product": sku_names.get(p["sku_id"], p["sku_id"]),
                    "current_stock": p["current_stock"],
                    "forecasted_demand": p["forecasted_demand"],
                    "target_stock": p["target_stock"]
                }
                for p in overstocked_stores[:5]
            ]
        }
