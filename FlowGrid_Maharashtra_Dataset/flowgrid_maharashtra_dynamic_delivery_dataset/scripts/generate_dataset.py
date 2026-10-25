#!/usr/bin/env python3
"""Generate FlowGrid Maharashtra delivery + road-risk demo data.

Observed fuel prices are a dated snapshot (2026-10-24). All other operational
records are synthetic, and route distances are explicit estimates until enriched
with a routing engine. Seeded output for reproducibility.
"""
from pathlib import Path
from datetime import date, timedelta
import csv, json, math, random, re

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
RNG = random.Random(20261024)
START = date(2026, 7, 27)
END = date(2026, 10, 24)
DAYS = (END - START).days + 1

# Current city/district labels and coordinates represent town/district-centre points,
# not warehouse gates or verified street addresses. Price values are the NDTV city
# rates reported for 24 Oct 2026; the legacy source labels are retained separately.
LOCATIONS = [
 ('AKL','Akola','Akola',20.7059,77.0219,'Vidarbha','plateau','Akola'),
 ('AMR','Amravati','Amravati',20.9374,77.7796,'Vidarbha','plateau','Amravati'),
 ('CSN','Chhatrapati Sambhajinagar','Aurangabad',19.8762,75.3433,'Marathwada','plateau','Aurangabad'),
 ('BHA','Bhandara','Bhandara',21.1702,79.6528,'Vidarbha','plateau','Bhandara'),
 ('BUL','Buldhana','Buldhana',20.5293,76.1842,'Vidarbha','plateau','Buldhana'),
 ('CHD','Chandrapur','Chandrapur',19.9615,79.2961,'Vidarbha','plateau','Chandrapur'),
 ('DHL','Dhule','Dhule',20.9042,74.7749,'North Maharashtra','plateau','Dhule'),
 ('GAD','Gadchiroli','Gadchiroli',20.1809,80.0030,'Vidarbha','forest_rural','Gadchiroli'),
 ('GON','Gondia','Gondia',21.4624,80.2209,'Vidarbha','forest_rural','Gondia'),
 ('MUM','Mumbai','Greater Mumbai',19.0760,72.8777,'Konkan','urban_coastal','Greater Mumbai'),
 ('HNG','Hingoli','Hingoli',19.7147,77.1425,'Marathwada','plateau','Hingoli'),
 ('JLG','Jalgaon','Jalgaon',21.0077,75.5626,'North Maharashtra','plateau','Jalgaon'),
 ('JAL','Jalna','Jalna',19.8410,75.8864,'Marathwada','plateau','Jalna'),
 ('KOL','Kolhapur','Kolhapur',16.7050,74.2433,'Western Maharashtra','ghat_edge','Kolhapur'),
 ('LAT','Latur','Latur',18.4088,76.5604,'Marathwada','plateau','Latur'),
 ('NGP','Nagpur','Nagpur',21.1458,79.0882,'Vidarbha','urban_plateau','Nagpur'),
 ('NDD','Nanded','Nanded',19.1383,77.3210,'Marathwada','plateau','Nanded'),
 ('NDB','Nandurbar','Nandurbar',21.3756,74.2421,'North Maharashtra','hilly_rural','Nandurbar'),
 ('NSK','Nashik','Nashik',19.9975,73.7898,'North Maharashtra','ghat_edge','Nashik'),
 ('DHR','Dharashiv','Osmanabad',18.1860,76.0419,'Marathwada','plateau','Osmanabad'),
 ('PLG','Palghar','Palghar',19.6967,72.7699,'Konkan','coastal_ghat','Palghar'),
 ('PRB','Parbhani','Parbhani',19.2704,76.7747,'Marathwada','plateau','Parbhani'),
 ('PUN','Pune','Pune',18.5204,73.8567,'Western Maharashtra','urban_plateau','Pune'),
 ('RAI','Alibag (Raigad district)','Raigarh',18.6414,72.8722,'Konkan','coastal_ghat','Raigarh'),
 ('RAT','Ratnagiri','Ratnagiri',16.9902,73.3120,'Konkan','coastal_ghat','Ratnagiri'),
 ('SAN','Sangli','Sangli',16.8524,74.5815,'Western Maharashtra','plateau','Sangli'),
 ('SAT','Satara','Satara',17.6805,74.0183,'Western Maharashtra','ghat_edge','Satara'),
 ('SIN','Oros (Sindhudurg district)','Sindhudurg',16.1580,73.6927,'Konkan','coastal_ghat','Sindhudurg'),
 ('SOL','Solapur','Solapur',17.6599,75.9064,'Western Maharashtra','plateau','Solapur'),
 ('THN','Thane','Thane',19.2183,72.9781,'Konkan','urban_coastal','Thane'),
 ('WRD','Wardha','Wardha',20.7453,78.6022,'Vidarbha','plateau','Wardha'),
 ('WSH','Washim','Washim',20.1110,77.1333,'Vidarbha','plateau','Washim'),
 ('YAV','Yavatmal','Yavatmal',20.3888,78.1204,'Vidarbha','plateau','Yavatmal'),
]
FUEL = {
 'AKL':(111.78,98.48),'AMR':(112.89,99.54),'CSN':(114.86,100.71),'BHA':(112.59,99.24),
 'BUL':(112.51,99.16),'CHD':(112.17,98.86),'DHL':(112.24,98.90),'GAD':(113.17,99.83),
 'GON':(112.95,99.58),'MUM':(111.21,97.83),'HNG':(113.17,99.83),'JLG':(111.81,98.47),
 'JAL':(113.18,99.83),'KOL':(112.57,99.23),'LAT':(113.05,99.69),'NGP':(111.80,98.48),
 'NDD':(113.17,99.84),'NDB':(112.64,99.28),'NSK':(112.44,99.07),'DHR':(113.02,99.65),
 'PLG':(112.29,98.89),'PRB':(113.17,99.83),'PUN':(112.04,98.68),'RAI':(109.08,102.29),
 'RAT':(113.13,99.77),'SAN':(111.90,98.59),'SAT':(112.39,99.05),'SIN':(113.17,99.83),
 'SOL':(112.52,99.18),'THN':(111.38,98.00),'WRD':(112.59,99.25),'WSH':(112.58,99.23),
 'YAV':(113.32,99.97)
}

# Major corridors are references/labels, not asserted end-to-end route matches.
CORRIDORS = [
 ('COR-NH48','NH 48','National Highway','Pune–Satara–Kolhapur corridor','Pune','Kolhapur','Maharashtra PWD/NHAI and OSM reference; exact path must be routed'),
 ('COR-NH65','NH 65','National Highway','Pune–Solapur corridor','Pune','Solapur','Approximate corridor reference; exact path must be routed'),
 ('COR-NH60','NH 60 / major highway corridor','National Highway','Pune–Nashik corridor','Pune','Nashik','Approximate corridor reference; verify current official road designation'),
 ('COR-NH160','NH 160','National Highway','Mumbai–Nashik corridor','Mumbai','Nashik','Approximate corridor reference; exact path must be routed'),
 ('COR-NH66','NH 66','National Highway','Konkan coastal corridor','Mumbai','Ratnagiri','NHAI reported 2026 monsoon flooding on Panvel–Indapur section; event date in evidence file'),
 ('COR-NH61','NH 61','National Highway','Ahilyanagar–Kalyan / Malshej Ghat area','Nashik','Thane','Reported subsidence/closure event in July 2026; historical evidence only, not a current closure'),
 ('COR-SM','Samruddhi Mahamarg (Hindu Hrudaysamrat Balasaheb Thackeray Maharashtra Samruddhi Mahamarg)','Expressway','Mumbai–Nagpur expressway corridor','Mumbai','Nagpur','Approx. 701 km full corridor is a published reference; route access/ramps vary'),
 ('COR-LOCAL','Local/state-road links','State/Municipal/Rural road','Final-mile roads to town-centre points','','','Must be evaluated from live routing and local traffic/road notices'),
]

WAREHOUSES = [
 ('W-MUM','Mumbai Central Demo DC','MUM',19.0760,72.8777,25000),
 ('W-PUN','Pune Central Demo DC','PUN',18.5204,73.8567,22000),
 ('W-NGP','Nagpur Central Demo DC','NGP',21.1458,79.0882,22000),
 ('W-KOL','Kolhapur Central Demo DC','KOL',16.7050,74.2433,12000),
]

# Simple rough-zone detour factors for synthetic estimates. Replaced by OSRM when refreshed.
REGION_DETOUR = {
 'Konkan':1.34,'Vidarbha':1.22,'Marathwada':1.20,'North Maharashtra':1.25,
 'Western Maharashtra':1.22,
}
VEHICLE_TYPES = [
 ('MINI','Mini goods vehicle','diesel',750,12.0,40),
 ('LCV','Light commercial truck','diesel',2500,9.5,55),
 ('MCV','Medium commercial truck','diesel',7500,6.2,65),
 ('HCV','Heavy goods truck','diesel',12000,4.2,70),
]

# Load existing 100-SKU catalogue; details retain original per-SKU data-origin flags.
source_skus = DATA / 'products_source.csv'
with source_skus.open(newline='', encoding='utf-8-sig') as f:
    sku_rows = list(csv.DictReader(f))


def write_csv(path, rows, fieldnames=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows and not fieldnames: raise ValueError(f'No rows/fields for {path}')
    if fieldnames is None: fieldnames = list(rows[0].keys())
    with path.open('w', newline='', encoding='utf-8-sig') as f:
        w=csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        w.writeheader(); w.writerows(rows)


def haversine_km(lat1, lon1, lat2, lon2):
    r=6371.0088
    p1,p2=math.radians(lat1),math.radians(lat2)
    dp=math.radians(lat2-lat1); dl=math.radians(lon2-lon1)
    a=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(math.sqrt(a))


def loc_dict():
    return {f'L-MH-{a}': {'code':a,'name':b,'source_label':c,'lat':d,'lon':e,'region':f,'terrain':g,'fuel_label':h}
            for a,b,c,d,e,f,g,h in LOCATIONS}

locs=loc_dict()
# locations: city/district centres only; no claim of precise customer/warehouse address.
location_rows=[]
for l_id, v in locs.items():
    p,d=FUEL[v['code']]
    location_rows.append({
        'location_id':l_id,'location_name':v['name'],'source_fuel_table_label':v['fuel_label'],
        'district_or_city':v['name'],'state':'Maharashtra','country':'India',
        'latitude':v['lat'],'longitude':v['lon'],'geopoint_type':'town_or_district_centre_approximation',
        'environment_region':v['region'],'terrain_class':v['terrain'],
        'petrol_inr_per_litre_2026_10_24':p,'diesel_inr_per_litre_2026_10_24':d,
        'coordinates_status':'approximate city/district-centre coordinates; verify for production routing',
        'fuel_price_source':'https://www.ndtv.com/fuel-prices/petrol-price-in-maharashtra-state; https://www.ndtv.com/fuel-prices/diesel-price-in-maharashtra-state'
    })
write_csv(DATA/'locations.csv', location_rows)
# Point GeoJSON for map prototypes. Coordinates are city/district-centre approximations.
location_features=[]
for r in location_rows:
    location_features.append({'type':'Feature','geometry':{'type':'Point','coordinates':[float(r['longitude']),float(r['latitude'])]},
                              'properties':{'location_id':r['location_id'],'location_name':r['location_name'],
                                            'environment_region':r['environment_region'],'terrain_class':r['terrain_class'],
                                            'geopoint_type':r['geopoint_type'],'coordinates_status':r['coordinates_status']}})
(DATA/'locations.geojson').write_text(json.dumps({'type':'FeatureCollection','features':location_features},ensure_ascii=False),encoding='utf-8')

# Source SKU data to simplified join table.
product_rows=[]
for r in sku_rows:
    product_rows.append({
        'sku_id':r.get('sku_id',''),'product_name':r.get('product_name',''),'brand':r.get('brand',''),
        'category_id':r.get('category_id',''),'unit_of_measure':r.get('unit_of_measure',''),
        'unit_weight_kg':r.get('unit_weight_kg',''),'unit_volume_l':r.get('unit_volume_l',''),
        'unit_cost_inr':r.get('unit_cost',''),'unit_price_inr':r.get('unit_price',''),
        'shelf_life_days_estimated':r.get('estimated_total_shelf_life_days',''),
        'storage_class':r.get('storage_class',''),'source_listing_url':r.get('source_listing_url',''),
        'source_capture_date':r.get('source_capture_date',''),'data_origin':r.get('data_origin','')
    })
write_csv(DATA/'products_100sku.csv', product_rows)

warehouse_rows=[]
for wid,name,code,lat,lon,cap in WAREHOUSES:
    warehouse_rows.append({'warehouse_id':wid,'warehouse_name':name,'location_id':f'L-MH-{code}',
                           'latitude':lat,'longitude':lon,'daily_dispatch_capacity_kg_assumed':cap,
                           'facility_status':'SYNTHETIC_DEMO_SITE','address_precision':'city-centre point; not a real facility address'})
write_csv(DATA/'warehouses.csv', warehouse_rows)

# Generate synthetic fleet: explicit km/L assumptions, not OEM guarantees.
vehicles=[]
vid=1
for kind,name,fuel,capacity,mileage,n in VEHICLE_TYPES:
    for i in range(1,n+1):
        # fleet mileage is varied modestly around the benchmark to avoid making every unit identical
        m=round(mileage*RNG.uniform(0.90,1.10),2)
        vehicles.append({'vehicle_id':f'V-{kind}-{i:03d}','vehicle_type':name,'fuel_type':fuel,
                         'payload_capacity_kg':capacity,'mileage_km_per_litre_assumed':m,
                         'fuel_consumption_l_per_100km_assumed':round(100/m,2),
                         'average_speed_dry_kmph_assumed':round(RNG.uniform(30,55) if kind in ('MINI','LCV') else RNG.uniform(35,60),1),
                         'availability_status':'SYNTHETIC_FLEET','mileage_source':'synthetic planning assumption; replace with fleet telematics/OEM data'})
        vid+=1
write_csv(DATA/'vehicles.csv', vehicles)

corridor_rows=[]
for cid,road,klass,desc,frm,to,notes in CORRIDORS:
    corridor_rows.append({'corridor_id':cid,'road_designation':road,'road_class':klass,'corridor_description':desc,
                          'reference_from':frm,'reference_to':to,'data_status':'REFERENCE_ONLY_NOT_ROUTE_GEOMETRY',
                          'source_notes':notes})
write_csv(DATA/'road_corridor_references.csv', corridor_rows)

# Route options: warehouse to every destination. These are modelled road-distance estimates
# until the provided OSRM refresh script has populated routed_source + actual km.
route_rows=[]
for wid,wname,wcode,wlat,wlon,wcap in WAREHOUSES:
    for loc_id,v in locs.items():
        direct=haversine_km(wlat,wlon,v['lat'],v['lon'])
        same=(wcode==v['code'])
        factor=1.0 if same else REGION_DETOUR[v['region']]
        # additional detour around ghat/coastal and rural links; still an estimate.
        if v['terrain'] in ('coastal_ghat','ghat_edge','hilly_rural') and not same: factor*=1.06
        if v['terrain'] in ('urban_coastal','urban_plateau') and not same: factor*=1.03
        road_km=8.0 if same else round(direct*factor,1)
        road_class='urban_local' if same else ('national_highway_plus_final_mile' if direct>60 else 'state_highway_or_urban_arterial')
        corridor='COR-LOCAL'
        a=wcode; b=v['code']
        pair={a,b}
        if {'PUN','SAT'}<=pair or {'PUN','KOL'}<=pair or {'SAT','KOL'}<=pair or {'PUN','SAN'}<=pair: corridor='COR-NH48'
        elif {'PUN','SOL'}<=pair: corridor='COR-NH65'
        elif {'PUN','NSK'}<=pair: corridor='COR-NH60'
        elif {'MUM','NSK'}<=pair: corridor='COR-NH160'
        elif (wcode in ('MUM','PUN') and v['region']=='Konkan') or (wcode=='MUM' and b in ('RAT','SIN','RAI')): corridor='COR-NH66'
        elif {'MUM','NGP'}<=pair: corridor='COR-SM'
        elif {'THN','NSK'}<=pair: corridor='COR-NH61'
        speed=48 if road_class=='national_highway_plus_final_mile' else 32
        duration=25.0 if same else round(road_km/max(speed,1)*60,1)
        route_rows.append({'route_id':f'R-{wid}-{loc_id[5:]}','warehouse_id':wid,'warehouse_name':wname,
                           'origin_location_id':f'L-MH-{wcode}','destination_location_id':loc_id,
                           'origin_latitude':wlat,'origin_longitude':wlon,'destination_latitude':v['lat'],'destination_longitude':v['lon'],
                           'straight_line_distance_km':round(direct,2),'road_distance_km_initial_estimate':road_km,'road_distance_km':road_km,
                           'route_distance_status':'ESTIMATED_NOT_ROUTED','route_distance_method':'synthetic_intra_city_radius_assumption' if same else 'haversine_distance_times_region_road_detour_factor',
                           'road_class_mix_estimate':road_class,'representative_corridor_id':corridor,
                           'baseline_drive_time_minutes_est':duration,'baseline_drive_time_status':'SIMULATED_SPEED_ESTIMATE_NOT_LIVE_TRAFFIC',
                           'route_geometry_status':'NOT_INCLUDED','routing_source':'none; use scripts/enrich_osrm_routes.py for OSM-routed distances'})
write_csv(DATA/'route_options.csv', route_rows)

# Daily city fuel history: source point is observed on Oct 24; previous dates are deliberately
# synthetic price scenarios, not actual historical station prices. Small state-wide scenario shocks
# are shared, and city-specific tax/local differences are preserved from the observed base.
fuel_daily=[]
cur=START
common_shock=0.0
for day_idx in range(DAYS):
    d=START+timedelta(days=day_idx)
    if d < END:
        if RNG.random()<0.20:
            common_shock=max(-0.035,min(0.045,common_shock+RNG.uniform(-0.012,0.012)))
        if (END-d).days<10:
            # Ease scenario back to observed city price; not a statement about actual history.
            common_shock *= 0.86
    else:
        common_shock=0.0
    for loc_id,v in locs.items():
        bp,bd=FUEL[v['code']]
        city_adj=RNG.uniform(-0.003,0.003) if day_idx < DAYS-1 else 0.0
        simulated_shock=max(-0.05,min(0.06,common_shock+city_adj))
        if d==END:
            petrol,diesel=bp,bd
            status='OBSERVED_CITY_RATE_SNAPSHOT'
            note='City/district rate reported for 2026-10-24; outlet-level price may differ.'
        else:
            petrol=round(bp*(1+simulated_shock),2)
            diesel=round(bd*(1+simulated_shock),2)
            status='SIMULATED_SCENARIO_NOT_HISTORICAL_OBSERVATION'
            note='Synthetic scenario derived from 2026-10-24 city baseline; not verified historical rates.'
        fuel_daily.append({'date':d.isoformat(),'location_id':loc_id,'location_name':v['name'],
                           'petrol_inr_per_litre':petrol,'diesel_inr_per_litre':diesel,
                           'price_data_status':status,'price_validity':'snapshot / scenario only',
                           'source_url_petrol':'https://www.ndtv.com/fuel-prices/petrol-price-in-maharashtra-state',
                           'source_url_diesel':'https://www.ndtv.com/fuel-prices/diesel-price-in-maharashtra-state',
                           'note':note})
write_csv(DATA/'fuel_prices_daily.csv', fuel_daily)

# Daily road/weather-risk scenarios for each route. These are not actual IMD observations.
road_daily=[]
for rr in route_rows:
    dest=locs[rr['destination_location_id']]
    terrain=dest['terrain']; region=dest['region']; dist=rr['road_distance_km']
    for day_idx in range(DAYS):
        d=START+timedelta(days=day_idx)
        mon=d.month
        if mon in (7,8): rain_p=0.43
        elif mon==9: rain_p=0.30
        elif mon==10: rain_p=0.13
        else: rain_p=0.08
        if region=='Konkan': rain_p=min(0.85,rain_p+0.16)
        elif region=='Vidarbha': rain_p=max(0.04,rain_p-0.04)
        rain_event=RNG.random()<rain_p
        if rain_event:
            shape=1.8 if region=='Konkan' else 1.2
            rainfall=max(0.5,min(110,round(RNG.gammavariate(shape,7.0),1)))
        else:
            rainfall=round(RNG.uniform(0,1.5),1)
        if terrain in ('coastal_ghat','ghat_edge','hilly_rural'):
            terrain_risk=0.19
        elif terrain in ('forest_rural','hilly_rural'):
            terrain_risk=0.16
        elif terrain.startswith('urban'):
            terrain_risk=0.12
        else: terrain_risk=0.08
        waterlogged=bool(rainfall>=40 and (region=='Konkan' or terrain in ('coastal_ghat','urban_coastal')) and RNG.random()<0.40)
        pothole_risk=round(min(0.95,terrain_risk+(0.16 if rainfall>=20 else 0)+(0.12 if mon in (7,8,9) else 0)+RNG.uniform(-0.04,0.06)),2)
        if waterlogged:
            condition='WATERLOGGING_SCENARIO'; severity='high'; reduction=0.45; extra_delay=RNG.uniform(30,120)
        elif terrain in ('coastal_ghat','ghat_edge') and rain_event and rainfall>=15:
            condition='WET_GHAT_SCENARIO'; severity='high'; reduction=0.28; extra_delay=RNG.uniform(20,70)
        elif rain_event and rainfall>=15:
            condition='WET_ROAD_SCENARIO'; severity='medium'; reduction=0.18; extra_delay=RNG.uniform(10,45)
        elif terrain.startswith('urban') and RNG.random()<0.36:
            condition='URBAN_CONGESTION_SCENARIO'; severity='medium'; reduction=0.22; extra_delay=RNG.uniform(10,40)
        elif pothole_risk>=0.35:
            condition='ROUGH_SURFACE_RISK_SCENARIO'; severity='low'; reduction=0.10; extra_delay=RNG.uniform(5,20)
        else:
            condition='DRY_OR_NORMAL_SCENARIO'; severity='low'; reduction=0.0; extra_delay=0.0
        urban_add=0.18 if terrain.startswith('urban') else 0.04
        congestion=round(max(0.02,min(0.98,urban_add+(0.25 if condition=='URBAN_CONGESTION_SCENARIO' else 0)+(0.10 if rain_event else 0)+RNG.uniform(-0.05,0.07))),2)
        visibility=round(max(0.3,9.5-(rainfall*0.045)-(3 if condition=='WATERLOGGING_SCENARIO' else 0)-RNG.uniform(0,1.2)),1)
        base_minutes=float(rr['baseline_drive_time_minutes_est'])
        weather_delay=round(base_minutes*reduction+extra_delay,1)
        road_daily.append({'date':d.isoformat(),'route_id':rr['route_id'],'warehouse_id':rr['warehouse_id'],
                           'destination_location_id':rr['destination_location_id'],'environment_region':region,'terrain_class':terrain,
                           'rainfall_mm_scenario':rainfall,'visibility_km_scenario':visibility,'road_condition_scenario':condition,
                           'risk_severity_scenario':severity,'pothole_or_surface_risk_index_scenario':pothole_risk,
                           'waterlogging_flag_scenario':str(waterlogged).lower(),'traffic_congestion_index_scenario':congestion,
                           'estimated_speed_reduction_fraction':reduction,'estimated_extra_delay_minutes':round(extra_delay,1),
                           'estimated_weather_traffic_delay_minutes':weather_delay,'data_status':'SIMULATED_MAHA_ENVIRONMENT_SCENARIO',
                           'observed_condition_source':'none; connect IMD district warnings/rainfall and official highway notices for live use'})
write_csv(DATA/'road_conditions_daily.csv', road_daily)

# One delivery order per destination/day, with 1-3 SKU lines. Route assignment and costs use the
# nearest estimated warehouse, then the smallest payload class able to carry the shipment.
route_by_dest={}
for rr in route_rows: route_by_dest.setdefault(rr['destination_location_id'],[]).append(rr)
warehouse_to_loc={w[0]:w for w in WAREHOUSES}
vehicle_by_type={}
for v in vehicles: vehicle_by_type.setdefault(v['vehicle_type'],[]).append(v)
capacity_order=[('Mini goods vehicle',750),('Light commercial truck',2500),('Medium commercial truck',7500),('Heavy goods truck',12000)]
fuel_lookup={(r['date'],r['location_id']):r for r in fuel_daily}
road_condition_lookup={(r['date'],r['route_id']):r for r in road_daily}
order_rows=[]; line_rows=[]; assignment_rows=[]

for day_idx in range(DAYS):
    d=START+timedelta(days=day_idx)
    weekday=d.weekday()
    for loc_id,vloc in locs.items():
        # weekend and seasonal demand variance; synthetic, not observed order history
        order_id=f'DO-{d.strftime("%Y%m%d")}-{vloc["code"]}'
        chosen_routes=sorted(route_by_dest[loc_id], key=lambda x:x['road_distance_km'])
        rr=chosen_routes[0]
        wh=warehouse_to_loc[rr['warehouse_id']]
        num_lines=RNG.choice([2,3,4,4,5,6,7,8])
        chosen_skus=RNG.sample(sku_rows, num_lines)
        gross_weight=0.0; gross_volume=0.0; total_value=0.0; total_units=0
        for j,sku in enumerate(chosen_skus,1):
            weight=float(sku.get('unit_weight_kg') or 1.0)
            volume=float(sku.get('unit_volume_l') or 1.0)
            qty=RNG.randint(25,250)
            total_units += qty
            line_weight=weight*qty; line_volume=volume*qty
            unit_price=float(sku.get('unit_price') or sku.get('listing_price_inr') or 100)
            line_value=unit_price*qty
            gross_weight+=line_weight; gross_volume+=line_volume; total_value+=line_value
            line_rows.append({'delivery_order_id':order_id,'line_number':j,'sku_id':sku['sku_id'],
                              'product_name':sku['product_name'],'category_id':sku.get('category_id',''),
                              'quantity_units':qty,'unit_weight_kg_source_estimate':round(weight,3),
                              'line_weight_kg_est':round(line_weight,2),'unit_volume_l_source_estimate':round(volume,3),
                              'line_volume_l_est':round(line_volume,2),'unit_price_inr_source_estimate':round(unit_price,2),
                              'line_value_inr_est':round(line_value,2),'line_data_status':'SYNTHETIC_ORDER_USING_SOURCE_CATALOG_SPECS'})
        # Recompute a truck class that can carry the entire order weight. For unusual bulky orders, HCV is used.
        selected_type=next((t for t,c in capacity_order if gross_weight<=c),'Heavy goods truck')
        selected_vehicle=RNG.choice(vehicle_by_type[selected_type])
        fuel_origin=fuel_lookup[(d.isoformat(),rr['origin_location_id'])]
        road_cond=road_condition_lookup[(d.isoformat(),rr['route_id'])]
        km=float(rr['road_distance_km'])
        mileage=float(selected_vehicle['mileage_km_per_litre_assumed'])
        adjusted_mileage=max(1,mileage*(1-float(road_cond['estimated_speed_reduction_fraction'])*0.55-(float(road_cond['traffic_congestion_index_scenario'])*0.08)))
        litres=km/adjusted_mileage if km>0 else 0.0
        diesel=float(fuel_origin['diesel_inr_per_litre'])
        urgency=RNG.choices(['standard','priority','urgent'],weights=[0.72,0.22,0.06])[0]
        order_rows.append({'delivery_order_id':order_id,'order_date':d.isoformat(),'requested_delivery_date':(d+timedelta(days=RNG.choice([0,1,1,2]))).isoformat(),
                           'destination_location_id':loc_id,'destination_name':vloc['name'],'destination_region':vloc['region'],
                           'warehouse_id':rr['warehouse_id'],'vehicle_id':selected_vehicle['vehicle_id'],'vehicle_type':selected_vehicle['vehicle_type'],
                           'sku_line_count':num_lines,'total_units':total_units,
                           'shipment_weight_kg_est':round(gross_weight,2),'shipment_volume_l_est':round(gross_volume,2),
                           'order_value_inr_est':round(total_value,2),'urgency':urgency,
                           'route_id':rr['route_id'],'road_distance_km_initial_estimate':round(km,1),'road_distance_km_current':round(km,1),'route_distance_status':'ESTIMATED_NOT_ROUTED',
                           'vehicle_mileage_km_per_litre_assumed':mileage,'road_condition_scenario':road_cond['road_condition_scenario'],
                           'traffic_congestion_index_scenario':road_cond['traffic_congestion_index_scenario'],
                           'route_delay_minutes_scenario':road_cond['estimated_weather_traffic_delay_minutes'],
                           'origin_diesel_price_inr_per_litre':diesel,'fuel_litres_est_one_way':round(litres,3),
                           'fuel_cost_inr_est_one_way':round(litres*diesel,2),'fuel_cost_inr_est_round_trip':round(litres*diesel*2,2),
                           'fuel_cost_status':'ESTIMATE_FROM_SIMULATED_ROUTE_AND_FLEET; origin city price; one-way route unless roundtrip column',
                           'record_status':'SYNTHETIC_ORDER_SCENARIO'})
        assignment_rows.append({'delivery_order_id':order_id,'route_id':rr['route_id'],'warehouse_id':rr['warehouse_id'],
                                'vehicle_id':selected_vehicle['vehicle_id'],'assignment_method':'nearest_estimated_road_distance_then_smallest_payload_class',
                                'assignment_status':'SYNTHETIC_BASELINE_NOT_OPTIMIZED','needs_optimizer_replan':'true' if urgency=='urgent' or road_cond['risk_severity_scenario']=='high' else 'false'})
write_csv(DATA/'delivery_orders.csv', order_rows)
write_csv(DATA/'delivery_order_lines.csv', line_rows)
write_csv(DATA/'delivery_assignments.csv', assignment_rows)

# A compact historical-source/event ledger, kept separate from simulated daily conditions.
events=[
 {'event_id':'EVT-NH66-20260707','event_date':'2026-07-07','corridor_id':'COR-NH66','location_or_section':'Kharoshi Phata; Wakan Phata; Sukeli Ghat; Khamb, Panvel–Indapur section','event_type':'heavy_rain_flooding_and_temporary_overtopping','source_status':'HISTORICAL_OFFICIAL_NOTICE','source_url':'https://www.pib.gov.in/PressReleasePage.aspx?PRID=2281965&lang=1&reg=1','summary':'NHAI/PIB reported heavy rainfall and temporary water overtopping/restricted movement on NH-66. This is historical evidence, not a current closure.'},
 {'event_id':'EVT-NH48-20260617','event_date':'2026-06-17','corridor_id':'COR-NH48','location_or_section':'Pune–Satara section, NH-48','event_type':'monsoon_preparedness_and_road_maintenance','source_status':'HISTORICAL_OFFICIAL_NOTICE','source_url':'https://www.pib.gov.in/PressReleasePage.aspx?PRID=2274178&lang=1&reg=1','summary':'NHAI described pre-monsoon readiness, pavement repair, rainfall/waterlogging/slope-failure risks. Not a live condition feed.'},
 {'event_id':'EVT-NH61-20260725','event_date':'2026-07-25','corridor_id':'COR-NH61','location_or_section':'NH-61 near Junnar / Malshej Ghat area','event_type':'road_damage_and_closure_reported','source_status':'HISTORICAL_NEWS_REPORT_NOT_OFFICIAL_LIVE_FEED','source_url':'https://indianexpress.com/article/cities/pune/malshej-ghat-closed-national-highway-61-road-damage-alternate-routes-10804395/','summary':'News report documented a road damage/closure event in July 2026. Must not be interpreted as current status.'},
]
write_csv(DATA/'historical_road_event_evidence.csv', events)

# Event stream for testing FlowGrid's event validation, impact gate, and replanning logic.
# Every row below is a synthetic test event; the historical road-event ledger is separate.
event_rows=[]
event_seq=1
prev_fuel={}
for day_idx in range(DAYS):
    d=START+timedelta(days=day_idx)
    date_s=d.isoformat()
    # Fuel shocks derived from the synthetic scenario series; log only material moves.
    for fr in [x for x in fuel_daily if x['date']==date_s]:
        key=fr['location_id']
        if key in prev_fuel:
            old=float(prev_fuel[key]); new=float(fr['diesel_inr_per_litre'])
            pct=(new-old)/old*100 if old else 0
            if abs(pct)>=0.8:
                event_rows.append({'event_id':f'EVT-{event_seq:06d}','event_timestamp':date_s+'T06:05:00+05:30',
                                   'event_type':'FUEL_PRICE_CHANGE','source_system':'SIMULATED_FUEL_SCENARIO',
                                   'source_status':'SYNTHETIC_TEST_EVENT','location_id':key,'route_id':'','warehouse_id':'',
                                   'severity':'medium' if abs(pct)<2 else 'high',
                                   'event_payload_json':json.dumps({'previous_diesel_inr_litre':old,'new_diesel_inr_litre':new,'change_percent':round(pct,2)},separators=(',',':')),
                                   'materiality_threshold_percent':0.8,'expected_guard_action':'RECOMPUTE_COST_AND_REPLAN_IF_IMPACT_MATERIAL',
                                   'is_simulated':'true'})
                event_seq+=1
        prev_fuel[key]=fr['diesel_inr_per_litre']
    # Up to two road-risk scenario events daily to keep the event log balanced.
    todays_road=[x for x in road_daily if x['date']==date_s and x['risk_severity_scenario']=='high']
    RNG.shuffle(todays_road)
    for rc in todays_road[:2]:
        event_rows.append({'event_id':f'EVT-{event_seq:06d}','event_timestamp':date_s+'T08:00:00+05:30',
                           'event_type':'ROAD_WEATHER_RISK','source_system':'SIMULATED_ROAD_CONDITION_SCENARIO',
                           'source_status':'SYNTHETIC_TEST_EVENT','location_id':rc['destination_location_id'],'route_id':rc['route_id'],
                           'warehouse_id':rc['warehouse_id'],'severity':rc['risk_severity_scenario'],
                           'event_payload_json':json.dumps({'condition':rc['road_condition_scenario'],'rainfall_mm':rc['rainfall_mm_scenario'],
                                                            'visibility_km':rc['visibility_km_scenario'],'estimated_delay_min':rc['estimated_weather_traffic_delay_minutes']},separators=(',',':')),
                           'materiality_threshold_percent':15,'expected_guard_action':'REPLAN_ROUTE_OR_REQUEST_MANUAL_REVIEW_IF_ROUTE_SAFETY_UNVERIFIED',
                           'is_simulated':'true'})
        event_seq+=1
    # Include one generated urgent-order event where available, with a schema-stable payload.
    todays_orders=[x for x in order_rows if x['order_date']==date_s and x['urgency']=='urgent']
    if todays_orders:
        selected=RNG.choice(todays_orders)
        event_rows.append({'event_id':f'EVT-{event_seq:06d}','event_timestamp':date_s+'T09:15:00+05:30',
                           'event_type':'URGENT_STORE_ORDER','source_system':'SIMULATED_ORDER_SERVICE',
                           'source_status':'SYNTHETIC_TEST_EVENT','location_id':selected['destination_location_id'],'route_id':selected['route_id'],
                           'warehouse_id':selected['warehouse_id'],'severity':'high',
                           'event_payload_json':json.dumps({'delivery_order_id':selected['delivery_order_id'],'urgency':'urgent',
                                                            'requested_delivery_date':selected['requested_delivery_date']},separators=(',',':')),
                           'materiality_threshold_percent':0,'expected_guard_action':'VALIDATE_INVENTORY_AND_CAPACITY_THEN_OPTIMIZE',
                           'is_simulated':'true'})
        event_seq+=1
write_csv(DATA/'operational_events.csv', event_rows)

guard_tests=[
 ('G-001','Valid urgent store order',True,True,2,0.50,True,True,'AUTO_REPLAN_THEN_COMMIT_IF_FEASIBLE','Urgency alone does not bypass inventory, capacity or commit checks.'),
 ('G-002','Untrusted RSS route closure claim',True,False,2,0.90,True,False,'REJECT_OR_MANUAL_REVIEW','An untrusted news/RSS claim is not a verified closure.'),
 ('G-003','Stale GPS event',True,True,180,0.20,True,True,'REQUEST_REFRESH_OR_MANUAL_REVIEW','Old location data must be marked stale.'),
 ('G-004','Fuel increase below materiality',True,True,3,0.002,True,True,'NO_ROUTE_CHANGE; LOG_COST_DELTA','Cost may update without needless route churn.'),
 ('G-005','Verified diesel price shock',True,True,3,0.035,True,True,'RECOMPUTE_COST; REPLAN_IF_POLICY_THRESHOLD_MET','Pricing event changes cost; route changes require measurable benefit.'),
 ('G-006','Heavy rain warning on ghat route',True,True,5,0.35,True,False,'SIMULATE_ALTERNATIVES_AND_REQUIRE_ROUTE_SAFETY_REVIEW','Weather warning is not proof that the road is open or passable.'),
 ('G-007','Vehicle payload exceeded',True,True,1,0.45,True,True,'REJECT_PLAN','Hard capacity constraint cannot be overridden by an LLM.'),
 ('G-008','Inventory reserve breached',True,True,1,0.40,False,True,'REJECT_PLAN_OR_REQUEST_STOCK_REPLENISHMENT','Do not commit negative available stock.'),
 ('G-009','Duplicate delivery event id',True,True,1,0.50,True,True,'IDEMPOTENT_NO_DUPLICATE_COMMIT','Deduplicate events before side effects.'),
 ('G-010','Invalid event schema',False,True,1,0.70,True,True,'REJECT_EVENT','Schema validation precedes forecast/optimization.'),
 ('G-011','Route provider unavailable',True,True,3,0.60,True,False,'NO_UNVERIFIED_ROUTE_COMMIT; MANUAL_REVIEW','Never replace failed live routing with a synthetic distance silently.'),
 ('G-012','Material alternative has no SLA improvement',True,True,4,0.01,True,True,'KEEP_CURRENT_PLAN_AND_LOG_REASON','Avoid route churn when improvement is under the policy threshold.'),
]
guard_rows=[]
for gid,name,schema,source_trusted,age,impact,stock_ok,route_verified,action,rationale in guard_tests:
    guard_rows.append({'test_case_id':gid,'scenario_name':name,'event_schema_valid':str(schema).lower(),
                       'source_trusted':str(source_trusted).lower(),'event_age_minutes':age,'estimated_relative_impact':impact,
                       'inventory_constraints_pass':str(stock_ok).lower(),'route_safety_or_access_verified':str(route_verified).lower(),
                       'expected_guard_action':action,'rationale':rationale,'data_status':'SYNTHETIC_DECISION_GUARD_TEST_FIXTURE'})
write_csv(DATA/'decision_guard_test_cases.csv',guard_rows)

# Metadata and row counts.
counts={}
for p in DATA.glob('*.csv'):
    with p.open(newline='',encoding='utf-8-sig') as f: counts[p.name]=sum(1 for _ in csv.DictReader(f))
meta={
 'dataset_name':'FlowGrid Maharashtra Dynamic Delivery & Road-Risk Dataset',
 'dataset_version':'1.0.0',
 'generation_seed':20261024,
 'generation_date':'2026-10-24',
 'date_window':{'start':START.isoformat(),'end':END.isoformat(),'days':DAYS},
 'scope':{'locations':len(location_rows),'warehouses':len(warehouse_rows),'vehicles':len(vehicles),'route_options':len(route_rows),'delivery_orders':len(order_rows),'delivery_order_lines':len(line_rows),'daily_route_condition_rows':len(road_daily),'daily_city_fuel_rows':len(fuel_daily),'operational_events':len(event_rows),'decision_guard_test_cases':len(guard_rows)},
 'data_integrity_notes':[
  'Only the 2026-10-24 city/district petrol and diesel rate snapshot is treated as observed. Historical fuel rows are synthetic scenarios, not actual historical quotations.',
  'City coordinates are approximate town/district-centre points, not customer addresses or warehouse gate coordinates.',
  'Road distance is estimated from great-circle distance multiplied by regional detour assumptions. It is not an OSRM/Google routed distance until refreshed.',
  'Road, weather, traffic, delay and condition rows are simulated Maharashtra environment scenarios, not measured IMD, police, NHAI or traffic feeds.',
  'Operational event stream and decision guard test cases are synthetic fixtures for testing the event-driven architecture; they are not actual business incidents.',
  'Vehicles, fleet mileage, capacities, warehouses, orders and assignments are synthetic planning data.',
  'Product catalogue is imported from the companion 100-SKU project snapshot; source listing prices and estimated product logistics attributes should be revalidated before commercial use.'
 ],
 'row_counts':counts
}
(ROOT/'metadata.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
print(json.dumps(meta,indent=2))
