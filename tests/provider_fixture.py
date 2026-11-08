"""Synthetic provider boundary for deterministic HTTP integration tests only."""
from datetime import UTC,datetime
import json
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
