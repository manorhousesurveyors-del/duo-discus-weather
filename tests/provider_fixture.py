"""Synthetic provider boundary for deterministic HTTP integration tests only."""
from datetime import UTC,datetime,timedelta
import json
import math
from threading import Lock
from time import sleep
from urllib.error import HTTPError
from urllib.parse import parse_qs,urlsplit
from config import GeoPoint
from tests.fixtures import forecast
from tests.test_ingestion import Response

START=datetime(2026,11,8,tzinfo=UTC)
NOW=START.replace(hour=8)

class Provider:
    def __init__(self,scenario='healthy',delay=0):
        self.scenario,self.delay=scenario,delay
        self.calls=[];self.lock=Lock()
        f=forecast(start=START,hours=144)
        self.units={'time':'unixtime',**f.units}
        self.hourly={'time':[int(t.timestamp()) for t in f.times],**{k:list(v) for k,v in f.hourly.items()}}

    def __call__(self,request,**kwargs):
        parsed=urlsplit(request.full_url);q=parse_qs(parsed.query)
        with self.lock:self.calls.append((parsed.hostname,q))
        if self.delay:sleep(self.delay)
        if self.scenario=='429':raise HTTPError(request.full_url,429,'Limited',{'Retry-After':'60'},None)
        if self.scenario=='timeout':raise TimeoutError('Synthetic slow upstream')
        if parsed.hostname!='api.open-meteo.com':raise AssertionError('Unexpected fixture host '+str(parsed.hostname))
        rows=[]
        for lat,lon in zip(q['latitude'][0].split(','),q['longitude'][0].split(',')):
            hourly={k:v[:] for k,v in self.hourly.items()}
            if q.get('models')==['ukmo_seamless']:
                for k in ('boundary_layer_height','cape','relative_humidity_850hPa'):
                    hourly[k]=[None]*len(hourly['time'])
            rows.append({'latitude':float(lat),'longitude':float(lon),'elevation':100,
                'utc_offset_seconds':0,'hourly_units':self.units,'hourly':hourly})
        return Response(json.dumps(rows).encode())


def met_compact(latitude=52.5, longitude=-2.9):
    """Realistic MET JSON shape; invented weather, solely for HTTP fixtures.

    Compact forecasts change from hourly to six-hour steps late in the range.
    Dewpoint is deliberately absent: this provider supplies relative humidity.
    Rain is an accumulation for the named interval, not an instantaneous rate.
    """
    series=[]
    for hour in (*range(73), *range(78,145,6)):
        when=START+timedelta(hours=hour)
        temperature=10+4*math.cos((when.hour-13)*math.pi/12)-(latitude-52)*.3
        details={
            'air_temperature':round(temperature,2),
            'relative_humidity':65.,
            'wind_speed':6.+(latitude-50)*.1,
            'wind_from_direction':250.,
            'wind_speed_of_gust':10.,
            'cloud_area_fraction':35.,
            'cloud_area_fraction_low':25.,
            'cloud_area_fraction_medium':10.,
            'cloud_area_fraction_high':15.,
            'air_pressure_at_sea_level':1015.,
        }
        interval=1 if hour<72 else 6
        series.append({'time':when.isoformat().replace('+00:00','Z'),'data':{
            'instant':{'details':details},
            f'next_{interval}_hours':{'summary':{'symbol_code':'partlycloudy_day'},
                                     'details':{'precipitation_amount':0.}},
        }})
    return {'type':'Feature','geometry':{'type':'Point','coordinates':[longitude,latitude,100]},
        'properties':{'meta':{'updated_at':NOW.isoformat().replace('+00:00','Z'),
            'units':{'air_temperature':'celsius','relative_humidity':'%',
                'wind_speed':'m/s','wind_from_direction':'degrees','wind_speed_of_gust':'m/s',
                'cloud_area_fraction':'%','cloud_area_fraction_low':'%',
                'cloud_area_fraction_medium':'%','cloud_area_fraction_high':'%',
                'air_pressure_at_sea_level':'hPa','precipitation_amount':'mm'}},
            'timeseries':series}}


class FailoverProvider(Provider):
    """Two-provider fixture: only Open-Meteo fails, unless fully offline."""
    def __call__(self,request,**kwargs):
        parsed=urlsplit(request.full_url);q=parse_qs(parsed.query)
        if parsed.hostname=='api.open-meteo.com' and self.scenario=='healthy':
            return super().__call__(request,**kwargs)
        if parsed.hostname not in ('api.open-meteo.com','api.met.no'):
            raise AssertionError('Unexpected fixture host '+str(parsed.hostname))
        with self.lock:self.calls.append((parsed.hostname,q))
        if self.delay:sleep(self.delay)
        if self.scenario=='offline':raise TimeoutError('Both synthetic providers offline')
        if parsed.hostname=='api.open-meteo.com':
            if self.scenario=='429':raise HTTPError(request.full_url,429,'Limited',{'Retry-After':'60'},None)
            raise TimeoutError('Synthetic Open-Meteo timeout')
        if parsed.path!='/weatherapi/locationforecast/2.0/compact':
            raise AssertionError('Unexpected MET endpoint '+parsed.path)
        return Response(json.dumps(met_compact(float(q['lat'][0]),float(q['lon'][0]))).encode())
