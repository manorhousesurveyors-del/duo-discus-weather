"""Keyless OGN/SpotTheGlider HTTPS snapshot bridge with bounded freshness.

A shared worker publishes only current UK glider reports through Streamlit's
same-origin static server. Map timers poll that JSON without Python reruns.
"""
from datetime import UTC,datetime
from pathlib import Path
from threading import Thread,Event,Lock
import json,time
import xml.etree.ElementTree as ET
from ogn_live import (MAX_AGE_SECONDS, DDB, FEEDS, fetch, parse_devices,
                      parse_spot_xml, TrafficCache, AprsReceiver)

ROOT=Path(__file__).resolve().parent
FEED=FEEDS[0]  # Backwards-compatible public constant.
SNAPSHOT=ROOT/'static'/'ogn'/'uk.json'
CACHE=ROOT/'var'/'ogn-devices.json'


class TrafficWorker:
    def __init__(self,path=SNAPSHOT):
        self.path=Path(path);self.stop=Event();self.thread=None;self.devices={};self.registry_at=0
        self.cache=TrafficCache()
        self.aprs=AprsReceiver(self.cache,lambda:self.devices,lambda:time.time()<getattr(self,"active_until",0))
    def write(self,body):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.tmp');temporary.write_text(json.dumps(body,separators=(',',':')));temporary.replace(self.path)
    def once(self):
        if time.time()-self.registry_at>43200:
            try:
                cached=CACHE.exists() and time.time()-CACHE.stat().st_mtime<43200
                raw=CACHE.read_bytes() if cached else fetch(DDB,25_000_000)
                self.devices=parse_devices(raw)
                if not cached:
                    CACHE.parent.mkdir(exist_ok=True);CACHE.write_bytes(raw)
                self.registry_at=time.time()
            except (ValueError,OSError):self.registry_at=time.time()-42800
        rows=[];sources=[];errors=[]
        # Both mirror variants are useful: they need not return identical sets.
        for url in FEEDS:
            try:
                body=parse_spot_xml(fetch(url,8_000_000),devices=self.devices)
                rows.extend(body['aircraft']);sources.append(url)
            except (ValueError,OSError,ET.ParseError) as exc:errors.append(type(exc).__name__)
        now=datetime.now(UTC)
        aircraft=self.cache.merge(rows,now=now)
        # Apply newly refreshed registry opt-outs to retained/APRS reports too.
        aircraft=[p for p in aircraft if not any(d.get('tracked')!='Y' for d in self.devices.get(p['id'],[]))]
        for p in aircraft:
            registry=self.devices.get(p['id'],[])
            if any(d.get('identified')!='Y' for d in registry):
                p.update(registration='?'+p['id'][-2:],competition_id='',aircraft_model='',highlight=False)
        return {'schema':1,'status':'live' if sources else 'degraded','fetched_at':now.isoformat(),
                'aircraft':aircraft,'source':'OGN APRS / SpotTheGlider','sources':sources,
                'aprs_status':self.aprs.status,'errors':errors,
                'note':('Live feed received. ' if sources else 'HTTPS feed unavailable; retained/APRS positions shown. ')+
                       'Reports expire after 15 minutes. Check age; not collision avoidance.'}
    def run(self):
        while not self.stop.is_set():
            if time.time()>getattr(self,'active_until',0):
                self.stop.wait(.5);continue
            try:body=self.once()
            except (ValueError,OSError,ET.ParseError) as exc:
                body={'schema':1,'status':'unavailable','fetched_at':datetime.now(UTC).isoformat(),'aircraft':[],
                      'source':'OGN / SpotTheGlider','note':'Live feed unavailable ('+type(exc).__name__+'). No synthetic traffic is shown in Live mode.'}
            try:self.write(body)
            except OSError:pass
            self.stop.wait(20)
    def start(self):
        self.write({'schema':1,'status':'connecting','fetched_at':datetime.now(UTC).isoformat(),'aircraft':[],
                    'source':'OGN / SpotTheGlider','note':'Connecting to the live UK feed…'})
        self.active_until=time.time()+35
        self.aprs.start()
        self.thread=Thread(target=self.run,name='duo-ogn-feed',daemon=True);self.thread.start();return self


_worker=None
_lock=Lock()
def ensure_worker():
    global _worker
    with _lock:
        if _worker is None:_worker=TrafficWorker().start()
    return _worker


def set_worker_active(active: bool) -> None:
    """Renew or pause an existing worker; this hook never starts a new thread."""
    if _worker is not None:_worker.active_until=time.time()+35 if active else 0


def traffic_heartbeat():
    import streamlit as st
    @st.fragment(run_every='20s')
    def heartbeat():
        ensure_worker();set_worker_active(True)
    heartbeat()


def add_traffic(map_view,*,show=False,recorded=False):
    from client_map import script
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    if show and not recorded and get_script_run_ctx(suppress_warning=True):ensure_worker();set_worker_active(True)
    script(map_view,'ogn_traffic.js','installDuoTraffic',{'show':show,'recorded':recorded,'path':'app/static/ogn/uk.json','maxAgeSeconds':MAX_AGE_SECONDS})
