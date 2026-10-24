"""Evidence and conversational fallbacks for the free-form flight assistant."""
from dataclasses import asdict
from datetime import timedelta,datetime,time
import re
import math
from briefing_bot import site_evidence,resolve_turnpoints,explain_task,LOCAL_POINTS
from config import MONITORING_ZONES,WAVE_RIDGES,GeoPoint,LOCAL_TIMEZONE
from soaring_physics import distance_bearing
from task_planning import nearby_forecasts,corridor_points,assess_task
from day_rating import rate_day
from json_payload import finite_data

SITES={**MONITORING_ZONES,
 'denbigh':WAVE_RIDGES['denbigh'].point,'mid_wales':WAVE_RIDGES['cambrian'].point,
 'peak_district':WAVE_RIDGES['mam_tor'].point,'yorkshire':WAVE_RIDGES['yorkshire_dales'].point,
 'cotswolds':GeoPoint('cotswolds','Cotswolds',51.8,-1.9),
 'church_stretton':GeoPoint('church_stretton','Church Stretton',52.537,-2.806)}
ALIASES={'long mynd':'shropshire','mynd':'shropshire','mgc':'shropshire','midland gliding club':'shropshire','denbigh':'denbigh','snowdonia':'snowdonia','eryri':'snowdonia','south wales':'south_wales','talgarth':'south_wales','mid wales':'mid_wales','herefordshire':'herefordshire','didcot':'oxfordshire','oxfordshire':'oxfordshire','goole':'humberside','humberside':'humberside','cambridgeshire':'cambridgeshire','lincolnshire':'lincolnshire','south yorkshire':'south_yorkshire','yorkshire':'yorkshire','peak district':'peak_district','cotswolds':'cotswolds'}


def requested_site(question,selected=None):
    if selected and re.search(r'\b(?:here|clicked|selected|this location|this airfield|that point|map point)\b',question,re.I):return selected
    matches=[(m.start(),-len(alias),SITES[key]) for alias,key in ALIASES.items() for m in re.finditer(r'\b'+re.escape(alias)+r'\b',question,re.I)]
    return sorted(matches,key=lambda x:x[:2])[0][2] if matches else selected or SITES['shropshire']


def checked_route(points,forecasts,day,loading):
    weather=nearby_forecasts(corridor_points(points),forecasts,maximum_km=75)
    result=assess_task(points,weather,day,loading)
    return result,{'codes':[p.code for p in points],'verdict':result.verdict,'distance_km':round(result.distance_km,1),
        'briefing':explain_task(result),'hourly_legs':finite_data([asdict(r) for r in result.hourly_legs]),
        'coverage':'Screening from loaded weather within 75 km; verify the exact corridor before flight.'}


def build_context(question,catalog,forecasts,day,today,loading,*,selected=None,active=(),assessment=None):
    origin=requested_site(question,selected)
    site=site_evidence(forecasts,day,origin=origin)
    site['stars']=asdict(rate_day(nearby_forecasts((origin,),forecasts,maximum_km=75),origin,day,loading))
    regional=[]
    from map_weather import soaring_samples
    # Preserve hourly evidence throughout the five-day outlook, not just ratings.
    for key,point in SITES.items():
        hours=site_evidence(forecasts,day,origin=point)
        nearest=min(forecasts,key=lambda f:distance_bearing(point,f.location)[0],default=None)
        local=nearby_forecasts((point,),forecasts,maximum_km=75)
        ratings=[]
        for i in range(5):
            forecast_day=today+timedelta(days=i)
            daily=hours if forecast_day==day else site_evidence(forecasts,forecast_day,origin=point)
            rating=asdict(rate_day(local,point,forecast_day,loading))
            rating['hours']=[{k:v for k,v in h.items() if k not in ('upper_winds','wave_bands')} for h in daily['hours']]
            ratings.append(rating)
        sample=[]
        if nearest and distance_bearing(point,nearest.location)[0]<=75:
            sample=soaring_samples((nearest,),datetime.combine(day,time(13),LOCAL_TIMEZONE),loading)
        regional.append({'key':key,'name':point.name,'latitude':point.latitude,'longitude':point.longitude,
            'hours':hours['hours'],'five_days':ratings,'midday':sample[0] if sample else None,'source':hours.get('forecast_point'),
            'source_distance_km':hours.get('distance_km')})
    from bga_turnpoints import Turnpoint
    def geographic(point):return Turnpoint('BRIEF_'+point.key.upper(),point.name,point.latitude,point.longitude,None,source='Geographic briefing reference, not a declared BGA turnpoint')
    circuits=[]
    for name,keys in [('Long Mynd → Goole → Didcot → Church Stretton → Long Mynd',('shropshire','humberside','oxfordshire','church_stretton','shropshire')),
                      ('Snowdonia → South Wales',('snowdonia','south_wales'))]:
        _,row=checked_route(tuple(geographic(SITES[k]) for k in keys),forecasts,day,loading);circuits.append({'name':name,**row})
    explicit=resolve_turnpoints(question,catalog)
    proposed=explicit if len(explicit)>=2 and re.search(r'\b(?:to|via|then|back)\b|→|->',question,re.I) else ()
    route=proposed or active;route_context=None
    if route and len(route)>=2:
        try:_,route_context=checked_route(route,forecasts,day,loading)
        except ValueError:pass
    return finite_data({'day':day,'today':today,'loading':loading,'site':site,'regions':regional,
        'special_forecast':bool(re.search(r'\b(?:soaring|weather) forecast\b',question,re.I)),
        'circuits':circuits,'active_task':[p.code for p in active],'proposed_task':[p.code for p in proposed],
        'task':route_context,'catalog':[{'code':p.code,'name':p.name,'lat':p.latitude,'lon':p.longitude} for p in (*catalog.turnpoints,*LOCAL_POINTS.values())],
        'limits':'Loaded model forecasts; missing evidence remains unknown. No live NOTAM clearance, observed lift or launch approval.'}),proposed


def threshold(values,lower,upper,unit):
    usable=[v for v in values if v is not None]
    if not usable:return 'unknown: missing forecast evidence'
    peak=max(usable)
    answer='yes, reaches the upper threshold' if peak>=upper else 'reaches the lower threshold only' if peak>=lower else 'no, below both thresholds'
    return f'{peak:.1f} {unit} peak; {answer}'


def local_response(question,context):
    site=context['site'];hours=site['hours'];name=site['site'];day=context['day']
    lines=[f"**{name} · {day}**",'Let’s compare the wind, usable lift and the route options.']
    def f(v,d=0):return 'unknown' if v is None else f'{v:.{d}f}'
    lines+=['**Wind and ridge lift**']
    if hours:
        rows=[]
        for h in hours:
            rows.append(f"| {h['time']} | {f(h['wind_kt'])} / {f(h['from_deg'])} | {f(h['gust_kt'])} | {f(h['climb_kt'],1)} | {f(h['cloud_base_ft'])} | {f(h['wave_score'])} |")
        lines.append('| Local hour | Wind kt / from °T | Gust kt | Thermal kt | Base ft AMSL | Wave /100 |\n|---|---|---|---|---|---|\n'+'\n'.join(rows))
        midday=min(hours,key=lambda h:abs(int(h['time'][:2])-13))
        upper='; '.join(f"{u['hpa']} hPa ({u['height_ft']:,} ft): {f(None if u['u_mps'] is None or u['v_mps'] is None else (u['u_mps']**2+u['v_mps']**2)**.5*1.94384)} kt from {f(None if u['u_mps'] is None or u['v_mps'] is None else math.degrees(math.atan2(-u['u_mps'],-u['v_mps']))%360)}°T" for u in midday['upper_winds'])
        lines.append('Upper wind near midday: '+(upper or 'not available')+'.')
        lines.append('**Wave height bands · midday background air motion**\n\n'+ '; '.join(f"{b['altitude_ft']:,} ft: {f(None if b.get('w') is None else b['w']*1.94384,1)} kt" for b in midday['wave_bands'])+'. These are coarse pressure-profile ascent/sink rates. Terrain wave lift and sink are shown separately on Wave Watch.')
        lines.append('Local day rating: '+str(site.get('stars',{}).get('stars') or 'unknown')+' stars.')
        lines.append(f"Forecast source: {site['model']} at {site['forecast_point']}, {site['distance_km']:g} km from this location. Retrieved {site['retrieved']}.")
        face=[h for h in hours if h['ridge_component_kt'] is not None and h['ridge_component_kt']>=5]
        if midday['ridge']:
            lines.append(f"Ridge reference: {midday['ridge']}, facing {midday['ridge_facing_deg']}°T. "+('Wind towards this face exceeds 5 kt at '+', '.join(h['time'] for h in face)+'.' if face else 'The sampled winds do not establish useful flow towards this face.'))
        else:lines.append('No surveyed local ridge orientation is loaded here. Do not transfer the Mynd’s west-ridge rule to this site.')
    else:lines.append('This location has no complete hourly forecast for that day. The regional comparison below shows where evidence exists; wind and launch conditions here remain unknown.')
    lines+=['**Wave, cloud and launch/landing hazards**',
        'Stable air and suitable winds above the ridge can support wave. Read the Wave Watch height bands; these are model estimates, not observed climbs. Missing wave scores are unknown.',
        'Check gusts, crosswind, low cloud, showers and lee-side rotor. Confirm actual launch method, ground run and landing options locally. The short West launch at the Mynd needs current site guidance; forecasts cannot clear it.']
    lines+=['**Where the better sectors lie**', '| Sector | 3.5–4.5 kt thermal target | 4,500–5,000 ft base target | Midday STF | Stars on selected day |','|---|---|---|---|---|']
    ranked=[]
    for row in context['regions']:
        climb=[h['climb_kt'] for h in row['hours']];base=[h['cloud_base_ft'] for h in row['hours']]
        sample=row['midday'] or {};rating=next((r for r in row['five_days'] if r['day']==day),{})
        lines.append(f"| {row['name']} | {threshold(climb,3.5,4.5,'kt')} | {threshold(base,4500,5000,'ft AMSL')} | {f(sample.get('stf'))} kt | {rating.get('stars') or 'unknown'} |")
        if sample.get('pfd') is not None:ranked.append((sample['pfd'],row))
    if ranked:
        best=max(ranked,key=lambda x:x[0])[1]
        lines.append(f"The strongest sampled distance sector is **{best['name']}**. Compare its hourly window before extending that way. Blue sea air and headwind legs can consume the margin.")
        candidates=sorted(context['catalog'],key=lambda p:((p['lat']-best['latitude'])*111)**2+((p['lon']-best['longitude'])*67)**2)[:4]
        lines.append('Nearby turnpoints to inspect: '+', '.join(f"{p['code']} · {p['name']}" for p in candidates)+'. These are options to assess, not cleared turnpoints.')
    lines+=['**Five-day outlook**']
    for row in context['regions']:
        lines.append(row['name']+': '+ '; '.join(f"{r['day']} {str(r['stars'])+'★' if r['stars'] else 'unknown'} / {f(r['distance_km'])} km" for r in row['five_days'])+'.')
    lines+=['**Unpowered Duo Discus · circuit viability**']
    for circuit in context['circuits']:
        lines.append(f"**{circuit['name']}** · {circuit['briefing']} {circuit['coverage']} These geographic reference points are not a BGA declaration.")
    if context.get('task'):
        task=context['task'];lines+=['**Your task · leg tactics**',task['briefing']]
        for leg in sorted({r['leg'] for r in task['hourly_legs']}):
            rows=[r for r in task['hourly_legs'] if r['leg']==leg and not r['missing']]
            r=max(rows,key=lambda r:r['groundspeed_kmh'] or 0,default=None)
            lines.append(f"**{leg}**: "+(f"best sampled thermal {f(r['net_climb_knots'],1)} kt, base {f(r['cloud_base_ft_amsl'])} ft AMSL, STF {f(None if r['cruise_airspeed_kmh'] is None else r['cruise_airspeed_kmh']/1.852)} kt; along-track wind {f(r['along_wind_knots'],1)} kt, crosswind {f(r['cross_wind_knots'],1)} kt. Check low margins before widening this leg." if r else 'weather coverage does not establish a flight time; keep a shorter option and inspect the local hourly forecast.'))
    lines.append('Airspace pinch points need the Task Planner obstacle check and current NOTAMs. Missing evidence cannot prove a route achievable.')
    return re.sub(r'\|\n\n(?=\|)','|\n','\n\n'.join(lines))


def validated_suggestions(response,catalog):
    """Only grounded catalog codes can become clickable proposed routes."""
    points={**catalog.by_code,**LOCAL_POINTS};results=[]
    proposed=response.get('suggested_routes',[])
    if not isinstance(proposed,list):return results
    for row in proposed[:3]:
        if not isinstance(row,dict) or not isinstance(row.get('codes'),list):continue
        codes=row['codes']
        if not 2<=len(codes)<=20 or any(not isinstance(c,str) or c not in points for c in codes):continue
        task=tuple(points[c] for c in codes)
        from task_planning import validate_task
        try:validate_task(task)
        except ValueError:continue
        results.append((str(row.get('name','Suggested task'))[:100],str(row.get('reason',''))[:1000],task))
    return results
