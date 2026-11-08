"""Production synchronous weather path with a synthetic HTTP boundary.

DUO_TEST_SCENARIO selects the boundary. No weather, OGN or alert traffic leaves
this fixture. Use verify_architecture.py; never deploy this file as the app.
"""
import json,os,sys
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from threading import Lock,Thread
from time import perf_counter
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['DUO_OGN_ENABLED']='0'
os.environ['DUO_DASHBOARD_SNAPSHOT']=''
import streamlit as st
import app,sync_weather
from data_ingestion import JsonResponse,ServiceError
from tests.provider_fixture import FailoverProvider,NOW
from urllib.error import HTTPError
from urllib.parse import urlencode,urlsplit
from urllib.request import Request

class Clock(datetime):
    @classmethod
    def now(cls,tz=None):return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    @classmethod
    def fromtimestamp(cls,value,tz=None):return datetime.fromtimestamp(value,tz)
    @classmethod
    def combine(cls,day,hour,tzinfo=None):return datetime.combine(day,hour,tzinfo)


# Reproduce an exception retained from an older import: it deliberately does
# NOT inherit today's IngestionError and is not today's exported class object.
StaleDeferredWeather=type('DeferredWeather',(RuntimeError,),{'__module__':'weather_engine'})


class FixtureTransport:
    """Host-restricted clients prove failover bypasses the stale primary."""
    def __init__(self,provider,host):
        self.provider=provider;self.lock=Lock();self.started=None;self.completed=None
        self.host=host;self.attempts=[];self.deferred_raises=0

    def get_json(self,url,params,*,ttl_seconds=0):
        with self.lock:
            if self.started is None:self.started=perf_counter()
            self.attempts.append((urlsplit(url).hostname,dict(params)))
        request=Request(url+'?'+urlencode(params))
        try:
            if urlsplit(url).hostname!=self.host:
                raise AssertionError('Provider used the wrong fixture transport: '+url)
            if self.host=='api.open-meteo.com' and self.provider.scenario=='deferred':
                with self.lock:self.deferred_raises+=1
                raise StaleDeferredWeather('Synthetic stale provider gate failed before HTTP')
            with self.provider(request,timeout=4.0) as response:
                rows=json.loads(response.read())
        except HTTPError as exc:
            if self.provider.scenario=='429':raise  # Exercise an unnormalised provider exception.
            raise ServiceError('Synthetic provider HTTP '+str(exc.code),status=exc.code) from exc
        except TimeoutError as exc:
            raise ServiceError('Synthetic provider timeout') from exc
        finally:
            with self.lock:self.completed=perf_counter()
        if urlsplit(url).hostname=='api.met.no':return JsonResponse(rows,NOW)
        wanted={'time',*str(params.get('hourly','')).split(',')}
        for row in rows:
            row['hourly']={key:value for key,value in row['hourly'].items() if key in wanted}
            row['hourly_units']={key:value for key,value in row['hourly_units'].items() if key in wanted}
        return JsonResponse(rows,NOW)

@st.cache_resource
def install():
    scenario=os.environ.get('DUO_TEST_SCENARIO','healthy')
    provider=FailoverProvider(scenario,.05)
    # The older broad fixture used native surface_pressure only. The compact
    # production query requests the provider's standard MSL pressure instead.
    provider.hourly['pressure_msl']=[1013.25]*len(provider.hourly['time'])
    transport=FixtureTransport(provider,'api.open-meteo.com')
    met_transport=FixtureTransport(provider,'api.met.no')
    class Statistics(BaseHTTPRequestHandler):
        def do_GET(self):
            with provider.lock:calls=list(provider.calls)
            starts=[];ends=[];attempts=[];deferred=0
            for client in (transport,met_transport):
                with client.lock:
                    if client.started is not None:starts.append(client.started)
                    if client.completed is not None:ends.append(client.completed)
                    attempts.extend(client.attempts);deferred+=client.deferred_raises
            span=(max(ends)-min(starts))*1000 if ends else None
            body=json.dumps({'requests':len(calls),'calls':calls,'provider_span_ms':span,
                             'transport_attempts':attempts,'deferred_raises':deferred}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
        def do_POST(self):
            scenario=self.rfile.read(int(self.headers.get('Content-Length','0'))).decode()
            if scenario not in ('healthy','429','timeout','offline','deferred'):
                self.send_error(400);return
            provider.scenario=scenario
            provider.delay=.05
            self.send_response(204);self.end_headers()
        def log_message(self,*args):pass
    statistics=ThreadingHTTPServer(('127.0.0.1',0),Statistics)
    Thread(target=statistics.serve_forever,daemon=True).start()
    return provider,transport,met_transport,statistics.server_address[1]

provider,transport,met_transport,statistics_port=install()
sync_weather.transport=lambda:transport
sync_weather.met_transport=lambda:met_transport
sync_weather.datetime=Clock
app.datetime=Clock
app.main()
st.caption('Fixture server render ms: '+str(st.session_state['_page_render_ms']))
st.caption('Fixture full renders: '+str(st.session_state['_full_render_count']))
st.caption('Fixture HTTP requests: '+str(len(provider.calls)))
st.caption('Fixture statistics URL: http://127.0.0.1:'+str(statistics_port))
st.caption('Synthetic browser test only · '+provider.scenario)
