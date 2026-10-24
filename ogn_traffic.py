"""Keyless OGN/SpotTheGlider HTTPS snapshot bridge with bounded freshness.

A shared worker publishes only current UK glider reports through Streamlit's
same-origin static server. Map timers poll that JSON without Python reruns.
"""
from datetime import UTC,datetime,timedelta
from pathlib import Path
from threading import Thread,Event,Lock
from urllib.request import Request,urlopen
import hashlib,json,math,time
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parent
FEED='https://live.glidernet.org/lxml.php?a=0&b=61.2&c=49.7&d=2.2&e=-8.7&z=0&x=2'
DDB='https://ddb.glidernet.org/download/?j=1'
SNAPSHOT=ROOT/'static'/'ogn'/'uk.json'
CACHE=ROOT/'var'/'ogn-devices.json'
MAX_AGE_SECONDS=120


def fetch(url,limit):
    with urlopen(Request(url,headers={'User-Agent':'DuoDiscusWeather/1.0 (UK glider weather overlay)'}),timeout=12) as response:
        raw=response.read(limit+1)
    if len(raw)>limit:raise ValueError('OGN response exceeds size limit')
    return raw


def parse_devices(raw):
    body=json.loads(raw)
    if not isinstance(body,dict) or not isinstance(body.get('devices'),list) or len(body['devices'])>200000:raise ValueError('Invalid OGN device registry')
    devices={}
    for d in body['devices']:
        if not isinstance(d,dict):raise ValueError('Invalid OGN device record')
        key=str(d.get('device_id','')).upper()
        if len(key)!=6 or any(c not in '0123456789ABCDEF' for c in key):continue
        devices.setdefault(key,[]).append({k:str(d.get(k,''))[:80] for k in ('registration','cn','tracked','identified','device_type')})
    return devices


def parse_spot_xml(raw,*,now=None,devices=None):
    now=now or datetime.now(UTC);devices=devices or {}
    if len(raw)>8_000_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('Unsupported OGN XML')
    root=ET.fromstring(raw)
    if root.tag!='markers':raise ValueError('Unexpected OGN response')
    reports={};rejected=0
    for marker in root.findall('m'):
        try:
            r=marker.attrib['a'].split(',')
            if len(r)!=14:raise ValueError('Unknown OGN record layout')
            lat,lon,alt,age,heading,speed,vario=map(float,(r[0],r[1],r[4],r[6],r[7],r[8],r[9]))
            if not all(math.isfinite(x) for x in (lat,lon,alt,age,heading,speed,vario)):raise ValueError('Nonfinite report')
            if r[10]!='1' or not (49.7<=lat<=61.2 and -8.7<=lon<=2.2):continue
            if not (0<=age<=MAX_AGE_SECONDS and -100<=alt<=15000 and 0<=heading<=360 and 0<=speed<=600 and abs(vario)<=50):continue
            # OGN's table includes stationary devices. Movement is an airborne
            # proxy, not proof that the aircraft has left the ground.
            if speed<25:continue
            clock=datetime.strptime(r[5],'%H:%M:%S').time()
            observed=now.replace(hour=clock.hour,minute=clock.minute,second=clock.second,microsecond=0)
            if observed>now+timedelta(seconds=30):observed-=timedelta(days=1)
            if not -30<=(now-observed).total_seconds()<=MAX_AGE_SECONDS+5:continue
            address=r[12].upper();registered=devices.get(address,[])
            if any(d['tracked']!='Y' for d in registered):continue
            public=[d for d in registered if d['identified']=='Y' and d['registration']==r[3]]
            label=public[0]['registration'] if public else ('OGN '+hashlib.sha256(r[13].encode()).hexdigest()[:6].upper())
            cn=public[0]['cn'] if public else ''
            ident=hashlib.sha256((address+':'+r[13]).encode()).hexdigest()[:16]
            report={'id':ident,'lat':lat,'lon':lon,'registration':label,'competition_id':cn,
                'altitude_ft':round(alt/.3048),'groundspeed_kt':round(speed/1.852,1),'heading':heading%360,
                'vario_kt':round(vario*1.943844492,1),'observed_at':observed.isoformat(),
                'highlight':label.replace('-','').upper()=='GCKML','receiver':r[11][:40]}
            if ident not in reports or report['observed_at']>reports[ident]['observed_at']:reports[ident]=report
        except (ValueError,KeyError,IndexError):rejected+=1
    if rejected and rejected==len(root.findall('m')):raise ValueError('OGN record layout changed')
    return {'schema':1,'status':'live','fetched_at':now.isoformat(),'aircraft':list(reports.values()),
        'rejected':rejected,'source':'OGN / SpotTheGlider · live.glidernet.org',
        'note':'Moving glider reports ≤120 seconds old. Ground movement can be included. Coverage is incomplete; not collision avoidance.'}


class TrafficWorker:
    def __init__(self,path=SNAPSHOT):
        self.path=Path(path);self.stop=Event();self.thread=None;self.devices={};self.registry_at=0
    def write(self,body):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.tmp');temporary.write_text(json.dumps(body,separators=(',',':')));temporary.replace(self.path)
    def once(self):
        if time.time()-self.registry_at>86400:
            try:
                raw=CACHE.read_bytes() if CACHE.exists() and time.time()-CACHE.stat().st_mtime<86400 else fetch(DDB,25_000_000)
                self.devices=parse_devices(raw);CACHE.parent.mkdir(exist_ok=True);CACHE.write_bytes(raw);self.registry_at=time.time()
            except (ValueError,OSError):self.registry_at=time.time()-86000 # retry registry in ~7 minutes
        return parse_spot_xml(fetch(FEED,8_000_000),devices=self.devices)
    def run(self):
        while not self.stop.is_set():
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
        self.thread=Thread(target=self.run,name='duo-ogn-feed',daemon=True);self.thread.start();return self


_worker=None
_lock=Lock()
def ensure_worker():
    global _worker
    with _lock:
        if _worker is None:_worker=TrafficWorker().start()
    return _worker


def add_traffic(map_view,*,show=False,recorded=False):
    from client_map import script
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    if not recorded and get_script_run_ctx(suppress_warning=True):ensure_worker()
    script(map_view,'ogn_traffic.js','installDuoTraffic',{'show':show,'recorded':recorded,'path':'app/static/ogn/uk.json'})
