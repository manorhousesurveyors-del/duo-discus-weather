"""Bounded OpenAir/GeoJSON import and horizontal corridor screening.

Airspace boundaries/activation are source data. No imported file can establish
current NOTAM activity or grant clearance; absent geometry stays unassessed.
"""
from datetime import UTC,date,datetime
from functools import lru_cache
import hashlib
from pathlib import Path
import json
import math
import re
from wave_map import destination

DEFAULT_PATH=Path(__file__).with_name('data')/'uk_airspace.geojson'
NEXT_PATH=Path(__file__).with_name('data')/'uk_airspace_next.geojson'
LOCAL_PATH=Path(__file__).with_name('var')/'uk_airspace.geojson'


def coordinate(text):
    pattern=r'(\d{1,3})(?::(\d{1,2}))?(?::([\d.]+))?\s*([NSEW])'
    matches=re.findall(pattern,text.upper())
    if len(matches)!=2:raise ValueError('Invalid OpenAir coordinate: '+text[:80])
    values={}
    for d,m,s,h in matches:
        minutes=float(m or 0);seconds=float(s or 0)
        if minutes>=60 or seconds>=60:raise ValueError('Invalid OpenAir DMS coordinate')
        values[h]=(float(d)+minutes/60+seconds/3600)*(-1 if h in 'SW' else 1)
    lat=next((v for h,v in values.items() if h in 'NS'),None);lon=next((v for h,v in values.items() if h in 'EW'),None)
    if lat is None or lon is None or not -90<=lat<=90 or not -180<=lon<=180:raise ValueError('Invalid OpenAir coordinate')
    return lat,lon


def parse_openair(text,*,source='User OpenAir upload'):
    features=[];current=None;points=[];centre=None;clockwise=True
    def finish():
        if current is None:return
        if len(points)<3:raise ValueError('Airspace has no usable polygon: '+current.get('name','unnamed'))
        ring=[[lon,lat] for lat,lon in points]
        if ring[0]!=ring[-1]:ring.append(ring[0])
        features.append({'type':'Feature','properties':current.copy(),'geometry':{'type':'Polygon','coordinates':[ring]}})
    for line in text.splitlines():
        line=line.split('*',1)[0].strip()
        if not line:continue
        code=line[:2].upper();value=line[2:].strip()
        if code=='AC':finish();current={'class':value,'name':'Unnamed airspace','floor':'Unknown','ceiling':'Unknown'};points=[];centre=None;clockwise=True
        elif current is None:continue
        elif code=='AN':current['name']=value
        elif code=='AL':current['floor']=value
        elif code=='AH':current['ceiling']=value
        elif code=='DP':points.append(coordinate(value))
        elif line.upper().startswith('V X='):centre=coordinate(line[4:])
        elif line.upper().startswith('V D='):clockwise=line[4:].strip()!='-'
        elif code=='DC':
            if centre is None:raise ValueError('OpenAir circle needs a centre')
            radius=float(value)*1.852
            if not 0<radius<1000:raise ValueError('Invalid OpenAir radius')
            points.extend(destination(*centre,angle,radius) for angle in range(0,360,3))
        elif code in ('DA','DB'):
            if centre is None:raise ValueError('OpenAir arc needs a centre')
            if code=='DA':radius,start,end=map(float,value.split(','));radius*=1.852
            else:
                from config import GeoPoint
                from soaring_physics import distance_bearing
                left,right=value.split(',',1);a,b=coordinate(left),coordinate(right)
                radius,start=distance_bearing(GeoPoint('c','Centre',*centre),GeoPoint('a','Arc',*a));_,end=distance_bearing(GeoPoint('c','Centre',*centre),GeoPoint('b','Arc',*b))
            if not 0<radius<1000:raise ValueError('Invalid OpenAir arc radius')
            span=(end-start)%360 if clockwise else (start-end)%360
            steps=max(1,math.ceil(span/3));sign=1 if clockwise else -1
            points.extend(destination(*centre,(start+sign*span*i/steps)%360,radius) for i in range(steps+1))
        elif code in ('DY','DS'):raise ValueError('Unsupported OpenAir geometry: '+code)
        if len(points)>20000 or len(features)>10000:raise ValueError('Airspace geometry exceeds size limit')
    finish()
    return validate_geojson({'type':'FeatureCollection','features':features,'source':source,'retrieved_at':datetime.now(UTC).isoformat()})


def validate_geojson(body):
    if not isinstance(body,dict) or body.get('type')!='FeatureCollection' or not isinstance(body.get('features'),list) or not 1<=len(body['features'])<=10000:raise ValueError('Expected an airspace GeoJSON FeatureCollection')
    for f in body['features']:
        if not isinstance(f,dict) or f.get('type')!='Feature':raise ValueError('Invalid airspace feature')
        g=f.get('geometry',{});properties=f.get('properties',{})
        if not isinstance(g,dict) or g.get('type') not in ('Polygon','MultiPolygon') or not isinstance(properties,dict):raise ValueError('Airspace needs polygon geometries and properties')
        if not isinstance(g.get('coordinates'),list) or not g['coordinates']:raise ValueError('Missing airspace coordinates')
        polygons=[g['coordinates']] if g['type']=='Polygon' else g['coordinates']
        bounds=[float('inf'),float('inf'),-float('inf'),-float('inf')]
        for polygon in polygons:
            if not isinstance(polygon,list) or not polygon:raise ValueError('Empty airspace polygon')
            for ring in polygon:
                if not isinstance(ring,list) or not 4<=len(ring)<=20000 or ring[0]!=ring[-1]:raise ValueError('Airspace ring must be closed')
                for point in ring:
                    if not isinstance(point,list) or len(point)<2 or not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in point[:2]) or not -180<=point[0]<=180 or not -90<=point[1]<=90:raise ValueError('Invalid airspace point')
                    bounds=[min(bounds[0],point[0]),min(bounds[1],point[1]),max(bounds[2],point[0]),max(bounds[3],point[1])]
        for key in ('name','class','floor','ceiling'):
            if key in properties and not isinstance(properties[key],str):raise ValueError('Airspace labels must be text')
            properties.setdefault(key,'Unknown')
        f['properties']=properties
        f['bbox']=bounds
    return body


def import_airspace(data,filename):
    if len(data)>12_000_000:raise ValueError('Airspace file exceeds 12 MB')
    text=data.decode('utf-8-sig')
    return validate_geojson(json.loads(text)) if filename.lower().endswith(('.json','.geojson')) else parse_openair(text)


@lru_cache(maxsize=4)
def _read_airspace(path,modified):
    raw=Path(path).read_bytes()
    if len(raw)>12_000_000:raise ValueError('Airspace file exceeds 12 MB')
    body=validate_geojson(json.loads(raw))
    body['content_hash']=hashlib.sha256(raw).hexdigest()
    return body


def load_airspace(on_date=None):
    """Prefer explicit uploads; otherwise select the latest effective AIRAC set."""
    on_date=on_date or datetime.now(UTC).date()
    if LOCAL_PATH.exists():return _read_airspace(str(LOCAL_PATH),LOCAL_PATH.stat().st_mtime_ns)
    bodies=[_read_airspace(str(p),p.stat().st_mtime_ns) for p in (DEFAULT_PATH,NEXT_PATH) if p.exists()]
    if not bodies:return None
    eligible=[b for b in bodies if b.get('effective_date','0001-01-01')<=on_date.isoformat()]
    return max(eligible,key=lambda b:b.get('effective_date','')) if eligible else min(bodies,key=lambda b:b.get('effective_date',''))


def airspace_date_warning(body,on_date):
    if body is None:return 'Airspace boundary checks are UNASSESSED: no dataset loaded.'
    try:start,end=date.fromisoformat(body['effective_date']),date.fromisoformat(body['valid_until'])
    except (KeyError,TypeError,ValueError):return 'Airspace effective dates are unavailable. Verify this supplied file before use.'
    if not start<=on_date<=end:return f'Airspace dataset is outside its AIRAC period ({start} to {end}) for {on_date}. Refresh boundaries; intersections are only a reference.'
    return None


def _inside(point,ring):
    x,y=point;inside=False
    for a,b in zip(ring,ring[1:]):
        if (a[1]>y)!=(b[1]>y) and x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:inside=not inside
    return inside


def _cross(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def _point_segment(p,a,b):
    length=(b[0]-a[0])**2+(b[1]-a[1])**2
    f=max(0,min(1,((p[0]-a[0])*(b[0]-a[0])+(p[1]-a[1])*(b[1]-a[1]))/length)) if length else 0
    return math.hypot(p[0]-a[0]-f*(b[0]-a[0]),p[1]-a[1]-f*(b[1]-a[1]))


def _segment_distance(a,b,c,d):
    if (_cross(a,b,c)*_cross(a,b,d)<=0 and _cross(c,d,a)*_cross(c,d,b)<=0
        and max(min(a[0],b[0]),min(c[0],d[0]))<=min(max(a[0],b[0]),max(c[0],d[0]))
        and max(min(a[1],b[1]),min(c[1],d[1]))<=min(max(a[1],b[1]),max(c[1],d[1]))):return 0
    return min(_point_segment(p,x,y) for p,x,y in ((a,c,d),(b,c,d),(c,a,b),(d,a,b)))


def corridor_intersections(a,b,body,buffer_km=5):
    if body is None:return ()
    scale=111.195*math.cos(math.radians((a.latitude+b.latitude)/2))
    project=lambda p:(p[0]*scale,p[1]*111.195)
    start,end=project((a.longitude,a.latitude)),project((b.longitude,b.latitude));found=[]
    latitude_margin=buffer_km/111.195;longitude_margin=buffer_km/max(abs(scale),1e-6)
    envelope=(min(a.longitude,b.longitude)-longitude_margin,min(a.latitude,b.latitude)-latitude_margin,
              max(a.longitude,b.longitude)+longitude_margin,max(a.latitude,b.latitude)+latitude_margin)
    for f in body['features']:
        bbox=f.get('bbox')
        if bbox and (bbox[2]<envelope[0] or bbox[0]>envelope[2] or bbox[3]<envelope[1] or bbox[1]>envelope[3]):continue
        g=f['geometry'];polygons=[g['coordinates']] if g['type']=='Polygon' else g['coordinates'];distance=float('inf')
        for poly in polygons:
            rings=[[project(p) for p in ring] for ring in poly]
            if any(_inside(p,rings[0]) and not any(_inside(p,hole) for hole in rings[1:]) for p in (start,end)):distance=0
            for ring in rings:
                distance=min(distance,min(_segment_distance(start,end,c,d) for c,d in zip(ring,ring[1:])))
        if distance<=buffer_km:found.append({**f['properties'],'distance_km':round(distance,1)})
    return tuple(found)
