#!/usr/bin/env python3
"""Structural and referential-integrity checks for the FlowGrid dataset."""
from pathlib import Path
import csv, json, collections, sys
ROOT=Path(__file__).resolve().parents[1]; DATA=ROOT/'data'

def read(name):
    with (DATA/name).open(newline='',encoding='utf-8-sig') as f: return list(csv.DictReader(f))
def assert_unique(rows,key,name,errors):
    vals=[r.get(key,'') for r in rows]
    if len(vals)!=len(set(vals)): errors.append(f'{name}: duplicate {key}')
def main():
    e=[]; stats={}
    files={p.name:read(p.name) for p in DATA.glob('*.csv')}
    for name,rows in files.items(): stats[name]={'rows':len(rows),'columns':len(rows[0]) if rows else 0}
    loc=files['locations.csv']; wh=files['warehouses.csv']; routes=files['route_options.csv']; orders=files['delivery_orders.csv']; lines=files['delivery_order_lines.csv']; vehicles=files['vehicles.csv']; skus=files['products_100sku.csv']; fuel=files['fuel_prices_daily.csv']; road=files['road_conditions_daily.csv']; assigns=files['delivery_assignments.csv']
    assert_unique(loc,'location_id','locations.csv',e); assert_unique(wh,'warehouse_id','warehouses.csv',e)
    assert_unique(routes,'route_id','route_options.csv',e); assert_unique(orders,'delivery_order_id','delivery_orders.csv',e)
    assert_unique(vehicles,'vehicle_id','vehicles.csv',e)
    if len(loc)!=33: e.append(f'Expected 33 locations, got {len(loc)}')
    if len(wh)!=4: e.append(f'Expected 4 warehouses, got {len(wh)}')
    if len(routes)!=len(loc)*len(wh): e.append(f'Expected locations x warehouses routes={len(loc)*len(wh)}, got {len(routes)}')
    dates=sorted({r['date'] for r in fuel})
    if len(dates)!=90: e.append(f'Expected 90 fuel dates, got {len(dates)}')
    if sum(r['price_data_status']=='OBSERVED_CITY_RATE_SNAPSHOT' for r in fuel)!=33: e.append('Expected one observed fuel snapshot per location (33 rows)')
    if any(r['data_status']!='SIMULATED_MAHA_ENVIRONMENT_SCENARIO' for r in road): e.append('Road condition rows contain unexpected observed/live status')
    route_ids={r['route_id'] for r in routes}; loc_ids={r['location_id'] for r in loc}; wh_ids={r['warehouse_id'] for r in wh}; vehicle_map={r['vehicle_id']:r for r in vehicles}; sku_ids={r['sku_id'] for r in skus}; order_ids={r['delivery_order_id'] for r in orders}
    if any(r['route_id'] not in route_ids for r in orders): e.append('Delivery orders reference missing routes')
    if any(r['destination_location_id'] not in loc_ids for r in routes): e.append('Routes reference missing destination locations')
    if any(r['warehouse_id'] not in wh_ids for r in routes): e.append('Routes reference missing warehouses')
    if any(r['delivery_order_id'] not in order_ids or r['sku_id'] not in sku_ids for r in lines): e.append('Order lines contain dangling order/SKU references')
    if len(assigns)!=len(orders): e.append('Assignments count does not match orders')
    overweight=[]
    for o in orders:
        v=vehicle_map.get(o['vehicle_id'])
        if not v: e.append(f"Missing vehicle {o['vehicle_id']}"); continue
        if float(o['shipment_weight_kg_est'])>float(v['payload_capacity_kg'])+0.001:
            overweight.append(o['delivery_order_id'])
    if overweight: e.append(f'{len(overweight)} orders exceed assigned vehicle payload')
    if any(float(r['road_distance_km'])<0 for r in routes): e.append('Negative route distance detected')
    if any(r.get('route_distance_status')!='ESTIMATED_NOT_ROUTED' for r in routes):
        stats['routing_note']='One or more routes were refreshed; inspect route_distance_status per row.'
    else: stats['routing_note']='Baseline route distances are estimates; run the OSRM enrichment script for network-routed distance/time.'
    summary={'status':'PASS' if not e else 'FAIL','errors':e,'stats':stats}
    (ROOT/'validation_report.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))
    sys.exit(1 if e else 0)
if __name__=='__main__': main()
