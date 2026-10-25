# FlowGrid — Maharashtra Dynamic Delivery & Road-Risk Dataset

**Version:** 1.0.0  
**Generated:** 24 October 2026  
**Historical/scenario window:** 27 July–24 October 2026 (90 calendar days)  
**Use:** Hackathon prototype, forecasting/optimization experiments, route-cost simulation, and testing the FlowGrid event-driven replanning guard.

## What's included

| File | Rows | Purpose |
|---|---:|---|
| `data/locations.csv` | 33 | Maharashtra city/district-centre points, coordinates, region/terrain tags, dated city fuel snapshot |
| `data/locations.geojson` | 33 features | Point layer for MapLibre/MapCN prototypes; coordinate precision is approximate |
| `data/products_source.csv` | 100 | Original companion SKU source table retained so the generator can be rerun from the ZIP |
| `data/warehouses.csv` | 4 | Synthetic distribution-centre anchors in Mumbai, Pune, Nagpur, and Kolhapur |
| `data/vehicles.csv` | 230 | Synthetic fleet, capacity, assumed km/L, fuel consumption, and baseline speed |
| `data/route_options.csv` | 132 | Four origin options for each location; current distance is estimated until routing enrichment is run |
| `data/road_corridor_references.csv` | 8 | Reference corridors (NH-48, NH-65, NH-60, NH-160, NH-66, NH-61, Samruddhi Mahamarg, local links) |
| `data/fuel_prices_daily.csv` | 2,970 | City-level petrol/diesel values; 24 Oct 2026 snapshot is source-reported and earlier dates are simulated scenarios |
| `data/road_conditions_daily.csv` | 11,880 | Route-day rainfall, visibility, congestion, wet-road/ghat/waterlogging/rough-surface scenarios |
| `data/delivery_orders.csv` | 2,970 | One synthetic order per location per day, including estimated distance, mileage, risk, liters, and fuel cost |
| `data/delivery_order_lines.csv` | 14,542 | SKU line items for each order |
| `data/delivery_assignments.csv` | 2,970 | Baseline warehouse/vehicle assignment to test re-planning decisions |
| `data/products_100sku.csv` | 100 | Join table from the companion 100-SKU catalogue snapshot |
| `data/historical_road_event_evidence.csv` | 3 | Separate ledger of reported historical road/weather events; not current closures |
| `data/operational_events.csv` | 359 | Synthetic event stream for fuel-price shocks, road/weather risk, and urgent orders |
| `data/decision_guard_test_cases.csv` | 12 | Test fixtures for stale/untrusted/invalid events, capacity guards, and low-materiality replanning |
| `metadata.json` | — | Dataset scope, row counts, dates, and limitations |
| `validation_report.json` | — | Structural validation results after running the validator |

## Important data quality rule

This is a **hybrid dataset**, not a complete live telemetry dump. The source-backed and simulated fields are deliberately distinguished:

- **Observed snapshot:** Petrol and diesel prices by listed Maharashtra city/district as reported for **24 October 2026** by NDTV's city-price pages. Retail prices can vary by outlet; IndianOil says prices are revised daily around 6 am and provides dealer-specific lookup instructions. Verify at an actual dealer before production use.
- **Simulated scenarios:** All prior daily fuel prices; daily rainfall, visibility, congestion, road surface risk, delay, and waterlogging values; delivery orders; warehouse sites; vehicle inventory/mileage; and baseline assignments.
- **Estimated, not road-routed:** Coordinates are approximate city/district-centre anchors. Initial road kilometres are straight-line distance multiplied by regional detour assumptions. They are **not** a verified route distance and must not be described as precise road measurements.
- **Historical evidence only:** The road event ledger preserves source-reported events from June/July 2026 for testing how FlowGrid would respond. It does not assert that a road is closed today.
- **Product data:** The 100-SKU join table reuses the companion project catalogue. Some catalogue prices, weights, volumes, and shelf-life values are snapshots or estimates and should be rechecked before commercial decisions.

## Refresh route distances from OpenStreetMap/OSRM

The route enrichment script replaces estimated distances and baseline durations with network-routed values from OpenStreetMap via OSRM when the machine running it has internet access, and stores retrieved line geometry in `data/route_geometries.geojson`. Same-city origin/destination rows retain an 8 km intra-city estimate because both records share a city-centre point; precise store geocodes are required to route those rows. It does **not** provide live traffic, road-surface observations, bridge restrictions, truck-access validation, or current closures.

```bash
pip install requests
python scripts/validate_dataset.py
python scripts/enrich_osrm_routes.py --limit 5
# If the five routes look good, refresh all routes:
python scripts/enrich_osrm_routes.py
python scripts/refresh_imd_feeds.py
python scripts/validate_dataset.py
```

The OSRM public demo route service is best-effort and subject to rate limits; the script waits at least 1.1 seconds between requests. `refresh_imd_feeds.py` attempts to save raw official weather/rainfall/warning feeds separately; access may require additional authorisation and failures are logged. It does not overwrite simulation data or automatically infer road surface/closure status. For a deadline-driven hackathon demo or a commercial deployment, use an approved routing provider or self-host OSRM/Valhalla, retain provider attribution, and cache results. Do not treat a successful route response as proof that a route is open or suitable for the assigned truck.

## How to join the data

- Orders → lines: `delivery_orders.delivery_order_id = delivery_order_lines.delivery_order_id`
- Orders → route: `delivery_orders.route_id = route_options.route_id`
- Orders → vehicle: `delivery_orders.vehicle_id = vehicles.vehicle_id`
- Orders → warehouse: `delivery_orders.warehouse_id = warehouses.warehouse_id`
- Orders → location: `delivery_orders.destination_location_id = locations.location_id`
- Fuel feature: `(delivery_orders.order_date, delivery_orders.warehouse_id)` → warehouse `location_id` → `fuel_prices_daily(location_id, date)` for the **origin** diesel price. The order table already includes that matched value.
- Road condition: `(delivery_orders.order_date, delivery_orders.route_id) = road_conditions_daily(date, route_id)`
- Order lines → product: `delivery_order_lines.sku_id = products_100sku.sku_id`

## Suggested FlowGrid features

**Demand/ops features:** destination region, weekday, urgency, SKU/category, units, kg, volume, distance, lead time, vehicle class, payload utilisation, mileage km/L.  
**Fuel and cost features:** origin diesel price, litres per one-way trip, estimated fuel cost, fuel price shock/scenario flag.  
**Disruption features:** road-condition scenario, rainfall, visibility, congestion index, road-surface risk, expected delay, environment region, terrain class, and historical event proximity.  
**Target labels for scenario tests:** `road_condition_scenario`, `risk_severity_scenario`, `needs_optimizer_replan`, or a computed cost/time delta. These labels are synthetic and should only be used to test the end-to-end workflow—not to claim real-world model accuracy.

## Validation

Run:

```bash
python scripts/validate_dataset.py
```

It checks unique identifiers, route coverage, expected row counts, source/status labels, foreign-key references, and vehicle payload feasibility. Review `validation_report.json` before integrating it into your application.

## Provenance

See [`docs/SOURCES_AND_LIMITATIONS.md`](docs/SOURCES_AND_LIMITATIONS.md) and [`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md). The `scripts/refresh_imd_feeds.py` command saves IMD responses as raw JSON snapshots in `data/live_source_snapshots/`; normalise and time-match them before using them to alter route plans.
