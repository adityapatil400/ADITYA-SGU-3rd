# Flipkart Product Catalog: Source and Data-Quality Notes

**Snapshot date recorded in the CSV:** `2026-10-24`  
**Catalog rows:** 100  
**Important:** These rows were transcribed from visible public category-listing pages. They are not a Flipkart API export, a live feed, a guaranteed product-detail URL, or an endorsement by Flipkart.

## Category listing sources

The `source_listing_url` column in each row points to the corresponding category/brand listing used as the source. Listings can change and prices can vary based on seller, selected pack/variant, PIN code, offer, stock and time. Verify each product and displayed price before commercial use.

| Product category | Rows | Public source |
|---|---:|---|
| Edible oil / Fortune | 15 | [Flipkart Fortune edible oil listings](https://www.flipkart.com/food-products/edible-oil/fortune~brand/pr?sid=eat%2C18p) |
| Pulses / Tata Sampann | 15 | [Flipkart Tata Sampann dal listings](https://www.flipkart.com/food-products/pulses/dal/tata-sampann~brand/pr?sid=eat%2Canl%2Covb) |
| Salt / Tata | 10 | [Flipkart Tata salt listings](https://www.flipkart.com/food-products/salt/tata~brand/pr?sid=eat%2C5ht) |
| Dairy powder / Amul | 6 | [Flipkart Amul milk powder listings](https://www.flipkart.com/food-products/milk-powder/amul~brand/pr?sid=eat%2Cxog) |
| Breakfast cereals / oats / muesli | 15 | [Flipkart breakfast cereal listings](https://www.flipkart.com/food-products/breakfast-cereal/pr?sid=eat%2Ccpy) |
| Rice | 14 | [Flipkart rice listings](https://www.flipkart.com/food-products/rice/pr?sid=eat%2Cyul) |
| Tea | 10 | [Flipkart tea listings](https://www.flipkart.com/food-products/tea-powder/pr?sid=eat%2Cfpm) |
| Noodles / sauces / seasoning / Maggi | 10 | [Flipkart Maggi listings](https://www.flipkart.com/food-products/maggi~brand/pr?sid=eat) |
| Honey / Dabur | 5 | [Flipkart Dabur honey listings](https://www.flipkart.com/food-products/honey/dabur~brand/pr?sid=eat%2Clnz) |

## Field provenance

- **Observed listing snapshot:** `product_name`, `brand`, `category_id`, `pack_size_text`, `listing_price_inr`, `source_listing_url`, `source_capture_date`, `source_platform`, `price_snapshot_status`, `catalog_data_status`, `data_origin`.
- **Approximate/synthetic product logistics:** `logistics_weight_kg`, `logistics_volume_l`, `weight_basis`, `logistics_specs_source`, `estimated_total_shelf_life_days`, `min_remaining_shelf_life_days`, and `storage_class`. Pack sizes sometimes do not translate cleanly into weight (e.g. a liquid volume); these are planning estimates, not certified shipping dimensions or product-safety facts.
- **Synthetic commercial and operational values:** `unit_cost`, stock quantities, batch expiry dates, daily sales, promotions, demand anomalies, warehouse/store coordinates, route distances and ETAs, vehicle costs, agents, orders and assignments.

## Modeling guidance

1. Use catalog listing prices as **price snapshot features**, not verified procurement costs.
2. Keep `data_origin`, `source_listing_url` and status labels in the data lineage.
3. Never present synthetic daily sales as Flipkart sales or customer demand.
4. Do not claim that a synthetic batch expiry or model-estimated shelf-life value came from the product packaging.
5. For forecasting validation against observed retail sales, use a suitable public historical dataset such as [Kaggle M5 Forecasting Accuracy](https://www.kaggle.com/competitions/m5-forecasting-accuracy/data), subject to its terms, and report results separately from synthetic demonstrations.
6. Refresh the listing snapshot when reused for a later demo, preserve the previous capture date, and record any changed price/pack fields.
