# FlowGrid Product Catalog + 5× Synthetic Demand Dataset

This package combines a **100-item product master assembled from public Flipkart category-listing snapshots** with a larger synthetic FMCG operations dataset for FlowGrid. It is designed for model development, data-pipeline testing, and demonstration of inventory allocation and dispatch logic.

## What is real and what is synthetic?

- **Observed from public Flipkart listing pages:** product names, brands, pack descriptions, category, listing-page URL, and a displayed price snapshot recorded on `2026-10-24`.
- **Approximate or synthetic product attributes:** logistics weight/volume where not directly verified, unit procurement cost, storage/shelf-life planning fields, and other estimated logistic values. Consult each row's source/status columns.
- **Entirely synthetic:** sales history, demand anomalies, promotions, warehouses, store locations, inventory quantities, batch/expiry dates, agents, vehicles, route distances/ETAs, orders, assignments, and transport costs.

Flipkart price and availability can vary by seller, serviceable PIN code, variant, offer, and capture time. The catalog uses public **category listing URLs**, not a guaranteed product-detail URL/API for every SKU. Refresh and manually verify prices before using them for purchasing or commercial decisions. This dataset is not an official Flipkart API export and should not be represented as real sales or operational data.

## Default dataset size

The current default contains:

- 30 synthetic retail stores
- 100 product/SKU rows using Flipkart listing snapshots
- 3 synthetic warehouses
- 15 synthetic vehicles and 15 synthetic agents
- 365 days of daily demand per store/SKU pair
- **1,095,000 raw daily sales records** = `30 × 100 × 365`
- **1,011,000 model-ready rows** after the 28-day lag/rolling-feature warm-up
- Chronological exports: 843,000 train rows, 84,000 validation rows, and 84,000 test rows

Compared with the previous 30-store × 20-SKU × 365-day data, this catalog expansion raises raw sales volume from 219,000 to 1,095,000 rows (5×). It is also 15× the original 10-store × 20-SKU × 365-day smoke dataset. The `--scale-factor` argument scales stores, agents, vehicles, and orders; `--skus` selects catalog products.

## Included files

### Product master and operational data (`data/*.csv`)

| File | Default rows | Purpose |
|---|---:|---|
| `flipkart_product_catalog.csv` | 100 | Product names, brands, pack labels, observed listing-price snapshots, source URLs and provenance |
| `skus.csv` | 100 | FlowGrid SKU master joined to the catalog; includes product, pack, approximate logistics, listing price, and synthetic unit cost |
| `synthetic_sales_daily.csv` | 1,095,000 | Daily store/SKU sales proxy with calendar, simulated promotion and diagnostic anomaly fields |
| `warehouses.csv` | 3 | Synthetic warehouse locations and capacities |
| `stores.csv` | 30 | Synthetic store locations, service zones, priorities, receiving windows |
| `inventory_batches.csv` | 600 | Synthetic stock quantities, lot identifiers, received/expiry dates and quality status |
| `vehicles.csv` | 15 | Synthetic payload capacities and transport-cost assumptions |
| `agents.csv` | 15 | Synthetic availability, shift, vehicle and location status |
| `route_estimates.csv` | 90 | Synthetic warehouse/store pair distance, ETA, cost, and straight-line GeoJSON |
| `orders.csv` | 60 | Synthetic order headers, urgency and deadlines |
| `order_lines.csv` | 120 | Synthetic product quantities for each order |
| `assignments.csv` | 5 | Coherent sample assignments linking order, agent, vehicle, route and store |
| `dataset_manifest.json` | 1 | Generation settings, row counts, provenance and limitations |

### Model-ready data (`data/model_ready/`)

- `sales_features.csv`: 1,011,000 rows with lag, rolling and calendar features.
- `train.csv`: 843,000 chronological rows.
- `validation.csv`: 84,000 chronological rows.
- `test.csv`: 84,000 later, held-out rows.
- `feature_schema.json`: target, feature allowlist, split protocol, and excluded diagnostic fields.

The first 28 days of each store/SKU history are removed because lag-28 and rolling-28 features are unavailable. All exported CSVs are intended to have **zero null values and zero blank strings**. Optional states use explicit values such as `NONE` rather than an empty cell.

## Product fields and source provenance

The most relevant fields are:

- `sku_id`, `product_name`, `brand`, `category_id`, `pack_size_text`
- `listing_price_inr`, `source_listing_url`, `source_capture_date`, `price_snapshot_status`
- `logistics_weight_kg`, `logistics_volume_l`, `logistics_specs_source`
- `estimated_total_shelf_life_days`, `min_remaining_shelf_life_days`, `storage_class`
- `catalog_data_status`, `data_origin`

`skus.csv` additionally contains FlowGrid's stable `source_item_id`, `unit_cost`, `unit_cost_source`, and planning-unit fields. Listing price is an observed snapshot; procurement cost, sales/demand and operational logistics values are synthetic or estimated. Do not use estimated expiry or weight as verified food-safety or carrier data.

See [`docs/flipkart_product_data_sources.md`](docs/flipkart_product_data_sources.md) for category source URLs and limitations.

## Windows setup

Recommended: 64-bit Python 3.11. In PowerShell, from this folder:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\generate_flowgrid_demo.py --scale-factor 3 --skus 100 --days 365
.\.venv\Scripts\python.exe scripts\validate_dataset.py
```

The `generate_3x_data.bat` file runs generation and validation sequentially. The generator overwrites generated CSVs under `data/` by default. Use `--output-dir` to write data elsewhere. Generation reads product rows from `data/flipkart_product_catalog.csv`; use `--product-catalog` if your catalog is in another location.

## Adjust data volume

```powershell
# Default: 30 stores × 100 SKUs × 365 days = 1,095,000 sales rows
.\.venv\Scripts\python.exe scripts\generate_flowgrid_demo.py --scale-factor 3 --skus 100 --days 365

# Smaller local smoke run: 30 stores × 30 SKUs × 180 days = 162,000 rows
.\.venv\Scripts\python.exe scripts\generate_flowgrid_demo.py --scale-factor 3 --skus 30 --days 180

# Longer history: 30 stores × 100 SKUs × 730 days = 2,190,000 rows
.\.venv\Scripts\python.exe scripts\generate_flowgrid_demo.py --scale-factor 3 --skus 100 --days 730
```

The product catalog currently has 100 entries. `--skus` cannot exceed the number of rows in the catalog unless more product entries are added first. When using a machine with limited free RAM, start with the smaller smoke run before building all model features.

## Build model features and train the baseline comparison

The default generator creates model-ready files automatically. To recreate them separately:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_model_features.py --input data\synthetic_sales_daily.csv --output-dir data\model_ready
```

To run the Random Forest versus seasonal-naive evaluation:

```powershell
.\.venv\Scripts\python.exe scripts\train_forecasting.py --n-estimators 50 --max-depth 12 --n-jobs 2
```

The training script compares validation WAPE with a seasonal-naive `lag_7` prediction and adopts Random Forest only if it clears the configured relative improvement threshold. Otherwise, the baseline remains active. It writes metrics and any passing model artefact to `artifacts/`.

`synthetic_anomaly_flag` is a target-day diagnostic label and **must not be used as a predictor**, because it would leak information about an event that may not be known at prediction time. `synthetic_promo_flag` is assumed known at the prediction cutoff. Feature availability must match the real deployment scenario.

## Validate data integrity

```powershell
.\.venv\Scripts\python.exe scripts\validate_dataset.py
```

The validator checks required columns, non-null/blank values, primary keys, foreign-key relationships, full sales-panel coverage, data ranges, finite numeric fields, inventory arithmetic, GeoJSON parsing, assignment status, model split consistency, and manifest row counts. Passing structural validation is not evidence that synthetic data represents actual retail demand or that the model will be accurate.

## Public historical data for external validation

For a separate public-sales evaluation, obtain the [Kaggle M5 Forecasting Accuracy data](https://www.kaggle.com/competitions/m5-forecasting-accuracy/data), confirm dataset terms, then use `scripts/prepare_m5_subset.py` and `scripts/prepare_model_features.py`. Public sales history does not include FlowGrid's warehouse inventory, batch expiry, vehicles, agent status or routing data; keep those separate. Do not blend synthetic and public observations without preserving `data_origin` and describing the evaluation design.

## GitHub team-collaboration note

The generated raw and model-ready CSVs are large, and `train.csv`/`sales_features.csv` can exceed GitHub's 100 MB single-file limit. The repository `.gitignore` excludes generated sales/feature exports but keeps the small product catalog and code. Share the ZIP for a pre-generated copy, or let teammates clone the code and regenerate data locally. Do not commit API secrets or `.env` files.
