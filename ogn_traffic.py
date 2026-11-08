"""Process-wide live OGN receiver; page navigation never pauses acquisition."""
from datetime import UTC,datetime
from pathlib import Path
from threading import Thread,Event
from concurrent.futures import ThreadPoolExecutor
import json,os,time
import xml.etree.ElementTree as ET
import streamlit as st
from ogn_live import (MAX_AGE_SECONDS, DDB, FEEDS, fetch, parse_devices,
                      parse_spot_xml, TrafficCache, AprsReceiver, MirrorSession)

ROOT=Path(__file__).resolve().parent
FEED=FEEDS[0]
SNAPSHOT=ROOT/'static'/'ogn'/'uk.json'
CACHE=ROOT/'var'/'ogn-devices.json'
TRAFFIC_API_REVISION=2


@st.cache_resource(ttl=43200,show_spinner=False)
def device_registry():
    """Shared 12-hour DDB resource, with a bounded on-disk public-data cache."""
    cached=CACHE.exists() and time.time()-CACHE.stat().st_mtime<43200
    raw=CACHE.read_bytes() if cached else fetch(DDB,25_000_000)
    devices=parse_devices(raw)
    if not cached:
        CACHE.parent.mkdir(exist_ok=True)
        temporary=CACHE.with_suffix('.tmp');temporary.write_bytes(raw);temporary.replace(CACHE)
    return devices


class TrafficWorker:
    def __init__(self,path=SNAPSHOT):
        self.path=Path(path);self.stop=Event();self.thread=None;self.devices={};self.registry_at=0
        self.cache=TrafficCache()
        self.aprs=AprsReceiver(self.cache,lambda:self.devices,lambda:not self.stop.is_set())
        self.mirrors=[MirrorSession() for _ in FEEDS]
        self.http_pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='duo-ogn-xml')
        self.http_state={'sources':[],'errors':[],'last_success':None,'checked_at':None}

    def write(self,body):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(body,separators=(',',':'),allow_nan=False));temporary.replace(self.path)

    def once(self):
        """One background refresh; both cookie sessions run alongside APRS."""
        if time.time()-self.registry_at>43200:
            try:self.devices=device_registry();self.registry_at=time.time()
            except (ValueError,OSError):self.registry_at=time.time()-42840
        jobs=[(url,self.http_pool.submit(session.fetch,url,8_000_000)) for url,session in zip(FEEDS,self.mirrors)]
        rows=[];sources=[];errors=[]
        for url,job in jobs:
            try:
                body=parse_spot_xml(job.result(),devices=self.devices)
                for row in body['aircraft']:row['source_url']=url
                rows.extend(body['aircraft']);sources.append(url)
            except (ValueError,OSError,ET.ParseError) as exc:errors.append(type(exc).__name__)
        self.cache.merge(rows)
        now=datetime.now(UTC).isoformat()
        self.http_state={'sources':sources,'errors':errors,'checked_at':now,
                         'last_success':now if sources else self.http_state['last_success']}
        return self.snapshot()

    def snapshot(self):
        now=datetime.now(UTC);aircraft=self.cache.merge([],now=now);state=self.http_state
        # Registry privacy changes also apply to previously received positions.
        aircraft=[p for p in aircraft if not any(d.get('tracked')!='Y' for d in self.devices.get(p['id'],[]))]
        for p in aircraft:
            registry=self.devices.get(p['id'],[])
            if any(d.get('identified')!='Y' for d in registry):
                anonymous='_'+p['id'][-2:].lower()
                p.update(registration=anonymous,competition_id='',badge=anonymous,category='other',aircraft_model='',highlight=False)
        http_live=bool(state['sources'] and state['last_success'] and
                       (now-datetime.fromisoformat(state['last_success'])).total_seconds()<60)
        aprs_live=self.aprs.status.startswith('Connected to ')
        live_sources=(['OGN APRS-IS Live Stream'] if aprs_live else [])+(['live.glidernet.org XML'] if http_live else [])
        retained_sources=sorted({p['source'] for p in aircraft})
        return {'schema':2,'status':'live' if live_sources else 'degraded' if aircraft else 'connecting',
                'fetched_at':now.isoformat(),'aircraft':aircraft,'target_count':len(aircraft),
                'fresh_count':sum(p['age_seconds']<=120 for p in aircraft),
                'source':' + '.join(live_sources or retained_sources) or 'No live source connected',
                'live_sources':live_sources,'sources':state['sources'],'errors':state['errors'],
                'http_last_success':state['last_success'],'aprs_status':self.aprs.status,
                'aprs_packets':self.aprs.received_packets,'aprs_last_packet':self.aprs.last_received,
                'note':'Positions fade after 2 minutes and expire after 20 minutes. Not collision avoidance.'}

    def run(self):
        while not self.stop.is_set():
            try:self.once()
            except (ValueError,OSError,ET.ParseError) as exc:
                self.http_state={**self.http_state,'sources':[],'errors':[type(exc).__name__]}
            self.stop.wait(15)

    def publish(self):
        # Newly arriving APRS packets reach the static endpoint immediately,
        # even during a slow HTTPS request or the first 4.5-second warm-up.
        while not self.stop.is_set():
            try:self.write(self.snapshot())
            except OSError:pass
            self.stop.wait(1)

    def start(self):
        self.write(self.snapshot());self.aprs.start()
        self.thread=Thread(target=self.run,name='duo-ogn-http',daemon=True);self.thread.start()
        self.publisher=Thread(target=self.publish,name='duo-ogn-publish',daemon=True);self.publisher.start()
        return self

    def close(self):
        self.stop.set();self.aprs.stop.set()
        self.http_pool.shutdown(wait=False,cancel_futures=True)


_worker=None


@st.cache_resource(show_spinner=False)
def ensure_worker():
    global _worker
    _worker=TrafficWorker().start()
    return _worker


def start_background():
    """Called on live app startup, independent of the selected page."""
    if os.getenv('DUO_OGN_ENABLED','1')=='1':
        try:return ensure_worker()
        except OSError:
            import logging
            logging.getLogger(__name__).exception('Live traffic publisher unavailable')
    return None


def set_worker_active(active: bool) -> None:
    """Compatibility hook: the shared receiver now stays active across pages."""
    return None


def traffic_heartbeat():
    @st.fragment(run_every='5s')
    def heartbeat():
        worker=start_background()
        if worker is None:
            st.caption('Live telemetry disabled on this host. No aircraft are fabricated.');return
        body=worker.snapshot()
        st.caption(f"{body['source']} · {body['fresh_count']} positions ≤2 min · "
                   f"{body['target_count']} recent targets ≤20 min · {body['aprs_status']}")
    heartbeat()


def add_traffic(map_view,*,show=False,recorded=False):
    from client_map import script
    script(map_view,'ogn_traffic.js','installDuoTraffic',{'show':show,'recorded':recorded,
           'path':'app/static/ogn/uk.json','maxAgeSeconds':MAX_AGE_SECONDS})
