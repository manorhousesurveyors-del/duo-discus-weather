"""BGA waypoint catalog, validated CUP/CSV/JSON import, and atomic public sync.

The bundled catalog is source data, not a hand-written approximation. The CLI
updates only a complete, validated UK list. User imports are kept separately.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import hashlib
from html.parser import HTMLParser
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Callable, Iterable
from urllib.parse import quote, unquote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
import ssl

from config import GeoPoint

ROOT = Path(__file__).resolve().parent
CATALOG_PATH = ROOT / 'data' / 'bga_turnpoints.json'
LOCAL_CATALOG_PATH = ROOT / 'var' / 'bga_turnpoints.json'
IMPORT_PATH = ROOT / 'var' / 'imported_turnpoints.json'
MAX_BYTES = 8 * 1024 * 1024
MAX_POINTS = 10000
SOURCE_PAGES = (
    'https://soaringweb.org/TP/BGA/files.html',
    'https://www.newportpeace.co.uk/turningpoints.htm',
)
ALLOWED_SOURCE_HOSTS = frozenset(('www.newportpeace.co.uk', 'newportpeace.co.uk',
                                  'soaringweb.org', 'www.soaringweb.org'))
# Stable direct links are added only after inspecting the publisher's index.
SOURCE_FILES: tuple[str, ...] = ()


class TurnpointError(ValueError):
    """Invalid/unsafe waypoint input or incomplete source data."""


@dataclass(frozen=True)
class Turnpoint:
    code: str
    name: str
    latitude: float
    longitude: float
    elevation_m: float | None
    country: str = 'GB'
    description: str = ''
    style: int = 1
    runway_direction: int | None = None
    runway_length_m: float | None = None
    frequency: str = ''
    source: str = ''
    official: bool = False
    extras: dict[str, str] = field(default_factory=dict, compare=False)

    def __post_init__(self):
        if not re.fullmatch(r'[A-Z0-9][A-Z0-9_-]{0,23}', self.code):
            raise TurnpointError('Waypoint code must contain 1–24 uppercase letters, digits, _ or -')
        if not self.name.strip() or len(self.name) > 200 or any(ord(c) < 32 for c in self.name):
            raise TurnpointError('Waypoint needs a valid, nonempty name')
        GeoPoint(self.code, self.name, self.latitude, self.longitude)
        for value in (self.elevation_m, self.runway_length_m):
            if value is not None and not math.isfinite(value):
                raise TurnpointError('Waypoint heights/lengths must be finite')
        if self.elevation_m is not None and not -500 <= self.elevation_m <= 9000:
            raise TurnpointError('Waypoint elevation outside physical bounds')
        if self.runway_direction is not None and not 0 <= self.runway_direction <= 360:
            raise TurnpointError('Runway direction outside 0–360 degrees')
        if self.runway_length_m is not None and self.runway_length_m < 0:
            raise TurnpointError('Runway length cannot be negative')
        if not 0 <= self.style <= 100:
            raise TurnpointError('Invalid CUP waypoint style')
        if len(self.description) > 10000 or len(self.frequency) > 100:
            raise TurnpointError('Waypoint text exceeds size limit')
        if self.official and not re.fullmatch(r'[A-Z0-9]{3}', self.code):
            raise TurnpointError('An official BGA identifier must be a three-character trigraph')

    @property
    def point(self) -> GeoPoint:
        return GeoPoint('bga:'+self.code, f'{self.code} · {self.name}', self.latitude, self.longitude)


@dataclass(frozen=True)
class Catalog:
    turnpoints: tuple[Turnpoint, ...]
    source: str
    retrieved_at: str
    content_sha256: str
    source_sha256: str = ''
    published_version: str = ''
    attribution: str = ('BGA list supplied by Tim Newport-Peace via the Worldwide Soaring Turnpoint Exchange. '
                        'Official BGA identifiers in an unofficial mirror; for planning/flight verification, '
                        'not navigation. Check the definitive BGA list and current airspace restrictions. '
                        'https://soaringweb.org/TP/disclaimer.html')

    @property
    def by_code(self) -> dict[str, Turnpoint]:
        return {p.code: p for p in self.turnpoints}


def _text(data: bytes | str) -> str:
    if isinstance(data, str):
        if len(data.encode('utf-8')) > MAX_BYTES:
            raise TurnpointError('Waypoint file exceeds 8 MiB')
        return data.lstrip('\ufeff')
    if len(data) > MAX_BYTES:
        raise TurnpointError('Waypoint file exceeds 8 MiB')
    try:
        return data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return data.decode('cp1252')  # Historic SeeYou files commonly use Windows ANSI.


def parse_coordinate(value, *, latitude: bool) -> float:
    """Decimal degrees, SeeYou DDMM.mmm[N/S], and separated DMS/DDM."""
    if value is None or isinstance(value, bool):
        raise TurnpointError('Missing coordinate')
    text = str(value).strip().upper()
    hemisphere = text[-1:] if text[-1:] in 'NSEW' else ''
    if hemisphere:
        if hemisphere not in ('NS' if latitude else 'EW'):
            raise TurnpointError('Coordinate hemisphere does not match its axis')
        text = text[:-1].strip()
    negative = text.startswith('-')
    if hemisphere and text.startswith(('+', '-')):
        raise TurnpointError('Use a hemisphere or signed coordinate, not both')
    try:
        if re.search(r"[°:'\"\s]", text):
            parts = [float(p) for p in re.split(r"[°:'\"\s]+", text) if p]
            if not 1 <= len(parts) <= 3 or any(not 0 <= p < 60 for p in parts[1:]):
                raise TurnpointError('Invalid DMS/DDM minutes or seconds')
            result = abs(parts[0]) + (parts[1]/60 if len(parts)>1 else 0) + (parts[2]/3600 if len(parts)>2 else 0)
            result *= -1 if negative else 1
        elif hemisphere and len(text.split('.')[0]) == (4 if latitude else 5):
            split = 2 if latitude else 3
            degrees, minutes = float(text[:split]), float(text[split:])
            if not 0 <= minutes < 60:
                raise TurnpointError('CUP minutes must be below 60')
            result = degrees + minutes/60
        else:
            result = float(text)
    except (ValueError, IndexError) as exc:
        raise TurnpointError('Invalid waypoint coordinate') from exc
    if hemisphere in ('S', 'W'):
        result = -result
    bound = 90 if latitude else 180
    if not math.isfinite(result) or not -bound <= result <= bound:
        raise TurnpointError('Waypoint coordinate outside geographic bounds')
    return result


def parse_length(value, *, default_unit='m') -> float | None:
    if value is None or str(value).strip() == '':
        return None
    match = re.fullmatch(r'\s*([-+]?\d+(?:\.\d+)?)\s*(m|ft|feet|f)?\s*', str(value), re.I)
    if not match:
        raise TurnpointError('Elevation/length must be a number in m or ft')
    unit = (match[2] or default_unit).lower()
    return float(match[1]) * (.3048 if unit in ('ft', 'feet', 'f') else 1)


def _record(raw: dict, source: str, official: bool) -> Turnpoint:
    fields = {re.sub(r'[^a-z0-9]', '', str(k).lower()): v for k,v in raw.items() if k is not None}
    def first(*keys, default=''):
        return next((fields[k] for k in keys if k in fields and fields[k] not in (None,'')), default)
    name = str(first('name','fullname','title','waypointname')).strip()
    code = str(first('code','trigraph','bga','bgacode','id','shortname')).strip().upper()
    if not code and re.match(r'^[A-Z0-9]{3}(?:\s|$)', name):
        code = name[:3]
    if name.startswith(code+'-'):
        name = name[len(code)+1:]
    elevation = first('elevationm','elev','elevation','altitude','alt',default=None)
    if elevation is None:
        elevation_m = parse_length(first('elevationft','altitudeft',default=None),default_unit='ft')
    else:
        elevation_m = parse_length(elevation)
    runway = first('runwaydirection','rwdir',default=None)
    try:
        style = int(first('style','type',default=1))
        direction = int(runway) if runway is not None else None
    except (TypeError,ValueError) as exc:
        raise TurnpointError('Invalid CUP style/runway direction') from exc
    return Turnpoint(code,name,parse_coordinate(first('latitude','lat'),latitude=True),
                     parse_coordinate(first('longitude','lon','lng','long'),latitude=False),elevation_m,
                     str(first('country',default='GB')).upper(),str(first('description','desc','comment')),
                     style,direction,parse_length(first('runwaylengthm','rwlen',default=None)),
                     str(first('frequency','freq')),source,official,
                     {str(k):str(v) for k,v in raw.items() if k is not None and v is not None})


def _unique(points: Iterable[Turnpoint]) -> tuple[Turnpoint, ...]:
    result = {}
    for point in points:
        if point.code in result:
            raise TurnpointError(f'Duplicate waypoint code: {point.code}')
        result[point.code] = point
        if len(result) > MAX_POINTS:
            raise TurnpointError('Too many waypoints')
    if not result:
        raise TurnpointError('No waypoints found')
    return tuple(sorted(result.values(),key=lambda p:p.code))


def parse_turnpoints(data: bytes | str, format: str | None = None, *, source='user upload', official=False) -> tuple[Turnpoint, ...]:
    """Parse a full waypoint section; CUP task/options sections are not waypoints.

    Optional CUP columns are retained in extras. JSON accepts normalized lists,
    {turnpoints/waypoints: [...]}, and GeoJSON Point FeatureCollections.
    """
    text = _text(data)
    selected = (format or ('json' if text.lstrip().startswith(('[','{')) else 'cup')).lower().lstrip('.')
    if selected == 'json':
        try:
            body = json.loads(text,parse_constant=lambda _: (_ for _ in ()).throw(TurnpointError('Non-finite JSON number')))
        except (ValueError,TypeError) as exc:
            raise TurnpointError('Invalid waypoint JSON') from exc
        if isinstance(body,dict) and body.get('type') == 'FeatureCollection':
            records = []
            features = body.get('features')
            if not isinstance(features,list):
                raise TurnpointError('GeoJSON needs a features list')
            for feature in features:
                if not isinstance(feature,dict) or not isinstance(feature.get('geometry'),dict) or not isinstance(feature.get('properties',{}),dict):
                    raise TurnpointError('Invalid GeoJSON feature')
                geometry = feature.get('geometry') or {}
                coordinates = geometry.get('coordinates',[])
                if geometry.get('type') != 'Point' or not isinstance(coordinates,list) or len(coordinates) not in (2,3):
                    raise TurnpointError('GeoJSON waypoints must be Point geometries')
                records.append({**feature.get('properties',{}),'longitude':coordinates[0],'latitude':coordinates[1],
                                **({'elevation_m':coordinates[2]} if len(coordinates)==3 else {})})
        else:
            records = body.get('turnpoints',body.get('waypoints')) if isinstance(body,dict) else body
        if not isinstance(records,list) or not all(isinstance(r,dict) for r in records):
            raise TurnpointError('JSON must contain a waypoint object list')
        return _unique(_record(r,source,official) for r in records)
    if selected not in ('cup','csv'):
        raise TurnpointError('Supported waypoint formats: CUP, CSV, JSON')
    section = text.split('-----Related Tasks-----',1)[0]
    # Keep newlines inside quoted multiline CUP descriptions. Only leading comments
    # are removed; a description line beginning '#' remains part of its field.
    lines = section.splitlines(keepends=True)
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith(('#','*'))):
        lines.pop(0)
    if not lines:
        raise TurnpointError('Empty waypoint file')
    try:
        dialect = csv.Sniffer().sniff(lines[0],delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    try:
        reader = csv.DictReader(io.StringIO(''.join(lines)),dialect=dialect,strict=True)
        if not reader.fieldnames:
            raise TurnpointError('Waypoint CSV header missing')
        def records():
            for row in reader:
                if None in row:
                    raise TurnpointError('CSV row has more fields than its header')
                yield _record(row,source,official)
        return _unique(records())
    except (csv.Error,TypeError) as exc:
        raise TurnpointError('Malformed waypoint CSV/CUP') from exc


def validate_uk_catalog(points: tuple[Turnpoint, ...], previous_count: int | None = None) -> None:
    if len(points) < 500:
        raise TurnpointError('Automatic sync requires a complete UK list (at least 500 turnpoints)')
    if any(not re.fullmatch(r'[A-Z0-9]{3}',p.code) or not 49 <= p.latitude <= 61.5 or not -9 <= p.longitude <= 2.5
           or p.elevation_m is None for p in points):
        raise TurnpointError('UK source has invalid trigraphs, bounds or missing elevations')
    lookup = {p.code:p for p in points}
    mynd = lookup.get('MYN')
    if mynd is None or abs(mynd.latitude-52.52) > .05 or abs(mynd.longitude+2.88) > .05:
        raise TurnpointError('Complete source must include verified Long Mynd MYN coordinates')
    if min(p.latitude for p in points)>51 or max(p.latitude for p in points)<57 or min(p.longitude for p in points)>-5:
        raise TurnpointError('Source does not cover the UK geographically')
    if previous_count and len(points) < .8*previous_count:
        raise TurnpointError('Source lost over 20% of turnpoints; refusing automatic replacement')


def content_hash(points: Iterable[Turnpoint]) -> str:
    # Retrieval time and transport URL must not cause a new commit on every check.
    records = [{k:v for k,v in asdict(p).items() if k not in ('source','extras')}
               for p in sorted(points,key=lambda p:p.code)]
    return hashlib.sha256(json.dumps(records,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def atomic_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as file:
            temporary = Path(file.name)
            json.dump(body,file,ensure_ascii=False,indent=2,allow_nan=False)
            file.write('\n'); file.flush(); os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_catalog(catalog: Catalog, path: Path) -> None:
    atomic_json(path,{'schema_version':1,**asdict(catalog)})


def load_catalog(path: Path = CATALOG_PATH) -> Catalog:
    raw = json.loads(path.read_text(encoding='utf-8'))
    if raw.get('schema_version') != 1:
        raise TurnpointError('Unsupported BGA catalog schema')
    points = _unique(Turnpoint(**p) for p in raw['turnpoints'])
    if content_hash(points) != raw['content_sha256']:
        raise TurnpointError('Catalog content checksum mismatch')
    return Catalog(points,raw['source'],raw['retrieved_at'],raw['content_sha256'],
                   raw.get('source_sha256',''),raw.get('published_version',''),raw.get('attribution',''))


def active_catalog() -> Catalog:
    catalogs = [load_catalog(path) for path in (CATALOG_PATH,LOCAL_CATALOG_PATH) if path.exists()]
    if not catalogs:
        raise TurnpointError('No bundled or synchronized catalog found')
    catalog = max(catalogs,key=lambda c:(c.published_version,c.retrieved_at))
    if IMPORT_PATH.exists():
        imported = load_catalog(IMPORT_PATH)
        merged = {**catalog.by_code,**imported.by_code}
        points = tuple(sorted(merged.values(),key=lambda p:p.code))
        return Catalog(points,catalog.source+' + user import',catalog.retrieved_at,content_hash(points),
                       catalog.source_sha256,catalog.published_version,catalog.attribution)
    return catalog


def search_turnpoints(points: Iterable[Turnpoint], query: str, limit: int = 100) -> tuple[Turnpoint, ...]:
    query = query.strip().casefold()
    matched = [p for p in points if not query or query in p.code.casefold() or query in p.name.casefold()
               or (p.code=='MYN' and query in 'long mynd')]
    return tuple(sorted(matched,key=lambda p:(p.code.casefold()!=query,not p.code.casefold().startswith(query),p.name))[:limit])


def _source_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in ALLOWED_SOURCE_HOSTS or parsed.username or parsed.password:
        raise TurnpointError('Update URLs must use HTTPS on an approved waypoint source host')
    return url


class _SourceRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return super().redirect_request(req,fp,code,msg,headers,_source_url(newurl))


def fetch_source(url: str) -> bytes:
    _source_url(url)
    opener = build_opener(HTTPSHandler(context=ssl.create_default_context()),_SourceRedirect())
    with opener.open(Request(url,headers={'User-Agent':'DuoDiscusWeather/1.0 (BGA waypoint sync)'}),timeout=30) as response:
        _source_url(response.url)
        if response.status != 200:
            raise TurnpointError(f'Waypoint provider returned HTTP {response.status}')
        raw = response.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:
        raise TurnpointError('Waypoint response exceeds 8 MiB')
    return raw


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':
            link=dict(attrs).get('href','')
            if urlsplit(link).path.lower().endswith(('.cup','.csv','.json')):
                self.links.append(link)


def discover_sources(fetch: Callable[[str],bytes] = fetch_source) -> tuple[str, ...]:
    urls = list(SOURCE_FILES)
    for page in SOURCE_PAGES:
        try:
            parser=_Links(); parser.feed(_text(fetch(page)))
            for link in parser.links:
                url=quote(urljoin(page,link),safe=':/%?=&')
                try: urls.append(_source_url(url))
                except TurnpointError: continue
        except (OSError,ValueError):
            continue
    # Prefer SeeYou's original BGA trigraph list over generic application CSVs.
    # Discover on every run so a new year's filename is picked up automatically.
    def priority(url):
        name=unquote(urlsplit(url).path).lower()
        versions=re.findall(r'(20\d{2})(?:\s+revision\s+|)([a-z])?',name)
        version=max(versions,default=('0000',''))
        return (version,name.endswith('.cup'),'seeyou' in name)
    return tuple(sorted(dict.fromkeys(urls),key=priority,reverse=True))


@dataclass(frozen=True)
class SyncResult:
    changed: bool
    count: int
    added: int
    removed: int
    modified: int
    source: str
    content_sha256: str


def update_catalog(path: Path = CATALOG_PATH, *, fetch: Callable[[str],bytes] = fetch_source,
                   urls: Iterable[str] | None = None, baseline: Catalog | None = None) -> SyncResult:
    previous = load_catalog(path) if path.exists() else baseline
    if baseline and previous and (baseline.published_version,baseline.retrieved_at) > (previous.published_version,previous.retrieved_at):
        previous = baseline
    candidates = tuple(urls) if urls is not None else discover_sources(fetch)
    errors=[]
    for url in candidates:
        try:
            _source_url(url)
            version_match=re.search(r'(20\d{2})(?:\s+Revision\s+|)([a-z])?',unquote(url),re.I)
            version=''.join(version_match.groups(default='')).lower() if version_match else ''
            if previous and previous.published_version and version and version<previous.published_version:
                raise TurnpointError('Source version is older than the saved catalog')
            raw=fetch(url)
            points=parse_turnpoints(raw,Path(urlsplit(url).path).suffix,source=url,official=True)
            validate_uk_catalog(points,len(previous.turnpoints) if previous else None)
            digest=content_hash(points)
            before=previous.by_code if previous else {}; after={p.code:p for p in points}
            added=len(after.keys()-before.keys()); removed=len(before.keys()-after.keys())
            modified=sum(content_hash([before[k]])!=content_hash([after[k]]) for k in before.keys() & after.keys())
            changed=previous is None or previous.content_sha256!=digest
            if changed:
                save_catalog(Catalog(points,url,datetime.now(UTC).isoformat(),digest,hashlib.sha256(raw).hexdigest(),version),path)
            return SyncResult(changed,len(points),added,removed,modified,url,digest)
        except (OSError,ValueError,KeyError) as exc:
            errors.append(f'{urlsplit(url).hostname}: {type(exc).__name__}: {exc}')
    raise TurnpointError('No complete BGA source could be validated. Existing catalog preserved. '+
                         ('; '.join(errors[:5]) if errors else 'No downloadable waypoint links found.'))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--update',action='store_true',help='Check public sources; atomically save validated changes')
    parser.add_argument('--output',type=Path,default=CATALOG_PATH)
    args=parser.parse_args(argv)
    if args.update:
        print(json.dumps(asdict(update_catalog(args.output)),indent=2))
    else:
        catalog=load_catalog(args.output)
        validate_uk_catalog(catalog.turnpoints)
        print(json.dumps({'count':len(catalog.turnpoints),'source':catalog.source,'sha256':catalog.content_sha256},indent=2))
    return 0


if __name__=='__main__':
    try: raise SystemExit(main())
    except (OSError,ValueError,KeyError) as exc:
        import sys
        print(f'BGA sync failed: {exc}',file=sys.stderr)
        raise SystemExit(2) from None
