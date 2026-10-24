"""A deterministic forecast-grounded task assistant; no model/API key required.

Intent parsing never executes user text. Suggestions are generated geometrically,
screened with nearby weather, then assessed with actual corridor forecasts by the
UI before they may be described as achievable or loaded automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
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


def parse_intent(question: str, catalog: Catalog) -> BotIntent:
    text=question.strip(); lower=text.casefold()
    target=re.search(r'\b(100|250|300|500|750)\s*(?:km|k)?\b',lower)
    origin=re.search(r'\b(?:from|starting at|start at)\s+([a-z0-9]{3})\b',lower)
    code=origin[1].upper() if origin else 'MYN'
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
    matches=re.findall(r'\b[A-Za-z0-9]{3}\b',text)
    codes=[code.upper() for code in matches if code.upper() in catalog.by_code]
    if codes: return BotIntent('turnpoint',query=codes[0])
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
