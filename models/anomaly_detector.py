"""
Anomaly Detection & Shock Prevention for FlowGrid
Implements:
1. Isolation Forest for unsupervised outlier and demand spike detection.
2. Anomaly evaluation against synthetic/historical ground truth flags.
3. Outlier clamping safeguard to prevent forecast pollution and over-ordering.
"""

import numpy as np
import pandas as pd
import joblib
from typing import Dict, Any, Tuple, Optional
from sklearn.ensemble import IsolationForest
from sklearn.metrics import precision_score, recall_score, f1_score, roc_auc_score


class AnomalyDetector:
    def __init__(self, contamination: float = 0.01, random_state: int = 42):
        self.contamination = contamination
        self.random_state = random_state
        self.iso_model: Optional[IsolationForest] = None
        self.feature_columns: list = []
        self.metrics: Dict[str, float] = {}

    def fit(self, df: pd.DataFrame, feature_cols: list) -> IsolationForest:
        """
        Trains Isolation Forest on past demand, price, promo, and rolling statistics.
        """
        self.feature_columns = feature_cols
        X = df[feature_cols].values

        self.iso_model = IsolationForest(
            contamination=self.contamination,
            random_state=self.random_state,
            n_jobs=-1
        )
        self.iso_model.fit(X)
        return self.iso_model

    def predict(self, df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        """
        Inference:
        Returns:
            is_anomaly: binary array (1 if anomaly, 0 if normal)
            anomaly_scores: continuous decision scores (lower means more anomalous)
        """
        if self.iso_model is None:
            # Fallback: rule-based rolling Z-score
            if 'rolling_mean_7' in df.columns and 'rolling_std_7' in df.columns:
                z_score = np.abs(df['units_sold'] - df['rolling_mean_7']) / np.maximum(df['rolling_std_7'], 1.0)
                is_anomaly = (z_score > 3.0).astype(int).values
                return is_anomaly, -z_score.values
            return np.zeros(len(df), dtype=int), np.zeros(len(df))

        X = df[self.feature_columns].values
        # IsolationForest: -1 for outlier, 1 for inlier
        raw_preds = self.iso_model.predict(X)
        is_anomaly = (raw_preds == -1).astype(int)
        decision_scores = self.iso_model.decision_function(X)

        return is_anomaly, decision_scores

    def evaluate(self, df: pd.DataFrame, ground_truth_col: str = "synthetic_anomaly_flag") -> Dict[str, float]:
        """
        Evaluates detection performance against known ground truth flags.
        """
        if ground_truth_col not in df.columns:
            return {"status": "no_ground_truth"}

        y_true = df[ground_truth_col].values.astype(int)
        is_anomaly, scores = self.predict(df)

        prec = float(precision_score(y_true, is_anomaly, zero_division=0))
        rec = float(recall_score(y_true, is_anomaly, zero_division=0))
        f1 = float(f1_score(y_true, is_anomaly, zero_division=0))
        
        # ROC AUC using inverted decision function (-scores: higher means more anomalous)
        try:
            auc = float(roc_auc_score(y_true, -scores))
        except Exception:
            auc = 0.5

        self.metrics = {
            "precision": prec,
            "recall": rec,
            "f1_score": f1,
            "roc_auc": auc,
            "detected_anomalies_count": int(np.sum(is_anomaly)),
            "actual_anomalies_count": int(np.sum(y_true))
        }
        return self.metrics

    @staticmethod
    def clamp_anomalies(df: pd.DataFrame, is_anomaly: np.ndarray) -> pd.DataFrame:
        """
        Faulty Output Safeguard:
        Prevents temporary demand shocks or stockout zeros from corrupting
        the training set or inflating safety stock recommendations.
        Replaces anomalous units_sold with 7-day rolling mean.
        """
        df_clean = df.copy()
        if 'rolling_mean_7' in df_clean.columns:
            mask = (is_anomaly == 1)
            df_clean.loc[mask, 'units_sold'] = np.round(df_clean.loc[mask, 'rolling_mean_7']).astype(int)
        return df_clean

    def save(self, filepath: str):
        payload = {
            "iso_model": self.iso_model,
            "feature_columns": self.feature_columns,
            "metrics": self.metrics
        }
        joblib.dump(payload, filepath)

    def load(self, filepath: str):
        payload = joblib.load(filepath)
        self.iso_model = payload.get("iso_model")
        self.feature_columns = payload.get("feature_columns", [])
        self.metrics = payload.get("metrics", {})
