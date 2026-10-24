"""Forecast-grounded pilot assistant with optional Google Gemini narration.

Intent parsing never executes user text. Suggestions are generated geometrically,
screened with nearby weather, then assessed with actual corridor forecasts by the
UI before they may be described as achievable or loaded automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import math
import os
import re
from typing import Iterable

from bga_turnpoints import Catalog, Turnpoint, search_turnpoints
from config import LOCAL_TIMEZONE
from data_ingestion import Forecast
from soaring_physics import WaveResult
from task_planning import (DayClassification, TaskAssessment, assess_task, candidate_tasks,
                           corridor_points, nearby_forecasts, classify_day)


@dataclass(frozen=True)
class BotIntent:
    kind: str
    target_km: int = 250
    origin_code: str = 'MYN'
    load_best: bool = False
    query: str = ''
    shape: str = 'either'
    route: tuple[Turnpoint, ...] = ()


# Local ridge references supplied by the pilot. These are NOT BGA trigraphs.
LOCAL_POINTS = {
    'MNDN': Turnpoint('MNDN', 'Mynd North', 52.5550, -2.8620, None,
                     source='Pilot-defined northern ridge reference', official=False),
    'MNDS': Turnpoint('MNDS', 'Mynd South', 52.4820, -2.9020, None,
                     source='Pilot-defined southern ridge reference', official=False),
}
STOPWORDS = frozenset('the to and from back fly want for with can you tomorrow today west east north south why is my task not how day wind air off out all are but has had see get let new now our use was way who she him his its may end any too one two'.split())
ALIASES = {'long mynd':'MYN', 'the mynd':'MYN', 'mynd':'MYN', 'mgc':'MYN',
           'midland gliding club':'MYN', 'mynd north':'MNDN', 'mndn':'MNDN', 'myn n':'MNDN',
           'mynd south':'MNDS', 'mnds':'MNDS', 'myn s':'MNDS',
           'the mynd north':'MNDN', 'the mynd south':'MNDS'}


def resolve_turnpoints(question: str, catalog: Catalog) -> tuple[Turnpoint, ...]:
    """Longest phrase first; ordinary English never becomes a BGA code."""
    names = {p.name.casefold(): p.code for p in catalog.turnpoints if len(p.name) >= 5 and p.name.casefold() not in STOPWORDS}
    names.update(ALIASES)
    pattern = r'(?<!\w)(?:' + '|'.join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r'|[a-z0-9]{3})(?!\w)'
    points = []
    for match in re.finditer(pattern, question, re.I):
        token = match[0]; lower = token.casefold()
        explicit = token.isupper() and bool(re.search(r'\b(?:code|turnpoint|where is)\s*$', question[:match.start()], re.I))
        if lower in STOPWORDS and not explicit:
            continue
        if lower not in names and not token.isupper():
            continue
        code = names.get(lower, token.upper())
        point = LOCAL_POINTS.get(code) or catalog.by_code.get(code)
        if point is not None:
            points.append(point)
    return tuple(points)


def briefing_date(question: str, selected: date, today: date) -> date:
    if re.search(r'\btomorrow\b', question, re.I): return today + timedelta(days=1)
    if re.search(r'\btoday\b', question, re.I): return today
    explicit=re.search(r'\b(20\d\d-\d\d-\d\d)\b',question)
    if explicit:
        try:return date.fromisoformat(explicit[1])
        except ValueError:pass
    for i,name in enumerate(('monday','tuesday','wednesday','thursday','friday','saturday','sunday')):
        if re.search(r'\b'+name+r'\b',question,re.I):return today+timedelta(days=(i-today.weekday())%7)
    return selected


def parse_intent(question: str, catalog: Catalog) -> BotIntent:
    text=question.strip(); lower=text.casefold()
    points=resolve_turnpoints(text,catalog)
    if len(points)>=2 and re.search(r'\b(?:to|via|then|back|fly|load|route)\b|->|→',lower):
        return BotIntent('route',query=text,route=points)
    target=re.search(r'\b(100|250|300|500|750)\s*(?:km|k)?\b',lower)
    origin=re.search(r'\b(?:from|starting at|start at)\s+([a-z0-9]{3})\b',lower)
    code=points[0].code if points else 'MYN'
    if 'long mynd' in lower: code='MYN'
    if any(word in lower for word in ('suggest','recommend','find','plan','load')) and (target or 'task' in lower or 'triangle' in lower):
        shape='triangle' if 'triangle' in lower else ('out-and-return' if re.search(r'out.?and.?return|out.?return',lower) else 'either')
        return BotIntent('suggest',int(target[1]) if target else 250,code,'load' in lower,text,shape)
    if any(word in lower for word in ('why','achiev','weak','launch','elapsed','speed','brief my')):
        return BotIntent('explain',query=text)
    if any(word in lower for word in ('wave','ridge')): return BotIntent('wave',query=text)
    if any(word in lower for word in ('street','cloud','wind','thermal')): return BotIntent('weather',query=text)
    if 'my task' in lower or 'this task' in lower: return BotIntent('explain',query=text)
    if any(word in lower for word in ('day','distance','how far','briefing')): return BotIntent('day',query=text)
    if points: return BotIntent('turnpoint',query=points[0].code)
    for prefix in ('where is ','find turnpoint ','turnpoint '):
        if lower.startswith(prefix): return BotIntent('turnpoint',query=text[len(prefix):])
    return BotIntent('help',query=text)


def format_windows(assessment: TaskAssessment) -> str:
    return ', '.join(f'{w.earliest.astimezone(LOCAL_TIMEZONE):%H:%M}–{w.latest.astimezone(LOCAL_TIMEZONE):%H:%M}'
                     for w in assessment.launch_windows) or 'No supported launch window'


def explain_task(assessment: TaskAssessment | None) -> str:
    if assessment is None:
        return 'Build or load a task first. I can then explain its hourly weather, launch windows and weakest leg.'
    text=f'**{assessment.verdict}** · {assessment.distance_km:.0f} km. '
    if assessment.best:
        best=assessment.best
        text+=(f'Estimated elapsed time {best.elapsed_hours:.1f} h; launch {format_windows(assessment)} local. '
               f'Average MacCready cruise {best.average_maccready_kmh:.0f} km/h and task ground speed '
               f'{best.average_groundspeed_kmh:.0f} km/h. Weakest leg: **{best.weakest_leg}**. ')
        risks=[flag for flag in best.risk_flags if flag!='polar fit speed bounded']
        if risks: text+='Margins: '+', '.join(risks)+'. '
        elif assessment.verdict.startswith('MARGINAL'):
            text+='The supported launch window, finish reserve or spatial weather coverage is limited. '
    else:
        missing=[r for r in assessment.hourly_legs if r.missing and r.valid_at.astimezone(LOCAL_TIMEZONE).hour<18]
        blocked=[r for r in assessment.hourly_legs if not r.missing and r.groundspeed_kmh is not None and r.groundspeed_kmh<=0]
        if missing:
            r=missing[0]; text+=f'Weather is missing on {r.leg} at {r.valid_at.astimezone(LOCAL_TIMEZONE):%H:%M}; no complete flight can be established. '
        if blocked:
            r=min(blocked,key=lambda row:row.usable_depth_m or 0)
            text+=(f'{r.leg} at {r.valid_at.astimezone(LOCAL_TIMEZONE):%H:%M} has '
                   f'{r.net_climb_knots:.1f} kt conservative climb and {r.usable_depth_m:.0f} m usable depth AGL; '
                   'height, climb, wind progress or rain blocks that interval. ')
        if not missing and not blocked: text+='The complete distance does not fit inside the supported soaring window. '
    return text+'These calculations still require operational airspace, terrain and landing checks.'


def answer_question(intent: BotIntent, catalog: Catalog, assessment: TaskAssessment | None,
                    classification: DayClassification | None, waves: Iterable[WaveResult]=()) -> str:
    if intent.kind=='explain': return explain_task(assessment)
    if intent.kind=='day':
        if classification is None: return 'The day classification needs a complete local forecast.'
        distance='' if classification.distance_km is None else f' Approximate screening capacity {classification.distance_km:.0f} km.'
        return f'**{classification.label}** at {classification.scope}.{distance} {classification.explanation}'
    if intent.kind=='turnpoint':
        points=search_turnpoints(catalog.turnpoints,intent.query,limit=5)
        if not points: return 'No matching turnpoint in the current catalog. Try its trigraph or part of the full name.'
        return '\n\n'.join(f'**{p.code} — {p.name}**: {p.latitude:.5f}°, {p.longitude:.5f}°; '+
            ('elevation unknown' if p.elevation_m is None else f'{p.elevation_m:.0f} m AMSL')+
            ('. BGA source record.' if p.official else '. User-imported/coordinate record.') for p in points)
    if intent.kind=='wave':
        eligible=[w for w in waves if w.complete and w.score is not None and w.score>=75]
        if not eligible: return 'No complete wave score reaches 75/100 at the selected hour. Missing vertical-motion evidence remains provisional.'
        return 'Complete wave-potential signals: '+', '.join(f'{w.site.replace("_"," ")} {w.score:.0f}/100' for w in eligible[:5])+'. Wave scores do not add unverified lift to thermal task speeds.'
    if intent.kind=='weather':
        if assessment is None: return 'Enter a task to inspect wind, cloud base, conservative climb and street alignment along its sampled corridor.'
        rows=[r for r in assessment.hourly_legs if not r.missing and r.valid_at.astimezone(LOCAL_TIMEZONE).hour<18]
        if not rows: return 'No complete corridor weather is available for this task/day.'
        strongest=max(rows,key=lambda r:r.net_climb_knots)
        street_hours=sum(r.cloud_streets for r in rows)
        return (f'Best sampled leg/hour: {strongest.leg}, {strongest.valid_at.astimezone(LOCAL_TIMEZONE):%H:%M}: '
                f'{strongest.net_climb_knots:.1f} kt conservative climb, cloud base {strongest.cloud_base_ft_amsl:.0f} ft AMSL, '
                f'usable depth {strongest.usable_depth_m:.0f} m AGL, along-track wind {strongest.along_wind_knots:+.1f} kt '
                f'and crosswind {strongest.cross_wind_knots:.1f} kt. {street_hours} leg/hour samples meet the street heuristic; '
                'street alignment is an opportunity indicator, not a speed bonus.')
    return ('Ask “suggest a 250 km triangle from MYN”, “find and load a 300 km out-and-return”, '
            '“why is my task marginal?”, “what kind of day is this?”, “cloud streets along my task”, or “where is MYN?”. '
            'Answers use the selected day, wing loading, BGA catalog and calculated forecasts.')


def shortlist_tasks(catalog: Catalog, origin: Turnpoint, target_km: int, forecasts: Iterable[Forecast],
                    day: date, loading: str, *, preference, shape='either', limit=3) -> tuple[tuple[Turnpoint, ...], ...]:
    """Nearby weather ranks candidates only; callers must fetch/verify each corridor."""
    candidates=candidate_tasks(catalog.turnpoints,origin,target_km,limit=30)
    if shape=='triangle': candidates=tuple(t for t in candidates if len(t)==4)
    elif shape=='out-and-return': candidates=tuple(t for t in candidates if len(t)==3)
    forecasts=tuple(forecasts)
    capacities={f.location.key:classify_day((f,),f.location,day,loading,preference=(f.model,)).distance_km for f in forecasts}
    from soaring_physics import distance_bearing
    scored=[]
    for task in candidates:
        approximate=nearby_forecasts(corridor_points(task),forecasts,maximum_km=75)
        result=assess_task(task,approximate,day,loading,preference=preference)
        good=result.best is not None
        street=sum(row.cloud_streets for row in result.hourly_legs)
        progress=max((r.groundspeed_kmh or 0 for r in result.hourly_legs),default=0)
        corridor_capacity=[]
        for point in corridor_points(task):
            nearest=min(forecasts,key=lambda f:distance_bearing(point,f.location)[0],default=None)
            if nearest and distance_bearing(point,nearest.location)[0]<=75:
                capacity=capacities.get(nearest.location.key)
                if capacity is not None:corridor_capacity.append(capacity)
        bottleneck=min(corridor_capacity,default=0)
        speed=result.best.average_groundspeed_kmh if result.best else progress
        score=(good,result.verdict!='INSUFFICIENT DATA',speed,bottleneck,street,-abs(result.distance_km-target_km))
        scored.append((score,task))
    return tuple(task for _,task in sorted(scored,key=lambda item:item[0],reverse=True)[:limit])


def site_evidence(forecasts, day, *, origin=None):
    """Hourly local observations of the selected forecast, never user wind claims."""
    from config import MONITORING_ZONES, WAVE_RIDGES, MPS_TO_KNOTS
    from soaring_physics import Profile, distance_bearing, evaluate_thermals, evaluate_wave
    origin = origin or MONITORING_ZONES['shropshire']
    nearest = min(forecasts, key=lambda f: distance_bearing(origin, f.location)[0], default=None)
    if nearest is None or distance_bearing(origin, nearest.location)[0] > 75:
        return {'day': day.isoformat(), 'site': origin.name, 'hours': [], 'coverage': 'No forecast within 75 km'}
    ridge=min(WAVE_RIDGES.values(),key=lambda r:distance_bearing(origin,r.point)[0])
    if distance_bearing(origin,ridge.point)[0]>45: ridge=None
    hours = []
    for when in nearest.times:
        local = when.astimezone(LOCAL_TIMEZONE)
        if local.date() != day or not 9 <= local.hour <= 18: continue
        profile = Profile.from_forecast(nearest, when)
        thermal = evaluate_thermals(profile)
        wave = evaluate_wave(profile, ridge) if ridge else None
        values = profile.values
        speed, direction, gust = (values.get(k) for k in ('wind_speed_10m','wind_direction_10m','wind_gusts_10m'))
        on_face = None if speed is None or direction is None or ridge is None else speed*MPS_TO_KNOTS*math.cos(math.radians(direction-ridge.perpendicular_wind_azimuths_deg[0]))
        from wave_map import at_height,ALTITUDES_FT
        bands=[{'altitude_ft':ft,**(at_height(profile,ft*.3048) or {})} for ft in ALTITUDES_FT]
        hours.append({'time':local.strftime('%H:%M %Z'), 'wind_kt':None if speed is None else speed*MPS_TO_KNOTS,
            'from_deg':direction, 'gust_kt':None if gust is None else gust*MPS_TO_KNOTS,
            'ridge_component_kt':on_face, 'cloud_base_ft':thermal.cloud_base_ft_amsl,
            'climb_kt':thermal.net_climb_knots, 'rain_mm':values.get('precipitation'),
            'wave_score':wave.score if wave else None, 'wave_complete':wave.complete if wave else False,
            'upper_winds':[{'hpa':l.pressure_hpa,'height_ft':round(l.height_m*3.28084),'u_mps':l.u_mps,'v_mps':l.v_mps,'ascent_kt':None if l.w_mps is None else l.w_mps*MPS_TO_KNOTS} for l in profile.levels if l.pressure_hpa in (950,850,800,700,500)],
            'wave_bands':bands, 'ridge':ridge.point.name if ridge else None, 'ridge_facing_deg':ridge.perpendicular_wind_azimuths_deg[0] if ridge else None})
    return {'day':day.isoformat(), 'site':origin.name, 'model':nearest.model,
            'forecast_point':nearest.location.name, 'distance_km':round(distance_bearing(origin,nearest.location)[0],1),
            'retrieved':nearest.fetched_at.isoformat(), 'hours':hours}


def pilot_briefing(question, forecasts, day, *, assessment=None, route=()):
    """Useful local weather briefing even before a task exists or without a key."""
    evidence = site_evidence(forecasts, day)
    hours = evidence['hours']
    def fmt(value, digits=0): return '—' if value is None else f'{value:.{digits}f}'
    lines = [f'**Long Mynd · {day:%A %d %B}**',
             'Let’s check the wind on the west-facing ridge, then cloud and lift.']
    if hours:
        winds = [h['wind_kt'] for h in hours if h['wind_kt'] is not None]
        gusts = [h['gust_kt'] for h in hours if h['gust_kt'] is not None]
        face = [h for h in hours if h['ridge_component_kt'] is not None and h['ridge_component_kt'] >= 5]
        lines += ['**Step 1 · Wind and ridge lift**',
            (f"Forecast surface wind ranges from {min(winds):.0f} to {max(winds):.0f} kt. " if winds else 'Surface wind is missing. ')+
            (f"Peak gusts reach {max(gusts):.0f} kt." if gusts else 'Gust data is missing.'),
            (f"Flow supports possible west-face ridge lift during {', '.join(h['time'] for h in face)}. " if face else 'No sampled hour establishes useful west-face flow. ')+
            'This checks the wind towards the ridge, using a 280° facing direction. It does not predict a safe height gain.',
            '**Step 2 · Hourly weather**',
            '| Local time | Wind kt / from °T | Gust kt | Into west face kt | Cloud base ft AMSL | Thermal kt | Rain mm/h | Wave /100 |',
            '|---|---|---|---|---|---|---|---|']
        for h in hours:
            lines.append(f"| {h['time']} | {fmt(h['wind_kt'])} / {fmt(h['from_deg'])} | {fmt(h['gust_kt'])} | {fmt(h['ridge_component_kt'],1)} | {fmt(h['cloud_base_ft'])} | {fmt(h['climb_kt'],1)} | {fmt(h['rain_mm'],1)} | {fmt(h['wave_score'])}{'' if h['wave_complete'] else ' provisional'} |")
        lines += [f"Source: {evidence['model']} at {evidence['forecast_point']}, {evidence['distance_km']:g} km away. Retrieved {evidence['retrieved']}."]
    else:
        lines += ['**Step 1 · Wind and ridge lift**',
            'Hourly forecast data is missing for this date. I cannot confirm tomorrow’s wind or cloud height.',
            'A westerly wind blows towards the Mynd’s west-facing ridge. That can support ridge lift. '
            'Crosswinds, gusts and showers can make lift uneven. An easterly flow puts this face downwind.']
    lines += ['**Step 3 · Wave, cloud and the West launch**',
        'Wave needs stable air and suitable wind above the ridge. The hourly scores flag potential; provisional scores have missing evidence.',
        'Low cloud may block ridge access or hide the hills. Thermals and ridge lift are separate sources of lift.',
        'For the short West takeoff, check actual wind, gusts, crosswind and available ground run. '
        'These forecasts cannot clear a launch. Confirm the launch method, aircraft limits and current site guidance with the duty instructor. '
        'Strong westerlies also warrant checks for rotor and turbulent lee-side air.']
    if route:
        lines += ['**Step 4 · Your route**', ' → '.join(f'{p.code} · {p.name}' for p in route),
            'MNDN and MNDS are pilot-defined ridge references. Their elevations are unverified.']
    if assessment is not None:
        lines += [explain_task(assessment)]
        for i in sorted({r.leg_index for r in assessment.hourly_legs}):
            rows=[r for r in assessment.hourly_legs if r.leg_index==i]
            good=[r for r in rows if not r.missing and r.cruise_airspeed_kmh is not None]
            label=rows[0].leg if rows else f'Leg {i+1}'
            if good:
                r=max(good,key=lambda r:r.groundspeed_kmh or 0)
                lines += [f"**{label}** · Best sampled hour {r.valid_at.astimezone(LOCAL_TIMEZONE):%H:%M}. "
                    f"Thermal climb {fmt(r.net_climb_knots,1)} kt; cloud base {fmt(r.cloud_base_ft_amsl)} ft AMSL. "
                    f"Speed to fly {r.cruise_airspeed_kmh/1.852:.0f} kt; XC speed {fmt(r.groundspeed_kmh)} km/h. "
                    f"Along-track wind {fmt(r.along_wind_knots,1)} kt; crosswind {fmt(r.cross_wind_knots,1)} kt. "
                    'Use the ridge and wave checks above; neither provides assumed lift in this thermal task estimate.']
            else: lines += [f'**{label}** · Corridor weather cannot establish a thermal speed or flight time. Ridge and wave lift still need local assessment.']
    text='\n\n'.join(lines)
    text=re.sub(r'\|\n\n(?=\|)', '|\n', text)
    return text, evidence


def gemini_key(runtime='', secrets=None):
    """Credentials stay in server memory and never enter prompts or task files."""
    value = runtime or (secrets or {}).get('GEMINI_API_KEY', '') or os.environ.get('GEMINI_API_KEY', '')
    return str(value).strip()


def gemini_briefing(question, evidence, local_answer, catalog, *, api_key='', model=None, client_factory=None):
    """Compatibility facade; production requests use the dependency-free REST client."""
    if not api_key:return local_answer,'Local forecast assistant'
    from gemini_rest import GeminiREST,GeminiError
    client=(client_factory or GeminiREST)(api_key)
    connection=client.connect()
    if connection.error:return local_answer,connection.error
    models=tuple(dict.fromkeys(([model] if model in connection.models else [])+list(connection.models)))
    try:
        result,active=client.generate(question,evidence,models=models)
        return result['answer']+'\n\n'+local_answer,'Connected to Gemini ('+active+')'
    except GeminiError as exc:return local_answer,str(exc)
