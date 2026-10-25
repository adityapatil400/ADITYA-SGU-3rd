"""
Feature Engineering & Data Pipeline for FlowGrid
Ensures zero data leakage, chronological splitting, and strict data sanitization.
"""

import pandas as pd
import numpy as np
from typing import Tuple, List, Dict, Any


class FeaturePipeline:
    def __init__(self, lag_days: List[int] = None, rolling_windows: List[int] = None):
        self.lag_days = lag_days or [1, 2, 7, 14, 28]
        self.rolling_windows = rolling_windows or [7, 14, 28]
        self.feature_columns: List[str] = []
        self.store_mapping: Dict[str, int] = {}
        self.sku_mapping: Dict[str, int] = {}

    def fit_transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Engineers features with strict temporal ordering to prevent data leakage.
        """
        data = df.copy()
        
        # Ensure correct datetime and sort
        data['date'] = pd.to_datetime(data['date'])
        data = data.sort_values(['store_id', 'sku_id', 'date']).reset_index(drop=True)

        # 1. Store and SKU Categorical Encoding
        if not self.store_mapping:
            unique_stores = sorted(data['store_id'].unique())
            self.store_mapping = {s: i for i, s in enumerate(unique_stores)}
        if not self.sku_mapping:
            unique_skus = sorted(data['sku_id'].unique())
            self.sku_mapping = {s: i for i, s in enumerate(unique_skus)}

        data['store_code'] = data['store_id'].map(self.store_mapping)
        data['sku_code'] = data['sku_id'].map(self.sku_mapping)

        # 2. Calendar & Cyclical Features
        data['day_of_week'] = data['date'].dt.day_of_week
        data['month'] = data['date'].dt.month
        data['day_of_month'] = data['date'].dt.day
        data['is_weekend'] = (data['day_of_week'] >= 5).astype(int)

        # Cyclical transformations for periodicity
        data['sin_dow'] = np.sin(2 * np.pi * data['day_of_week'] / 7.0)
        data['cos_dow'] = np.cos(2 * np.pi * data['day_of_week'] / 7.0)
        data['sin_month'] = np.sin(2 * np.pi * data['month'] / 12.0)
        data['cos_month'] = np.cos(2 * np.pi * data['month'] / 12.0)

        # 3. Price & Promo Features. Normalize against prior prices only so
        # future prices cannot affect historical or holdout features.
        prior_sku_price = data.groupby(['store_id', 'sku_id'], sort=False)['unit_price'].transform(
            lambda values: values.shift(1).expanding(min_periods=1).mean()
        )
        data['price_ratio'] = data['unit_price'] / prior_sku_price.replace(0, np.nan)
        data['price_ratio'] = data['price_ratio'].replace([np.inf, -np.inf], np.nan).fillna(1.0)
        if 'synthetic_promo_flag' not in data.columns:
            data['synthetic_promo_flag'] = 0
        data['synthetic_promo_flag'] = data['synthetic_promo_flag'].fillna(0).astype(int)

        # 4. Strictly Past Lag Features (Shifted by store and SKU)
        # Shift ensures demand from time t is NEVER accessible at time t prediction
        grouped = data.groupby(['store_id', 'sku_id'])['units_sold']

        for lag in self.lag_days:
            data[f'lag_{lag}'] = grouped.shift(lag)

        # 5. Strictly Past Rolling Window Aggregations (Shifted by 1 first)
        shifted_demand = grouped.shift(1)
        for w in self.rolling_windows:
            data[f'rolling_mean_{w}'] = shifted_demand.groupby([data['store_id'], data['sku_id']]).transform(
                lambda x: x.rolling(w, min_periods=1).mean()
            )
            data[f'rolling_std_{w}'] = shifted_demand.groupby([data['store_id'], data['sku_id']]).transform(
                lambda x: x.rolling(w, min_periods=1).std()
            ).fillna(0.0)

        # 6. Warmup rows have no past observations. Use neutral zero values;
        # whole-series means/medians would leak future target information.
        for lag in self.lag_days:
            data[f'lag_{lag}'] = data[f'lag_{lag}'].fillna(0.0)

        for w in self.rolling_windows:
            data[f'rolling_mean_{w}'] = data[f'rolling_mean_{w}'].fillna(0.0)
            data[f'rolling_std_{w}'] = data[f'rolling_std_{w}'].fillna(0.0)

        # 7. Record feature column list
        self.feature_columns = [
            'store_code', 'sku_code', 'unit_price', 'price_ratio',
            'day_of_week', 'month', 'day_of_month', 'is_weekend',
            'sin_dow', 'cos_dow', 'sin_month', 'cos_month',
            'synthetic_promo_flag'
        ] + [f'lag_{l}' for l in self.lag_days] + \
          [f'rolling_mean_{w}' for w in self.rolling_windows] + \
          [f'rolling_std_{w}' for w in self.rolling_windows]

        # Final sanitization
        data = self.sanitize_features(data)
        return data

    @staticmethod
    def sanitize_features(df: pd.DataFrame) -> pd.DataFrame:
        """
        Guarantees that no NaNs, infinite values, or corrupted types enter the model.
        """
        clean_df = df.copy()
        numeric_cols = clean_df.select_dtypes(include=[np.number]).columns
        
        # Replace inf with nan, then fillna with 0
        clean_df[numeric_cols] = clean_df[numeric_cols].replace([np.inf, -np.inf], np.nan)
        clean_df[numeric_cols] = clean_df[numeric_cols].fillna(0)
        
        # Ensure units_sold is non-negative
        if 'units_sold' in clean_df.columns:
            clean_df['units_sold'] = np.clip(clean_df['units_sold'], 0, None)
            
        return clean_df

    @staticmethod
    def split_chronological(df: pd.DataFrame, holdout_days: int = 30) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Splits data chronologically into train and holdout sets.
        Completely prevents future leakage into the training set.
        """
        df = df.copy()
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        max_date = df['date'].max()
        split_date = max_date - pd.Timedelta(days=holdout_days)

        train_set = df[df['date'] <= split_date].copy().reset_index(drop=True)
        holdout_set = df[df['date'] > split_date].copy().reset_index(drop=True)

        return train_set, holdout_set
