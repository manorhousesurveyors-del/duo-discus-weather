"""Fetch attributed ASSelect/YAIXM data and build bounded GeoJSON snapshots.

Default: ASSelect's announced AIRAC release, saved as current or upcoming.
A historical source commit requires an explicit AIRAC date; release notes are
retained as evidence. No LOA concession or NOTAM activation is inferred.
"""
from datetime import UTC,date,datetime,timedelta
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from urllib.request import Request,urlopen
from shapely import make_valid
from shapely.geometry import shape,mapping

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from airspace import validate_geojson
from config import GeoPoint
from soaring_physics import distance_bearing
from wave_map import destination

ROOT=Path(__file__).resolve().parents[1]/'data'
SOURCE='https://asselect.uk/yaixm.json'
ATTRIBUTION='Alan Sparrow / ASSelect; derived from the UK AIP and contributors. Source author waives copyright and related rights.'


def compact_coordinate(value):
    match=re.fullmatch(r'(\d{2})(\d{2})(\d{2}(?:\.\d+)?)([NS])\s+(\d{3})(\d{2})(\d{2}(?:\.\d+)?)([EW])',value)
    if not match:raise ValueError('Invalid YAIXM coordinate: '+str(value)[:80])
    a,m,s,h,b,n,t,k=match.groups()
    if max(float(m),float(s),float(n),float(t))>=60:raise ValueError('Invalid YAIXM minutes/seconds')
    lat=(int(a)+int(m)/60+float(s)/3600)*(-1 if h=='S' else 1)
    lon=(int(b)+int(n)/60+float(t)/3600)*(-1 if k=='W' else 1)
    GeoPoint('source','Source',lat,lon)
    return lat,lon


def radius_km(value):
    match=re.fullmatch(r'([\d.]+)\s*(nm|km|m)',value)
    if not match:raise ValueError('Unsupported YAIXM radius')
    radius=float(match[1])*{'nm':1.852,'km':1,'m':.001}[match[2]]
    if not 0<radius<1000:raise ValueError('Invalid YAIXM radius')
    return radius


def boundary_ring(parts):
    points=[]
    for part in parts:
        if set(part)=={'line'}:
            points.extend(compact_coordinate(p) for p in part['line'])
        elif set(part)=={'circle'}:
            if len(parts)!=1:raise ValueError('Circle cannot be combined with another boundary')
            item=part['circle'];centre=compact_coordinate(item['centre']);radius=radius_km(item['radius'])
            points=[destination(*centre,angle,radius) for angle in range(0,360,3)]
        elif set(part)=={'arc'}:
            if not points:raise ValueError('Arc needs a preceding endpoint')
            item=part['arc'];centre=compact_coordinate(item['centre']);end=compact_coordinate(item['to'])
            radius=radius_km(item['radius'])
            def bearing(point):return distance_bearing(GeoPoint('c','Centre',*centre),GeoPoint('p','Arc',*point))[1]
            start,finish=bearing(points[-1]),bearing(end)
            if item['dir'] not in ('cw','ccw'):raise ValueError('Unknown arc direction')
            sign=1 if item['dir']=='cw' else -1;span=((finish-start)*sign)%360
            steps=max(1,math.ceil(span/3))
            points.extend(destination(*centre,start+sign*span*i/steps,radius) for i in range(1,steps))
            points.append(end) # Retain the source endpoint, avoiding precision slivers.
        else:raise ValueError('Unsupported YAIXM boundary: '+str(list(part)))
    ring=[]
    for lat,lon in points:
        point=[round(lon,6),round(lat,6)]
        if not ring or point!=ring[-1]:ring.append(point)
    if len(ring)<3:raise ValueError('Boundary needs three different points')
    if ring[0]!=ring[-1]:ring.append(ring[0])
    return ring


def convert_yaixm(body,*,effective_date,source,source_commit,source_hashes):
    if not isinstance(body,dict) or not isinstance(body.get('airspace'),list):raise ValueError('Missing YAIXM airspace data')
    features=[];excluded=0;normalised=0
    for section in ('airspace','rat'):
        for area in body.get(section,[]):
            kind,local=area['type'],area.get('localtype','')
            if kind not in {'CTA','CTR','TMA','ATZ','D','R','P'} and local not in {'MATZ','RMZ','TMZ','DZ','HIRTA','GVS','LASER'}:
                excluded+=1;continue # Gliding sites/LOAs/ILS symbols are not restriction polygons.
            for index,volume in enumerate(area['geometry']):
                name=volume.get('name',area['name'])+(' '+str(volume['seq']) if 'seq' in volume else '')
                ring=boundary_ring(volume['boundary'])
                properties={'name':name,'class':volume.get('class',area.get('class',local or kind)),
                    'floor':str(volume['lower']),'ceiling':str(volume['upper']),
                    'type':kind,'local_type':local,'source_section':section,
                    'rules':volume.get('rules',area.get('rules',[])),
                    'activation':'Temporary restriction: verify dates and activation' if section=='rat' else 'Check current AIP, NOTAMs and operating hours'}
                geometry={'type':'Polygon','coordinates':[ring]}
                polygon=shape(geometry)
                if not polygon.is_valid:
                    # Split crossings using the same even/odd interior rule as
                    # the corridor checker. Keep every edge and small polygon;
                    # buffer(0) could silently discard pieces and is not used.
                    corrected=make_valid(polygon,method='linework')
                    if (not corrected.is_valid or corrected.geom_type not in ('Polygon','MultiPolygon')
                        or corrected.boundary.hausdorff_distance(polygon.boundary)>1e-9):
                        raise ValueError('Airspace topology needs manual review: '+name)
                    geometry=json.loads(json.dumps(mapping(corrected)))
                    properties['topology_note']='Self-crossing ring split into polygons; all boundary edges retained.'
                    normalised+=1
                features.append({'type':'Feature','id':area.get('id',area['name'])+':'+str(index),
                    'properties':properties,'geometry':geometry})
    effective=date.fromisoformat(effective_date)
    result={'type':'FeatureCollection','source':source,'source_commit':source_commit,
        'source_sha256':source_hashes,'retrieved_at':datetime.now(UTC).isoformat(),
        'effective_date':effective.isoformat(),'valid_until':(effective+timedelta(days=27)).isoformat(),
        'attribution':ATTRIBUTION,'excluded_nonrestriction_entries':excluded,
        'normalised_crossing_rings':normalised,
        'limitations':'Third-party AIP transcription, principally at/below FL195. No LOA concessions applied. Arcs sampled at <=3 degrees. Temporary activation and NOTAMs require official checks.',
        'features':features}
    return validate_geojson(result)


def fetch(url):
    request=Request(url,headers={'User-Agent':'DuoDiscusWeather/1.0 (UK gliding forecast and airspace checks)'})
    with urlopen(request,timeout=30) as response:raw=response.read(12_000_001)
    if len(raw)>12_000_000:raise ValueError('Airspace source exceeds 12 MB')
    return raw


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit',help='Exact ahsparrow/airspace commit for a historical release')
    parser.add_argument('--effective-date',help='YYYY-MM-DD AIRAC date, required with --commit')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    if args.commit:
        if not re.fullmatch('[0-9a-f]{40}',args.commit) or not args.effective_date:parser.error('Use a full commit hash and --effective-date')
        import yaml
        base='https://raw.githubusercontent.com/ahsparrow/airspace/'+args.commit+'/'
        sources={name:fetch(base+name) for name in ('airspace.yaml','rat.yaml','release.txt')}
        if 'AIRAC: '+args.effective_date not in sources['release.txt'].decode():raise ValueError('AIRAC date not present in source release notes')
        body={}
        for name in ('airspace.yaml','rat.yaml'):body.update(yaml.safe_load(sources[name]))
        effective,commit,source=args.effective_date,args.commit,'https://github.com/ahsparrow/airspace/tree/'+args.commit
    else:
        sources={'yaixm.json':fetch(SOURCE)};body=json.loads(sources['yaixm.json'])
        effective=body['release']['airac_date'][:10];commit=body['release']['commit'];source=SOURCE
    result=convert_yaixm(body,effective_date=effective,source=source,source_commit=commit,
        source_hashes={k:hashlib.sha256(v).hexdigest() for k,v in sources.items()})
    if not 500<=len(result['features'])<=5000:raise ValueError('Unexpected national boundary count')
    output=args.output or ROOT/('uk_airspace_next.geojson' if date.fromisoformat(effective)>date.today() else 'uk_airspace.geojson')
    if output.exists():
        old=json.loads(output.read_text())
        if old.get('effective_date','')>effective:raise ValueError('Refusing an older release over newer airspace')
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary=output.with_suffix('.tmp');temporary.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'))+'\n');temporary.replace(output)
    print(json.dumps({'output':str(output),'features':len(result['features']),'effective_date':effective,'valid_until':result['valid_until'],'source_commit':commit}))


if __name__=='__main__':main()
