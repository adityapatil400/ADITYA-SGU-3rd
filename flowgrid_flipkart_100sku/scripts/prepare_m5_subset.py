"""Prepare a null-free, FlowGrid-compatible M5 long-format sales subset.

Place sales_train_evaluation.csv, calendar.csv and sell_prices.csv in --input-dir.
This script writes a daily panel that can be passed directly to
scripts/prepare_model_features.py --input <sales_daily_m5_subset.csv>.
Missing historical sell_price values are transparently imputed within each
store/SKU series and marked with unit_price_imputed=1. M5 does not provide a
verified promotion flag or anomaly label; availability flags remain 0.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

METADATA = ["id", "item_id", "dept_id", "cat_id", "store_id", "state_id"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, help="Folder containing the downloaded M5 CSV files")
    parser.add_argument("--output-dir", default="data/m5_processed")
    parser.add_argument("--n-food", type=int, default=15)
    parser.add_argument("--n-household", type=int, default=5)
    parser.add_argument("--tail-days", type=int, default=365, help="Use the last N days; 0 uses all available days")
    args = parser.parse_args()
    inp, out = Path(args.input_dir), Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    sales_path, cal_path, price_path = inp / "sales_train_evaluation.csv", inp / "calendar.csv", inp / "sell_prices.csv"
    for path in (sales_path, cal_path, price_path):
        if not path.exists():
            raise FileNotFoundError(f"Missing {path}. Download the M5 files from Kaggle first.")

    meta = pd.read_csv(sales_path, usecols=METADATA)
    rng = np.random.default_rng(42)
    item_meta = meta[["item_id", "cat_id", "dept_id"]].drop_duplicates().sort_values(["cat_id", "item_id"])
    chosen: list[str] = []
    for category, n in (("FOODS", args.n_food), ("HOUSEHOLD", args.n_household)):
        candidates = item_meta[item_meta["cat_id"] == category]
        if len(candidates) < n:
            raise ValueError(f"Requested {n} {category} items but only found {len(candidates)}")
        chosen.extend(rng.choice(candidates["item_id"].to_numpy(), size=n, replace=False).tolist())
    chosen_set = set(chosen)

    header = pd.read_csv(sales_path, nrows=0).columns.tolist()
    day_cols = [c for c in header if c.startswith("d_")]
    if args.tail_days > 0:
        day_cols = day_cols[-min(args.tail_days, len(day_cols)):]
    wide_parts = []
    for chunk in pd.read_csv(sales_path, usecols=METADATA + day_cols, chunksize=100):
        kept = chunk[chunk["item_id"].isin(chosen_set)].copy()
        if not kept.empty:
            wide_parts.append(kept)
    if not wide_parts:
        raise ValueError("No chosen items found in sales_train_evaluation.csv")
    wide = pd.concat(wide_parts, ignore_index=True)

    store_sources = sorted(wide["store_id"].unique())
    source_items = sorted(chosen_set)
    store_map = {source: f"STORE-{i + 1:03d}" for i, source in enumerate(store_sources)}
    sku_map = {source: f"SKU-{i + 1:03d}" for i, source in enumerate(source_items)}

    long_df = wide.melt(id_vars=METADATA, value_vars=day_cols, var_name="d", value_name="units_sold")
    calendar = pd.read_csv(cal_path)
    cal_keep = [c for c in ["d", "date", "wm_yr_wk", "weekday", "wday", "month", "year", "event_name_1", "event_type_1", "event_name_2", "event_type_2", "snap_CA", "snap_TX", "snap_WI"] if c in calendar.columns]
    long_df = long_df.merge(calendar[cal_keep], on="d", how="left", validate="many_to_one")
    prices = pd.read_csv(price_path, usecols=["store_id", "item_id", "wm_yr_wk", "sell_price"])
    long_df = long_df.merge(prices, on=["store_id", "item_id", "wm_yr_wk"], how="left", validate="many_to_one")

    long_df["unit_price_imputed"] = long_df["sell_price"].isna().astype(int)
    long_df = long_df.rename(columns={"sell_price": "unit_price", "cat_id": "category_id", "item_id": "source_item_id"})
    long_df["store_id"] = long_df["store_id"].map(store_map)
    long_df["sku_id"] = long_df["source_item_id"].map(sku_map)
    long_df["date"] = pd.to_datetime(long_df["date"], errors="coerce")
    if long_df["date"].isna().any():
        raise ValueError("Calendar merge produced missing/invalid dates.")
    long_df["weekday"] = long_df["date"].dt.day_name()
    long_df["day_of_week"] = long_df["date"].dt.dayofweek.astype(int)
    long_df["month"] = long_df["date"].dt.month.astype(int)
    long_df["is_weekend"] = (long_df["day_of_week"] >= 5).astype(int)
    long_df["units_sold"] = pd.to_numeric(long_df["units_sold"], errors="coerce")
    if long_df["units_sold"].isna().any() or (long_df["units_sold"] < 0).any():
        raise ValueError("M5 sales target contains missing or negative values.")
    long_df["units_sold"] = long_df["units_sold"].astype(int)

    # Impute missing prices within each store/SKU series; keep an audit flag.
    long_df = long_df.sort_values(["store_id", "sku_id", "date"]).reset_index(drop=True)
    was_missing = long_df["unit_price"].isna()
    long_df["unit_price"] = long_df.groupby(["store_id", "sku_id"], sort=False)["unit_price"].transform(lambda s: s.ffill().bfill())
    sku_medians = long_df.groupby("sku_id")["unit_price"].transform("median")
    long_df["unit_price"] = long_df["unit_price"].fillna(sku_medians)
    overall_median = long_df["unit_price"].median()
    long_df["unit_price"] = long_df["unit_price"].fillna(overall_median)
    if long_df["unit_price"].isna().any() or (long_df["unit_price"] <= 0).any():
        raise ValueError("Price imputation failed or yielded nonpositive price values.")
    long_df["unit_price_imputed"] = was_missing.astype(int)

    # M5 has no verified promo flag / anomaly labels in these source files.
    long_df["synthetic_promo_flag"] = 0
    long_df["promo_feature_available"] = 0
    long_df["synthetic_anomaly_flag"] = 0
    long_df["anomaly_label_available"] = 0
    long_df["data_origin"] = "Kaggle_M5_historical"

    # Preserve useful source metadata; use explicit sentinel values for optional event text.
    for col in ("event_name_1", "event_type_1", "event_name_2", "event_type_2", "state_id", "category_id", "dept_id", "source_item_id"):
        if col in long_df.columns:
            long_df[col] = long_df[col].fillna("NONE").astype(str)
    for col in ("wm_yr_wk", "wday", "year", "snap_CA", "snap_TX", "snap_WI"):
        if col in long_df.columns:
            long_df[col] = pd.to_numeric(long_df[col], errors="coerce").fillna(0).astype(int)
    long_df["date"] = long_df["date"].dt.strftime("%Y-%m-%d")
    output_cols = [
        "date", "store_id", "sku_id", "units_sold", "unit_price", "weekday", "day_of_week", "month", "is_weekend",
        "synthetic_promo_flag", "synthetic_anomaly_flag", "promo_feature_available", "unit_price_imputed", "anomaly_label_available", "data_origin",
        "source_item_id", "state_id", "category_id", "dept_id", "wm_yr_wk", "event_name_1", "event_type_1", "event_name_2", "event_type_2", "snap_CA", "snap_TX", "snap_WI",
    ]
    output_cols = [col for col in output_cols if col in long_df.columns]
    output = long_df[output_cols].sort_values(["date", "store_id", "sku_id"]).reset_index(drop=True)
    if output.isna().any().any() or any(output[c].astype(str).str.strip().eq("").any() for c in output.select_dtypes(include=["object", "string"]).columns):
        raise ValueError("Null or blank values remain in the prepared M5 output.")
    output.to_csv(out / "sales_daily_m5_subset.csv", index=False)
    pd.DataFrame([{"source_store_id": k, "store_id": v, "data_origin": "Kaggle_M5_historical"} for k, v in store_map.items()]).to_csv(out / "store_mapping.csv", index=False)
    pd.DataFrame([{"source_item_id": k, "sku_id": v, "data_origin": "Kaggle_M5_historical"} for k, v in sku_map.items()]).to_csv(out / "sku_mapping.csv", index=False)
    print(f"Selected {len(chosen_set)} SKUs across {len(store_map)} stores")
    print(f"Daily rows: {len(output):,}; columns: {len(output.columns)}; nulls: {int(output.isna().sum().sum())}")
    print(f"Output: {out / 'sales_daily_m5_subset.csv'}")
    print("Price values imputed where missing; see unit_price_imputed. M5 promotion/anomaly labels are unavailable, so availability flags are 0.")
    print("Sales/price data are public historical observations; all FlowGrid logistics data remain separate synthetic records.")


if __name__ == "__main__":
    main()
