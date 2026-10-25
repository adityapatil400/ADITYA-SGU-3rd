"""
Static Allocation Baseline for FlowGrid
Implements the PRD-mandated comparison baseline (PRD §13.3 & §12):
Allocates stock greedily to the nearest eligible warehouse with available stock,
using a fixed store priority rule without joint multi-warehouse global optimization.
"""

from typing import Dict, List, Tuple, Any
import numpy as np


class StaticAllocationBaseline:
    def __init__(self, transport_cost_per_km: float = 1.8, shortage_penalty_per_unit: float = 150.0):
        self.transport_cost_per_km = transport_cost_per_km
        self.shortage_penalty = shortage_penalty_per_unit

    def solve(
        self,
        warehouses: List[str],
        stores: List[str],
        skus: List[str],
        inventory: Dict[Tuple[str, str], int],
        demand: Dict[Tuple[str, str], int],
        distance_matrix: Dict[Tuple[str, str], float]
    ) -> Dict[str, Any]:
        """
        Executes static allocation baseline:
        - For each store (in fixed lexicographical order):
          - For each SKU:
            - Find the single nearest warehouse that has stock.
            - Greedily ship as much as possible up to demand.
            - If nearest warehouse runs out, remaining demand is left unmet or shipped suboptimally.
        """
        curr_inv = {k: max(0, int(v)) for k, v in inventory.items()}
        allocations = []
        unmet_list = []
        total_allocated = 0
        total_unmet = 0
        total_transport_cost = 0.0

        for s in stores:
            for k in skus:
                d_needed = demand.get((s, k), 0)
                if d_needed <= 0:
                    continue

                # Sort warehouses strictly by distance to this store
                sorted_by_dist = sorted(
                    warehouses,
                    key=lambda w: distance_matrix.get((w, s), 999.0)
                )

                # Static rule: strictly prioritize nearest warehouse
                fulfilled_for_sku = 0
                for w in sorted_by_dist:
                    avail = curr_inv.get((w, k), 0)
                    if avail > 0 and d_needed > 0:
                        ship = min(avail, d_needed)
                        curr_inv[(w, k)] -= ship
                        d_needed -= ship
                        fulfilled_for_sku += ship
                        dist = distance_matrix.get((w, s), 25.0)
                        allocations.append({
                            "warehouse_id": w,
                            "store_id": s,
                            "sku_id": k,
                            "allocated_quantity": int(ship),
                            "route_distance_km": dist,
                            "route_cost": round(dist * self.transport_cost_per_km, 2)
                        })
                        total_allocated += ship
                        total_transport_cost += dist * self.transport_cost_per_km

                if d_needed > 0:
                    unmet_list.append({
                        "store_id": s,
                        "sku_id": k,
                        "unmet_quantity": int(d_needed),
                        "requested_demand": demand.get((s, k), 0)
                    })
                    total_unmet += d_needed

        total_demand = sum(demand.values())
        fulfillment_rate = (total_allocated / total_demand * 100.0) if total_demand > 0 else 100.0

        # Objective = Transport Cost + (Unmet Units * Shortage Penalty)
        objective_value = total_transport_cost + (total_unmet * self.shortage_penalty)

        return {
            "strategy": "STATIC_NEAREST_ALLOCATION",
            "is_feasible": True,
            "allocations": allocations,
            "unmet_demand": unmet_list,
            "total_demand_units": int(total_demand),
            "total_allocated_units": int(total_allocated),
            "total_unmet_units": int(total_unmet),
            "fulfillment_rate_pct": round(fulfillment_rate, 2),
            "modeled_transport_cost": round(total_transport_cost, 2),
            "shortage_penalty_cost": round(total_unmet * self.shortage_penalty, 2),
            "total_objective_value": round(objective_value, 2),
            "violations_detected": 0
        }
