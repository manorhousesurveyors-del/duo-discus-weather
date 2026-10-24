"""Dedicated task map, bounded route import and weather/terrain screening."""
from datetime import timedelta
import html
import math
from pathlib import Path
import json
import numpy as np
import streamlit as st

from config import DUO_DISCUS_POLARS,FORECAST_MODELS,LOCAL_TIMEZONE,METRES_TO_FEET,MPS_TO_KNOTS,GeoPoint
from data_ingestion import JsonHttpClient,IngestionError
from soaring_physics import Profile,evaluate_thermals,distance_bearing,cross_country_speed
from task_planning import sampled_legs,task_distance
from wave_map import destination
from airspace import corridor_intersections,load_airspace,airspace_date_warning


def energy_sample_rows(points):
    rows=[]
    for index,leg in enumerate(sampled_legs(points)):
        bearing=distance_bearing(points[index].point,points[index+1].point)[1];sections=[]
        for j,p in enumerate(leg):
            options=[p]
            if 0<j<len(leg)-1:
                for side in (-1,1):
                    lat,lon=destination(p.latitude,p.longitude,bearing+90*side,10)
                    options.append(GeoPoint(f'energy:{index}:{j}:{side}',f'Leg {index+1} offset {side}',lat,lon))
            sections.append(tuple(options))
        rows.append(tuple(sections))
    return tuple(rows)


def convergence_at(forecasts,point,when):
    samples=[]
    for f in forecasts:
        distance,_=distance_bearing(point,f.location)
        if distance>90:continue
        try:v=f.at(when)
        except KeyError:continue
        speed,direction=v.get('wind_speed_10m'),v.get('wind_direction_10m')
        if speed is None or direction is None:continue
        angle=math.radians(direction)
        samples.append(((f.location.longitude-point.longitude)*111195*math.cos(math.radians(point.latitude)),
                        (f.location.latitude-point.latitude)*111195,-speed*math.sin(angle),-speed*math.cos(angle)))
    if len(samples)<4:return None
    a=np.asarray(samples);design=np.column_stack((a[:,0],a[:,1],np.ones(len(a))))
    if np.linalg.matrix_rank(design)<3:return None
    u=np.linalg.lstsq(design,a[:,2],rcond=None)[0];v=np.linalg.lstsq(design,a[:,3],rcond=None)[0]
    return float(-(u[0]+v[1])*1e4)


def energy_line(points,forecasts,when,loading='dry',airspace=None):
    """Dynamic programming through ±10 km sample corridors; no invented lift bonus."""
    lookup={f.location.key:f for f in forecasts};routes=[]
    from global_land_mask import globe
    def speed(point,bearing):
        f=lookup.get(point.key)
        if f is None:return None
        try:p=Profile.from_forecast(f,when)
        except KeyError:return None
        t=evaluate_thermals(p);wind=p.level(925) or p.level(850)
        if (t.conservative_climb_knots is None or t.conservative_climb_knots<=0 or t.usable_top_ft_amsl is None
            or t.usable_top_ft_amsl/METRES_TO_FEET-f.terrain_elevation_m<500
            or p.values.get('precipitation') is None or p.values['precipitation']>=1 or wind is None or wind.u_mps is None):return None
        if not globe.is_land(point.latitude,point.longitude):return None
        return cross_country_speed(t.conservative_climb_knots/MPS_TO_KNOTS,bearing,wind.u_mps,wind.v_mps,DUO_DISCUS_POLARS[loading])[0]
    for leg_index,sections in enumerate(energy_sample_rows(points)):
        states={sections[0][0].key:(0.,[sections[0][0]])}
        for section in sections[1:]:
            next_states={}
            for b in section:
                candidates=[]
                for elapsed,path in states.values():
                    a=path[-1];distance,bearing=distance_bearing(a,b);sa,sb=speed(a,bearing),speed(b,bearing)
                    if not sa or not sb:continue
                    if corridor_intersections(a,b,airspace,buffer_km=0):continue
                    candidates.append((elapsed+distance/2*(1/sa+1/sb),path+[b]))
                if candidates:next_states[b.key]=min(candidates,key=lambda item:item[0])
            states=next_states
        if not states:routes.append({'leg':leg_index+1,'points':[],'hours':None,'note':'No supported candidate energy line at this hour; missing/blocked weather or mapped airspace.'})
        else:
            elapsed,path=min(states.values(),key=lambda item:item[0])
            routes.append({'leg':leg_index+1,'points':[(p.latitude,p.longitude) for p in path],
                           'hours':elapsed,'note':'Lowest modelled elapsed-time candidate among centre / ±10 km samples. Check airspace, terrain and actual streets.'})
    return routes


@st.cache_data(ttl=86400,show_spinner=False)
def terrain_samples(points):
    """Sample track plus a 2 km lateral corridor, at no more than 5 km spacing."""
    locations=[];legs=[]
    for index,leg in enumerate(sampled_legs(points,spacing_km=5)):
        indices=[];bearing=distance_bearing(points[index].point,points[index+1].point)[1]
        for p in leg:
            for side in (-1,0,1):
                lat,lon=destination(p.latitude,p.longitude,bearing+side*90,2) if side else (p.latitude,p.longitude)
                indices.append(len(locations));locations.append((lat,lon))
        legs.append(indices)
    if len(locations)>5000:raise ValueError('Route terrain sample budget exceeded; shorten the task')
    elevations=[];client=JsonHttpClient()
    for start in range(0,len(locations),100):
        chunk=locations[start:start+100]
        body=client.get_json('https://api.open-meteo.com/v1/elevation',{'latitude':','.join(str(p[0]) for p in chunk),
            'longitude':','.join(str(p[1]) for p in chunk)},ttl_seconds=86400).body
        values=body.get('elevation') if isinstance(body,dict) else None
        if not isinstance(values,list) or len(values)!=len(chunk) or any(v is not None and (isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v)) for v in values):raise IngestionError('Invalid terrain elevation response')
        elevations.extend(values)
    return tuple({'peak_m':max((elevations[j] for j in ids if elevations[j] is not None),default=None),
                  'missing':sum(elevations[j] is None for j in ids),'samples':len(ids)} for ids in legs)


def hazard_report(points,forecasts,assessment,terrain,airspace,when):
    from uk_airfields import load_airfields
    from global_land_mask import globe
    reports=[]
    major=[a for a in load_airfields() if a.kind in ('large_airport','medium_airport') or 'RAF ' in a.name]
    for i,leg in enumerate(sampled_legs(points,spacing_km=5)):
        weather=[r for r in assessment.hourly_legs if r.leg_index==i] if assessment else []
        base=max((r.cloud_base_ft_amsl for r in weather if r.cloud_base_ft_amsl is not None),default=None)
        ground=terrain[i] if terrain else {'peak_m':None,'missing':len(leg),'samples':0}
        clearance=base-ground['peak_m']*METRES_TO_FEET if base is not None and ground['peak_m'] is not None else None
        coastal=False;sea=False
        for p in leg:
            sea|=not bool(globe.is_land(p.latitude,p.longitude))
            coastal|=any(not bool(globe.is_land(*destination(p.latitude,p.longitude,b,15))) for b in (0,90,180,270))
        airfields=[a.name for a in major if min(distance_bearing(p,a.point)[0] for p in leg)<20]
        zones=corridor_intersections(points[i].point,points[i+1].point,airspace)
        reports.append({'Leg':f'{points[i].code} → {points[i+1].code}',
            'Sampled terrain max ft':None if ground['peak_m'] is None else round(ground['peak_m']*METRES_TO_FEET),
            'Best cloud-base margin ft':None if clearance is None else round(clearance),
            'Terrain warning':'Unassessed / missing DEM samples' if clearance is None or ground['missing'] else ('Less than 1,500 ft even at best cloud base' if clearance<1500 else 'Sampled margin only; peaks, masts and rotor are not cleared'),
            'Coast / sea breeze':'Over-water track; landing options reduced' if sea else ('Within 15 km of coast: sea-breeze / sink risk depends on onshore flow' if coastal else 'No coastal proximity flag'),
            'Rain / spreadout':'; '.join(sorted({flag for r in weather for flag in r.risk_flags if 'precipitation' in flag or 'spread' in flag})) or ('No hourly weather available' if not weather else 'No flag in supported hourly samples'),
            'Airspace / danger areas':'; '.join(f"{z.get('name','Unnamed')} ({z.get('class','?')}, {z.get('floor','?')}–{z.get('ceiling','?')})" for z in zones) or ('UNASSESSED: no boundary file loaded' if airspace is None else 'No intersection within 5 km of supplied boundaries; current NOTAMs still required'),
            'Nearby major airfields':', '.join(airfields) or 'None within advisory 20 km radius'})
    return reports


def hourly_details(points,forecasts,assessment):
    lookup={f.location.key:f for f in forecasts};result={}
    for i,leg in enumerate(sampled_legs(points)):
        for when in sorted({r.valid_at for r in assessment.hourly_legs if r.leg_index==i}):
            bases=[]
            for point in leg:
                f=lookup.get(point.key)
                if f is None:continue
                try:v=f.at(when)
                except KeyError:continue
                t,d=v.get('temperature_2m'),v.get('dew_point_2m')
                if t is not None and d is not None:bases.append(max(0,t-d)*400)
            result[i,when.isoformat()]={'cloud_agl':min(bases) if len(bases)==len(leg) else None,
                'convergence':convergence_at(forecasts,leg[len(leg)//2],when)}
    return result


def planner_panel(catalog,day,when,loading,model_key,bucket,forecasts,points,assessment,aviation,*,map_builder,recorded=False):
    from streamlit_folium import st_folium
    from planning_ui import process_map_event,active_task,set_task,weather_table,fetch_corridor
    from task_import import import_task
    from route_tools import complete_task,signature
    from airspace import import_airspace,LOCAL_PATH
    st.subheader('Task planning map')
    st.caption('Click BGA points or airfields in order. Panning, zooming and hover reports stay in your browser.')
    with st.expander('Import a complete CUP / XCSoar task, or airspace boundaries'):
        upload=st.file_uploader('Load an existing task',type=['cup','tsk'],key='planner_task_upload',help='Import one complete SeeYou Related Tasks declaration or an XCSoar Task. Waypoint-only CUP files are rejected to avoid inventing a route order.')
        if st.button('Load uploaded task',disabled=upload is None,key='planner_import',help='Replace this date’s task with the uploaded Start–Turnpoints–Finish sequence. Review imported observation zones in your instrument.'):
            try:
                imported=import_task(upload.getvalue(),Path(upload.name).suffix,catalog=catalog.turnpoints)
                set_task(day,imported,rerun=False);complete_task(day,imported);st.rerun()
            except ValueError as exc:st.error(str(exc))
        boundaries=st.file_uploader('Current airspace boundaries',type=['txt','air','json','geojson'],key='planner_airspace_upload',help='Load current OpenAir or polygon GeoJSON with class, name, floor and ceiling fields. This enables route intersection checks; NOTAM activity still needs an official check.')
        if st.button('Use uploaded airspace',disabled=boundaries is None,help='Validate and save this supplied boundary file locally for route analysis. Existing data is retained if validation fails.'):
            try:
                body=import_airspace(boundaries.getvalue(),boundaries.name);LOCAL_PATH.parent.mkdir(exist_ok=True);LOCAL_PATH.write_text(json.dumps(body));st.rerun()
            except (ValueError,OSError) as exc:st.error(str(exc))
    try:airspace=load_airspace(day)
    except (ValueError,OSError) as exc:st.warning(f'Airspace file unavailable: {exc}');airspace=None
    date_warning=airspace_date_warning(airspace,day)
    if date_warning:st.warning(date_warning)
    elif airspace:st.caption(f"Airspace: AIRAC {airspace['effective_date']} · valid through {airspace['valid_until']} · {len(airspace['features']):,} boundary volumes. Current activation still needs a NOTAM check.")
    request_key=(signature(points),day.isoformat(),when.isoformat(),loading,model_key,airspace.get('content_hash') if airspace else None)
    report=st.session_state.get('route_analysis')
    if report and report['key']!=request_key:report=None
    view,_=map_builder(forecasts,when,layers=[],task=points,turnpoints=catalog.turnpoints,visual_mode='route',
        map_identity='planner',loading=loading,anchor=when,aviation=aviation,show_airfields=True,live_airfields=not recorded,selected_airfield=st.session_state.get('airfield_popup:last_planner_event'))
    if airspace:
        import folium
        from airspace import display_airspace
        display=display_airspace(airspace)
        display={**display,'features':[{**f,'properties':{k:html.escape(v) if isinstance(v,str) else v for k,v in f['properties'].items()}} for f in display['features']]}
        folium.GeoJson(display,name='Airspace / danger boundaries · simplified display',show=False,
            style_function=lambda _:dict(color='#b13655',weight=1,fillOpacity=.06),
            tooltip=folium.GeoJsonTooltip(fields=['name','class','floor','ceiling'],aliases=['Name','Class','Lower limit','Upper limit'])).add_to(view)
    if report:
        import folium
        for line in report['energy']:
            if line['points']:
                folium.PolyLine(line['points'],color='#12b596',weight=5,dash_array='8 5',tooltip=folium.Tooltip('Candidate energy line: '+html.escape(line['note']))).add_to(view)
    # Folium's layer menu references every overlay at render time. It must be
    # emitted after the planner's extra layers so their JS variables exist.
    from client_map import reorder_layer_control
    reorder_layer_control(view)
    def on_click():process_map_event(st.session_state.get('planner_map',{}),catalog,day,'Add to task',event_key='last_planner_event',visual_override='route')
    interaction=st_folium(view,height=620,use_container_width=True,pixelated=False,key='planner_map',on_change=on_click,wrap_longitude=False,returned_objects=['last_object_clicked','last_clicked'])
    if interaction and interaction!=st.session_state.get('last_planner_event'):
        old=active_task(day);process_map_event(interaction,catalog,day,'Add to task',event_key='last_planner_event',visual_override='route')
        if active_task(day)!=old:st.rerun()
    requested=st.session_state.pop('analyse_client_task',False)
    if st.button('Analyse Task & Route Weather',type='primary',disabled=len(points)<2,width='stretch',key='analyse_route',help='Analyse the saved route. For an unsaved browser draft, use Analyse / save on the map. Current airspace activation needs a NOTAM check.') or (requested and len(points)>=2):
        try:
            from task_planning import assess_task,nearby_forecasts
            samples=tuple({p.key:p for leg in energy_sample_rows(points) for row in leg for p in row}.values())
            with st.spinner('Analysing route weather, terrain and candidate energy lines…'):
                if recorded:sample_forecasts=nearby_forecasts(samples,forecasts,maximum_km=15);terrain=None;failures=('Recorded mode: no live DEM query.',)
                else:
                    batch=fetch_corridor(samples,model_key,bucket);sample_forecasts=batch.forecasts;failures=tuple(f.message for f in batch.failures)
                    try:terrain=terrain_samples(points)
                    except (IngestionError,ValueError) as exc:terrain=None;failures+=(f'Terrain: {exc}',)
                evaluated=assess_task(points,sample_forecasts,day,loading,preference=(FORECAST_MODELS[model_key].api_name,))
                st.session_state['route_analysis']={'key':request_key,'assessment':evaluated,
                    'energy':energy_line(points,sample_forecasts,when,loading,airspace),
                    'hazards':hazard_report(points,sample_forecasts,evaluated,terrain,airspace,when),
                    'hourly_details':hourly_details(points,sample_forecasts,evaluated),
                    'failures':failures,'airspace_source':airspace.get('source','User-supplied boundaries') if airspace else None}
            st.rerun()
        except (ValueError,IngestionError) as exc:st.error(str(exc))
    if not report:return
    result=report['assessment'];st.subheader('Operational route synopsis')
    from planning_ui import task_result
    task_result(points,result,key='analysis')
    if result.best:
        weather_table([{'Leg':f'{points[i].code} → {points[i+1].code}','STF kt':leg.cruise_airspeed_kmh/1.852,
            'XC km/h':leg.groundspeed_kmh,'XC kt':leg.groundspeed_kmh/1.852,'Elapsed min':leg.elapsed_hours*60} for i,leg in enumerate(result.best.legs)],
            {'Leg':'Ordered route leg.','STF kt':'MacCready inter-thermal airspeed in knots.','XC km/h':'Leg ground progress including climb time and wind.','XC kt':'The same ground speed in knots.','Elapsed min':'Integrated leg elapsed time in minutes.'})
    weather_table([{'Time':r.valid_at.astimezone(LOCAL_TIMEZONE).strftime('%H:%M %Z'),'Leg':r.leg,'Climb kt':r.net_climb_knots,
        'Cloud base ft AMSL':r.cloud_base_ft_amsl,'Cloud base ft AGL':report['hourly_details'].get((r.leg_index,r.valid_at.isoformat()),{}).get('cloud_agl'),
        'Usable depth ft AGL':None if r.usable_depth_m is None else r.usable_depth_m*METRES_TO_FEET,
        'Along wind kt':r.along_wind_knots,'Convergence':report['hourly_details'].get((r.leg_index,r.valid_at.isoformat()),{}).get('convergence'),'Risk':'; '.join(r.risk_flags)} for r in result.hourly_legs],
        {'Time':'Hourly weather in Europe/London.','Leg':'Task leg sampled along the corridor.','Climb kt':'Conservative net thermal climb.','Cloud base ft AMSL':'Estimated condensation height above mean sea level.','Cloud base ft AGL':'Lowest potential condensation height above model ground across this leg, from temperature minus dewpoint. Missing corridor samples stay blank.','Usable depth ft AGL':'Usable thermal depth above model terrain, which may stop below cloud base.','Along wind kt':'Positive tailwind, negative headwind.','Convergence':'Hourly local surface-wind plane-fit convergence in 10⁻⁴ s⁻¹; a sampled estimate, not a resolved thermal street.','Risk':'Rain, spreadout, shallow mixing or unavailable weather.'})
    st.subheader('Route obstacles and hazards')
    weather_table(report['hazards'],{'Leg':'Route endpoints.','Sampled terrain max ft':'Maximum DEM sample across track and ±2 km at 5 km intervals; narrow peaks/masts can be missed.','Best cloud-base margin ft':'Highest supported hourly cloud base minus sampled terrain maximum; actual clearance can be lower.','Terrain warning':'Screening result, not a terrain-clearance guarantee.','Coast / sea breeze':'Coastal proximity or water crossing. Onshore winds and boundary layers determine actual sink.','Rain / spreadout':'Flags found in hourly task weather.','Airspace / danger areas':'Horizontal intersections within 5 km of supplied polygons, with published vertical limits. Activation/NOTAMs and clearance are separate.','Nearby major airfields':'Traffic/airspace advisory within 20 km, not the shape or extent of controlled airspace.'})
    if not report['airspace_source']:st.warning('Airspace and danger-area boundary checks are UNASSESSED. Upload a current OpenAir/GeoJSON file above; nearby-airfield warnings are only a proximity screen.')
    else:st.caption('Airspace source: '+str(report['airspace_source'])+'. Verify effective dates, vertical limits and current NOTAMs.')
    st.caption('Green dashed lines show weather-supported candidate deviations at the selected hour. They do not establish terrain, airspace or landing clearance.')
    for line in report['energy']:st.caption(f'Leg {line["leg"]}: '+line['note'])
    for failure in report['failures']:st.warning(failure)
