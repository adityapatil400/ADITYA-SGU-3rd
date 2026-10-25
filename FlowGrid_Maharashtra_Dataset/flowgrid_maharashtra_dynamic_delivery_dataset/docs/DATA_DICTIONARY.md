# Data dictionary

## `locations.csv`
- `location_id`: stable key, e.g. `L-MH-PUN`.
- `latitude`, `longitude`: approximate town/district-centre point in decimal degrees.
- `environment_region`, `terrain_class`: manually assigned environment tags for scenario generation, not official administrative classifications.
- `petrol_inr_per_litre_2026_10_24`, `diesel_inr_per_litre_2026_10_24`: source-reported values for 24 October 2026.

## `warehouses.csv`
- `warehouse_id`: stable key; sites are demo-only anchors.
- `daily_dispatch_capacity_kg_assumed`: synthetic dispatch assumption, not measured facility capability.

## `vehicles.csv`
- `payload_capacity_kg`: assumed maximum payload for this synthetic truck record.
- `mileage_km_per_litre_assumed`: synthetic benchmark with variation; replace with telematics or fleet-maintenance data.
- `fuel_consumption_l_per_100km_assumed`: calculated as `100 / mileage_km_per_litre_assumed`.

## `route_options.csv`
- One candidate route per warehouse-location pair.
- `straight_line_distance_km`: haversine distance between coordinate anchors.
- `road_distance_km_initial_estimate`: baseline estimate created by regional detour factors.
- `road_distance_km`: active route distance (initial estimate until optional OSRM refresh succeeds).
- `route_distance_status`: `ESTIMATED_NOT_ROUTED` until successfully refreshed to `OSM_ROUTED_DISTANCE`.
- `baseline_drive_time_minutes_est`: estimate until refreshed; then OSRM's baseline route duration, still not traffic-aware.
- `representative_corridor_id`: corridor tag for context, **not a confirmed path match**.

## `fuel_prices_daily.csv`
- `date`, `location_id`: composite key for location-day fuel pricing.
- `petrol_inr_per_litre`, `diesel_inr_per_litre`: numeric values for cost modeling.
- `price_data_status`: observed snapshot only on 2026-10-24; all earlier dates are explicitly simulated.

## `road_conditions_daily.csv`
- Composite key: `date`, `route_id`.
- `rainfall_mm_scenario`, `visibility_km_scenario`, `traffic_congestion_index_scenario`, `pothole_or_surface_risk_index_scenario`, `waterlogging_flag_scenario`: modeled features for stress testing.
- `road_condition_scenario`, `risk_severity_scenario`: simulated scenario labels.
- `estimated_weather_traffic_delay_minutes`: scenario-generated extra delay. This is not an official ETA.

## `delivery_orders.csv`
- One synthetic order per destination per day.
- `shipment_weight_kg_est`, `shipment_volume_l_est`, `order_value_inr_est`: computed from randomly generated order lines and catalogue attributes.
- `road_distance_km_initial_estimate`, `road_distance_km_current`: initial route estimate and active route distance; an optional routing refresh updates the current value.
- `vehicle_mileage_km_per_litre_assumed`: joins the assigned vehicle's mileage assumption.
- `fuel_litres_est_one_way`, `fuel_cost_inr_est_one_way`, `fuel_cost_inr_est_round_trip`: computed estimates using origin-city diesel price and modeled mileage adjustment. One-way route distance is used; round-trip cost is twice the estimate.
- `record_status`: synthetic order scenario.

## `delivery_order_lines.csv`
- `delivery_order_id`, `line_number`: composite line key.
- `sku_id`: foreign key into `products_100sku.csv`.
- Unit weight, unit volume and price retain source-catalogue fields where available but can be estimated snapshot values.

## `delivery_assignments.csv`
- Baseline assignment heuristic: nearest estimated warehouse, then smallest vehicle capacity class that can fit the shipment.
- It is not the result of OR-Tools, a production route optimizer, or a capacity-constrained multi-stop dispatch plan.

## `road_corridor_references.csv` and `historical_road_event_evidence.csv`
- Corridor names provide reference context only.
- Event ledger rows capture source-reported historical events with date and URL; they are not current status.


## `locations.geojson`
- GeoJSON `FeatureCollection` with one approximate city/district-centre point per location.
- Coordinate order follows GeoJSON: `[longitude, latitude]`.
- Use for visual orientation only; not suitable for store-level navigation.

## `operational_events.csv`
- Synthetic events representing material fuel-price moves, high road/weather-risk scenarios, and urgent store orders.
- `source_status` is explicitly `SYNTHETIC_TEST_EVENT`; these are test messages, not live alerts.
- `event_payload_json` holds the event-specific fields as compact JSON. A production system should validate schema, source trust, event age, deduplication/idempotency, and the relevant business constraints before planning or committing.

## `decision_guard_test_cases.csv`
- Twelve synthetic test cases and expected guard actions for stale/untrusted events, payload or inventory failures, provider outages, and low-materiality changes.
- These are acceptance-test fixtures, not learned labels or actual recorded incidents.

## `products_source.csv`
- Source table from the companion 100-SKU catalogue used to regenerate `products_100sku.csv` and order-line examples.
