"""Resolve named UK places without treating ordinary English as BGA codes."""
from dataclasses import dataclass
import re
import unicodedata
import streamlit as st
from config import GeoPoint,MONITORING_ZONES
from data_ingestion import JsonHttpClient,IngestionError

class PlaceError(ValueError):pass

@dataclass(frozen=True)
class Place:
    point: GeoPoint
    source: str


def normal(value):
    return re.sub(r'[^a-z0-9]+',' ',unicodedata.normalize('NFKD',value).casefold()).strip()


def requested_name(question):
    # Keep the place phrase, dropping date/radius/forecast qualifiers.
    match=re.search(r'\b(?:at|around|near|for|in|from|over)\s+(.+)',question,re.I)
    value=match.group(1) if match else question
    value=re.split(r'\b(?:today|tomorrow|tonight|within|only|on|this|next|with|weather|soaring|forecast|conditions|thermal|ridge|wave)\b|[?!;]',value,flags=re.I)[0]
    value=re.sub(r'\b(?:please|the)\b','',value,flags=re.I).strip(' ,.:"\'')
    return value[:100]


@st.cache_data(ttl=86400,show_spinner=False,max_entries=256)
def geocode(name):
    body=JsonHttpClient().get_json('https://geocoding-api.open-meteo.com/v1/search',
        {'name':name,'count':10,'language':'en','format':'json','countryCode':'GB'},ttl_seconds=86400).body
    rows=[p for p in body.get('results',[]) if p.get('country_code')=='GB']
    exact=[p for p in rows if normal(p.get('name',''))==normal(name)]
    rows=exact or rows
    if not rows:raise PlaceError(f'No UK place matched “{name}”. Try its town and county, airfield name, BGA code or map coordinate.')
    # Avoid quietly forecasting the wrong Newport or other duplicate place.
    if len(rows)>1 and normal(rows[0]['name'])==normal(rows[1]['name']):
        labels=list(dict.fromkeys(p['name']+', '+p.get('admin1','')+', '+p.get('admin2','') for p in rows))
        raise PlaceError('Several places match. Please choose a map point or include the county: '+'; '.join(labels[:4]))
    row=rows[0]
    point=GeoPoint('place:'+str(row['id']),row['name'],float(row['latitude']),float(row['longitude']))
    return Place(point,'Open-Meteo geocoding / GeoNames · '+', '.join(filter(None,(row.get('admin2'),row.get('admin1')))))


def resolve_place(question,catalog,selected=None,*,allow_network=False):
    if selected and re.search(r'\b(?:here|clicked|selected|this location|this airfield|that point|map point)\b',question,re.I):
        return Place(selected,'Selected map coordinate')
    coordinate=re.search(r'(?<!\d)(5\d(?:\.\d+)?|60(?:\.\d+)?)\s*[,/]\s*(-?\d(?:\.\d+)?)',question)
    if coordinate:
        point=GeoPoint('clicked','Requested coordinate',*map(float,coordinate.groups()))
        if not -9<=point.longitude<=2.5:raise PlaceError('Choose a coordinate within the UK forecast area.')
        return Place(point,'User coordinate')
    from uk_airfields import load_airfields
    # Explicit club aliases point to published airfield/turnpoint coordinates.
    aliases={'midland gliding club':'MYN','midlands gliding club':'MYN','long mynd':'MYN','the mynd':'MYN','mgc':'MYN'}
    q=normal(question)
    phrase=normal(requested_name(question))
    named_phrase=bool(phrase and re.search(r'\b(?:at|around|near|for|in|from|over)\s+',question,re.I)
        and not re.search(r'\b(?:task|route|day|week|me|us|can|what|how|fly|help|suggest|plan)\b',phrase))
    def matches_name(n):
        if named_phrase:
            # A catalog's "Stretton" must not steal "Church Stretton". A whole
            # requested place takes precedence over a shorter embedded name.
            return bool(re.fullmatch(re.escape(n)+r'(?: gliding club| gliding site| airfield| airport)?',phrase))
        return bool(re.search(r'\b'+re.escape(n)+r'\b',q))
    for alias,code in aliases.items():
        if re.search(r'\b'+re.escape(alias)+r'\b',q) and code in catalog.by_code:
            p=catalog.by_code[code]
            return Place(GeoPoint(p.point.key,'Long Mynd / Midland Gliding Club',p.latitude,p.longitude),'BGA '+code+' published coordinate')
    matches=[]
    for a in load_airfields():
        names=[a.name,*a.aliases]
        if a.ident=='EGBP':names.append('Cotswold')
        # Distinctive club names may omit the generic airfield suffix.
        if a.name.lower().endswith(' airfield'):names.append(a.name[:-9])
        for name in names:
            n=normal(name)
            if len(n)>=4 and matches_name(n):matches.append((len(n)+.5,a.point,'UK airfield catalog · OurAirports / NOAA'))
        if a.ident in re.findall(r'\b[A-Z0-9-]{3,10}\b',question):matches.append((len(a.ident),a.point,'UK airfield '+a.ident))
    for p in catalog.turnpoints:
        n=normal(p.name)
        if len(n)>=4 and matches_name(n):matches.append((len(n),GeoPoint(p.point.key,p.name,p.latitude,p.longitude),'BGA '+p.code+' published coordinate'))
        if p.code in re.findall(r'\b[A-Z0-9]{3}\b',question):matches.append((len(p.code),GeoPoint(p.point.key,p.name,p.latitude,p.longitude),'BGA '+p.code+' published coordinate'))
    if matches:
        _,point,source=max(matches,key=lambda m:m[0]);return Place(point,source)
    name=requested_name(question)
    generic={'','me','us','my task','my route','a task','a weather','a soaring','five days','5 days','day','week','weekend','a day','a while','a bit'}
    if name and normal(name) not in generic and not re.search(r'\b(task|route|sector|where|which|explain|fly|help|suggest|plan|can|how|what|wind|lift|forecast)\b',name,re.I):
        if allow_network:
            try:return geocode(name)
            except IngestionError as exc:raise PlaceError(f'Place lookup for “{name}” is unavailable: {exc}. Use a named airfield, BGA code or click the map.') from None
        raise PlaceError(f'“{name}” needs a UK place lookup. Select its map coordinate or enable live geocoding.')
    if selected:return Place(selected,'Selected map coordinate')
    return Place(MONITORING_ZONES['shropshire'],'Default Long Mynd forecast point')


def sector_radius(question):
    match=re.search(r'\b(\d{1,3}(?:\.\d+)?)\s*(?:km|kilometres?|kilometers?)\s*(?:local\s*)?(?:sector|radius|around|of)?',question,re.I)
    # Task distances do not redefine a local forecast sector.
    if match and (re.search(r'\b(within|radius|sector|local|only)\b',question,re.I)):
        return max(1.,min(100.,float(match.group(1))))
    return 20.


@st.cache_data(ttl=1800,show_spinner=False,max_entries=96)
def local_forecasts(point,radius,model_key,refresh_bucket):
    from data_ingestion import OpenMeteoClient
    from config import FORECAST_MODELS
    from math import radians,sin,cos,asin,atan2,degrees
    points=[point];lat,lon=radians(point.latitude),radians(point.longitude);d=radius/6371
    for bearing in (0,90,180,270):
        a=radians(bearing);y=asin(sin(lat)*cos(d)+cos(lat)*sin(d)*cos(a))
        x=lon+atan2(sin(a)*sin(d)*cos(lat),cos(d)-sin(lat)*sin(y))
        points.append(GeoPoint(point.key+':sector'+str(bearing),point.name+' sector '+str(bearing)+'°',degrees(y),degrees(x)))
    return OpenMeteoClient().fetch_batched(points,(FORECAST_MODELS[model_key],))
