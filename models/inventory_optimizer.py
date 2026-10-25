"""
FlowGrid Inventory Optimizer: Net Replenishment, Safety Stock & Stockout Risk Engine
Fulfills Core Concept 2 of Problem Statement 2 (Distribution Intelligence):
- Computes store-level net replenishment: Net = max(0, Target Demand + Safety Stock - Current Stock - In-Transit)
- Evaluates dynamic safety stock using demand volatility (sigma) and lead time
- Categorizes inventory health: CRITICAL_STOCKOUT_RISK, LOW_STOCK, BALANCED, OVERSTOCKED_EXCESS
- Generates Before-vs-After stockout risk metrics
"""

from typing import Dict, List, Tuple, Any, Optional
import numpy as np
import pandas as pd


class InventoryOptimizer:
    """
    Computes store-level inventory positions, dynamic safety stocks,
    net replenishment requirements, and stockout risk profiles.
    """

    def __init__(
        self,
        service_level_z: float = 1.65,  # 1.65 = 95% service level, 2.05 = 98%
        default_lead_time_days: float = 1.5,
        min_safety_stock_units: int = 5
    ):
        self.z = service_level_z
        self.default_lead_time = default_lead_time_days
        self.min_safety_stock = min_safety_stock_units

    def compute_dynamic_safety_stock(
        self,
        daily_demand_std: float,
        lead_time_days: Optional[float] = None
    ) -> int:
        """
        SS = Z * sigma_demand * sqrt(Lead_Time)
        """
        lt = lead_time_days if lead_time_days is not None else self.default_lead_time
        ss = self.z * max(1.0, daily_demand_std) * np.sqrt(max(0.5, lt))
        return max(self.min_safety_stock, int(np.ceil(ss)))

    def calculate_net_replenishment(
        self,
        forecasted_demand: Dict[Tuple[str, str], int],        # (store_id, sku_id) -> predicted demand
        current_store_stock: Dict[Tuple[str, str], int],      # (store_id, sku_id) -> current on-hand
        demand_volatilities: Optional[Dict[Tuple[str, str], float]] = None,
        in_transit_stock: Optional[Dict[Tuple[str, str], int]] = None,
        lead_times: Optional[Dict[str, float]] = None         # store_id -> lead time days
    ) -> Dict[str, Any]:
        """
        Evaluates store-level stock status and calculates net replenishment.
        
        Example from Problem Statement:
        Store B in Kolhapur:
          - Demand: 300 packets
          - Current Stock: 50 packets
          - Target Stock: 300 packets (including safety buffer)
          - Net Replenishment Needed: max(0, 300 - 50) = 250 packets
        """
        demand_volatilities = demand_volatilities or {}
        in_transit_stock = in_transit_stock or {}
        lead_times = lead_times or {}

        replenishment_plan = []
        net_demand_dict = {}

        total_forecasted = 0
        total_current_stock = 0
        total_net_required = 0
        stockout_at_risk_count = 0
        overstocked_count = 0

        for (store_id, sku_id), f_demand in forecasted_demand.items():
            f_demand = max(0, int(f_demand))
            c_stock = max(0, int(current_store_stock.get((store_id, sku_id), 0)))
            transit = max(0, int(in_transit_stock.get((store_id, sku_id), 0)))
            sigma = demand_volatilities.get((store_id, sku_id), max(2.0, f_demand * 0.25))
            lt = lead_times.get(store_id, self.default_lead_time)

            # Dynamic safety stock
            safety_stock = self.compute_dynamic_safety_stock(sigma, lt)
            target_stock = f_demand + safety_stock

            # Effective available inventory at store
            effective_stock = c_stock + transit

            # Net Replenishment formula: max(0, target_stock - effective_stock)
            if effective_stock >= target_stock:
                net_qty = 0
            else:
                net_qty = target_stock - effective_stock

            # Inventory Health Classification
            if effective_stock <= safety_stock * 0.4:
                status = "CRITICAL_STOCKOUT_RISK"
                stockout_at_risk_count += 1
            elif effective_stock < target_stock:
                status = "LOW_STOCK"
                stockout_at_risk_count += 1
            elif effective_stock > target_stock * 1.5:
                status = "OVERSTOCKED_EXCESS"
                overstocked_count += 1
            else:
                status = "BALANCED"

            # Days of Supply remaining before replenishment
            daily_run_rate = max(1.0, f_demand / 7.0)  # weekly to daily
            days_of_supply = round(c_stock / daily_run_rate, 1)

            entry = {
                "store_id": store_id,
                "sku_id": sku_id,
                "forecasted_demand": f_demand,
                "current_stock": c_stock,
                "in_transit": transit,
                "safety_stock": safety_stock,
                "target_stock": target_stock,
                "net_replenishment_needed": int(net_qty),
                "days_of_supply": days_of_supply,
                "stock_status": status
            }
            replenishment_plan.append(entry)
            net_demand_dict[(store_id, sku_id)] = int(net_qty)

            total_forecasted += f_demand
            total_current_stock += c_stock
            total_net_required += net_qty

        total_pairs = len(forecasted_demand)
        risk_percentage = round((stockout_at_risk_count / total_pairs * 100.0), 1) if total_pairs > 0 else 0.0

        return {
            "store_sku_positions": replenishment_plan,
            "net_replenishment_dict": net_demand_dict,
            "summary": {
                "total_store_sku_pairs": total_pairs,
                "total_forecasted_demand": total_forecasted,
                "total_current_store_stock": total_current_stock,
                "total_net_replenishment_needed": total_net_required,
                "stockout_at_risk_count": stockout_at_risk_count,
                "overstocked_count": overstocked_count,
                "pre_replenishment_stockout_risk_pct": risk_percentage
            }
        }

    def simulate_store_current_stocks(
        self,
        forecasted_demand: Dict[Tuple[str, str], int],
        stockout_prob: float = 0.35,
        overstock_prob: float = 0.20,
        seed: int = 42
    ) -> Dict[Tuple[str, str], int]:
        """
        Generates realistic store-level stock distributions:
        - 35% stores running critically low (stockout imminent)
        - 20% stores overstocked (excess inventory)
        - 45% stores in normal operational band
        """
        np.random.seed(seed)
        store_stocks = {}

        for (s, k), d_val in forecasted_demand.items():
            rand_val = np.random.uniform(0.0, 1.0)
            if rand_val < stockout_prob:
                # Understocked / near stockout (e.g. 5% to 30% of demand)
                c_stock = int(np.random.uniform(0.05, 0.30) * d_val)
            elif rand_val < stockout_prob + overstock_prob:
                # Overstocked (e.g. 150% to 220% of demand)
                c_stock = int(np.random.uniform(1.5, 2.2) * d_val)
            else:
                # Normal operational buffer (e.g. 50% to 90% of demand)
                c_stock = int(np.random.uniform(0.5, 0.9) * d_val)

            store_stocks[(s, k)] = max(0, c_stock)

        return store_stocks
