"""Train/evaluate FlowGrid Random Forest against a seasonal-naive baseline.

Model selection uses validation only. Test metrics are calculated after selection.
Evaluation is rolling-origin one-step-ahead; actual sales history before each
prediction date is assumed available, which must be stated when reporting results.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import dump
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

ROOT = Path(__file__).resolve().parents[1]
CATEGORICAL = ["store_id", "sku_id", "weekday"]
NUMERIC = [
    "unit_price", "day_of_week", "month", "is_weekend", "synthetic_promo_flag",
    "promo_feature_available", "unit_price_imputed", "lag_1", "lag_7", "lag_14", "lag_28",
    "rolling_mean_7", "rolling_mean_28", "rolling_std_7", "rolling_std_28",
]
TARGET = "units_sold"


def metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float | None]:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.maximum(np.asarray(y_pred, dtype=float), 0)
    denom = float(np.abs(y_true).sum())
    wape = float(np.abs(y_true - y_pred).sum() / denom * 100) if denom > 0 else None
    return {
        "wape_percent": round(wape, 4) if wape is not None else None,
        "mae_units": round(float(np.mean(np.abs(y_true - y_pred))), 4),
        "bias_pred_minus_actual_units": round(float(np.mean(y_pred - y_true)), 4),
        "rows": int(len(y_true)),
    }


def build_model(n_estimators: int, max_depth: int, n_jobs: int) -> Pipeline:
    transform = ColumnTransformer(
        transformers=[
            ("categorical", OneHotEncoder(handle_unknown="ignore", dtype=np.float32), CATEGORICAL),
            ("numeric", "passthrough", NUMERIC),
        ],
        remainder="drop",
    )
    rf = RandomForestRegressor(
        n_estimators=n_estimators,
        max_depth=max_depth if max_depth > 0 else None,
        min_samples_leaf=2,
        max_features=0.9,
        random_state=42,
        n_jobs=n_jobs,
    )
    return Pipeline([("features", transform), ("model", rf)])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "model_ready")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts")
    parser.add_argument("--n-estimators", type=int, default=100)
    parser.add_argument("--max-depth", type=int, default=18, help="Use 0 for no depth limit; a bounded depth is recommended for laptop RAM/time.")
    parser.add_argument("--n-jobs", type=int, default=4, help="Limit CPU parallelism for a 16 GB laptop.")
    parser.add_argument("--min-relative-wape-improvement", type=float, default=0.05)
    parser.add_argument("--max-train-rows", type=int, default=0,
                        help="Optional reproducible cap for constrained laptops; 0 uses all training rows.")
    args = parser.parse_args()

    all_path = args.data_dir / "sales_features.csv"
    if not all_path.exists():
        raise SystemExit(f"Missing {all_path}. Run scripts/generate_flowgrid_demo.py first.")
    # Load compact dtypes so the 1M-row dataset can be evaluated on a 16 GB laptop.
    read_dtypes = {col: "category" for col in ["store_id", "sku_id", "weekday", "split", "data_origin"]}
    read_dtypes.update({"units_sold": "int32", "unit_price": "float32"})
    for col in ["lag_1", "lag_7", "lag_14", "lag_28", "rolling_mean_7", "rolling_mean_28", "rolling_std_7", "rolling_std_28"]:
        read_dtypes[col] = "float32"
    for col in ["day_of_week", "month", "is_weekend", "synthetic_promo_flag", "synthetic_anomaly_flag", "promo_feature_available", "unit_price_imputed", "anomaly_label_available"]:
        read_dtypes[col] = "int8"
    df = pd.read_csv(all_path, dtype=read_dtypes)
    needed = set(CATEGORICAL + NUMERIC + [TARGET, "split"])
    missing = sorted(needed - set(df.columns))
    if missing:
        raise SystemExit(f"Model-ready data missing columns: {missing}")
    if df[list(needed)].isna().any().any():
        raise SystemExit("Model-ready model inputs contain nulls; run scripts/validate_dataset.py.")

    split = df["split"].astype(str)
    train_mask = split.eq("train")
    val_mask = split.eq("validation")
    test_mask = split.eq("test")
    if not train_mask.any() or not val_mask.any() or not test_mask.any():
        raise SystemExit("Train/validation/test split is empty.")

    # Select only model columns for partitions; avoid copying every metadata column 3 times.
    model_columns = CATEGORICAL + NUMERIC
    train_rows = int(train_mask.sum())
    if args.max_train_rows > 0 and train_rows > args.max_train_rows:
        # Select evenly spaced chronological rows for a deterministic quick benchmark.
        chosen = np.linspace(0, train_rows - 1, args.max_train_rows, dtype=int)
        train_positions = np.flatnonzero(train_mask.to_numpy())[chosen]
        x_train = df.iloc[train_positions][model_columns]
        y_train = df.iloc[train_positions][TARGET].to_numpy(dtype=np.float32)
    else:
        x_train = df.loc[train_mask, model_columns]
        y_train = df.loc[train_mask, TARGET].to_numpy(dtype=np.float32)

    x_val = df.loc[val_mask, model_columns]
    y_val = df.loc[val_mask, TARGET].to_numpy(dtype=np.float32)
    baseline_val = df.loc[val_mask, "lag_7"].to_numpy(dtype=np.float32)
    baseline_val_metrics = metrics(y_val, baseline_val)

    candidate = build_model(args.n_estimators, args.max_depth, args.n_jobs)
    candidate.fit(x_train, y_train)
    rf_val_metrics = metrics(y_val, candidate.predict(x_val))
    base_wape = baseline_val_metrics["wape_percent"]
    rf_wape = rf_val_metrics["wape_percent"]
    improvement = None if base_wape in (None, 0) or rf_wape is None else (base_wape - rf_wape) / base_wape
    accepted = improvement is not None and improvement >= args.min_relative_wape_improvement

    args.output_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "evaluation_protocol": "rolling-origin one-step-ahead; lag features use only history before each target date",
        "selection_rule": f"Select Random Forest only when validation WAPE improves by at least {args.min_relative_wape_improvement * 100:.1f}% relative to seasonal-naive lag_7 baseline.",
        "row_counts": {"train_available": train_rows, "train_used": int(len(y_train)), "validation": int(val_mask.sum()), "test": int(test_mask.sum())},
        "validation": {
            "seasonal_naive": baseline_val_metrics,
            "random_forest_candidate": rf_val_metrics,
            "relative_wape_improvement": round(float(improvement), 6) if improvement is not None else None,
        },
        "selected_model": "random_forest" if accepted else "seasonal_naive",
        "test": {},
        "notes": [
            "Synthetic-data metrics demonstrate pipeline behavior, not real-world demand accuracy.",
            "synthetic_anomaly_flag is not used as a predictor because it represents target-day anomaly metadata.",
            "Use public sales data for a separate external evaluation and report domain limitations.",
        ],
    }

    if accepted:
        train_val_mask = train_mask | val_mask
        x_train_val = df.loc[train_val_mask, model_columns]
        y_train_val = df.loc[train_val_mask, TARGET].to_numpy(dtype=np.float32)
        final_model = build_model(args.n_estimators, args.max_depth, args.n_jobs)
        final_model.fit(x_train_val, y_train_val)
        x_test = df.loc[test_mask, model_columns]
        y_test = df.loc[test_mask, TARGET].to_numpy(dtype=np.float32)
        test_pred = final_model.predict(x_test)
        dump(final_model, args.output_dir / "flowgrid_random_forest.joblib")
        report["test"] = {"selected_model": metrics(y_test, test_pred), "seasonal_naive": metrics(y_test, df.loc[test_mask, "lag_7"].to_numpy(dtype=np.float32))}
        report["artifact"] = "flowgrid_random_forest.joblib"
    else:
        y_test = df.loc[test_mask, TARGET].to_numpy(dtype=np.float32)
        test_baseline = df.loc[test_mask, "lag_7"].to_numpy(dtype=np.float32)
        report["test"] = {"selected_model": metrics(y_test, test_baseline), "seasonal_naive": metrics(y_test, test_baseline)}
        report["artifact"] = "No Random Forest artifact selected; seasonal-naive baseline remains active."

    (args.output_dir / "forecast_metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nSaved report: {args.output_dir / 'forecast_metrics.json'}")


if __name__ == "__main__":
    main()
