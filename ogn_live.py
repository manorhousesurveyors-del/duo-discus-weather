"""Bounded OGN receivers and a 20-minute, device-keyed traffic cache.

APRS is receive-only. HTTPS remains available on hosts that prohibit public TCP.
Registry tracking/identification opt-outs apply equally to both transports.
"""
from datetime import UTC, datetime, timedelta
from threading import Event, Lock, Thread
from urllib.request import Request, urlopen
import csv
import io
import json
import math
import os
import re
import socket
import time
import xml.etree.ElementTree as ET

MAX_AGE_SECONDS = 20 * 60
DDB = 'https://ddb.glidernet.org/download/?j=1'
FEEDS = ('https://live.glidernet.org/lxml.php?a=1&b=60.5&c=49.5&d=2.5&e=-11.0',
         'https://live.glidernet.org/lxml.php?a=0&b=61.0&c=49.5&d=2.5&e=-11.0')
APRS_HOSTS = ('aprs.glidernet.org', 'glidern1.glidernet.org', 'glidern2.glidernet.org', 'glidern3.glidernet.org', 'glidern4.glidernet.org')
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
           'Referer': 'https://live.glidernet.org/', 'Origin': 'https://live.glidernet.org',
           'Accept': 'text/xml,application/xml,*/*'}


def aprs_login():
    import secrets
    return f'user DUO{secrets.randbelow(10000):04d} pass -1 vers duo-wx 1.0 filter r/54.2/-3.5/750\r\n'.encode('ascii')


class MirrorSession:
    """Cookie-preserving HTTP client, owned by one background fetch thread."""
    def __init__(self, session=None):
        import requests
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)
        # Retain the environment proxy and trusted CA; never disable TLS checks.
        self.session.verify = os.environ.get('SSL_CERT_FILE', True)
        self.warmed = False

    def fetch(self, url, limit):
        import requests
        if not self.warmed:
            try:
                with self.session.get('https://live.glidernet.org/', timeout=(2,2), stream=True) as response:
                    response.raise_for_status()
                self.warmed = True
            except requests.RequestException:
                pass  # A blocked landing page must not suppress a working XML feed.
        try:
            with self.session.get(url, timeout=(3,6), stream=True) as response:
                response.raise_for_status()
                chunks=[];size=0
                for chunk in response.iter_content(65536):
                    size+=len(chunk)
                    if size>limit:raise ValueError('OGN response exceeds size limit')
                    chunks.append(chunk)
                return b''.join(chunks)
        except requests.RequestException as exc:
            raise OSError('OGN HTTPS '+type(exc).__name__) from exc


def fetch(url, limit):
    with urlopen(Request(url, headers=HEADERS), timeout=6) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('OGN response exceeds size limit')
    return raw


def parse_devices(raw):
    body = json.loads(raw)
    if not isinstance(body, dict) or not isinstance(body.get('devices'), list) or len(body['devices']) > 200000:
        raise ValueError('Invalid OGN device registry')
    devices = {}
    for record in body['devices']:
        if not isinstance(record, dict):
            raise ValueError('Invalid OGN device record')
        key = str(record.get('device_id', '')).upper()
        if not re.fullmatch('[0-9A-F]{6}', key):
            continue
        devices.setdefault(key, []).append({k: str(record.get(k, ''))[:80] for k in
            ('registration', 'cn', 'tracked', 'identified', 'device_type', 'aircraft_model', 'aircraft_type')})
    return devices


def report(*, address, token='', lat, lon, altitude_m, heading, speed_kmh, vario_mps,
           observed, now, devices, aircraft_type=1, registration='', cn='', receiver='', source='live.glidernet.org XML', flarm=False):
    """Normalize units, identity and age without a climb/groundspeed exclusion."""
    observed = observed.astimezone(UTC)
    values = tuple(v for v in (lat, lon, altitude_m, heading, speed_kmh) if v is not None)
    if not all(math.isfinite(v) for v in values) or (vario_mps is not None and not math.isfinite(vario_mps)):
        raise ValueError('Nonfinite traffic report')
    if not (49.5 <= lat <= 61 and -11 <= lon <= 2.5):
        return None
    if not 0 <= aircraft_type <= 15:
        return None
    if not ((altitude_m is None or -500 <= altitude_m <= 20000) and
            (heading is None or 0 <= heading <= 360) and (speed_kmh is None or 0 <= speed_kmh <= 1500)):
        return None
    if vario_mps is not None and abs(vario_mps) > 50:
        return None
    if not -30 <= (now - observed).total_seconds() <= MAX_AGE_SECONDS:
        return None
    address = str(address).upper()
    valid_address = bool(re.fullmatch('[0-9A-F]{6}', address)) and address != '000000'
    ident = address if valid_address else str(token).upper()[:80]
    if not ident:
        raise ValueError('Missing device identity')
    registered = devices.get(address, []) if valid_address else []
    if any(d.get('tracked') != 'Y' for d in registered):
        return None
    public = [d for d in registered if d.get('identified') == 'Y']
    # Do not identify an ambiguous/opted-out registry entry using a feed alias.
    if registered:
        if len(public) == len(registered) and len({d['registration'] for d in public}) == 1:
            registration, cn = public[0]['registration'], public[0]['cn']
            model = public[0].get('aircraft_model', '')
        else:
            registration = cn = ''; model = ''
    else:
        model = ''
        if cn.startswith('_') or registration.lower() == str(token).lower():
            registration = cn = ''
    anonymous = not registration and not cn
    label = registration or '_' + ident[-2:].lower()
    badge = cn or (registration.replace('-', '')[-3:] if registration else label)
    ddb_glider=any(d.get('aircraft_type','').lower() in ('1','8','glider','sailplane','motor glider') for d in public)
    category = 'glider' if not anonymous and (aircraft_type in (1,8) or (aircraft_type==0 and ddb_glider)) else 'other'
    return {'id': ident, 'lat': lat, 'lon': lon, 'registration': label, 'competition_id': cn,
            'aircraft_model': model, 'aircraft_type': aircraft_type, 'badge':badge, 'category':category,
            'altitude_ft': None if altitude_m is None else round(altitude_m / .3048), 'groundspeed_kt': None if speed_kmh is None else round(speed_kmh / 1.852, 1),
            'heading': None if heading is None else heading % 360, 'vario_kt': None if vario_mps is None else round(vario_mps * 1.943844492, 1),
            'observed_at': observed.isoformat(), 'age_seconds': max(0, round((now-observed).total_seconds())),
            'airborne': None if speed_kmh is None else speed_kmh >= 25,  # movement proxy, never proof of flight
            'highlight': label.replace('-', '').upper() == 'GCKML', 'receiver': receiver[:40], 'source': source}


def clock_time(value, now):
    clock = datetime.strptime(value, '%H:%M:%S').time()
    observed = now.replace(hour=clock.hour, minute=clock.minute, second=clock.second, microsecond=0)
    if observed > now + timedelta(seconds=30):
        observed -= timedelta(days=1)
    return observed


def parse_spot_xml(raw, *, now=None, devices=None):
    now = now or datetime.now(UTC); devices = devices or {}
    if len(raw) > 8_000_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('Unsupported OGN XML')
    root = ET.fromstring(raw)
    if root.tag not in ('markers', 'aircraft'):
        raise ValueError('Unexpected OGN response')
    reports = {}; rejected = 0; markers = list(root)
    for marker in markers:
        try:
            attrs = marker.attrib
            if 'a' in attrs:
                r = next(csv.reader(io.StringIO(attrs['a'])))
                if len(r) == 13:
                    r.insert(6,None)  # Older mirror layout has UTC clock, no age column.
                if len(r) < 14:
                    raise ValueError('Unknown OGN packed layout')
                lat, lon, cn, reg, alt, clock, age, heading, speed, vario, kind, receiver, address, token = r[:14]
            else:
                def field(*names, default=None):
                    return next((attrs[k] for k in names if k in attrs), default)
                lat, lon = field('lat', 'latitude'), field('lon', 'lng', 'longitude')
                cn, reg = field('cn', 'competition_id', default=''), field('reg', 'registration', default='')
                alt, heading = field('alt', 'altitude'), field('heading', 'course', 'track')
                speed, vario = field('speed', 'groundspeed'), field('vario', 'climb')
                kind, receiver = field('type', 'aircraft_type', default='1'), field('receiver', default='')
                address, token = field('device_id', 'id', default=''), field('token', default='')
                age, clock = field('age'), field('time', 'timestamp')
            if age is not None and (not math.isfinite(float(age)) or not 0 <= float(age) <= MAX_AGE_SECONDS):
                continue
            if clock and ':' in clock and 'T' not in clock:
                observed = clock_time(clock, now)
            elif clock:
                observed = (datetime.fromtimestamp(float(clock), UTC) if str(clock).replace('.', '', 1).isdigit()
                            else datetime.fromisoformat(clock.replace('Z', '+00:00')))
                if observed.tzinfo is None:raise ValueError('Missing timestamp timezone')
            elif age is not None:
                observed = now - timedelta(seconds=float(age))
            else:
                raise ValueError('Missing observation time')
            row = report(address=address, token=token, lat=float(lat), lon=float(lon), altitude_m=float(alt) if alt not in (None,'') else None,
                         heading=float(heading) if heading not in (None,'') else None, speed_kmh=float(speed) if speed not in (None,'') else None, vario_mps=float(vario) if vario not in (None, '') else None,
                         observed=observed, now=now, devices=devices, aircraft_type=int(kind),
                         registration=reg, cn=cn, receiver=receiver)
            if row and (row['id'] not in reports or row['observed_at'] > reports[row['id']]['observed_at']):
                reports[row['id']] = row
        except (ValueError, TypeError, KeyError, IndexError, OverflowError):
            rejected += 1
    if markers and rejected == len(markers):
        raise ValueError('OGN record layout changed')
    return {'schema': 2, 'status': 'live', 'fetched_at': now.isoformat(), 'aircraft': list(reports.values()),
            'rejected': rejected, 'source': 'OGN / SpotTheGlider · live.glidernet.org',
            'note': 'Recent reports retained for 20 minutes; check position age. Not collision avoidance.'}


_POSITION = re.compile(r'(?P<lat>\d{4}\.\d{2})(?P<ns>[NS])[/\\A-Za-z0-9](?P<lon>\d{5}\.\d{2})(?P<ew>[EW]).')


def base91(text):
    value=0
    for char in text:
        digit=ord(char)-33
        if not 0<=digit<=90:raise ValueError('Invalid compressed APRS coordinate')
        value=value*91+digit
    return value


def packet_time(stamp, now):
    if stamp[2]=='h':
        return clock_time(':'.join((stamp[1][:2],stamp[1][2:4],stamp[1][4:])),now)
    from zoneinfo import ZoneInfo
    zone=UTC if stamp[2]=='z' else ZoneInfo('Europe/London')
    local=now.astimezone(zone)
    for offset in (0,-1,1):
        month=local.month-1+offset
        for fold in (0,1):
            try:
                candidate=datetime(local.year+month//12,month%12+1,int(stamp[1][:2]),
                    int(stamp[1][2:4]),int(stamp[1][4:]),tzinfo=zone,fold=fold).astimezone(UTC)
                if -30<=(now-candidate).total_seconds()<=MAX_AGE_SECONDS:return candidate
            except ValueError:continue
    return None


def parse_aprs(line, *, now=None, devices=None):
    """APRS !/= and /@ positions, plain or base-91 compressed, plus OGN telemetry.

    Untimestamped packets use receipt time, explicitly identified as such. A
    local '/' timestamp is interpreted in Europe/London for this UK receiver.
    Missing altitude/course/vario stays unknown rather than dropping a target.
    """
    now=now or datetime.now(UTC)
    if line.startswith('#') or len(line)>4096 or ':' not in line:return None
    header,payload=line.split(':',1);sender=header.split('>',1)[0]
    if not payload or payload[0] not in '!/=@':return None
    stamp=re.match(r'[/@](\d{6})([hz/])',payload)
    if payload[0] in '/@' and not stamp:return None
    observed=packet_time(stamp,now) if stamp else now
    if observed is None:return None
    position_text=payload[8:] if stamp else payload[1:]
    match=_POSITION.match(position_text)
    heading=speed=alt=None
    if match:
        lat,lon=float(match['lat']),float(match['lon'])
        if lat%100>=60 or lon%100>=60:return None
        lat=int(lat//100)+lat%100/60;lon=int(lon//100)+lon%100/60
        if match['ns']=='S':lat=-lat
        if match['ew']=='W':lon=-lon
        course=re.match(r'(\d{3})/(\d{3})',position_text[match.end():])
        if course:heading=float(course[1]);speed=float(course[2])*1.852
    else:
        if len(position_text)<13 or position_text[0] not in '/\\0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz':return None
        lat=90-base91(position_text[1:5])/380926
        lon=-180+base91(position_text[5:9])/190463
        c,s,t=(ord(v)-33 for v in position_text[10:13])
        if not (-1<=c<=90 and -1<=s<=90 and 0<=t<=90):return None
        if c>=0 and s>=0:
            if t&0x18==0x10:alt=1.002**(c*91+s)*.3048
            elif c<90:heading=c*4.;speed=(1.08**s-1)*1.852
    explicit_alt=re.search(r'/A=(-?\d{5,6})',payload)
    if explicit_alt:alt=int(explicit_alt[1])*.3048
    ident=re.search(r'\bid([0-9A-Fa-f]{2})([0-9A-Fa-f]{6})\b',payload)
    address=ident[2].upper() if ident else ''
    flags=int(ident[1],16) if ident else 0
    if flags&0x80:return None
    if not address:
        address_match=re.fullmatch(r'(?:FLR|OGN|ICA)([0-9A-Fa-f]{6})',sender)
        address=address_match[1].upper() if address_match else ''
        if not address and not re.fullmatch(r'[A-Z]-[A-Z0-9]{3,5}',sender):return None
    registry=devices or {}
    if flags&0x40:
        registry=dict(registry)
        registry[address]=[dict(d,identified='N') for d in registry.get(address,[{'tracked':'Y'}])]
    vario=re.search(r'([+-]\d+(?:\.\d+)?)fpm\b',payload)
    row=report(address=address,token=sender,lat=lat,lon=lon,altitude_m=alt,heading=heading,speed_kmh=speed,
        vario_mps=float(vario[1])*.00508 if vario else None,observed=observed,now=now,devices=registry,
        aircraft_type=(flags>>2)&15,source='OGN APRS-IS Live Stream',receiver=header[-40:],
        registration=sender if not address and re.fullmatch(r'[A-Z]-[A-Z0-9]{3,5}',sender) else '')
    if row:
        row['timestamp_kind']='packet UTC' if stamp and stamp[2]!='/' else 'packet Europe/London' if stamp else 'received'
        for name,pattern in (('rotation',r'([+-]?\d+(?:\.\d+)?)rot\b'),('signal_db',r'([+-]?\d+(?:\.\d+)?)dB\b')):
            value=re.search(pattern,payload);number=float(value[1]) if value else None
            row[name]=number if number is not None and math.isfinite(number) else None
    return row


class TrafficCache:
    def __init__(self):
        self._reports = {}; self._lock = Lock()

    def merge(self, rows, *, now=None):
        now = now or datetime.now(UTC)
        with self._lock:
            for row in rows:
                if not row:continue
                # Public full registrations can link distinct mirror/device aliases.
                reg=row.get('registration','')
                if reg and not reg.startswith(('_','?')):
                    alias=next((k for k,v in self._reports.items() if v.get('registration')==reg),None)
                    if alias and alias!=row['id']:
                        previous=self._reports.pop(alias)
                        if previous['observed_at']>row['observed_at']:row=previous
                old = self._reports.get(row['id'])
                if old is None or row['observed_at'] >= old['observed_at']:
                    self._reports[row['id']] = row
            self._reports = {k: v for k, v in self._reports.items()
                             if -30 <= (now-datetime.fromisoformat(v['observed_at'])).total_seconds() <= MAX_AGE_SECONDS}
            return [dict(v, age_seconds=max(0, round((now-datetime.fromisoformat(v['observed_at'])).total_seconds())))
                    for v in self._reports.values()]


class AprsReceiver:
    """Persistent daemon, bounded buffers and idle reads, reconnect across hosts."""
    def __init__(self, cache, devices, active, *, connector=socket.create_connection):
        self.cache=cache; self.devices=devices; self.active=active; self.connector=connector
        self.stop=Event(); self.thread=None; self.status='idle'; self.received_packets=0; self.last_received=None; self.ready=Event()
        self.login=aprs_login()
        # A managed HTTP proxy cannot tunnel public APRS TCP. Do not bypass it.
        self.enabled = os.getenv('DUO_OGN_APRS', '0' if any(os.getenv(k) for k in ('HTTPS_PROXY','https_proxy','HTTP_PROXY','http_proxy','ALL_PROXY','all_proxy')) else '1') == '1'

    def start(self):
        if not self.enabled:
            self.status='TCP disabled on this host; HTTPS fallback';return
        if self.thread and self.thread.is_alive():return
        self.thread=Thread(target=self.run, name='duo-ogn-aprs', daemon=True); self.thread.start()

    def run(self):
        host_index=0
        while not self.stop.is_set():
            if not self.active():self.stop.wait(.5);continue
            host=APRS_HOSTS[host_index % len(APRS_HOSTS)]; host_index+=1
            try:
                with self.connector((host,14580),timeout=3) as connection:
                    connection.sendall(self.login); connection.settimeout(2)
                    self.status='Connected to '+host; pending=b''; last=time.monotonic(); initial=last
                    while self.active() and not self.stop.is_set():
                        try:chunk=connection.recv(8192)
                        except socket.timeout:
                            if time.monotonic()-initial>=4.5:self.ready.set()
                            if time.monotonic()-last>45:break
                            continue
                        if not chunk:break
                        last=time.monotonic();pending+=chunk
                        if len(pending)>65536:raise ValueError('Oversized APRS line')
                        while b'\n' in pending:
                            line,pending=pending.split(b'\n',1)
                            try:row=parse_aprs(line.decode('ascii',errors='replace').strip(),devices=self.devices())
                            except (ValueError,OverflowError):continue
                            if row:
                                self.cache.merge([row]);self.received_packets+=1;self.last_received=datetime.now(UTC).isoformat();self.ready.set()
            except (OSError,ValueError):
                self.status='APRS unavailable; HTTPS fallback'
            if not self.stop.is_set():self.status='APRS reconnecting; HTTPS remains active'
            self.stop.wait(1)
