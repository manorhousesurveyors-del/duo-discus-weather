"""Custom-task geometry, sampled weather, launch-window integration and exports.

Verdicts describe modeled soaring capacity, not airspace/terrain clearance.
The existing alert route API remains compatible; custom planning uses this
stricter corridor sampler and a configurable usable-height threshold.
"""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta
import hashlib
import io
import math
import os
from pathlib import Path
from typing import Iterable
import xml.etree.ElementTree as ET

from bga_turnpoints import Turnpoint, TurnpointError, atomic_json
from config import DUO_DISCUS_POLARS, LOCAL_TIMEZONE, METRES_TO_FEET, MPS_TO_KNOTS, GeoPoint
from data_ingestion import Forecast, utc_datetime
from soaring_physics import Profile, cross_country_speed, distance_bearing, evaluate_thermals, select_forecast

MAX_TASK_POINTS = 20
SAMPLE_SPACING_KM = 35.0
MINIMUM_USABLE_DEPTH_M = 500.0
FULL_MARGIN_DEPTH_M = 650.0
FORECAST_PREFERENCE = ('ukmo_uk_deterministic_2km','icon_eu','ecmwf_ifs025')


def validate_task(points: Iterable[Turnpoint], *, allow_incomplete=False) -> tuple[Turnpoint, ...]:
    points = tuple(points)
    if len(points)>MAX_TASK_POINTS or (not allow_incomplete and len(points)<2):
        raise ValueError('A task needs 2–20 ordered points')
    for a,b in zip(points,points[1:]):
        if distance_bearing(a.point,b.point)[0] < .1:
            raise ValueError('Consecutive task points must be at least 100 m apart')
    return points


def task_distance(points: Iterable[Turnpoint]) -> float:
    points=tuple(points)
    return sum(distance_bearing(a.point,b.point)[0] for a,b in zip(points,points[1:]))


def coordinate_turnpoint(latitude: float, longitude: float, elevation_m=None) -> Turnpoint:
    if not 49 <= latitude <= 61.5 or not -9 <= longitude <= 2.5:
        raise ValueError('Select a coordinate within the UK planning area')
    digest=hashlib.sha256(f'{latitude:.6f},{longitude:.6f}'.encode()).hexdigest()[:8].upper()
    return Turnpoint('U'+digest,f'Custom {latitude:.4f}, {longitude:.4f}',latitude,longitude,elevation_m,
                     source='User map coordinate',official=False)


def add_task_point(points: Iterable[Turnpoint], point: Turnpoint, role='Append') -> tuple[Turnpoint, ...]:
    result=list(points)
    if role=='Start':
        if result: result[0]=point
        else: result.append(point)
    elif role=='Finish':
        if len(result)>=2: result[-1]=point
        else: result.append(point)
    elif role=='Turnpoint' and len(result)>=2:
        result.insert(len(result)-1,point)
    elif role in ('Append','Turnpoint'):
        result.append(point)
    else:
        raise ValueError('Unknown task point role')
    if len(result)>MAX_TASK_POINTS:
        raise ValueError('Maximum 20 task points')
    return tuple(result)


def reorder_task(points: Iterable[Turnpoint], index: int, offset: int) -> tuple[Turnpoint, ...]:
    result=list(points)
    if not 0 <= index < len(result) or not 0 <= index+offset < len(result):
        raise ValueError('Cannot move beyond the task sequence')
    result[index],result[index+offset]=result[index+offset],result[index]
    return tuple(result)


def _intermediate(a: GeoPoint, b: GeoPoint, fraction: float) -> GeoPoint:
    distance,bearing=distance_bearing(a,b)
    angle=distance*fraction/6371.0088
    lat,lon,az=math.radians(a.latitude),math.radians(a.longitude),math.radians(bearing)
    latitude=math.asin(math.sin(lat)*math.cos(angle)+math.cos(lat)*math.sin(angle)*math.cos(az))
    longitude=lon+math.atan2(math.sin(az)*math.sin(angle)*math.cos(lat),math.cos(angle)-math.sin(lat)*math.sin(latitude))
    latitude,longitude=round(math.degrees(latitude),6),round(math.degrees(longitude),6)
    return GeoPoint(f'corridor:{latitude:.6f}:{longitude:.6f}',f'Corridor {latitude:.3f}, {longitude:.3f}',latitude,longitude)


def sampled_legs(points: Iterable[Turnpoint], spacing_km=SAMPLE_SPACING_KM) -> tuple[tuple[GeoPoint, ...], ...]:
    points=validate_task(points)
    if not math.isfinite(spacing_km) or spacing_km<=0:
        raise ValueError('Sample spacing must be positive')
    legs=[]
    for a,b in zip(points,points[1:]):
        length=distance_bearing(a.point,b.point)[0]
        count=max(1,math.ceil(length/spacing_km))
        legs.append((a.point, *(_intermediate(a.point,b.point,i/count) for i in range(1,count)), b.point))
    return tuple(legs)


def corridor_points(points: Iterable[Turnpoint]) -> tuple[GeoPoint, ...]:
    return tuple({p.key:p for leg in sampled_legs(points) for p in leg}.values())


def nearby_forecasts(points: Iterable[GeoPoint], forecasts: Iterable[Forecast], *, maximum_km: float) -> tuple[Forecast, ...]:
    """Explicit coarse screening/recorded-mode aliases; never a live fetch substitute."""
    forecasts=tuple(forecasts)
    selected=[]
    for point in points:
        by_model={}
        for forecast in forecasts:
            distance=distance_bearing(point,forecast.location)[0]
            if distance <= maximum_km and (forecast.model not in by_model or distance<by_model[forecast.model][0]):
                by_model[forecast.model]=(distance,forecast)
        for distance,forecast in by_model.values():
            selected.append(replace(forecast,location=point,warnings=forecast.warnings+
                                   (f'Spatial approximation: {distance:.1f} km from stored {forecast.location.name}',)))
    return tuple(selected)


@dataclass(frozen=True)
class LegWeather:
    valid_at: datetime
    leg_index: int
    leg: str
    groundspeed_kmh: float | None
    cruise_airspeed_kmh: float | None
    net_climb_knots: float | None
    usable_depth_m: float | None
    cloud_base_ft_amsl: float | None
    along_wind_knots: float | None
    cross_wind_knots: float | None
    cloud_streets: bool
    risk_flags: tuple[str, ...]
    missing: bool


@dataclass(frozen=True)
class LaunchWindow:
    earliest: datetime
    latest: datetime


@dataclass(frozen=True)
class FlightSolution:
    launch: datetime
    finish: datetime
    elapsed_hours: float
    average_groundspeed_kmh: float
    average_maccready_kmh: float
    weakest_leg: str
    risk_flags: tuple[str, ...]


@dataclass(frozen=True)
class TaskAssessment:
    verdict: str
    distance_km: float
    best: FlightSolution | None
    launch_windows: tuple[LaunchWindow, ...]
    hourly_legs: tuple[LegWeather, ...]
    warnings: tuple[str, ...]
    sample_count: int


def assess_task(points: Iterable[Turnpoint], forecasts: Iterable[Forecast], day: date, loading='dry',
                *, preference=FORECAST_PREFERENCE, minimum_depth_m=MINIMUM_USABLE_DEPTH_M) -> TaskAssessment:
    points=validate_task(points)
    if not 0 < minimum_depth_m < 3000:
        raise ValueError('Minimum usable height must be between 0 and 3000 m AGL')
    legs=sampled_legs(points); forecasts=tuple(forecasts); polar=DUO_DISCUS_POLARS[loading]
    start=utc_datetime(datetime.combine(day,time(10),LOCAL_TIMEZONE))
    end=utc_datetime(datetime.combine(day,time(18),LOCAL_TIMEZONE))
    unique={p.key:p for leg in legs for p in leg}
    weather={}; missing_any=False
    for hour in range(9):
        when=start+timedelta(hours=hour)
        for point in unique.values():
            forecast=select_forecast(forecasts,point.key,when,preference)
            if forecast is None:
                weather[point.key,hour]=None; missing_any=missing_any or hour<8; continue
            profile=Profile.from_forecast(forecast,when); thermal=evaluate_thermals(profile)
            wind=next((l for l in (profile.level(925),profile.level(850)) if l and l.u_mps is not None),None)
            if thermal.conservative_climb_knots is None or thermal.usable_top_ft_amsl is None or wind is None:
                weather[point.key,hour]=None; missing_any=missing_any or hour<8; continue
            depth=thermal.usable_top_ft_amsl/METRES_TO_FEET-forecast.terrain_elevation_m
            flags=[]
            if depth < max(FULL_MARGIN_DEPTH_M,minimum_depth_m+150): flags.append('limited usable height')
            if thermal.overdevelopment: flags.append('spread-out risk')
            rain=profile.values.get('precipitation')
            if rain is not None and rain>0: flags.append('precipitation forecast')
            weather[point.key,hour]=(thermal,wind,depth,tuple(flags),rain)
    segments=[]; hourly=[]; conditions={}
    for index,leg in enumerate(legs):
        label=f'{points[index].code} → {points[index+1].code}'
        leg_segments=[]
        for a,b in zip(leg,leg[1:]):
            length,bearing=distance_bearing(a,b)
            segment_id=len(segments); segments.append((index,length)); leg_segments.append((segment_id,length))
            angle=math.radians(bearing)
            for hour in range(9):
                first,second=weather[a.key,hour],weather[b.key,hour]
                if first is None or second is None:
                    conditions[segment_id,hour]=None; continue
                thermal,wind,depth,flags,rain=first
                other,other_wind,other_depth,other_flags,other_rain=second
                climb=min(thermal.conservative_climb_knots,other.conservative_climb_knots)
                depth=min(depth,other_depth)
                u=(wind.u_mps+other_wind.u_mps)/2; v=(wind.v_mps+other_wind.v_mps)/2
                ground,cruise,limited=cross_country_speed(climb/MPS_TO_KNOTS,bearing,u,v,polar)
                flags=tuple(dict.fromkeys(flags+other_flags+(('polar fit speed bounded',) if limited else ())))
                if depth<minimum_depth_m or max(rain or 0,other_rain or 0)>=1:
                    ground=0.
                conditions[segment_id,hour]=(ground,cruise*3.6,climb,depth,
                    min(thermal.cloud_base_ft_amsl,other.cloud_base_ft_amsl),
                    (u*math.sin(angle)+v*math.cos(angle))*MPS_TO_KNOTS,
                    abs(u*math.cos(angle)-v*math.sin(angle))*MPS_TO_KNOTS,
                    thermal.cloud_streets is True or other.cloud_streets is True,flags)
        for hour in range(9):
            rows=[conditions[sid,hour] for sid,_ in leg_segments]
            missing=any(row is None for row in rows)
            if missing:
                result=LegWeather(start+timedelta(hours=hour),index,label,None,None,None,None,None,None,None,False,
                                  ('Missing corridor weather; no interpolation in time',),True)
            else:
                length=sum(length for _,length in leg_segments)
                blocked=any(row[0]<=0 for row in rows)
                speed=0. if blocked else length/sum(d/r[0] for (_,d),r in zip(leg_segments,rows))
                avg=lambda col:sum(d*r[col] for (_,d),r in zip(leg_segments,rows))/length
                result=LegWeather(start+timedelta(hours=hour),index,label,speed,avg(1),min(r[2] for r in rows),
                    min(r[3] for r in rows),min(r[4] for r in rows),avg(5),max(r[6] for r in rows),
                    any(r[7] for r in rows),tuple(dict.fromkeys(f for r in rows for f in r[8])),False)
            hourly.append(result)
    length=sum(d for _,d in segments)
    solutions=[]
    for minutes in range(0,480,15):
        launch=start+timedelta(minutes=minutes); now=launch; risk=[]; cruise_hours=0.; successful=True
        leg_time=[0.] * len(legs); leg_distance=[0.] * len(legs)
        for segment_id,(leg_index,distance) in enumerate(segments):
            remaining=distance
            while remaining>1e-7:
                if now>=end: successful=False; break
                hour=int((now-start).total_seconds()//3600)
                row=conditions.get((segment_id,hour))
                if row is None or row[0]<=0: successful=False; break
                next_hour=start+timedelta(hours=hour+1)
                duration=min((next_hour-now).total_seconds()/3600,remaining/row[0])
                if duration<=0: successful=False; break
                travelled=min(remaining,row[0]*duration)
                remaining-=travelled; now+=timedelta(hours=duration)
                if abs((next_hour-now).total_seconds())<.00001: now=next_hour
                cruise_hours+=row[1]*duration
                leg_time[leg_index]+=duration; leg_distance[leg_index]+=travelled; risk.extend(row[8])
            if not successful: break
        if successful:
            elapsed=(now-launch).total_seconds()/3600
            weakest=min(range(len(legs)),key=lambda i:leg_distance[i]/leg_time[i])
            solutions.append(FlightSolution(launch,now,elapsed,length/elapsed,cruise_hours/elapsed,
                              f'{points[weakest].code} → {points[weakest+1].code}',tuple(dict.fromkeys(risk))))
    windows=[]
    for solution in solutions:
        if windows and solution.launch-windows[-1].latest==timedelta(minutes=15):
            windows[-1]=LaunchWindow(windows[-1].earliest,solution.launch)
        else: windows.append(LaunchWindow(solution.launch,solution.launch))
    weather_risks=lambda s:tuple(r for r in s.risk_flags if r!='polar fit speed bounded')
    best=min(solutions,key=lambda s:(bool(weather_risks(s)),s.elapsed_hours,s.launch)) if solutions else None
    warnings=['Modelled capacity only: no airspace, terrain clearance, landing options or flight-envelope approval.',
              f'Weather sampled every ≤{SAMPLE_SPACING_KM:g} km; minimum usable thermal depth {minimum_depth_m:.0f} m AGL.',
              'Launches checked every 15 minutes, 10:00–18:00 Europe/London; hourly weather held within each interval.']
    approximated=any(any('Spatial approximation:' in w for w in f.warnings) for f in forecasts)
    if approximated: warnings.append('Some weather uses explicitly labelled nearby stored samples; confirm fresh corridor forecasts.')
    if best:
        clean_launches=[s.launch for s in solutions if not weather_risks(s) and s.finish<=end-timedelta(minutes=30)]
        consecutive=maximum=0; previous_launch=None
        for launch in clean_launches:
            consecutive=consecutive+15 if previous_launch and launch-previous_launch==timedelta(minutes=15) else 0
            maximum=max(maximum,consecutive); previous_launch=launch
        wide=maximum>=60
        reserve=best.finish<=end-timedelta(minutes=30)
        # Bounded polar speed is informative, not itself a weather margin failure.
        risks=[r for r in best.risk_flags if r!='polar fit speed bounded']
        verdict='FULLY ACHIEVABLE' if wide and reserve and not risks and not approximated else 'MARGINAL / NARROW WINDOW'
    else:
        verdict='INSUFFICIENT DATA' if missing_any else 'NOT ACHIEVABLE'
        warnings.append('No complete modeled flight found; missing data is not evidence of poor weather.' if missing_any else
                        'At least one required corridor/time has inadequate climb, usable height, progress or rain conditions.')
    return TaskAssessment(verdict,length,best,tuple(windows),tuple(hourly),tuple(warnings),len(unique))


@dataclass(frozen=True)
class DayClassification:
    label: str
    distance_km: float | None
    scope: str
    explanation: str


def classify_day(forecasts: Iterable[Forecast], origin: GeoPoint, day: date, loading='dry',
                 *, preference=FORECAST_PREFERENCE, wave_available=False) -> DayClassification:
    """Conservative local-day screening, not proof of a remote 500 km corridor."""
    forecasts=tuple(forecasts); capacity=run=0.; known=0
    for hour in range(10,18):
        when=utc_datetime(datetime.combine(day,time(hour),LOCAL_TIMEZONE))
        forecast=select_forecast(forecasts,origin.key,when,preference)
        if forecast is None: run=0.; continue
        profile=Profile.from_forecast(forecast,when); thermal=evaluate_thermals(profile)
        wind=next((l for l in (profile.level(925),profile.level(850)) if l and l.u_mps is not None),None)
        if thermal.conservative_climb_knots is None or thermal.usable_top_ft_amsl is None or wind is None:
            run=0.; continue
        known+=1
        depth=thermal.usable_top_ft_amsl/METRES_TO_FEET-forecast.terrain_elevation_m
        speed=min(cross_country_speed(thermal.conservative_climb_knots/MPS_TO_KNOTS,b,wind.u_mps,wind.v_mps,
                                      DUO_DISCUS_POLARS[loading])[0] for b in (0,90,180,270))
        if depth<MINIMUM_USABLE_DEPTH_M or (profile.values.get('precipitation') or 0)>=1: speed=0.
        run=run+speed if speed>0 else 0.; capacity=max(capacity,run)
    if known<8:
        return DayClassification('Day classification unavailable',None,origin.name,
                                 f'Only {known}/8 local soaring hours have the required weather fields.')
    capacity*=.9  # Ten percent time/distance reserve in this screening estimate.
    if capacity>=500: label='500+ km Day'
    elif capacity>=300: label='300 km Day'
    elif capacity>=250: label='250 km Day'
    elif capacity>=100: label='100 km Local Task Day'
    else: label='Non-Cross-Country / Wave Only Day' if wave_available else 'Non-Cross-Country Day'
    return DayClassification(label,capacity,origin.name,
        'Local weather capacity with worst-cardinal-direction wind and 10% reserve; remote tasks need their own corridor assessment.')


def candidate_tasks(catalog: Iterable[Turnpoint], origin: Turnpoint, target_km: int, *, limit=24) -> tuple[tuple[Turnpoint, ...], ...]:
    if target_km not in (100,250,300,500,750):
        raise ValueError('Choose 100, 250, 300, 500 or 750 km')
    polar=[]
    for point in catalog:
        if not point.official or point.code==origin.code: continue
        distance,bearing=distance_bearing(origin.point,point.point)
        if 15 <= distance <= target_km*.56: polar.append((point,distance,bearing))
    candidates={}
    def consider(task):
        distance=task_distance(task)
        if target_km<=distance<=target_km*1.08:
            lengths=[distance_bearing(a.point,b.point)[0] for a,b in zip(task,task[1:])]
            if len(task)==4 and min(lengths)<.18*distance: return
            candidates[tuple(p.code for p in task)]=(abs(distance-target_km),tuple(task))
    for point,_,_ in polar: consider((origin,point,origin))
    # Geographic sector selection bounds the quadratic triangle search.
    selected=[]
    for sector in range(12):
        sector_points=[entry for entry in polar if int(entry[2]//30)==sector]
        selected.extend(sorted(sector_points,key=lambda x:abs(x[1]-target_km/3))[:3])
    for a,_,az_a in selected:
        for b,_,az_b in selected:
            if a.code>=b.code: continue
            if abs((az_a-az_b+180)%360-180)<35: continue
            consider((origin,a,b,origin)); consider((origin,b,a,origin))
    ranked=sorted(candidates.values(),key=lambda pair:(pair[0],tuple(p.code for p in pair[1])))
    # Include both out-and-returns and triangles when available.
    out=[task for _,task in ranked if len(task)==3][:max(1,limit//3)]
    triangles=[task for _,task in ranked if len(task)==4][:limit-len(out)]
    return tuple(out+triangles)


def _cup_coordinate(value: float, latitude: bool) -> str:
    # Round total minutes first so 59.9996 carries correctly into the next degree.
    minutes=round(abs(value)*60,3); degrees=int(minutes//60); remaining=minutes-degrees*60
    return f'{degrees:0{2 if latitude else 3}d}{remaining:06.3f}'+(('N' if value>=0 else 'S') if latitude else ('E' if value>=0 else 'W'))


def export_cup(points: Iterable[Turnpoint], title='Duo Discus task') -> str:
    points=validate_task(points); stream=io.StringIO(newline=''); writer=csv.writer(stream,lineterminator='\r\n')
    writer.writerow(('name','code','country','lat','lon','elev','style','rwdir','rwlen','freq','desc'))
    def safe(value):
        value=str(value)
        return "'"+value if value.startswith(('=','+','-','@')) else value
    unique={}
    for p in points:
        if p.code in unique and p!=unique[p.code]: raise TurnpointError('Conflicting waypoint definitions in task')
        unique[p.code]=p
    names={p.code:(p.name if sum(other.name==p.name for other in unique.values())==1 else p.code+' '+p.name)
           for p in unique.values()}
    for p in unique.values():
        writer.writerow((safe(names[p.code]),p.code,p.country,_cup_coordinate(p.latitude,True),_cup_coordinate(p.longitude,False),
            '' if p.elevation_m is None else f'{p.elevation_m:.1f}m',p.style,p.runway_direction if p.runway_direction is not None else '',
            '' if p.runway_length_m is None else f'{p.runway_length_m:.1f}m',safe(p.frequency),safe(p.description)))
    writer.writerow(('-----Related Tasks-----',))
    # SeeYou order: task name, takeoff, start, turnpoints, finish, landing.
    writer.writerow((safe(title),safe(names[points[0].code]),*(safe(names[p.code]) for p in points),safe(names[points[-1].code])))
    return stream.getvalue()


def export_tsk(points: Iterable[Turnpoint]) -> str:
    points=validate_task(points)
    root=ET.Element('Task',{'type':'RT','task_scored':'1','aat_min_time':'0','start_max_speed':'0',
        'start_max_height':'0','start_max_height_ref':'MSL','finish_min_height':'0','finish_min_height_ref':'AGL',
        'fai_finish':'0','min_points':'2','max_points':str(MAX_TASK_POINTS),'homogeneous_tps':'0',
        'is_closed':'1' if points[0].code==points[-1].code else '0'})
    for i,p in enumerate(points):
        kind='Start' if i==0 else ('Finish' if i==len(points)-1 else 'Turn')
        point=ET.SubElement(root,'Point',{'type':kind})
        attributes={'name':f'{p.code} {p.name}','id':str(i),'comment':p.description}
        if p.elevation_m is not None: attributes['altitude']=f'{p.elevation_m:.1f}'
        waypoint=ET.SubElement(point,'Waypoint',attributes)
        ET.SubElement(waypoint,'Location',{'latitude':f'{p.latitude:.8f}','longitude':f'{p.longitude:.8f}'})
        ET.SubElement(point,'ObservationZone',{'type':'Cylinder','radius':'500'} if kind=='Turn' else {'type':'Line','length':'1000'})
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n'+ET.tostring(root,encoding='unicode')+'\n'


def task_store() -> Path:
    return Path(os.environ.get('DUO_TASK_STORE',Path(__file__).resolve().parent/'var'/'tasks'))


def save_daily_task(day: date, points: Iterable[Turnpoint]) -> None:
    points=tuple(points)
    if len(points)>MAX_TASK_POINTS: raise ValueError('Maximum 20 points')
    atomic_json(task_store()/(day.isoformat()+'.json'),{'schema_version':1,'date':day.isoformat(),'points':[asdict(p) for p in points]})


def load_daily_task(day: date) -> tuple[Turnpoint, ...]:
    import json
    path=task_store()/(day.isoformat()+'.json')
    if not path.exists(): return ()
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version')!=1 or data.get('date')!=day.isoformat() or len(data.get('points',[]))>MAX_TASK_POINTS:
        raise ValueError('Invalid saved daily task')
    return tuple(Turnpoint(**point) for point in data['points'])
