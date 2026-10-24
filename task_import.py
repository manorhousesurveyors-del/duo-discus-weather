"""Read complete CUP task declarations and XCSoar TSK routes, preserving order."""
import csv
import io
import re
import xml.etree.ElementTree as ET
from bga_turnpoints import Turnpoint,parse_turnpoints
from task_planning import validate_task


def import_task(data: bytes,format: str,*,catalog=()):
    if len(data)>2_000_000:raise ValueError('Task upload exceeds 2 MB')
    try:text=data.decode('utf-8-sig')
    except UnicodeDecodeError:text=data.decode('cp1252')
    format=format.lower().lstrip('.')
    if format=='cup':
        points=parse_turnpoints(text,'cup');by_name={p.name:p for p in points};by_code={p.code:p for p in points}
        if '-----Related Tasks-----' not in text:raise ValueError('CUP contains waypoints only. Export a CUP with a Related Tasks declaration.')
        section=text.split('-----Related Tasks-----',1)[1]
        rows=[r for r in csv.reader(io.StringIO(section)) if r and any(c.strip() for c in r)]
        declarations=[r for r in rows if len(r)>=5 and r[0] not in ('Options',) and not r[0].startswith('ObsZone=')]
        if len(declarations)!=1:raise ValueError('Upload a CUP containing exactly one task declaration')
        # The first/last waypoint references are takeoff and landing, not scored route points.
        references=declarations[0][2:-1];route=[]
        for ref in references:
            point=by_name.get(ref) or by_code.get(ref)
            if point is None:raise ValueError('CUP task refers to an unknown waypoint: '+ref)
            route.append(point)
    elif format=='tsk':
        if re.search(r'<!\s*(DOCTYPE|ENTITY)',text,re.I):raise ValueError('XML entity/doctype declarations are not allowed')
        try:root=ET.fromstring(text)
        except ET.ParseError as exc:raise ValueError('Invalid TSK XML') from exc
        if root.tag!='Task':raise ValueError('TSK must have a Task root')
        route=[];known=tuple(catalog)
        for i,node in enumerate(root.findall('Point')):
            waypoint=node.find('Waypoint');location=node.find('Waypoint/Location')
            if waypoint is None or location is None:raise ValueError('TSK waypoint/location missing')
            try:lat,lon=float(location.attrib['latitude']),float(location.attrib['longitude'])
            except (KeyError,ValueError) as exc:raise ValueError('Invalid TSK waypoint coordinate') from exc
            name=waypoint.get('name',f'Imported point {i+1}');code=name.split(' ',1)[0].upper()
            match=next((p for p in known if p.code==code and abs(p.latitude-lat)<1e-5 and abs(p.longitude-lon)<1e-5),None)
            if match:point=match
            else:
                if not re.fullmatch(r'[A-Z0-9][A-Z0-9_-]{0,23}',code):code=f'IMP{i+1:02}'
                try:elevation=float(waypoint.attrib['altitude']) if 'altitude' in waypoint.attrib else None
                except ValueError as exc:raise ValueError('Invalid TSK elevation') from exc
                point=Turnpoint(code,name,lat,lon,elevation,description=waypoint.get('comment',''),source='Imported XCSoar task')
            route.append(point)
        kinds=[n.get('type') for n in root.findall('Point')]
        if not kinds or kinds[0]!='Start' or kinds[-1]!='Finish':raise ValueError('TSK must begin with Start and end with Finish')
    else:raise ValueError('Use a .cup or .tsk task file')
    return validate_task(route)
