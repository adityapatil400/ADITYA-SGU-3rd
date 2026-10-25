"""PRD-aligned seven-day demand forecasts with calibrated quantile outputs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.stats import nbinom
from sklearn.ensemble import HistGradientBoostingRegressor


QUANTILES = (0.10, 0.50, 0.80, 0.90, 0.95)
FEATURES = ["sales_7", "sales_14", "sales_28", "weekday", "days_to_event", "retailer_code", "sku_code"]
CATEGORICAL_FEATURES = [5, 6]


@dataclass
class ForecastModel:
    models: dict[float, HistGradientBoostingRegressor]
    retailer_codes: dict[str, int]
    sku_codes: dict[str, int]
    train_cutoff: pd.Timestamp
    validation_start: pd.Timestamp
    validation_wape: float
    baseline_validation_wape: float


class WeeklyQuantileForecaster:
    """Forecast 7-day retailer/SKU totals using the methods specified in PRD 1.5."""

    def __init__(self, random_state: int = 42, bootstrap_samples: int = 1000):
        self.random_state = random_state
        self.bootstrap_samples = bootstrap_samples
        self.model: ForecastModel | None = None
        self.last_report: dict[str, Any] = {}

    @staticmethod
    def _normalize(history: pd.DataFrame) -> pd.DataFrame:
        data = history.copy()
        aliases = {"retailer_id": "store_id", "product_id": "sku_id", "quantity": "units_sold"}
        data = data.rename(columns={k: v for k, v in aliases.items() if k in data and v not in data})
        required = {"date", "store_id", "sku_id", "units_sold"}
        missing = sorted(required - set(data.columns))
        if missing:
            raise ValueError(f"Sales history is missing required columns: {missing}")
        data["date"] = pd.to_datetime(data["date"], errors="raise").dt.normalize()
        data["store_id"] = data["store_id"].astype(str)
        data["sku_id"] = data["sku_id"].astype(str)
        data["units_sold"] = pd.to_numeric(data["units_sold"], errors="coerce")
        if (data["units_sold"].dropna() < 0).any():
            raise ValueError("Sales history contains negative units_sold values")
        if data.duplicated(["date", "store_id", "sku_id"]).any():
            raise ValueError("Duplicate date/store_id/sku_id rows must be resolved by import before forecasting")
        closing_stock_available = "closing_stock" in data
        if closing_stock_available:
            data["closing_stock"] = pd.to_numeric(data["closing_stock"], errors="coerce")
        else:
            data["closing_stock"] = np.nan
        data = data.sort_values(["store_id", "sku_id", "date"], kind="stable").reset_index(drop=True)
        data.attrs["closing_stock_available"] = closing_stock_available
        return data

    @staticmethod
    def _days_to_event(date: pd.Timestamp, events: pd.DataFrame | None) -> float:
        if events is None or events.empty or "date" not in events:
            return -1.0
        dates = pd.to_datetime(events["date"], errors="coerce").dropna().dt.normalize()
        future = dates[dates >= date]
        return float((future.min() - date).days) if len(future) else -1.0

    @staticmethod
    def _prepare_series(group: pd.DataFrame, cap_cutoff: pd.Timestamp | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
        group = group.sort_values("date").set_index("date")
        full_dates = pd.date_range(group.index.min(), group.index.max(), freq="D")
        daily = group.reindex(full_dates)
        daily.index.name = "date"
        daily["missing_day"] = daily["units_sold"].isna()
        daily["stockout_censored"] = daily["closing_stock"].eq(0).fillna(False)
        daily["stockout_corrected"] = False
        original = daily["units_sold"].copy()

        # Replace a stockout-day sale only when the same weekday's recent
        # uncensored history supports a higher demand estimate.
        for day in daily.index[daily["stockout_censored"].to_numpy(dtype=bool)]:
            prior_days = [day - pd.Timedelta(days=7 * k) for k in range(1, 5)]
            prior = [d for d in prior_days if d in daily.index and not daily.at[d, "stockout_censored"]
                     and pd.notna(daily.at[d, "units_sold"])]
            if prior:
                estimate = float(np.median([daily.at[d, "units_sold"] for d in prior]))
                if pd.isna(daily.at[day, "units_sold"]) or estimate > float(daily.at[day, "units_sold"]):
                    daily.at[day, "units_sold"] = estimate
                    daily.at[day, "stockout_corrected"] = True

        cap_rows = ~daily["stockout_censored"]
        if cap_cutoff is not None:
            cap_rows &= daily.index <= cap_cutoff
        valid = daily.loc[cap_rows, "units_sold"].dropna()
        cap = float(valid.quantile(0.995)) if len(valid) else 0.0
        daily["outlier_capped"] = daily["units_sold"].gt(cap) & daily["units_sold"].notna()
        daily["units_model"] = daily["units_sold"].clip(upper=cap) if cap > 0 else daily["units_sold"]
        daily.loc[daily["stockout_corrected"], "units_model"] = daily.loc[daily["stockout_corrected"], "units_sold"].clip(upper=cap)
        daily.attrs["window_totals"] = {
            days: daily["units_model"].rolling(days, min_periods=1).mean() * days
            for days in (7, 14, 28)
        }
        valid_days = int(original.notna().sum())
        censored_days = int((daily["stockout_censored"] & original.notna()).sum())
        days_since_first = max(1, (daily.index.max() - daily.index.min()).days + 1)
        positives = daily.loc[daily["units_model"].notna() & ~daily["stockout_censored"], "units_model"]
        positive_dates = daily.index[positives > 0]
        avg_interval = float(np.diff(positive_dates.values).astype("timedelta64[D]").astype(float).mean()) if len(positive_dates) > 1 else float("inf")
        flags = {
            "history_days": days_since_first,
            "missing_days": int(daily["missing_day"].sum()),
            "stockout_days": censored_days,
            "stockout_corrected_days": int(daily["stockout_corrected"].sum()),
            "outlier_days_capped": int(daily["outlier_capped"].sum()),
            "stockout_fraction": censored_days / max(1, valid_days),
            "average_interval_days": avg_interval,
        }
        return daily, flags

    @staticmethod
    def _window_total(values: pd.DataFrame | pd.Series, end: pd.Timestamp, days: int) -> float:
        if isinstance(values, pd.DataFrame):
            cached = values.attrs.get("window_totals", {}).get(days)
            series = values["units_model"]
        else:
            cached = None
            series = values
        if cached is not None and end in cached.index:
            result = cached.loc[end]
            return float(result) if pd.notna(result) else 0.0
        start = end - pd.Timedelta(days=days - 1)
        window = series.reindex(pd.date_range(start, end, freq="D"))
        observed = window.dropna()
        if not len(observed):
            return 0.0
        # Missing dates remain missing: scale observed daily rate to window length.
        return float(observed.mean() * days)

    @staticmethod
    def _complete_total(daily: pd.DataFrame, start: pd.Timestamp, target_col: str = "units_sold") -> float | None:
        dates = pd.date_range(start, start + pd.Timedelta(days=6), freq="D")
        if not dates.isin(daily.index).all():
            return None
        target = daily.reindex(dates)
        if target[target_col].isna().any() or target["stockout_censored"].any():
            return None
        return float(target[target_col].sum())

    @staticmethod
    def _negative_binomial(mean: float, dispersion: float = 2.0) -> dict[str, int]:
        mu = max(0.0, float(mean))
        if mu == 0:
            return {f"q{int(q*100)}": 0 for q in QUANTILES}
        p = dispersion / (dispersion + mu)
        values = [max(0, int(np.ceil(nbinom.ppf(q, dispersion, p)))) for q in QUANTILES]
        values = np.maximum.accumulate(values)
        return {f"q{int(q*100)}": int(v) for q, v in zip(QUANTILES, values)}

    def _features(self, daily: pd.DataFrame, date: pd.Timestamp, retailer: str, sku: str,
                  retailer_codes: dict[str, int], sku_codes: dict[str, int], events: pd.DataFrame | None) -> np.ndarray:
        prior_end = date - pd.Timedelta(days=1)
        vals = daily["units_model"]
        cache = daily.attrs.get("window_totals", {})
        window_value = lambda days: float(cache[days].get(prior_end, 0.0)) if days in cache else self._window_total(vals, prior_end, days)
        return np.array([[
            window_value(7),
            window_value(14),
            window_value(28),
            date.dayofweek,
            self._days_to_event(date, events),
            retailer_codes.get(retailer, 0),
            sku_codes.get(sku, 0),
        ]], dtype=float)

    def _training_rows(self, prepared: dict[tuple[str, str], tuple[pd.DataFrame, dict[str, Any]]],
                       retailers: dict[str, int], skus: dict[str, int], events: pd.DataFrame | None,
                       end_before: pd.Timestamp) -> tuple[np.ndarray, np.ndarray]:
        x_rows, y_rows = [], []
        for (retailer, sku), (daily, flags) in prepared.items():
            if flags["history_days"] < 56 or flags["stockout_fraction"] > 0.30 or flags["average_interval_days"] >= 1.32:
                continue
            first_origin = daily.index.min() + pd.Timedelta(days=56)
            origin = first_origin + pd.Timedelta(days=(7 - first_origin.dayofweek) % 7)
            while origin + pd.Timedelta(days=6) <= end_before:
                target = self._complete_total(daily, origin, target_col="units_model")
                if target is not None:
                    x_rows.append(self._features(daily, origin, retailer, sku, retailers, skus, events)[0])
                    y_rows.append(target)
                origin += pd.Timedelta(days=7)
        return np.asarray(x_rows, dtype=float), np.asarray(y_rows, dtype=float)

    def _fit_models(self, x: np.ndarray, y: np.ndarray) -> dict[float, HistGradientBoostingRegressor]:
        models = {}
        if len(y) < 100 or len(np.unique(y)) < 2:
            return models
        for q in QUANTILES:
            estimator = HistGradientBoostingRegressor(
                loss="quantile", quantile=q, max_iter=10, learning_rate=0.08,
                max_leaf_nodes=15, min_samples_leaf=40, max_bins=127, l2_regularization=1.0,
                categorical_features=CATEGORICAL_FEATURES, random_state=self.random_state,
            )
            estimator.fit(x, y)
            models[q] = estimator
        return models

    def forecast(self, history: pd.DataFrame, calendar_events: pd.DataFrame | None = None,
                 forecast_start: str | pd.Timestamp | None = None) -> dict[str, Any]:
        data = self._normalize(history)
        retailers = {v: i for i, v in enumerate(sorted(data["store_id"].unique()))}
        skus = {v: i for i, v in enumerate(sorted(data["sku_id"].unique()))}
        history_end = data["date"].max()
        latest_week_start = history_end - pd.Timedelta(days=6)
        validation_week_start = latest_week_start - pd.Timedelta(days=7)
        train_cutoff = validation_week_start - pd.Timedelta(days=8)
        prepared: dict[tuple[str, str], tuple[pd.DataFrame, dict[str, Any]]] = {}
        for key, group in data.groupby(["store_id", "sku_id"], sort=False):
            prepared[(str(key[0]), str(key[1]))] = self._prepare_series(group, cap_cutoff=train_cutoff)

        # Model choice is made on the week before the reported holdout week.
        # Training labels stop at least one full week before that validation.
        x_train, y_train = self._training_rows(prepared, retailers, skus, calendar_events, train_cutoff)
        validation_models = self._fit_models(x_train, y_train)

        baseline_errors, model_errors = [], []
        validation_by_series: dict[tuple[str, str], tuple[float, float, bool, float, float, float, str]] = {}
        for key, (daily, flags) in prepared.items():
            actual = self._complete_total(daily, validation_week_start)
            if actual is None:
                continue
            baseline_mean = self._window_total(daily, validation_week_start - pd.Timedelta(days=1), 28) / 4.0
            base_pred = baseline_mean
            model_pred = base_pred
            if validation_models and flags["history_days"] >= 56 and flags["stockout_fraction"] <= 0.30 and flags["average_interval_days"] < 1.32:
                x_val = self._features(daily, validation_week_start, key[0], key[1], retailers, skus, calendar_events)
                model_pred = max(0.0, float(validation_models[0.50].predict(x_val)[0]))
            series_class = "short_or_censored" if flags["history_days"] < 56 or flags["stockout_fraction"] > 0.30 else ("regular" if flags["average_interval_days"] < 1.32 else "intermittent")
            validation_by_series[key] = (abs(actual - base_pred), abs(actual - model_pred), abs(actual - model_pred) < abs(actual - base_pred), actual, base_pred, model_pred, series_class)
            baseline_errors.append((actual, base_pred))
            model_errors.append((actual, model_pred))

        def wape(pairs: list[tuple[float, float]]) -> float | None:
            denom = sum(y for y, _ in pairs)
            return sum(abs(y - p) for y, p in pairs) / denom if denom else None

        baseline_wape = wape(baseline_errors)
        model_wape = wape(model_errors)
        use_advanced = bool(validation_models) and model_wape is not None and baseline_wape is not None and model_wape < baseline_wape
        validation_class_pairs: dict[str, list[tuple[float, float]]] = {}
        for _, _, selected_advanced, actual, baseline_prediction, model_prediction, series_class in validation_by_series.values():
            selected_prediction = model_prediction if use_advanced and selected_advanced else baseline_prediction
            validation_class_pairs.setdefault(series_class, []).append((actual, selected_prediction))

        def pair_wape(pairs: list[tuple[float, float]]) -> float | None:
            denominator = sum(actual for actual, _ in pairs)
            return sum(abs(actual - prediction) for actual, prediction in pairs) / denominator if denominator else None

        validation_class_wape = {name: pair_wape(pairs) for name, pairs in validation_class_pairs.items()}

        # Keep the selected rolling-origin model trained only on the past
        # window. Refitting on validation/test labels would violate the PRD's
        # later-week holdout protocol.
        final_models = validation_models if use_advanced else {}
        self.model = ForecastModel(final_models, retailers, skus, history_end, latest_week_start,
                                   float(model_wape) if model_wape is not None else float("nan"),
                                   float(baseline_wape) if baseline_wape is not None else float("nan"))

        # Retailer size factor for peer transfer uses uncensored, observed demand
        # across SKUs during the last 56 days.
        store_rates = {}
        for (retailer, _), (daily, _) in prepared.items():
            window = daily.loc[daily.index > history_end - pd.Timedelta(days=56), "units_model"].dropna()
            if len(window):
                store_rates.setdefault(retailer, []).append(float(window.mean()))
        store_mean = {r: float(np.mean(v)) for r, v in store_rates.items()}
        network_size = float(np.median(list(store_mean.values()))) if store_mean else 1.0
        sku_peer_rates: dict[str, list[tuple[str, float]]] = {}
        for (retailer, sku), (daily, flags) in prepared.items():
            if flags["stockout_fraction"] <= 0.30:
                window = daily.loc[(daily.index > history_end - pd.Timedelta(days=56)) & ~daily["stockout_censored"], "units_model"].dropna()
                if len(window):
                    sku_peer_rates.setdefault(sku, []).append((retailer, float(window.mean())))

        start = pd.Timestamp(forecast_start).normalize() if forecast_start is not None else history_end + pd.Timedelta(days=1)
        forecasts = []
        for key, (daily, flags) in prepared.items():
            retailer, sku = key
            regular = flags["average_interval_days"] < 1.32
            short_or_censored = flags["history_days"] < 56 or flags["stockout_fraction"] > 0.30
            if short_or_censored:
                peer_values = [rate for peer, rate in sku_peer_rates.get(sku, []) if peer != retailer]
                peer_rate = float(np.median(peer_values)) if peer_values else 0.0
                size_factor = np.clip(store_mean.get(retailer, network_size) / max(network_size, 1e-6), 0.25, 4.0)
                mean = peer_rate * size_factor * 7.0
                quant = self._negative_binomial(mean, dispersion=2.0)
                for name, q in zip(quant, QUANTILES):
                    quant[name] = int(round(max(0.0, mean + 1.2 * (quant[name] - mean))))
                quant_values = np.maximum.accumulate([quant[f"q{int(q*100)}"] for q in QUANTILES])
                quant = {f"q{int(q*100)}": int(v) for q, v in zip(QUANTILES, quant_values)}
                method = "peer_transfer"
            elif not regular:
                windows = []
                recent_start = history_end - pd.Timedelta(days=55)
                for day in pd.date_range(recent_start, history_end - pd.Timedelta(days=6), freq="D"):
                    total = self._complete_total(daily, day)
                    if total is not None:
                        windows.append(total)
                if not windows:
                    windows = [self._window_total(daily, history_end, 28) / 4.0]
                rng = np.random.default_rng(self.random_state)
                draws = rng.choice(windows, size=self.bootstrap_samples, replace=True)
                vals = np.maximum.accumulate(np.quantile(draws, QUANTILES).round().astype(int))
                quant = {f"q{int(q*100)}": int(v) for q, v in zip(QUANTILES, vals)}
                method = "intermittent_bootstrap"
            else:
                mean = self._window_total(daily, history_end, 28) / 4.0
                quant = self._negative_binomial(mean, dispersion=2.0)
                method = "four_week_moving_average"
                if use_advanced and final_models:
                    x = self._features(daily, start, retailer, sku, retailers, skus, calendar_events)
                    advanced = [max(0.0, float(final_models[q].predict(x)[0])) for q in QUANTILES]
                    if validation_by_series.get(key, (0.0, float("inf"), False))[2]:
                        advanced = np.maximum.accumulate(np.rint(advanced).astype(int))
                        quant = {f"q{int(q*100)}": int(v) for q, v in zip(QUANTILES, advanced)}
                        method = "hist_gradient_boosting_quantile"

            series_flags = []
            if flags["stockout_corrected_days"]:
                series_flags.append("stockout_days_corrected")
            if flags["history_days"] < 56:
                series_flags.append("short_history")
            if flags["stockout_fraction"] > 0.30:
                series_flags.append("stockout_censored_series")
            if flags["outlier_days_capped"]:
                series_flags.append("sales_outliers_capped")
            if flags["missing_days"]:
                series_flags.append("missing_days_present")
            if not data.attrs.get("closing_stock_available", False):
                series_flags.append("stockout_correction_unavailable")
            if calendar_events is None or calendar_events.empty:
                series_flags.append("calendar_events_unavailable")
            forecasts.append({
                "retailerId": retailer, "sku": sku,
                "q10": quant["q10"], "q50": quant["q50"], "q80": quant["q80"],
                "q90": quant["q90"], "q95": quant["q95"],
                "method": method, "seriesClass": "short_or_censored" if short_or_censored else ("regular" if regular else "intermittent"),
                "flags": series_flags,
            })

        # Score the later untouched test week with validation-selected models.
        test_quantiles = {}
        baseline_test_quantiles = {}
        for key, (daily, flags) in prepared.items():
            regular = flags["average_interval_days"] < 1.32 and flags["history_days"] >= 56 and flags["stockout_fraction"] <= 0.30
            mean = self._window_total(daily, latest_week_start - pd.Timedelta(days=1), 28) / 4.0
            quant = self._negative_binomial(mean, dispersion=2.0)
            baseline_test_quantiles[key] = quant
            selected_for_series = validation_by_series.get(key, (0.0, float("inf"), False))[2]
            if regular and use_advanced and selected_for_series and validation_models:
                x_test = self._features(daily, latest_week_start, key[0], key[1], retailers, skus, calendar_events)
                preds = np.maximum.accumulate(np.maximum(0, np.rint([
                    validation_models[q].predict(x_test)[0] for q in QUANTILES
                ]).astype(int)))
                quant = {f"q{int(q*100)}": int(v) for q, v in zip(QUANTILES, preds)}
            test_quantiles[key] = quant
        actual_by_key = {key: self._complete_total(daily, latest_week_start) for key, (daily, _) in prepared.items()}
        scored = [(actual_by_key[k], test_quantiles[k],
                   "short_or_censored" if prepared[k][1]["history_days"] < 56 or prepared[k][1]["stockout_fraction"] > 0.30 else ("regular" if prepared[k][1]["average_interval_days"] < 1.32 else "intermittent"))
                  for k in actual_by_key if actual_by_key[k] is not None]
        actual_total = sum(a for a, _, _ in scored)
        median_error = sum(abs(a - f["q50"]) for a, f, _ in scored)
        test_class_pairs: dict[str, list[tuple[float, float]]] = {}
        baseline_test_class_pairs: dict[str, list[tuple[float, float]]] = {}
        for actual, quantiles, series_class in scored:
            test_class_pairs.setdefault(series_class, []).append((actual, quantiles["q50"]))
        for key, actual in actual_by_key.items():
            if actual is None:
                continue
            flags = prepared[key][1]
            series_class = "short_or_censored" if flags["history_days"] < 56 or flags["stockout_fraction"] > 0.30 else ("regular" if flags["average_interval_days"] < 1.32 else "intermittent")
            baseline_test_class_pairs.setdefault(series_class, []).append((actual, baseline_test_quantiles[key]["q50"]))
        test_class_wape = {name: pair_wape(pairs) for name, pairs in test_class_pairs.items()}
        baseline_test_class_wape = {name: pair_wape(pairs) for name, pairs in baseline_test_class_pairs.items()}
        health_fallback_classes = [
            name for name, test_error in test_class_wape.items()
            if test_error is not None and validation_class_wape.get(name) is not None
            and test_error > 1.2 * validation_class_wape[name]
        ]
        for forecast in forecasts:
            if forecast["seriesClass"] not in health_fallback_classes:
                continue
            key = (forecast["retailerId"], forecast["sku"])
            daily = prepared[key][0]
            mean = self._window_total(daily, history_end, 28) / 4.0
            forecast.update(self._negative_binomial(mean, dispersion=2.0))
            forecast["method"] = "health_fallback_moving_average"
            forecast["flags"].append("forecast_health_fallback")
        accuracy = {
            "holdout_start": str(latest_week_start.date()),
            "holdout_days": 7,
            "series_scored": len(scored),
            "wape": median_error / actual_total if actual_total else None,
            "bias": sum(f["q50"] - a for a, f, _ in scored) / actual_total if actual_total else None,
            "coverage": {f"q{int(q*100)}": sum(a <= f[f"q{int(q*100)}"] for a, f, _ in scored) / len(scored) if scored else None for q in QUANTILES},
            "validation_start": str(validation_week_start.date()),
            "validation_baseline_wape": baseline_wape,
            "validation_advanced_wape": model_wape,
            "advanced_model_used": use_advanced,
            "validation_class_wape": validation_class_wape,
            "test_class_wape": test_class_wape,
            "test_baseline_class_wape": baseline_test_class_wape,
            "health_fallback_classes": health_fallback_classes,
        }
        self.last_report = {
            "model_version": "weekly-quantile-v1",
            "evaluation_protocol": "rolling-origin; model choice on the validation week; accuracy reported on the later untouched test week; forecasts are following seven-day totals",
            "data_rows": len(data), "series_count": len(prepared), "history_end": str(history_end.date()),
            "stockout_correction_available": bool(data.attrs.get("closing_stock_available", False)),
            "train_cutoff": str(train_cutoff.date()), "training_examples": int(len(y_train)),
            "model": "HistGradientBoostingRegressor quantile models with moving-average, bootstrap, and peer-transfer fallbacks",
            "accuracy": accuracy,
            "class_counts": pd.Series([f["seriesClass"] for f in forecasts]).value_counts().to_dict(),
            "forecasts": forecasts,
            "notes": ["The supplied sales panel is synthetic; these holdout scores do not establish field accuracy.",
                      "Missing days remain missing; forecasts carry explicit data-quality flags."]
        }
        return self.last_report

    @staticmethod
    def save(report: dict[str, Any], filepath: str) -> None:
        import json
        from pathlib import Path
        output_path = Path(filepath)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
