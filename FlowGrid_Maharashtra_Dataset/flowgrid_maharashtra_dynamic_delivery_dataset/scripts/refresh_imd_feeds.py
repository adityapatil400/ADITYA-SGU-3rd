#!/usr/bin/env python3
"""Snapshot public IMD weather/rainfall/warning endpoints to separate raw JSON files.

This script does NOT modify simulated road_conditions_daily.csv. Feed formats,
availability and access policies can change; inspect the fetched payloads and build
an explicit, audited district-to-route normalizer before using them operationally.
"""
from pathlib import Path
from datetime import datetime, timezone
import json, re, sys
try:
    import requests
except ImportError:
    print('Missing dependency: pip install requests', file=sys.stderr); raise

ROOT=Path(__file__).resolve().parents[1]
BASE='https://api.imd.gov.in/api/v1/'
FEEDS={
    'current_weather':'current_wx',
    'district_nowcast':'districtnowcast',
    'district_warnings':'districtwarning',
    'district_rainfall':'districtrainfall',
}

def main():
    started=datetime.now(timezone.utc)
    out=ROOT/'data'/'live_source_snapshots'/started.strftime('%Y%m%dT%H%M%SZ')
    out.mkdir(parents=True,exist_ok=True)
    session=requests.Session()
    session.headers.update({'User-Agent':'FlowGrid-Maharashtra-Dataset/1.0 (IMD public API snapshot; research prototype)'})
    manifest={'created_at_utc':started.isoformat(),'source':'India Meteorological Department public API reference',
              'source_url':'https://api.imd.gov.in/public/api_reference.html','results':[]}
    for name,endpoint in FEEDS.items():
        url=BASE+endpoint
        entry={'name':name,'url':url}
        try:
            resp=session.get(url,timeout=30)
            entry['http_status']=resp.status_code
            resp.raise_for_status()
            try: payload=resp.json()
            except ValueError:
                raise RuntimeError('Response was not valid JSON; saved nothing for this endpoint')
            path=out/f'{name}.json'
            path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
            entry['saved_file']=str(path.relative_to(ROOT))
            entry['result_status']='FETCHED_RAW_JSON_REQUIRES_NORMALIZATION'
            entry['top_level_type']=type(payload).__name__
            if isinstance(payload,list): entry['record_count']=len(payload)
            elif isinstance(payload,dict) and isinstance(payload.get('data'),list): entry['record_count']=len(payload['data'])
        except Exception as exc:
            entry['result_status']='FETCH_FAILED_OR_UNAVAILABLE'
            entry['error']=str(exc)
        manifest['results'].append(entry)
    manifest['completion_at_utc']=datetime.now(timezone.utc).isoformat()
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False,indent=2))
    if all(x['result_status']!='FETCHED_RAW_JSON_REQUIRES_NORMALIZATION' for x in manifest['results']):
        sys.exit(2)
if __name__=='__main__': main()
