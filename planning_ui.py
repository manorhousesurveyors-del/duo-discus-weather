"""Streamlit task builder, catalog maintenance and forecast-grounded briefing UI."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import json
import re

import pandas as pd
import streamlit as st

import bga_turnpoints as bga
from briefing_bot import answer_question, explain_task, format_windows, parse_intent, shortlist_tasks
from config import FORECAST_MODELS, LOCAL_TIMEZONE, ROUTES, GeoPoint
from data_ingestion import OpenMeteoClient, IngestionError
from soaring_physics import distance_bearing
from task_planning import (add_task_point, assess_task, coordinate_turnpoint,
    corridor_points, export_cup, export_tsk, load_daily_task, nearby_forecasts, reorder_task,
    save_daily_task, task_distance, validate_task)


def weather_table(records, descriptions, **kwargs):
    """Every visible table column has a concrete hover explanation."""
    frame=pd.DataFrame(records)
    missing=set(frame.columns)-set(descriptions)
    if missing: raise ValueError('Missing table column help: '+', '.join(sorted(missing)))
    st.dataframe(frame,hide_index=True,width='stretch',
                 column_config={name:st.column_config.Column(name,help=descriptions[name]) for name in frame.columns},**kwargs)


def current_catalog():
    try:
        from app_cache import catalog_resource,file_version
        return catalog_resource(file_version((bga.CATALOG_PATH,bga.LOCAL_CATALOG_PATH,bga.IMPORT_PATH)))
    except (OSError,ValueError,KeyError) as exc:
        st.warning(f'BGA catalog unavailable: {exc}. Use Data quality to check sources or import a waypoint file.')
        return bga.Catalog((),'Unavailable','',bga.content_hash(()))


def active_task(day):
    """One active route, with separate persisted plans retained for other dates."""
    date_key=day.isoformat();tasks=st.session_state.setdefault('tasks_by_day',{})
    if date_key not in tasks:
        try:tasks[date_key]=load_daily_task(day)
        except (OSError,ValueError,KeyError) as exc:
            st.warning(f'Saved task could not be read: {exc}');tasks[date_key]=()
    if st.session_state.get('active_task_day')!=date_key:
        st.session_state['active_task_points']=tuple(tasks[date_key])
        st.session_state['active_task_day']=date_key
        st.session_state['task_version']=st.session_state.get('task_version',0)+1
    return tuple(st.session_state.get('active_task_points',()))


def set_task(day,points,*,rerun=True,client_version=None):
    active_task(day)
    if client_version is not None and client_version<=st.session_state.get('task_version',0):return False
    points=tuple(points);save_daily_task(day,points)
    st.session_state['active_task_points']=points
    st.session_state['active_task_day']=day.isoformat()
    st.session_state['task_version']=max(st.session_state.get('task_version',0)+1,client_version or 0)
    st.session_state.setdefault('tasks_by_day',{})[day.isoformat()]=points
    st.session_state['task_message']=f'Saved {len(points)} task points for {day:%d %b}.'
    for key in ('route_analysis','_weather_map_state','published_tasks'):
        st.session_state.pop(key,None)
    st.session_state.setdefault('completed_tasks',{}).pop(day.isoformat(),None)
    if len(points)>=3 and points[0]==points[-1]:
        from route_tools import complete_task
        try:complete_task(day,points)
        except ValueError:pass
    if rerun:st.rerun()
    return True


def task_payload():
    import uuid
    st.session_state.setdefault('task_session',str(uuid.uuid4()))
    from datetime import date
    day=st.session_state.get('active_task_day')
    points=tuple(st.session_state.get('active_task_points',()))
    from route_tools import is_complete
    return {'session':st.session_state['task_session'],'date':day,
        'version':st.session_state.get('task_version',0),
        'points':[{'code':p.code,'name':p.name,'lat':p.latitude,'lon':p.longitude,'elevation_m':p.elevation_m} for p in points],
        'complete':bool(day and is_complete(date.fromisoformat(day),points))}


def preset_task(catalog,route_key):
    points=[]
    for waypoint in ROUTES[route_key].waypoints:
        if not catalog.turnpoints: raise ValueError('Load the BGA catalog first')
        candidates=[p for p in catalog.turnpoints if p.official]
        point=min(candidates,key=lambda p:distance_bearing(waypoint,p.point)[0])
        if distance_bearing(waypoint,point.point)[0]>15:
            raise ValueError(f'No BGA turnpoint close to {waypoint.name}')
        points.append(point)
    return tuple(points)


def task_builder(catalog,day,*,location):
    prefix=f'{location}_{day.isoformat()}'
    points=active_task(day)
    st.subheader('BGA task builder')
    st.caption(f'{day:%a %d %b} · {len(points)} points · {task_distance(points):.0f} km')
    query=st.text_input('Search by BGA trigraph or name',key=prefix+'_query',
        help='Enter an official three-character code or part of a full name. Search results come from the current catalog, including explicitly imported points.')
    matches=bga.search_turnpoints(catalog.turnpoints,query)
    lookup=catalog.by_code
    selected=st.selectbox('Turnpoint', [p.code for p in matches],index=None,key=prefix+'_point',
        format_func=lambda code:f'{code} · {lookup[code].name}',
        help='Select a BGA point to add. Its official coordinates and elevation are retained in your task and exports.')
    role=st.selectbox('Add as',['Append','Start','Turnpoint','Finish'],key=prefix+'_role',
        help='Append builds a sequence in click order; Start/Finish replace those endpoints. Turnpoint inserts immediately before the current finish.')
    if location=='sidebar':
        st.session_state['task_add_role']=role
    if st.button('Add selected turnpoint',key=prefix+'_add',disabled=selected is None,width='stretch',
                 help='Apply the chosen point and role to this day’s task, then recalculate the corridor forecast.'):
        try: set_task(day,add_task_point(points,lookup[selected],role))
        except ValueError as exc: st.error(str(exc))
    sequence=st.text_input('Or enter a full sequence',key=prefix+'_sequence',placeholder='MYN … MYN',
        help='Separate trigraphs with spaces, commas or hyphens. Loading replaces this day’s ordered task; repeat the start code at the end for a closed task.')
    if st.button('Load entered sequence',key=prefix+'_sequence_load',disabled=not sequence.strip(),
                 help='Resolve every entered code against the catalog and load the complete sequence. Unknown codes leave your existing task unchanged.'):
        codes=[c.upper() for c in re.split(r'[\s,;>–—-]+',sequence.strip()) if c]
        unknown=[c for c in codes if c not in lookup]
        if unknown: st.error('Unknown codes: '+', '.join(unknown))
        elif len(codes)>20: st.error('Maximum 20 task points.')
        else: set_task(day,tuple(lookup[c] for c in codes))
    cols=st.columns(2)
    if cols[0].button('MYN start / finish',key=prefix+'_mynd',disabled='MYN' not in lookup,
        help='Set both endpoints to Long Mynd MYN, preserving any interior turnpoints. Add a turnpoint before evaluating an initially empty return task.'):
        set_task(day,(lookup['MYN'],*points[1:-1],lookup['MYN']))
    if cols[1].button('Close at start',key=prefix+'_close',disabled=not points,
        help='Append your first point as the finish. This creates an explicit return leg instead of assuming you will return home.'):
        if len(points)<2 or points[-1]!=points[0]: set_task(day,(*points,points[0]))
    if points:
        index=st.selectbox('Point to reorder / remove',list(range(len(points))),key=prefix+'_edit',
            format_func=lambda i:f'{i+1}. {points[i].code} · {points[i].name}',
            help='Choose the point to move or remove. The first and last remaining points become Start and Finish.')
        buttons=st.columns(3)
        if buttons[0].button('↑ Up',key=prefix+'_up',disabled=index==0,help='Move this point one place earlier in the task sequence.'):
            set_task(day,reorder_task(points,index,-1))
        if buttons[1].button('↓ Down',key=prefix+'_down',disabled=index==len(points)-1,help='Move this point one place later in the task sequence.'):
            set_task(day,reorder_task(points,index,1))
        if buttons[2].button('Remove',key=prefix+'_remove',help='Remove only the selected point and recalculate the remaining legs.'):
            set_task(day,points[:index]+points[index+1:])
    if st.button('Clear task',key=prefix+'_clear',disabled=not points,width='stretch',
                 help='Clear this day’s task and remove its map legs. Tasks saved for other days are preserved.'):
        set_task(day,())
    with st.expander('Optional presets'):
        st.caption('A preset loads only when you click its button.')
        for key in ('A','B'):
            if st.button('Load '+('Long Mynd triangle' if key=='A' else 'Snowdonia–South Wales'),key=prefix+'_preset_'+key,
                         help='Explicitly replace this day’s task with BGA points nearest the legacy template. Inspect the resolved turnpoints before flying.'):
                try: set_task(day,preset_task(catalog,key))
                except ValueError as exc: st.error(str(exc))


@st.cache_data(ttl=1800,show_spinner=False)
def fetch_corridor(points: tuple[GeoPoint, ...], model_key: str, refresh_bucket: int):
    return OpenMeteoClient().fetch_batched(points,(FORECAST_MODELS[model_key],))


def task_weather(points,base_forecasts,model_key,bucket,*,recorded=False):
    samples=corridor_points(points)
    if recorded:
        return nearby_forecasts(samples,base_forecasts,maximum_km=15), ('Recorded mode: only stored weather within 15 km is used; other corridor samples remain unavailable.',)
    batch=fetch_corridor(samples,model_key,bucket)
    return batch.forecasts,tuple(f'{f.target}: {f.message}' for f in batch.failures)


def task_result(points,assessment,*,key='map'):
    if not points:
        st.info('Build a task in the sidebar or below. The map starts without a preset route.')
        return
    weather_table([{'Order':i+1,'Role':'Start' if i==0 else ('Finish' if i==len(points)-1 else 'Turnpoint'),
                    'Code':p.code,'Name':p.name,'Elevation m':p.elevation_m,
                    'Leg km':None if i==0 else distance_bearing(points[i-1].point,p.point)[0]} for i,p in enumerate(points)],
        {'Order':'Task sequence; flights follow this order.','Role':'First point is Start and last is Finish; interior points are turnpoints.',
         'Code':'Official BGA trigraph or explicit custom-coordinate identifier.','Name':'Full source name; verify the intended physical feature.',
         'Elevation m':'Published turnpoint elevation in metres AMSL. Blank means no elevation was supplied.',
         'Leg km':'Great-circle distance from the preceding point, excluding airspace detours.'})
    try: validate_task(points)
    except ValueError as exc:
        st.warning(str(exc)); return
    columns=st.columns(2)
    columns[0].download_button('Export SeeYou .cup',export_cup(points,bga_zones=True),'duo-task.cup','text/plain',key=key+'_export_cup',
        help='Download the selected waypoint definitions and ordered SeeYou task. Check observation zones and competition requirements in your flight computer.')
    columns[1].download_button('Export XCSoar .tsk',export_tsk(points,bga_zones=True),'duo-task.tsk','application/xml',key=key+'_export_tsk',
        help='Download an XCSoar racing task with a 5 km start line, BGA fixed-course turning zones and a 1 km finish ring. Review these default zones and aircraft settings before use.')

    if assessment is None:
        st.warning('Task assessment is unavailable.'); return
    banner=f'{assessment.verdict} · {assessment.distance_km:.0f} km'
    if assessment.verdict=='FULLY ACHIEVABLE': st.success(banner)
    elif assessment.best or assessment.verdict=='INSUFFICIENT DATA': st.warning(banner)
    else: st.error(banner)
    cols=st.columns(4); best=assessment.best
    cols[0].metric('Estimated elapsed','—' if best is None else f'{best.elapsed_hours:.1f} h',
        help='Modeled time from launch to finish, including thermal climb and glide. Operational launch, landing and airspace allowances require separate planning.')
    cols[1].metric('Launch window',format_windows(assessment),
        help='Supported launch times in Europe/London, checked at 15-minute intervals. A narrow window or less than 30 minutes finish reserve makes the verdict marginal.')
    cols[2].metric('Average MacCready','—' if best is None else f'{best.average_maccready_kmh:.0f} km/h',
        help='Time-weighted cruise airspeed from the selected Duo Discus polar and conservative net climb. This differs from average task ground speed.')
    cols[3].metric('Task ground speed','—' if best is None else f'{best.average_groundspeed_kmh:.0f} km/h',
        help='Total task distance divided by modeled elapsed time, including climb time, headwind and crosswind correction.')
    if best: st.warning(f'Weakest leg: {best.weakest_leg}'+(' · '+', '.join(best.risk_flags) if best.risk_flags else ' · lowest achieved leg ground speed.'))
    st.caption(f'Modelled corridor assessment · {assessment.sample_count} weather sample points. '+assessment.warnings[0])
    with st.expander('Every leg, every hour'):
        weather_table([{'Time':r.valid_at.astimezone(LOCAL_TIMEZONE).strftime('%H:%M'),'Leg':r.leg,
            'Ground km/h':r.groundspeed_kmh,'Cruise km/h':r.cruise_airspeed_kmh,'STF kt':None if r.cruise_airspeed_kmh is None else r.cruise_airspeed_kmh/1.852,'Net climb kt':r.net_climb_knots,
            'Usable depth m':r.usable_depth_m,'Cloud base ft AMSL':r.cloud_base_ft_amsl,
            'Along wind kt':r.along_wind_knots,'Crosswind kt':r.cross_wind_knots,'Streets':r.cloud_streets,
            'Notes':'; '.join(r.risk_flags)} for r in assessment.hourly_legs],
            {'Time':'Hourly Europe/London forecast time; the last row describes the end of the soaring window.',
             'Leg':'Task endpoints; weather is also sampled between them at intervals no larger than 35 km.',
             'Ground km/h':'Harmonic leg progress including climb time and vector wind correction. Zero means a blocking condition; blank means missing weather.',
             'Cruise km/h':'MacCready cruise airspeed averaged along this leg; bounded to the supported polar fit.',
             'STF kt':'Recommended MacCready cruise airspeed in knots for this leg and hour, using the selected wing loading.',
             'Net climb kt':'Lowest conservative net thermal climb along this leg, after the 1.6 kt circling sink deduction.',
             'Usable depth m':'Minimum usable thermal height above model terrain. The default hard minimum is 500 m AGL.',
             'Cloud base ft AMSL':'Lowest estimated condensation height along the leg; usable mixing top can be lower.',
             'Along wind kt':'Positive is tailwind along this leg; negative is headwind. Both climb and glide drift enter the speed calculation.',
             'Crosswind kt':'Maximum crosswind magnitude sampled along the leg, used to evaluate ground-track progress.',
             'Streets':'At least one sample meets the wind/shear street heuristic. This is not an assumed speed bonus.',
             'Notes':'Coverage problems, shallow mixing, precipitation, spread-out or bounded polar speed for this leg/hour.'})
        for note in assessment.warnings: st.caption(note)


def process_map_event(interaction,catalog,day,mode,*,event_key='last_map_event',visual_override=None):
    """Called before sidebar widgets rerender; old map payloads cannot re-add points."""
    previous=st.session_state.get(event_key,{})
    st.session_state[event_key]=interaction.copy()
    visual=visual_override or st.session_state.get('visual_mode')
    point=None; coordinate=None
    tags=list(interaction.get('selected_tags') or [])
    clicked=interaction.get('last_object_clicked') or {}
    if clicked.get('duo') and clicked!=previous.get('last_object_clicked'):
        command=clicked['duo'];kind=command.get('kind');value=command.get('value','')
        if kind=='briefing_point':
            try:
                data=json.loads(value)
                st.session_state['briefing_point']=GeoPoint('clicked',str(data.get('name','Clicked map location'))[:100],float(data['lat']),float(data['lon']))
            except (ValueError,TypeError,KeyError):pass
            return
        if kind=='task_sync':
            from uk_airfields import airfield_by_id
            from route_tools import complete_task
            try:
                if len(value)>20000:raise ValueError('Task message is too large')
                draft=json.loads(value)
                if not isinstance(draft,dict):raise ValueError('Invalid task draft')
                if draft.get('date')!=day.isoformat():raise ValueError('Draft belongs to another forecast day')
                if not isinstance(draft.get('points'),list) or len(draft['points'])>20:raise ValueError('Maximum 20 task points')
                selected=[]
                for item in draft['points']:
                    point=catalog.by_code.get(item['code'])
                    if point is None:
                        from briefing_bot import LOCAL_POINTS
                        point=LOCAL_POINTS.get(item['code'])
                    if point is None:
                        airfield=airfield_by_id(item['code'])
                        point=airfield.turnpoint if airfield else coordinate_turnpoint(float(item['lat']),float(item['lon']))
                    selected.append(point)
                validate_task(selected,allow_incomplete=True)
                client_version=draft.get('version')
                if client_version is not None and (not isinstance(client_version,int) or client_version<0):raise ValueError('Invalid task version')
                if draft.get('session') and draft['session']!=st.session_state.get('task_session'):raise ValueError('Task belongs to another session')
                if not set_task(day,selected,rerun=False,client_version=client_version):return
                if draft.get('briefing_point'):
                    p=draft['briefing_point'];st.session_state['briefing_point']=GeoPoint('clicked',p.get('name','Clicked point'),p['lat'],p['lon'])
                if draft.get('complete') and len(selected)>=2:complete_task(day,selected)
                if draft.get('action') in ('finish','analyse','optimize','clear'):st.session_state['_task_full_refresh']=True
                if draft.get('action')=='analyse':st.session_state['analyse_client_task']=True
                if draft.get('action')=='optimize':st.session_state['open_optimizer']=True
            except (ValueError,TypeError,KeyError,OSError) as exc:st.session_state['task_error']=str(exc)
            return
        if kind=='airfield_report':
            from uk_airfields import airfield_by_id
            if airfield_by_id(value):st.session_state['airfield_popup:'+event_key]=value
            return
        st.session_state.pop('airfield_popup:'+event_key,None)
        if kind=='thermals':
            layers=[layer for layer in st.session_state.get('map_layers',[]) if layer!='Thermals']
            if value=='on':layers.append('Thermals')
            st.session_state['map_layers']=layers
            return
        tags.append(f'duo:{kind}:{value}:{command.get("nonce","")}')
        if kind=='airfield':
            from uk_airfields import airfield_by_id
            airfield=airfield_by_id(value)
            if airfield:
                st.session_state['selected_airfield']=airfield.ident
                st.session_state['airfield_popup:'+event_key]=airfield.ident
                point=airfield.turnpoint;coordinate=(point.latitude,point.longitude)
    fresh=[str(tag) for tag in tags if tag not in (previous.get('selected_tags') or [])]
    from map_modes import MODES
    from route_tools import complete_task
    for tag in fresh:
        if tag.startswith('duo:radar:') and visual=='radar':
            try:
                fields=tag.split(':')
                selected=GeoPoint('radar-click','Radar click',float(fields[2]),float(fields[3]))
                coordinate=(selected.latitude,selected.longitude)
            except (IndexError,ValueError):
                pass
        if tag.startswith('duo:mode:'):
            selected_mode=tag.split(':')[2]
            if selected_mode in MODES: st.session_state['visual_mode']=selected_mode
            return
        if tag.startswith('duo:action:'):
            action=tag.split(':')[2];points=active_task(day)
            try:
                if action=='undo':set_task(day,points[:-1],rerun=False)
                elif action=='clear':set_task(day,(),rerun=False)
                elif action=='finish':complete_task(day,points)
                elif action=='home':
                    if len(points)<2:raise ValueError('Choose Start and at least one turnpoint first')
                    if points[-1]!=points[0]:points=(*points,points[0]);set_task(day,points,rerun=False)
                    complete_task(day,points)
                elif action=='optimize':st.session_state['open_optimizer']=True
            except (ValueError,OSError) as exc:st.session_state['task_error']=str(exc)
            return
    selection=next((tag for tag in tags if str(tag).startswith('duo:point:') and
                    tag not in (previous.get('selected_tags') or [])),None)
    if selection:
        code=str(selection).split(':')[2]
        point=catalog.by_code.get(code)
        if point: coordinate=(point.latitude,point.longitude)
    marker=interaction.get('last_object_clicked'); count=interaction.get('last_object_clicked_count')
    background=interaction.get('last_clicked')
    marker_changed=marker and (count!=previous.get('last_object_clicked_count') or marker!=previous.get('last_object_clicked'))
    if coordinate:
        pass  # The explicit catalog selection takes priority over bubbled events.
    elif marker_changed:
        coordinate=(float(marker['lat']),float(marker['lng']))
        tip=str(interaction.get('last_object_clicked_tooltip') or '')
        match=re.search(r'(?:\bBGA |\bImported |(?:Start|Finish|Turnpoint) \d+: )([A-Z0-9_-]{1,24})\b',tip)
        if match: point=catalog.by_code.get(match[1])
    elif background and background!=previous.get('last_clicked'):
        coordinate=(float(background['lat']),float(background['lng']))
    if interaction.get('center'):
        centre=interaction['center']; st.session_state['map_centre']=(centre['lat'],centre['lng'])
    if interaction.get('zoom'): st.session_state['map_zoom']=interaction['zoom']
    thermal=next((tag for tag in tags if str(tag).startswith('duo:thermals:')),None)
    if thermal and thermal not in (previous.get('selected_tags') or []):
        layers=list(st.session_state.get('map_layers',[]))
        if thermal.endswith(':on') and 'Thermals' not in layers: layers.append('Thermals')
        if thermal.endswith(':off'): layers=[layer for layer in layers if layer!='Thermals']
        st.session_state['map_layers']=layers
    if coordinate and visual=='radar':
        st.session_state['radar_coordinate']=coordinate
        return
    if coordinate and visual in ('wind','thermal','pfd','xc','stf','wave'):
        return  # These modes handle point flags inside the map without a weather fetch/rerender.
    if coordinate:
        st.session_state['inspector_coordinate']=coordinate
        st.session_state['inspector_selection']='Map coordinate'
        if mode=='Add to task' or visual=='route':
            try:
                point=point or coordinate_turnpoint(*coordinate)
                new_points=add_task_point(active_task(day),point,'Append' if visual=='route' else st.session_state.get('task_add_role','Append'))
                validate_task(new_points,allow_incomplete=True)
                set_task(day,new_points,rerun=False)
                if len(new_points)>=3 and new_points[-1]==new_points[0]:complete_task(day,new_points)
            except (ValueError,OSError) as exc: st.session_state['task_error']=str(exc)


def catalog_panel(catalog):
    st.subheader('BGA turnpoint database')
    st.metric('Catalog points',f'{len(catalog.turnpoints):,}',
        help='Official bundled/synchronized BGA records plus any explicit local import. Source and elevation are retained; imported overrides are marked separately.')
    st.caption(catalog.attribution)
    st.caption(f'Source: {catalog.source} · retrieved {catalog.retrieved_at or "unknown"} · '+
               f'{sum(not p.official for p in catalog.turnpoints)} user-supplied records')
    if st.button('Check for BGA Turnpoint Updates',help='Fetch approved public BGA/UK waypoint sources and validate a complete UK list before atomically replacing the local catalog. Failed or partial downloads preserve the existing data.'):
        try:
            with st.spinner('Checking BGA waypoint sources…'):
                baseline=bga.load_catalog(bga.CATALOG_PATH) if bga.CATALOG_PATH.exists() else None
                result=bga.update_catalog(bga.LOCAL_CATALOG_PATH,baseline=baseline)
            st.session_state['bga_result']=('Updated' if result.changed else 'No changes')+f': {result.count} points; {result.added} added, {result.removed} removed, {result.modified} changed.'
            st.rerun()
        except (OSError,ValueError,KeyError) as exc: st.error(str(exc))
    if st.session_state.get('bga_result'): st.info(st.session_state['bga_result'])
    upload=st.file_uploader('Import a BGA / SeeYou waypoint file',type=['cup','csv','json'],
        help='Upload UTF-8 or Windows-ANSI CUP/CSV, normalized JSON or GeoJSON points. Imported codes override matching catalog entries locally and are marked user supplied.')
    if st.button('Use uploaded turnpoints',disabled=upload is None,
                 help='Validate the entire uploaded waypoint section and save a local import. Official bundled data remains available when this import is removed.'):
        try:
            points=bga.parse_turnpoints(upload.getvalue(),Path(upload.name).suffix,source='User upload: '+Path(upload.name).name)
            catalog=bga.Catalog(points,'User upload: '+Path(upload.name).name,datetime.now(UTC).isoformat(),bga.content_hash(points))
            bga.save_catalog(catalog,bga.IMPORT_PATH)
            st.session_state['bga_result']=f'Imported {len(points)} user-supplied turnpoints.'
            st.rerun()
        except (OSError,ValueError,KeyError) as exc: st.error(str(exc))
    if st.button('Remove local import',disabled=not bga.IMPORT_PATH.exists(),
                 help='Remove user-imported overrides and return to the official bundled or synchronized catalog. Saved task coordinates remain as previously selected.'):
        bga.IMPORT_PATH.unlink(missing_ok=True); st.rerun()
    with st.expander('Browse turnpoint records'):
        weather_table([{'Code':p.code,'Name':p.name,'Latitude':p.latitude,'Longitude':p.longitude,
                        'Elevation m':p.elevation_m,'Source type':'BGA source' if p.official else 'User supplied'} for p in catalog.turnpoints],
            {'Code':'BGA trigraph, or an explicit user identifier for imported points.','Name':'Full published or imported waypoint name.',
             'Latitude':'WGS84 decimal latitude, positive north.','Longitude':'WGS84 decimal longitude, positive east.',
             'Elevation m':'Source elevation in metres above mean sea level; missing is never replaced with zero.',
             'Source type':'BGA source records passed whole-catalog validation. User supplied means the coordinates have not been verified against that source.'})


def briefing_panel(catalog,day,loading,model_key,bucket,forecasts,assessment,classification,waves,*,recorded=False,today=None):
    import hashlib
    from briefing_bot import briefing_date,gemini_key
    from gemini_rest import GeminiREST,Connection,GeminiError
    from flight_briefing import build_context,local_response,validated_suggestions
    st.subheader('Help & Briefing Bot')
    st.caption('Ask freely about weather, flying sectors, a clicked location or any task. Route proposals are checked before you load them.')
    try: secret=st.secrets.get('GEMINI_API_KEY','')
    except (FileNotFoundError,st.errors.StreamlitSecretNotFoundError):secret=''
    with st.expander('Gemini settings · connection and models'):
        if 'gemini_runtime_key' not in st.session_state:st.session_state['gemini_runtime_key']=st.session_state.get('_gemini_runtime_saved','')
        runtime=st.text_input('GEMINI_API_KEY',type='password',key='gemini_runtime_key',help='Google API key or bearer token. Questions and forecast context go to Google only when you ask. The credential remains in server session memory.')
        st.session_state['_gemini_runtime_saved']=runtime
        auth=st.selectbox('Credential type',['auto','key','bearer'],format_func=lambda v:{'auto':'Auto detect','key':'API key','bearer':'Bearer token'}[v],help='Auto tries the credential type suggested by its prefix, then the alternative on an authentication error. AQ prefixes alone do not prove token validity.')
        key=gemini_key(runtime,{'GEMINI_API_KEY':secret})
        fingerprint=hashlib.sha256((auth+key).encode()).hexdigest()
        refresh=st.button('Check connection / refresh models',help='Query Google’s model list for this credential. Only available text models appear in the selector.')
        if st.button('Forget pasted key',help='Clear this session’s pasted credential.',on_click=lambda:(st.session_state.pop('gemini_runtime_key',None),st.session_state.pop('_gemini_runtime_saved',None))):st.rerun()
        saved=st.session_state.get('gemini_connection')
        from app_cache import gemini_discovery
        if refresh:gemini_discovery.clear(fingerprint,auth,_credential=key)
        connection=gemini_discovery(fingerprint,auth,_credential=key)
        if saved is None or saved['fingerprint']!=fingerprint or refresh:
            st.session_state.pop('gemini_active_model',None);st.session_state.pop('gemini_last_error',None)
        st.session_state['gemini_connection']={'fingerprint':fingerprint,'connection':connection}
        choices=list(connection.models)
        model=st.selectbox('Gemini model',choices or ['Local assistant'],help='Discovered from your account. Newer available Gemini versions appear first. Disabled models fall back to another listed model.')
        status=st.session_state.get('gemini_last_error') or connection.error
        if key and status:st.error(status)
        elif key:st.success('🟢 Connected to Gemini ('+st.session_state.get('gemini_active_model',model)+')')
        else:st.caption('Local forecast assistant · no key needed')
    history=st.session_state.setdefault('briefing_messages',[])
    for message in history[-12:]:
        with st.chat_message(message['role']):st.markdown(message['content'])
    buttons=st.columns(3);question=None;explicit_search=False
    if buttons[0].button('Suggest 250 km',help='Explicitly search candidate 250 km BGA tasks, and explain the regional weather even when none fits.'):
        question='Suggest a 250 km task from MYN';explicit_search=True
    if buttons[1].button('Suggest 300 km',help='Explicitly search candidate 300 km BGA tasks with hourly corridor checks.'):
        question='Suggest a 300 km task from MYN';explicit_search=True
    if buttons[2].button('Explain my task',help='Discuss the active route and alternatives against regional and hourly weather.'):
        question='Explain my task and the best sectors to use'
    question=st.chat_input('Ask for a task, day briefing, turnpoint or explanation…',max_chars=4000) or question
    if question:
        requested_day=briefing_date(question,day,today or datetime.now(LOCAL_TIMEZONE).date())
        selected=st.session_state.get('briefing_point')
        if not selected and st.session_state.get('inspector_coordinate'):
            lat,lon=st.session_state['inspector_coordinate'];selected=GeoPoint('clicked','Clicked map location',lat,lon)
        if not selected and st.session_state.get('selected_airfield'):
            from uk_airfields import airfield_by_id
            field=airfield_by_id(st.session_state['selected_airfield']);selected=field.turnpoint.point if field else None
        from place_resolver import resolve_place,sector_radius,local_forecasts,PlaceError
        try:
            resolved=resolve_place(question,catalog,selected,allow_network=not recorded)
        except PlaceError as exc:
            history.extend([{'role':'user','content':question},{'role':'assistant','content':str(exc)}])
            st.session_state['assistant_routes']={};st.rerun()
        if not recorded:
            with st.spinner('Loading the requested local forecast…'):
                batch=local_forecasts(resolved.point,sector_radius(question),model_key,bucket)
                forecasts=tuple({(f.location.key,f.model):f for f in (*forecasts,*batch.forecasts)}.values())
                if batch.failures:st.warning('; '.join(f.message for f in batch.failures))
                if re.search(r'\b(task|route|national|regions|sectors|triangle|cross.country)\b',question,re.I):
                    from app import load_sites
                    regional=load_sites(model_key,bucket)
                    forecasts=tuple({(f.location.key,f.model):f for f in (*forecasts,*regional.forecasts)}.values())
        with st.spinner('Preparing your requested briefing…'):
            evidence,proposed=build_context(question,catalog,forecasts,requested_day,today or day,loading,selected=selected,active=active_task(day),assessment=assessment,resolved=resolved)
            response=local_response(question,evidence);suggestions=[]
            if proposed:
                suggestions=[('Your described route','Review the weather verdict before loading.',proposed)]
                # Preserve explicit route-entry behaviour, but questions and AI
                # proposals never mutate the active task without the load button.
                if not re.search(r'\?|\b(?:forecast|could|should|would|suggest)\b',question,re.I):
                    set_task(requested_day,proposed,rerun=False)
                    from route_tools import complete_task
                    complete_task(requested_day,proposed)
                    response='Loaded your described route.\n\n'+response
                    if today is not None and 0<=(requested_day-today).days<5:st.session_state['bot_forecast_day']=(requested_day-today).days
            if explicit_search:
                intent=parse_intent(question,catalog);origin=catalog.by_code.get('MYN')
                tasks=shortlist_tasks(catalog,origin,intent.target_km,forecasts,requested_day,loading,preference=(FORECAST_MODELS[model_key].api_name,))
                suggestions.extend(('Candidate task','Compare the checked hourly corridor and margins.',task) for task in tasks)
            source='Local forecast assistant'
            if key and connection.models:
                try:
                    client=GeminiREST(key,connection.auth)
                    answer,active=client.generate(question,evidence,models=tuple(dict.fromkeys([model,*connection.models])),history=history if evidence['scope']['task_requested'] else ())
                    response=answer['answer']+'\n\n---\n\n**Checked weather evidence**\n\n'+response
                    if evidence['scope']['task_requested']:suggestions+=validated_suggestions(answer,catalog)
                    st.session_state['gemini_active_model']=active;st.session_state.pop('gemini_last_error',None)
                    source='Connected to Gemini ('+active+')'
                except GeminiError as exc:
                    st.session_state['gemini_last_error']=str(exc);source=str(exc)+' · local briefing below'
            elif key:source=connection.error+' · local briefing below'
            evaluated=[]
            for name,reason,task in suggestions[:4]:
                weather,failures=task_weather(task,forecasts,model_key,bucket,recorded=recorded)
                result=assess_task(task,weather,requested_day,loading,preference=(FORECAST_MODELS[model_key].api_name,))
                evaluated.append((name,reason,task,result,failures))
            st.session_state['assistant_routes']={'day':requested_day,'model':model_key,'loading':loading,'items':evaluated}
        history.extend([{'role':'user','content':question},{'role':'assistant','content':response+'\n\n*'+source+'*'}])
        st.session_state['briefing_messages']=history[-24:];st.rerun()
    saved=st.session_state.get('assistant_routes',{})
    if saved.get('model')==model_key and saved.get('loading')==loading:
        for i,(name,reason,task,result,failures) in enumerate(saved.get('items',[])):
            st.markdown('**'+name+'** · '+' → '.join(p.code for p in task))
            st.caption(reason+' '+explain_task(result))
            if failures:st.caption('; '.join(failures[:2]))
            if st.button('Load suggested route onto map',key=f'assistant_load_{i}',help='Load this proposed geometry on its briefing date. The displayed verdict may be marginal, unachievable or unknown; loading is not flight approval.'):
                set_task(saved['day'],task,rerun=False)
                from route_tools import complete_task
                complete_task(saved['day'],task)
                if today is not None and 0<=(saved['day']-today).days<5:st.session_state['bot_forecast_day']=(saved['day']-today).days
                st.rerun()
