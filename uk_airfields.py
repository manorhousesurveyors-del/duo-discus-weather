"""UK airfields, runway metadata and NOAA reporting capability, with provenance."""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from json_payload import browser_json
import json

from bga_turnpoints import Turnpoint
from config import GeoPoint
from soaring_physics import distance_bearing

DATA_PATH=Path(__file__).with_name('data')/'uk_airfields.json'


@dataclass(frozen=True)
class Airfield:
    ident: str
    name: str
    latitude: float
    longitude: float
    elevation_ft: float | None
    icao: str
    kind: str
    runways: tuple
    metar: bool
    taf: bool
    aliases: tuple
    status: str = 'Status requires operator check'

    @property
    def point(self):return GeoPoint(self.ident,self.name,self.latitude,self.longitude)

    @property
    def label(self):return ' / '.join((*self.aliases,self.name)) if self.aliases else self.name

    @property
    def turnpoint(self):
        runway=next(iter(self.runways),{})
        return Turnpoint(self.ident,self.label,self.latitude,self.longitude,
            None if self.elevation_ft is None else self.elevation_ft*.3048,
            style=4 if self.kind=='gliding_airfield' else 5,
            runway_direction=None if runway.get('heading_true') is None else round(runway['heading_true']),
            runway_length_m=runway.get('length_m'),source='OurAirports / NOAA',
            description=self.status+'; airfield waypoint, not an official BGA trigraph.')


@lru_cache(maxsize=1)
def load_airfields():
    body=json.loads(DATA_PATH.read_text())
    if body.get('schema_version')!=1:raise ValueError('Unsupported airfield schema')
    fields=tuple(Airfield(**{**row,'runways':tuple(row['runways']),'aliases':tuple(row['aliases'])}) for row in body['airfields'])
    if len({a.ident for a in fields})!=len(fields):raise ValueError('Duplicate airfield identifiers')
    for a in fields:a.point
    return fields


def airfield_by_id(ident):return next((a for a in load_airfields() if a.ident==ident),None)


def reporting_stations():return tuple(sorted(a.icao for a in load_airfields() if a.icao and (a.metar or a.taf)))


def nearest_station(airfield,*,taf=False):
    stations=[a for a in load_airfields() if a.icao and (a.taf if taf else a.metar)]
    station=min(stations,key=lambda a:distance_bearing(airfield.point,a.point)[0])
    return station,distance_bearing(airfield.point,station.point)[0]


def aviation_payload(batch):
    """Retain raw reports, timestamps and stale/validity flags; no station substitution."""
    if batch is None:return {}
    out={}
    for code in set(batch.metars)|set(batch.tafs):
        m,t=batch.metars.get(code),batch.tafs.get(code)
        out[code]={
            'metar':None if m is None else m.raw.get('rawOb'),
            'observed':None if m is None else m.observed_at.isoformat(),
            'stale':m.stale if m else True,
            'wind':None if m is None else {'speed':m.wind.speed_knots,'direction':m.wind.direction_deg,'gust':m.wind.gust_knots},
            'taf':None if t is None else t.raw.get('rawTAF'),
            'taf_valid':bool(t and t.currently_valid),
            'taf_from':None if t is None else t.valid_from.isoformat(),
            'taf_to':None if t is None else t.valid_to.isoformat(),
        }
    return out


def add_airfields(map_view,batch=None,*,live=True,route=False,selected=None):
    """Canvas markers; current model reports fetched on demand in the browser."""
    from branca.element import MacroElement,Template
    payload=airfield_records()
    element=MacroElement()
    element.payload=browser_json({'airfields':payload,'reports':aviation_payload(batch),'live':live,'route':route,'selected':selected})
    script=Path(__file__).with_name('assets').joinpath('airfields.js').read_text()
    element._template=Template('{% macro script(this, kwargs) %}\n'+script+'\ninstallDuoAirfields({{this._parent.get_name()}},{{this.payload|safe}});\n{% endmacro %}')
    element.add_to(map_view)


@lru_cache(maxsize=1)
def airfield_records():
    payload=[]
    for a in load_airfields():
        station,distance=nearest_station(a)
        taf_station,taf_distance=nearest_station(a,taf=True)
        payload.append({**a.__dict__,'label':a.label,'nearest':station.icao,'nearest_km':round(distance,1),
                        'nearest_taf':taf_station.icao,'nearest_taf_km':round(taf_distance,1)})
    return payload
