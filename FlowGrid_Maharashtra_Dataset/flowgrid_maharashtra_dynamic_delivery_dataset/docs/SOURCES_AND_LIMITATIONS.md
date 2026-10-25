# Sources, provenance, and limitations

## Source references

1. **Fuel prices — NDTV city/district rate snapshots, 24 Oct 2026**
   - Petrol: https://www.ndtv.com/fuel-prices/petrol-price-in-maharashtra-state
   - Diesel: https://www.ndtv.com/fuel-prices/diesel-price-in-maharashtra-state
   - Used for the 33 listed location-level baseline pairs in `locations.csv` and the 2026-10-24 rows in `fuel_prices_daily.csv`.
   - NDTV notes prices are part of dynamic fuel pricing; these are media/aggregator values and should be checked before external use.

2. **Official retail fuel-price guidance — IndianOil**
   - https://www.iocl.com/petrol-diesel-price
   - The page states retail prices are effected daily at 6 am and describes dealer-specific SMS lookups. It also warns prices may vary by outlet within a city/town/sales area.

3. **Official retail price structure — Petroleum Planning & Analysis Cell (PPAC), Government of India**
   - https://ppac.gov.in/index.php/retail-selling-price-rsp-of-petrol-diesel-and-domestic-lpg/price-build-up-of-petrol-and-diesel
   - Reference for official state retail-price build-up information. This dataset does not derive a price history from PPAC tables.

4. **Weather/rainfall/warnings — India Meteorological Department (IMD)**
   - API reference: https://api.imd.gov.in/public/api_reference.html
   - Documents endpoints including current weather, district nowcast, district-wise rainfall, district warnings, and NHAI highway forecast/warning categories. The simulated `road_conditions_daily.csv` was not populated from these live feeds.

5. **Maharashtra road project reference — Maharashtra Public Works Department**
   - https://pwd.maharashtra.gov.in/en/document-category/road-bridges/
   - Lists road and bridge project documents by region. The current dataset does not parse these project documents into a complete GIS road graph.

6. **Historical monsoon event — PIB / Ministry of Road Transport & Highways, 7 July 2026**
   - https://www.pib.gov.in/PressReleasePage.aspx?PRID=2281965&lang=1&reg=1
   - Describes temporary overtopping/flooding and movement restrictions at locations on NH-66 (Panvel–Indapur section). Included only as historical event evidence.

7. **Historical preparedness reference — PIB / Ministry of Road Transport & Highways, 17 June 2026**
   - https://www.pib.gov.in/PressReleasePage.aspx?PRID=2274178&lang=1&reg=1
   - Describes monsoon-preparedness measures on NH-48 Pune–Satara and hazards considered by NHAI. Not a road-condition observation for each route/day.

8. **Historical road-damage report — Indian Express, 26 July 2026**
   - https://indianexpress.com/article/cities/pune/malshej-ghat-closed-national-highway-61-road-damage-alternate-routes-10804395/
   - Reports damage/closure in the Malshej Ghat / NH-61 area in July 2026. Stored separately as news evidence, not treated as an official or current closure feed.

9. **OpenStreetMap road network**
   - https://www.openstreetmap.in/
   - Geofabrik India extract information: https://download.geofabrik.de/asia/india.html
   - OpenStreetMap is the road-network source used by the optional OSRM enrichment script. This package has **not** downloaded a road-network extract and the baseline distances were not calculated from the OSM graph.

10. **OSRM routing service and usage notes**
    - https://project-osrm.org/
    - Public FOSSGIS routing server notes: https://routing.openstreetmap.de/about.html
    - Public demonstration services are best-effort, rate-limited and not guaranteed. Routed baseline duration is not live traffic ETA.

## Why the road-condition fields are synthetic

A reliable road-condition truth set needs time-stamped observations at road-segment level: closures, speed, rainfall/waterlogging, potholes/surface state, restrictions, and source/validity timestamps. The public references above are useful, but they do not provide a complete validated point-by-point, live road-surface/traffic record for every lane in the delivery network. To avoid inventing “accurate” facts, this pack labels route-day weather/traffic/road risk as `SIMULATED_MAHA_ENVIRONMENT_SCENARIO` and keeps evidence-backed historical incidents in a separate ledger.

## Recommended production/hackathon refresh flow

1. Get network-routed road distance and route duration with a supported routing engine, then persist provider, fetch time, route status, and route geometry/version.
2. Refresh current district rainfall and weather warnings from IMD. Map the source timestamp, district/station, valid period and warning severity to nearby routes.
3. Ingest official NHAI/Highway Police closure/diversion notices where available. Treat a source-reported closure as a hard constraint only when location and validity dates match the route/time.
4. Verify diesel rates against an official dealer lookup before dispatch cost calculation; retain `price_source`, `observed_at`, `station/dealer`, and validity window.
5. Keep unknown/stale road conditions as `UNKNOWN` and escalate route-sensitive actions for manual review. Do not automatically substitute a simulated “dry road” record when the live source is unavailable.

## Source accuracy levels

- `OBSERVED_CITY_RATE_SNAPSHOT`: source-reported city/district fuel price for 2026-10-24; not guaranteed for every petrol station.
- `SIMULATED_SCENARIO_NOT_HISTORICAL_OBSERVATION`: generated stress-test values, not reconstructed history.
- `ESTIMATED_NOT_ROUTED`: approximate geodesic distance × road-detour factor.
- `SIMULATED_MAHA_ENVIRONMENT_SCENARIO`: scenario feature row, not measured weather/road condition.
- `HISTORICAL_OFFICIAL_NOTICE` / `HISTORICAL_NEWS_REPORT_NOT_OFFICIAL_LIVE_FEED`: separate historical event evidence, never a current closure assertion.
