#!/usr/bin/env python3
"""Replace estimated route distances with OpenStreetMap/OSRM routed distance/time.

Usage (from this folder):
  python scripts/enrich_osrm_routes.py --limit 5
  python scripts/enrich_osrm_routes.py

The public OSRM demo server is best-effort, limited to low-volume use, and is not a
live traffic or road-condition feed. Requests are deliberately spaced by 1.1 seconds.
For a production/hackathon presentation, prefer a self-hosted OSRM/Valhalla instance
or an approved commercial routing provider. OpenStreetMap attribution is required.
"""
from pathlib import Path
import argparse, csv, time, sys, json
from urllib.parse import quote
try:
    import requests
except ImportError:
    print('Missing dependency: pip install requests', file=sys.stderr); raise

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
ROUTES=DATA/'route_options.csv'
ORDERS=DATA/'delivery_orders.csv'
FUEL=DATA/'fuel_prices_daily.csv'
ROAD=DATA/'road_conditions_daily.csv'
BASE='https://router.project-osrm.org/route/v1/driving'
GEOJSON_OUT=DATA/'route_geometries.geojson'


def read_csv(path):
    with path.open(newline='',encoding='utf-8-sig') as f: return list(csv.DictReader(f))

def write_csv(path, rows):
    if not rows: return
    with path.open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0].keys()),extrasaction='ignore')
        w.writeheader();w.writerows(rows)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--limit',type=int,default=0,help='route count to refresh; 0=all')
    parser.add_argument('--overwrite',action='store_true',help='refresh routes already marked as OSM-routed')
    parser.add_argument('--sleep',type=float,default=1.1,help='seconds between requests; keep >=1.0 for public demo endpoint')
    args=parser.parse_args()
    if args.sleep < 1.0:
        parser.error('--sleep must be >= 1.0 seconds for the public demo endpoint')
    routes=read_csv(ROUTES)
    candidates=[r for r in routes if args.overwrite or r.get('route_distance_status')!='OSM_ROUTED_DISTANCE']
    geo_features={}
    if GEOJSON_OUT.exists():
        try:
            existing=json.loads(GEOJSON_OUT.read_text(encoding='utf-8'))
            geo_features={f.get('properties',{}).get('route_id'):f for f in existing.get('features',[]) if f.get('properties',{}).get('route_id')}
        except Exception:
            geo_features={}
    if args.limit>0: candidates=candidates[:args.limit]
    session=requests.Session()
    session.headers.update({'User-Agent':'FlowGrid-Maharashtra-Dataset/1.0 (open-source hackathon prototype; route refresh)'} )
    successes=0; failures=[]
    for idx,r in enumerate(candidates):
        # Warehouse and destination share a city-centre anchor for this row; actual store
        # addresses are unavailable, so don't turn that coincident-point case into a false 0 km.
        if r['origin_location_id']==r['destination_location_id']:
            r['route_distance_status']='ESTIMATED_NOT_ROUTED'
            r['route_distance_method']='synthetic_intra_city_radius_assumption_store_address_missing'
            r['routing_source']='not routed: origin and destination have same approximate city-centre coordinate; add store-level coordinates'
            continue
        coords=f"{r['origin_longitude']},{r['origin_latitude']};{r['destination_longitude']},{r['destination_latitude']}"
        url=f'{BASE}/{coords}?overview=full&geometries=geojson&alternatives=false&steps=false'
        try:
            resp=session.get(url,timeout=35)
            resp.raise_for_status()
            payload=resp.json()
            if payload.get('code')!='Ok' or not payload.get('routes'):
                raise RuntimeError(f"OSRM response code={payload.get('code')}")
            route=payload['routes'][0]
            r['road_distance_km_initial_estimate']=r.get('road_distance_km_initial_estimate') or r['road_distance_km']
            r['road_distance_km']=round(route['distance']/1000,2)
            r['route_distance_status']='OSM_ROUTED_DISTANCE'
            r['route_distance_method']='OSRM_car_profile_from_OpenStreetMap_network'
            r['baseline_drive_time_minutes_est']=round(route['duration']/60,1)
            r['baseline_drive_time_status']='OSM_ROUTED_DURATION_BASELINE_NOT_LIVE_TRAFFIC'
            r['routing_source']='https://router.project-osrm.org; based on OpenStreetMap; no live traffic guarantee'
            r['route_geometry_status']='OSM_ROUTED_GEOJSON' if route.get('geometry') else 'NOT_RETURNED'
            if route.get('geometry'):
                geo_features[r['route_id']]={
                    'type':'Feature',
                    'geometry':route['geometry'],
                    'properties':{
                        'route_id':r['route_id'],
                        'warehouse_id':r['warehouse_id'],
                        'destination_location_id':r['destination_location_id'],
                        'road_distance_km':r['road_distance_km'],
                        'baseline_drive_time_minutes':r['baseline_drive_time_minutes_est'],
                        'data_status':'OSM_ROUTED_NETWORK_GEOMETRY_NOT_LIVE_TRAFFIC'
                    }
                }
            successes+=1
        except Exception as e:
            failures.append({'route_id':r['route_id'],'error':str(e)})
            # Keep original estimated route and status if request fails.
        finally:
            if idx+1<len(candidates): time.sleep(args.sleep)
    write_csv(ROUTES,routes)
    GEOJSON_OUT.write_text(json.dumps({'type':'FeatureCollection','features':list(geo_features.values())},ensure_ascii=False),encoding='utf-8')

    # Refresh the active route length and estimated fuel cost for delivery orders.
    route_map={r['route_id']:r for r in routes}
    fuel_rows=read_csv(FUEL)
    fuel_lookup={(r['date'],r['location_id']):r for r in fuel_rows}
    road_rows=read_csv(ROAD)
    road_lookup={(r['date'],r['route_id']):r for r in road_rows}
    orders=read_csv(ORDERS)
    for o in orders:
        rr=route_map[o['route_id']]
        km=float(rr['road_distance_km'])
        o['road_distance_km_current']=round(km,2)
        o['route_distance_status']=rr['route_distance_status']
        # Recalculate fuel cost; road/mileage effects are still scenario assumptions.
        mileage=float(o['vehicle_mileage_km_per_litre_assumed'])
        cond=road_lookup.get((o['order_date'],o['route_id']),{})
        reduction=float(cond.get('estimated_speed_reduction_fraction') or 0)
        congestion=float(cond.get('traffic_congestion_index_scenario') or 0)
        adjusted=max(1,mileage*(1-reduction*0.55-congestion*0.08))
        litres=km/adjusted if km>0 else 0.0
        price=float(o['origin_diesel_price_inr_per_litre'])
        o['fuel_litres_est_one_way']=round(litres,3)
        o['fuel_cost_inr_est_one_way']=round(litres*price,2)
        o['fuel_cost_inr_est_round_trip']=round(litres*price*2,2)
        o['fuel_cost_status']='COST_ESTIMATE_USING_CURRENT_ROUTE_DISTANCE; fuel price source/status is in fuel_prices_daily.csv; traffic/mileage adjustments remain modeled'
    write_csv(ORDERS,orders)
    report={'attempted_routes':len(candidates),'successful_routes':successes,'failed_routes':len(failures),
            'source':'OpenStreetMap via OSRM public demo route service','date_refresh_run_local':time.strftime('%Y-%m-%d %H:%M:%S'),
            'warning':'OSRM route distance/time is not live traffic, current road surface condition, road closure or legal truck-access validation.',
            'failures':failures[:100]}
    (ROOT/'route_refresh_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='failures'},indent=2))
    if failures: print(f"See route_refresh_report.json for {len(failures)} failure(s).")

if __name__=='__main__': main()
