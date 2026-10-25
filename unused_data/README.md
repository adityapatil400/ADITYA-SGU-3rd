# FlowGrid: Archived & Legacy Data Repository (`unused_data/`)

This directory houses legacy, prototype, and unused data files that have been decoupled from the primary distribution pipeline to maintain a clean root workspace.

---

## 1. Archived Data Files Manifest

| Filename | File Size | Description & Original Purpose | Reason for Archival |
| :--- | :---: | :--- | :--- |
| **`synthetic_sales_daily.csv`** | 18.1 MB | Prototype 20-SKU synthetic daily sales table (219,000 records across 30 stores). | **Superseded** by the official Flipkart 100-SKU catalog (`flowgrid_flipkart_100sku/data/synthetic_sales_daily.csv`: 1,095,000 records). |
| **`sales_features.csv`** | 31.7 MB | Prototype feature engineering output for the 20-SKU mock slice. | **Superseded** by model-ready features in `flowgrid_flipkart_100sku/data/model_ready/sales_features.csv` (1,011,000 records). |
| **`train.csv`** | 26.4 MB | Prototype training split for the 20-SKU mock slice. | **Superseded** by `flowgrid_flipkart_100sku/data/model_ready/train.csv` (843,000 records). |
| **`validation.csv`** | 2.7 MB | Prototype validation split for the 20-SKU mock slice. | **Superseded** by `flowgrid_flipkart_100sku/data/model_ready/validation.csv` (84,000 records). |
| **`test.csv`** | 2.6 MB | Prototype holdout test split for the 20-SKU mock slice. | **Superseded** by `flowgrid_flipkart_100sku/data/model_ready/test.csv` (84,000 records). |

---

## 2. Active Operational Data Location

The active, production-grade FMCG distribution dataset is structured under `flowgrid_flipkart_100sku/data/`:

* **`skus.csv`**: 100 Flipkart FMCG SKUs (Biscuits, Edible Oils, Soaps, Shampoos, Packaged Foods) with physical logistics (weights, volumes, shelf life, listing prices).
* **`inventory_batches.csv`**: 600 discrete inventory batches with lot numbers and expiry dates for FEFO (First-Expired, First-Out) optimization.
* **`warehouses.csv`**: 3 regional fulfillment depots (Pune Central, Pune North, Pimpri).
* **`stores.csv`**: 30 regional retail destinations across geographic zones.
* **`route_estimates.csv`**: 90 verified route corridors with distance in kilometers, ETA, and transportation cost.
* **`vehicles.csv`**: 15 delivery vehicles with dual payload weight (kg) and volume (L) capacity constraints.
* **`model_ready/`**: Memory-mapped, zero-leakage pre-computed splits for AI demand forecasting (`train.csv`, `validation.csv`, `test.csv`).

---

## 3. Backward Compatibility Safeguard

All FlowGrid runners (`pipeline_runner.py`, `prd_test_suite.py`, `flowgrid_cli.py`) include automatic candidate search paths. If a user or legacy script requests `synthetic_sales_daily.csv`, the system automatically resolves between `flowgrid_flipkart_100sku/data/` and `unused_data/` without raising file errors.
