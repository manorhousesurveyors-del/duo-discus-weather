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
    try: return bga.active_catalog()
    except (OSError,ValueError,KeyError) as exc:
        st.warning(f'BGA catalog unavailable: {exc}. Use Data quality to check sources or import a waypoint file.')
        return bga.Catalog((),'Unavailable','',bga.content_hash(()))


def active_task(day):
    tasks=st.session_state.setdefault('tasks_by_day',{})
    if day.isoformat() not in tasks:
        try: tasks[day.isoformat()]=load_daily_task(day)
        except (OSError,ValueError,KeyError) as exc:
            st.warning(f'Saved task could not be read: {exc}')
            tasks[day.isoformat()]=()
    return tuple(tasks[day.isoformat()])


def set_task(day,points,*,rerun=True):
    points=tuple(points)
    save_daily_task(day,points)
    st.session_state.setdefault('tasks_by_day',{})[day.isoformat()]=points
    st.session_state['task_message']=f'Saved {len(points)} task points for {day:%d %b}.'
    if len(points)>=3 and points[0]==points[-1]:
        from route_tools import complete_task
        try:complete_task(day,points)
        except ValueError:pass
    if rerun: st.rerun()


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


@st.cache_data(ttl=900,show_spinner=False)
def fetch_map_grid(model_key,refresh_bucket,kind='soaring'):
    from map_weather import national_grid,soaring_grid,WIND_VARIABLES
    from config import HOURLY_VARIABLES
    return OpenMeteoClient().fetch_batched(national_grid() if kind=='wind' else soaring_grid(),
        (FORECAST_MODELS[model_key],),variables=WIND_VARIABLES if kind=='wind' else HOURLY_VARIABLES,batch_size=20)


@st.cache_data(ttl=900,show_spinner=False)
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
                set_task(day,selected,rerun=False)
                if draft.get('complete') and len(selected)>=2:complete_task(day,selected)
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
    from briefing_bot import briefing_date, pilot_briefing, gemini_key, gemini_briefing
    st.subheader('Help & Briefing Bot')
    st.caption('Ask about the selected day, your task, BGA points or a new route. Suggestions are checked against corridor weather before being marked achievable.')
    with st.expander('Gemini settings · optional'):
        runtime=st.text_input('GEMINI_API_KEY',type='password',key='gemini_runtime_key',help='Optional: use Google Gemini for a conversational overview. Your key stays in this server session. Weather and your question are sent to Google only when you ask. Leave blank for the local assistant.')
        model=st.selectbox('Gemini model',['gemini-2.5-flash','gemini-2.5-pro'],key='gemini_model',help='Flash answers quickly. Pro may take longer. Your Google account quota and charges apply.')
        if st.button('Forget pasted key',help='Remove the pasted key from this session.',on_click=lambda:st.session_state.pop('gemini_runtime_key',None)):
            st.rerun()
    try: secret_key=st.secrets.get('GEMINI_API_KEY','')
    except (FileNotFoundError,st.errors.StreamlitSecretNotFoundError): secret_key=''
    api_key=gemini_key(runtime,{'GEMINI_API_KEY':secret_key})
    context=f'{day.isoformat()}|{loading}|{model_key}|{bucket}|{catalog.content_sha256}'
    history=st.session_state.setdefault('briefing_messages',[])
    for message in history[-12:]:
        with st.chat_message(message['role']): st.markdown(message['content'])
    buttons=st.columns(3)
    question=None
    if buttons[0].button('Suggest 250 km',help='Search official BGA triangles and out-and-returns from MYN, screen weather, then verify the best candidate corridors.'):
        question='Suggest a 250 km task from MYN'
    if buttons[1].button('Suggest 300 km',help='Search 300 km BGA candidates from MYN using today’s selected model and wing loading. Missing data cannot produce an achievable recommendation.'):
        question='Suggest a 300 km task from MYN'
    if buttons[2].button('Explain my task',help='Explain the active task’s launch window, weakest leg, weather constraints and confidence limits using its current calculation.'):
        question='Why is my task achievable or not?'
    typed=st.chat_input('Ask for a task, day briefing, turnpoint or explanation…',max_chars=1000)
    question=typed or question
    if question:
        history.append({'role':'user','content':question})
        intent=parse_intent(question,catalog)
        requested_day=briefing_date(question,day,today or datetime.now(LOCAL_TIMEZONE).date())
        evidence={}
        if intent.kind=='route':
            route=intent.route
            try:
                validate_task(route)
                weather,failures=task_weather(route,forecasts,model_key,bucket,recorded=recorded)
                result=assess_task(route,weather,requested_day,loading,preference=(FORECAST_MODELS[model_key].api_name,))
                set_task(requested_day,route,rerun=False)
                from route_tools import complete_task
                complete_task(requested_day,route)
                if today is not None and 0<=(requested_day-today).days<5:
                    st.session_state['bot_forecast_day']=(requested_day-today).days
                response,evidence=pilot_briefing(question,forecasts,requested_day,assessment=result,route=route)
                response=f'Loaded your task for {requested_day:%a %d %b}.\n\n'+response
                if failures: response+='\n\nForecast gaps: '+'; '.join(failures[:3])
            except (ValueError,IngestionError) as exc:
                response=f'Please check this route: {exc}'
        elif intent.kind=='suggest':
            origin=catalog.by_code.get(intent.origin_code)
            if origin is None:
                response=f'Origin {intent.origin_code} is not in the current catalog. Check the trigraph in the task builder.'
            else:
                with st.spinner('Screening BGA geometry and checking candidate corridor forecasts…'):
                    tasks=shortlist_tasks(catalog,origin,intent.target_km,forecasts,day,loading,
                        preference=(FORECAST_MODELS[model_key].api_name,),shape=intent.shape)
                    suggestions=[]
                    for task in tasks:
                        weather,failures=task_weather(task,forecasts,model_key,bucket,recorded=recorded)
                        result=assess_task(task,weather,day,loading,preference=(FORECAST_MODELS[model_key].api_name,))
                        suggestions.append((task,result,failures))
                suggestions.sort(key=lambda entry:(entry[1].best is not None,entry[1].verdict=='FULLY ACHIEVABLE',
                                                   -entry[1].best.elapsed_hours if entry[1].best else -999),reverse=True)
                st.session_state['task_suggestions']={'context':context,'items':suggestions}
                good=[entry for entry in suggestions if entry[1].best is not None]
                if good:
                    task,result,_=good[0]
                    response=f'Best checked candidate: **'+ ' → '.join(p.code for p in task)+'**. '+explain_task(result)
                    if intent.load_best:
                        set_task(day,task,rerun=False); response+=' Loaded as your active task.'
                else:
                    response=(f'No achievable {intent.target_km} km task was established among {len(suggestions)} checked corridors. '
                              'Review the candidate reasons below, choose a shorter distance, or refresh incomplete forecasts. This bounded search is not proof that no other route could work.')
        elif intent.kind in ('weather','wave','day','help') or (intent.kind=='explain' and assessment is None):
            response,evidence=pilot_briefing(question,forecasts,requested_day,assessment=assessment if requested_day==day else None)
        else: response=answer_question(intent,catalog,assessment,classification,waves)
        with st.spinner('Preparing your pilot briefing…'):
            response,source=gemini_briefing(question,evidence,response,catalog,api_key=api_key,model=model)
        response+='\n\n*'+source+'*'
        history.append({'role':'assistant','content':response})
        st.session_state['briefing_messages']=history[-24:]
        st.rerun()
    saved=st.session_state.get('task_suggestions',{})
    if saved.get('context')==context:
        for i,(task,result,failures) in enumerate(saved['items']):
            st.markdown('**'+' → '.join(p.code for p in task)+f'** · {result.distance_km:.0f} km · {result.verdict}')
            st.caption(explain_task(result))
            if failures: st.caption('; '.join(failures[:3]))
            if st.button('Load this task',key=f'bot_load_{i}',disabled=result.best is None,
                         help='Load this verified candidate into the selected day’s builder and draw its legs. Operational checks remain necessary before flight.'):
                set_task(day,task)
    elif saved:
        st.caption('Earlier suggestions belong to different forecast settings. Request new suggestions for the selected day/model/loading.')
