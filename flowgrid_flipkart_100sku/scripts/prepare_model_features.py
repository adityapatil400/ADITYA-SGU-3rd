"""Create leakage-aware, null-free supervised features and chronological splits.

Evaluation protocol: rolling-origin one-step-ahead forecasting. For each target
Date, lag features use only observations strictly before that date. This is not
an immutable 28-day-ahead forecast made once at the start of the test window.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

KEYS = ["store_id", "sku_id"]
TARGET = "units_sold"
LAGS = (1, 7, 14, 28)
ROLLING_WINDOWS = (7, 28)
BASE_COLUMNS = [
    "date", "store_id", "sku_id", "units_sold", "unit_price", "weekday",
    "day_of_week", "month", "is_weekend", "synthetic_promo_flag",
    "synthetic_anomaly_flag", "promo_feature_available", "unit_price_imputed",
    "anomaly_label_available", "data_origin",
]
FEATURE_COLUMNS = [
    "unit_price", "day_of_week", "month", "is_weekend",
    "synthetic_promo_flag", "promo_feature_available", "unit_price_imputed",
    "lag_1", "lag_7", "lag_14", "lag_28",
    "rolling_mean_7", "rolling_mean_28", "rolling_std_7", "rolling_std_28",
]
CATEGORICAL_COLUMNS = ["store_id", "sku_id", "weekday"]


def build_features(input_csv: Path, output_dir: Path) -> dict[str, int]:
    # Categoricals reduce RAM substantially for high-row-count store/SKU panels.
    df = pd.read_csv(input_csv, dtype={"store_id": "category", "sku_id": "category", "weekday": "category", "data_origin": "category"})
    required_core = ["date", "store_id", "sku_id", "units_sold", "unit_price", "data_origin"]
    missing = sorted(set(required_core) - set(df.columns))
    if missing:
        raise ValueError(f"Sales CSV is missing core columns: {missing}")

    # Normalize compatible public-sales datasets to the same model schema.
    df["date"] = pd.to_datetime(df["date"], errors="raise").dt.normalize()
    if "weekday" not in df:
        df["weekday"] = df["date"].dt.day_name()
    if "day_of_week" not in df:
        df["day_of_week"] = df["date"].dt.dayofweek
    if "month" not in df:
        df["month"] = df["date"].dt.month
    if "is_weekend" not in df:
        df["is_weekend"] = (df["date"].dt.dayofweek >= 5).astype(int)
    if "synthetic_promo_flag" not in df:
        df["synthetic_promo_flag"] = 0
    if "synthetic_anomaly_flag" not in df:
        df["synthetic_anomaly_flag"] = 0
    if "promo_feature_available" not in df:
        df["promo_feature_available"] = int("synthetic_promo_flag" in pd.read_csv(input_csv, nrows=0).columns)
    if "unit_price_imputed" not in df:
        df["unit_price_imputed"] = 0
    if "anomaly_label_available" not in df:
        df["anomaly_label_available"] = int("synthetic_anomaly_flag" in pd.read_csv(input_csv, nrows=0).columns)
    if df[required_core + ["weekday", "day_of_week", "month", "is_weekend", "synthetic_promo_flag", "synthetic_anomaly_flag", "promo_feature_available", "unit_price_imputed", "anomaly_label_available"]].isna().any().any():
        bad = df.isna().sum()
        raise ValueError(f"Input sales data contains nulls: {bad[bad > 0].to_dict()}")
    df = df.sort_values(KEYS + ["date"], kind="stable").reset_index(drop=True)
    if df.duplicated(KEYS + ["date"]).any():
        raise ValueError("Input has duplicate store_id/SKU/date rows.")

    unique_dates = pd.DatetimeIndex(sorted(df["date"].unique()))
    n_days = len(unique_dates)
    if n_days < 90:
        raise ValueError("At least 90 daily observations are recommended for train/validation/test splits.")

    groups = df.groupby(KEYS, sort=False, observed=True)[TARGET]
    for lag in LAGS:
        df[f"lag_{lag}"] = groups.shift(lag)
    grouped_target = df.groupby(KEYS, sort=False, observed=True)[TARGET]
    for window in ROLLING_WINDOWS:
        df[f"rolling_mean_{window}"] = grouped_target.transform(
            lambda s, w=window: s.shift(1).rolling(w, min_periods=w).mean()
        )
    df["rolling_std_7"] = grouped_target.transform(
        lambda s: s.shift(1).rolling(7, min_periods=7).std(ddof=0)
    )
    df["rolling_std_28"] = grouped_target.transform(
        lambda s: s.shift(1).rolling(28, min_periods=28).std(ddof=0)
    )

    # Use date boundaries shared by all store-SKU series, never random row splits.
    date_pos = {pd.Timestamp(d): i for i, d in enumerate(unique_dates)}
    positions = df["date"].map(date_pos)
    validation_start = n_days - 56
    test_start = n_days - 28
    df["split"] = np.select(
        [positions < validation_start, positions < test_start],
        ["train", "validation"],
        default="test",
    )

    # The first 28 days are a feature warm-up window, not usable ML rows.
    required = BASE_COLUMNS + [f"lag_{lag}" for lag in LAGS]
    required += ["rolling_mean_7", "rolling_mean_28", "rolling_std_7", "rolling_std_28", "split"]
    features = df.dropna(subset=required).copy()
    features["date"] = features["date"].dt.strftime("%Y-%m-%d")

    # Normalize types to stable model-friendly values.
    for col in ["units_sold", "day_of_week", "month", "is_weekend", "synthetic_promo_flag", "synthetic_anomaly_flag", "promo_feature_available", "unit_price_imputed", "anomaly_label_available"]:
        features[col] = pd.to_numeric(features[col], errors="raise").astype("int64")
    for col in ["unit_price"] + [f"lag_{x}" for x in LAGS] + ["rolling_mean_7", "rolling_mean_28", "rolling_std_7", "rolling_std_28"]:
        features[col] = pd.to_numeric(features[col], errors="raise").astype("float32")
    for col in ["store_id", "sku_id", "weekday", "data_origin", "split"]:
        # Keep low-cardinality IDs/category labels as categoricals during processing.
        features[col] = features[col].astype("category")

    # Strict output gate: the exported model inputs must be complete and finite.
    if features.isna().any().any():
        bad = features.isna().sum()
        raise ValueError(f"Engineered model data contains nulls: {bad[bad > 0].to_dict()}")
    numeric = features.select_dtypes(include=[np.number])
    if not np.isfinite(numeric.to_numpy()).all():
        raise ValueError("Engineered model data contains non-finite numeric values.")
    if (features["units_sold"] < 0).any() or (features["unit_price"] <= 0).any():
        raise ValueError("Target must be nonnegative and unit_price must be positive.")

    output_dir.mkdir(parents=True, exist_ok=True)
    feature_order = BASE_COLUMNS + [f"lag_{lag}" for lag in LAGS]
    feature_order += ["rolling_mean_7", "rolling_mean_28", "rolling_std_7", "rolling_std_28", "split"]
    # Avoid a second million-row sort/copy: rows are already grouped by store/SKU/date,
    # and all partitions use shared chronological date boundaries.
    features = features[feature_order].reset_index(drop=True)
    features.to_csv(output_dir / "sales_features.csv", index=False, float_format="%.5f")
    counts = {"sales_features.csv": int(len(features))}
    for split in ("train", "validation", "test"):
        mask = features["split"] == split
        features.loc[mask].to_csv(output_dir / f"{split}.csv", index=False, float_format="%.5f")
        counts[f"{split}.csv"] = int(mask.sum())
    origins = sorted(str(x) for x in df["data_origin"].astype(str).unique()) if "data_origin" in df else []
    schema = {
        "dataset": f"FlowGrid model features derived from {input_csv.name}",
        "source_data_origin_values": origins,
        "target": TARGET,
        "categorical_features": CATEGORICAL_COLUMNS,
        "numeric_features": FEATURE_COLUMNS,
        "excluded_from_model_features": ["synthetic_anomaly_flag", "anomaly_label_available", "data_origin", "date", "split"],
        "split_protocol": "Chronological shared date split: final 28 dates=test; preceding 28 dates=validation; earlier dates=train.",
        "evaluation_protocol": "Rolling-origin one-step-ahead: lag/rolling inputs use values strictly before each target date.",
        "feature_warmup_days": 28,
        "null_policy": "Rows with unavailable lag/rolling features are dropped; exported CSVs contain no null values.",
        "counts": counts,
    }
    (output_dir / "feature_schema.json").write_text(json.dumps(schema, indent=2), encoding="utf-8")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "synthetic_sales_daily.csv")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "model_ready")
    args = parser.parse_args()
    counts = build_features(args.input.resolve(), args.output_dir.resolve())
    print("Model-ready feature data generated with no null values.")
    for name, count in counts.items():
        print(f"  {name:<24} {count:>10,} rows")


if __name__ == "__main__":
    main()
