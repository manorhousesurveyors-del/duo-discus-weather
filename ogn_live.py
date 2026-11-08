"""Bounded OGN receivers and a 15-minute, device-keyed traffic cache.

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

MAX_AGE_SECONDS = 15 * 60
DDB = 'https://ddb.glidernet.org/download/?j=1'
FEEDS = tuple(f'https://live.glidernet.org/lxml.php?a={a}&b=61.0&c=49.5&d=2.5&e=-11.0' for a in (0, 1))
APRS_HOSTS = ('aprs.glidernet.org', 'glidern1.glidernet.org', 'glidern2.glidernet.org')
APRS_LOGIN = b'user DUOWX pass -1 vers duo-discus-weather 1.0 filter r/54.0/-2.5/650\r\n'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; DuoDiscusWeather/1.0)',
           'Referer': 'https://live.glidernet.org/', 'Accept': '*/*'}


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
            ('registration', 'cn', 'tracked', 'identified', 'device_type', 'aircraft_model')})
    return devices


def report(*, address, token='', lat, lon, altitude_m, heading, speed_kmh, vario_mps,
           observed, now, devices, aircraft_type=1, registration='', cn='', receiver='', source='HTTPS', flarm=False):
    """Normalize units, identity and age without a climb/groundspeed exclusion."""
    observed = observed.astimezone(UTC)
    values = (lat, lon, altitude_m, heading, speed_kmh)
    if not all(math.isfinite(v) for v in values) or (vario_mps is not None and not math.isfinite(vario_mps)):
        raise ValueError('Nonfinite traffic report')
    if not (49.5 <= lat <= 61 and -11 <= lon <= 2.5):
        return None
    if aircraft_type not in (0, 1, 2, 8):  # unknown FLARM, glider, tug, powered/motor glider
        return None
    if not (-100 <= altitude_m <= 15000 and 0 <= heading <= 360 and 0 <= speed_kmh <= 600):
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
    if aircraft_type == 0 and not (flarm or any(d.get('device_type') in ('F','O') for d in registered)):
        return None
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
    label = registration or '?' + ident[-2:]
    return {'id': ident, 'lat': lat, 'lon': lon, 'registration': label, 'competition_id': cn,
            'aircraft_model': model, 'aircraft_type': aircraft_type,
            'altitude_ft': round(altitude_m / .3048), 'groundspeed_kt': round(speed_kmh / 1.852, 1),
            'heading': heading % 360, 'vario_kt': None if vario_mps is None else round(vario_mps * 1.943844492, 1),
            'observed_at': observed.isoformat(), 'age_seconds': max(0, round((now-observed).total_seconds())),
            'airborne': speed_kmh >= 25,  # movement proxy, never proof of flight
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
                alt, heading = field('alt', 'altitude'), field('heading', 'course', 'track', default='0')
                speed, vario = field('speed', 'groundspeed', default='0'), field('vario', 'climb')
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
            row = report(address=address, token=token, lat=float(lat), lon=float(lon), altitude_m=float(alt),
                         heading=float(heading), speed_kmh=float(speed), vario_mps=float(vario) if vario not in (None, '') else None,
                         observed=observed, now=now, devices=devices, aircraft_type=int(kind),
                         registration=reg, cn=cn, receiver=receiver)
            if row and (row['id'] not in reports or row['observed_at'] > reports[row['id']]['observed_at']):
                reports[row['id']] = row
        except (ValueError, TypeError, KeyError, IndexError, OverflowError):
            rejected += 1
    if markers and rejected == len(markers):
        raise ValueError('OGN record layout changed')
    return {'schema': 1, 'status': 'live', 'fetched_at': now.isoformat(), 'aircraft': list(reports.values()),
            'rejected': rejected, 'source': 'OGN / SpotTheGlider · live.glidernet.org',
            'note': 'Recent reports retained for 15 minutes; check position age. Not collision avoidance.'}


_POSITION = re.compile(r'(?P<lat>\d{4}\.\d{2})(?P<ns>[NS])[/\\](?P<lon>\d{5}\.\d{2})(?P<ew>[EW]).(?P<heading>\d{3})/(?P<speed>\d{3})')


def parse_aprs(line, *, now=None, devices=None):
    """OGN APRS uncompressed positions, timestamp, feet, knots and fpm vario."""
    now = now or datetime.now(UTC)
    if line.startswith('#') or len(line) > 4096 or ':' not in line:
        return None
    header, payload = line.split(':', 1)
    sender = header.split('>', 1)[0]
    position = _POSITION.search(payload)
    altitude = re.search(r'/A=(-?\d{5,6})', payload)
    ident = re.search(r'\bid([0-9A-Fa-f]{2})([0-9A-Fa-f]{6})\b', payload)
    if not position or not altitude or not ident:
        return None
    flags = int(ident[1], 16)
    if flags & 0x80:  # no-track flag
        return None
    kind = (flags >> 2) & 15
    if kind == 0 and not sender.startswith(('FLR', 'OGN')):
        return None
    stamp = re.match(r'[/@](\d{6})([hz/])', payload)
    if not stamp:
        return None  # never relabel an undated/replayed report as current
    if stamp[2] == 'h':
        observed = clock_time(':'.join((stamp[1][:2], stamp[1][2:4], stamp[1][4:])), now)
    elif stamp[2] == 'z':
        # DDHHMM UTC. Use an adjacent month only at the month boundary.
        observed = None
        for offset in (0, -1, 1):
            month = now.month - 1 + offset
            try:
                candidate = datetime(now.year + month // 12, month % 12 + 1, int(stamp[1][:2]),
                                     int(stamp[1][2:4]), int(stamp[1][4:]), tzinfo=UTC)
                if abs((now-candidate).total_seconds()) <= MAX_AGE_SECONDS:observed = candidate;break
            except ValueError:continue
        if observed is None:return None
    else:
        return None  # '/' is local time; receiver timezone is unknown
    lat = float(position['lat']); lon = float(position['lon'])
    if lat % 100 >= 60 or lon % 100 >= 60:return None
    lat = int(lat // 100) + lat % 100 / 60; lon = int(lon // 100) + lon % 100 / 60
    if position['ns'] == 'S':lat = -lat
    if position['ew'] == 'W':lon = -lon
    vario = re.search(r'([+-]\d+(?:\.\d+)?)fpm\b', payload)
    registry = devices or {}
    if flags & 0x40:  # stealth: preserve tracking opt-out and suppress identity
        registry = dict(registry)
        registry[ident[2].upper()] = [dict(d, identified='N') for d in registry.get(ident[2].upper(),
            [{'tracked':'Y'}])]
    return report(address=ident[2], lat=lat, lon=lon, altitude_m=int(altitude[1]) * .3048,
                  heading=float(position['heading']), speed_kmh=float(position['speed']) * 1.852,
                  vario_mps=float(vario[1]) * .00508 if vario else None,
                  observed=observed, now=now, devices=registry, aircraft_type=kind, source='APRS', receiver=header[-40:], flarm=sender.startswith(('FLR','OGN')))


class TrafficCache:
    def __init__(self):
        self._reports = {}; self._lock = Lock()

    def merge(self, rows, *, now=None):
        now = now or datetime.now(UTC)
        with self._lock:
            for row in rows:
                if not row:continue
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
        self.stop=Event(); self.thread=None; self.status='idle'
        # A managed HTTP proxy cannot tunnel public APRS TCP. Do not bypass it.
        self.enabled = os.getenv('DUO_OGN_APRS', '0' if os.getenv('HTTPS_PROXY') or os.getenv('https_proxy') else '1') == '1'

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
                    connection.sendall(APRS_LOGIN); connection.settimeout(2)
                    self.status='Connected to '+host; pending=b''; last=time.monotonic()
                    while self.active() and not self.stop.is_set():
                        try:chunk=connection.recv(8192)
                        except socket.timeout:
                            if time.monotonic()-last>45:break
                            continue
                        if not chunk:break
                        last=time.monotonic();pending+=chunk
                        if len(pending)>65536:raise ValueError('Oversized APRS line')
                        while b'\n' in pending:
                            line,pending=pending.split(b'\n',1)
                            try:row=parse_aprs(line.decode('ascii',errors='replace').strip(),devices=self.devices())
                            except (ValueError,OverflowError):continue
                            if row:self.cache.merge([row])
            except (OSError,ValueError):
                self.status='APRS unavailable; HTTPS fallback'
            self.stop.wait(3)
