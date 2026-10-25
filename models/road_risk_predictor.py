"""
Road Risk & Transit Delay Predictor for FlowGrid
Trained on Maharashtra Roadways & Dynamic Delivery Dataset (11,880 route-day observations).

Features:
1. LightGBM Regressor: High-accuracy transit delay prediction (minutes) considering rainfall,
   visibility, potholes/surface risk, traffic congestion, and ghat topography.
2. Random Forest Classifier: Multi-class road hazard risk severity (low / medium / high).
3. Benchmark against Corridor Baseline Heuristic with WAPE and R2 audit metrics.
4. Actionable operational outputs: transit delay, dynamic route surcharge, and replanning alerts.
"""

import os
import joblib
from typing import Dict, List, Tuple, Any, Optional
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import mean_absolute_error, r2_score, accuracy_score
import lightgbm as lgb


class RoadRiskDelayPredictor:
    def __init__(self, models_dir: str = "saved_models"):
        self.models_dir = models_dir
        os.makedirs(models_dir, exist_ok=True)

        self.delay_regressor: Optional[lgb.LGBMRegressor] = None
        self.hazard_classifier: Optional[RandomForestClassifier] = None

        self.feature_columns: List[str] = [
            "rainfall_mm_scenario",
            "visibility_km_scenario",
            "pothole_or_surface_risk_index_scenario",
            "waterlogging_flag_int",
            "traffic_congestion_index_scenario",
            "road_distance_km",
            "baseline_drive_time_minutes_est",
            "is_ghat_route",
            "is_wet_condition",
            "day_of_week",
            "terrain_encoded",
            "region_encoded",
            "corridor_encoded"
        ]

        self.terrain_map: Dict[str, int] = {}
        self.region_map: Dict[str, int] = {}
        self.corridor_map: Dict[str, int] = {}
        self.metrics: Dict[str, Any] = {}

    def _encode_categoricals(self, df: pd.DataFrame, fit: bool = False) -> pd.DataFrame:
        df = df.copy()
        if fit:
            terrains = sorted(df["terrain_class"].dropna().unique().tolist())
            self.terrain_map = {t: idx for idx, t in enumerate(terrains)}

            regions = sorted(df["environment_region"].dropna().unique().tolist())
            self.region_map = {r: idx for idx, r in enumerate(regions)}

            corridors = sorted(df["representative_corridor_id"].dropna().unique().tolist())
            self.corridor_map = {c: idx for idx, c in enumerate(corridors)}

        df["terrain_encoded"] = df["terrain_class"].map(self.terrain_map).fillna(0).astype(int)
        df["region_encoded"] = df["environment_region"].map(self.region_map).fillna(0).astype(int)
        df["corridor_encoded"] = df["representative_corridor_id"].map(self.corridor_map).fillna(0).astype(int)
        return df

    def train(self, df_features: pd.DataFrame, test_size: float = 0.2) -> Dict[str, Any]:
        """
        Trains LightGBM delay regressor and Random Forest hazard classifier on 11,880 route records.
        Strict chronological train-validation split (PRD FR-P0-01 zero leakage).
        """
        df_encoded = self._encode_categoricals(df_features, fit=True)

        # Sort chronologically
        if "date" in df_encoded.columns:
            df_encoded = df_encoded.sort_values("date").reset_index(drop=True)

        n = len(df_encoded)
        split_idx = int(n * (1.0 - test_size))
        train_df = df_encoded.iloc[:split_idx]
        val_df = df_encoded.iloc[split_idx:]

        X_train = train_df[self.feature_columns].fillna(0)
        y_delay_train = train_df["estimated_weather_traffic_delay_minutes"].fillna(0)
        y_sev_train = train_df["risk_severity_scenario"].fillna("low")

        X_val = val_df[self.feature_columns].fillna(0)
        y_delay_val = val_df["estimated_weather_traffic_delay_minutes"].fillna(0)
        y_sev_val = val_df["risk_severity_scenario"].fillna("low")

        # 1. Train LightGBM Delay Regressor
        self.delay_regressor = lgb.LGBMRegressor(
            n_estimators=120,
            learning_rate=0.06,
            max_depth=6,
            num_leaves=31,
            random_state=42,
            verbose=-1
        )
        self.delay_regressor.fit(X_train, y_delay_train)

        # 2. Train Random Forest Hazard Classifier
        self.hazard_classifier = RandomForestClassifier(
            n_estimators=80,
            max_depth=8,
            random_state=42,
            n_jobs=-1
        )
        self.hazard_classifier.fit(X_train, y_sev_train)

        # Evaluation
        pred_delay_val = np.maximum(0.0, self.delay_regressor.predict(X_val))
        pred_sev_val = self.hazard_classifier.predict(X_val)

        # Baseline: Corridor Average Delay
        corridor_means = train_df.groupby("representative_corridor_id")["estimated_weather_traffic_delay_minutes"].mean().to_dict()
        default_mean = y_delay_train.mean()
        baseline_preds = val_df["representative_corridor_id"].map(corridor_means).fillna(default_mean).values

        # Metrics
        lgbm_mae = float(mean_absolute_error(y_delay_val, pred_delay_val))
        base_mae = float(mean_absolute_error(y_delay_val, baseline_preds))
        lgbm_r2 = float(r2_score(y_delay_val, pred_delay_val))

        total_actual_delay = float(np.sum(y_delay_val))
        lgbm_wape = float(np.sum(np.abs(y_delay_val - pred_delay_val)) / max(1.0, total_actual_delay))
        base_wape = float(np.sum(np.abs(y_delay_val - baseline_preds)) / max(1.0, total_actual_delay))
        rel_improvement = float((base_wape - lgbm_wape) / max(1e-5, base_wape) * 100.0)

        classifier_acc = float(accuracy_score(y_sev_val, pred_sev_val))

        self.metrics = {
            "samples_train": len(X_train),
            "samples_val": len(X_val),
            "lgbm_delay_mae_minutes": round(lgbm_mae, 2),
            "baseline_delay_mae_minutes": round(base_mae, 2),
            "lgbm_r2_score": round(lgbm_r2, 4),
            "lgbm_delay_wape_percent": round(lgbm_wape * 100, 2),
            "baseline_delay_wape_percent": round(base_wape * 100, 2),
            "relative_wape_improvement_percent": round(rel_improvement, 2),
            "hazard_classification_accuracy_percent": round(classifier_acc * 100, 2),
            "meets_prd_5pct_gate": rel_improvement >= 5.0
        }

        self.save()
        return self.metrics

    def predict_route_risk(
        self,
        route_id: str,
        corridor_id: str,
        terrain_class: str,
        environment_region: str,
        road_distance_km: float,
        baseline_drive_time_minutes: float,
        rainfall_mm: float = 0.0,
        visibility_km: float = 9.0,
        traffic_congestion_index: float = 0.1,
        pothole_or_surface_risk_index: float = 0.2,
        waterlogging_flag: bool = False,
        day_of_week: int = 2
    ) -> Dict[str, Any]:
        """Predicts transit delay and hazard severity for a specific road corridor."""
        is_ghat = 1 if "ghat" in str(terrain_class).lower() else 0
        is_wet = 1 if rainfall_mm > 5.0 or waterlogging_flag else 0

        terrain_enc = self.terrain_map.get(terrain_class, 0)
        region_enc = self.region_map.get(environment_region, 0)
        corridor_enc = self.corridor_map.get(corridor_id, 0)

        row = pd.DataFrame([{
            "rainfall_mm_scenario": rainfall_mm,
            "visibility_km_scenario": visibility_km,
            "pothole_or_surface_risk_index_scenario": pothole_or_surface_risk_index,
            "waterlogging_flag_int": 1 if waterlogging_flag else 0,
            "traffic_congestion_index_scenario": traffic_congestion_index,
            "road_distance_km": road_distance_km,
            "baseline_drive_time_minutes_est": baseline_drive_time_minutes,
            "is_ghat_route": is_ghat,
            "is_wet_condition": is_wet,
            "day_of_week": day_of_week,
            "terrain_encoded": terrain_enc,
            "region_encoded": region_enc,
            "corridor_encoded": corridor_enc
        }])

        if self.delay_regressor is not None:
            pred_delay = float(np.maximum(0.0, self.delay_regressor.predict(row[self.feature_columns])[0]))
        else:
            # Fallback heuristic
            pred_delay = (rainfall_mm * 1.5) + (traffic_congestion_index * 60.0) + (is_ghat * 25.0)

        if self.hazard_classifier is not None:
            pred_sev = str(self.hazard_classifier.predict(row[self.feature_columns])[0])
            probs = self.hazard_classifier.predict_proba(row[self.feature_columns])[0]
            risk_score = float(np.max(probs))
        else:
            pred_sev = "high" if (rainfall_mm > 25.0 or waterlogging_flag) else ("medium" if is_ghat or rainfall_mm > 10.0 else "low")
            risk_score = 0.85 if pred_sev == "high" else (0.5 if pred_sev == "medium" else 0.2)

        # Operational metrics
        # Lateness penalty: INR 2.50 per minute of delay over 20 min
        extra_delay = max(0.0, pred_delay - 15.0)
        lateness_surcharge = round(extra_delay * 2.50 + (350.0 if is_ghat and is_wet else 0.0), 2)
        replan_recommended = pred_delay >= 60.0 or pred_sev == "high" or waterlogging_flag

        explanation = (
            f"Corridor {corridor_id} ({terrain_class}): "
            f"{'Wet road' if is_wet else 'Dry conditions'}, rainfall {rainfall_mm}mm, "
            f"congestion {int(traffic_congestion_index * 100)}%. "
            f"Predicted delay: +{round(pred_delay, 1)} min ({pred_sev.upper()} risk). "
            f"{'Action: REPLAN_RECOMMENDED (Delay > 60m or high ghat risk).' if replan_recommended else 'Action: ROUTE_COMMITTED (Acceptable road conditions).'}"
        )

        return {
            "route_id": route_id,
            "corridor_id": corridor_id,
            "terrain_class": terrain_class,
            "predicted_delay_minutes": round(pred_delay, 1),
            "risk_severity": pred_sev,
            "risk_score": round(risk_score, 3),
            "lateness_surcharge_inr": lateness_surcharge,
            "replan_recommended": replan_recommended,
            "explanation": explanation
        }

    def save(self) -> None:
        path = os.path.join(self.models_dir, "road_risk_delay_model.joblib")
        joblib.dump({
            "delay_regressor": self.delay_regressor,
            "hazard_classifier": self.hazard_classifier,
            "feature_columns": self.feature_columns,
            "terrain_map": self.terrain_map,
            "region_map": self.region_map,
            "corridor_map": self.corridor_map,
            "metrics": self.metrics
        }, path)

    def load(self) -> bool:
        path = os.path.join(self.models_dir, "road_risk_delay_model.joblib")
        if not os.path.exists(path):
            return False
        data = joblib.load(path)
        self.delay_regressor = data["delay_regressor"]
        self.hazard_classifier = data["hazard_classifier"]
        self.feature_columns = data["feature_columns"]
        self.terrain_map = data["terrain_map"]
        self.region_map = data["region_map"]
        self.corridor_map = data["corridor_map"]
        self.metrics = data.get("metrics", {})
        return True
