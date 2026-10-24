"""Route optimisation, completion state and task-sheet UI."""
from __future__ import annotations

import hashlib
import os
from urllib.parse import urlsplit, urlunsplit
import streamlit as st

from briefing_bot import shortlist_tasks
from config import FORECAST_MODELS
from data_ingestion import IngestionError
from task_planning import assess_task, task_distance, validate_task
from task_sheet import export_bundle, publish_downloads, download_url, qr_bytes


def signature(points):return tuple((p.code,p.latitude,p.longitude) for p in points)


def complete_task(day,points):
    validate_task(points)
    st.session_state.setdefault('completed_tasks',{})[day.isoformat()]=signature(points)


def is_complete(day,points):
    return len(points)>=2 and st.session_state.get('completed_tasks',{}).get(day.isoformat())==signature(points)


def choose_optimised(candidates,objective):
    eligible=[entry for entry in candidates if entry[1].best is not None]
    if not eligible:return None
    # Prioritise adequate weather margins, then requested optimisation objective.
    def score(entry):
        result=entry[1];best=result.best
        return (result.verdict=='FULLY ACHIEVABLE',result.distance_km if objective=='Best distance' else best.average_groundspeed_kmh,
                best.average_groundspeed_kmh,-best.elapsed_hours)
    return max(eligible,key=score)


def optimizer_panel(catalog,day,loading,model_key,bucket,forecasts,*,recorded=False):
    from planning_ui import active_task,set_task,task_weather,fetch_map_grid
    points=active_task(day)
    with st.expander('Optimise Route for Fastest Speed / Best Distance',expanded=st.session_state.get('open_optimizer',False)):
        st.caption('Search nearby BGA triangles and out-and-returns. Verify the best three corridors before loading a route.')
        cols=st.columns(3)
        distance=cols[0].selectbox('Route target',['200 km','300 km','500 km','Custom'],key='route_target',help='Search routes from this distance up to 8% longer. Custom targets can be 50–1000 km.')
        target=int(distance.split()[0]) if distance!='Custom' else st.number_input('Custom task distance · km',50,1000,250,10,help='Minimum distance for the route search; the search allows up to 8% additional distance.')
        objective=cols[1].selectbox('Optimise for',['Fastest speed','Best distance'],key='route_objective',help='Prefer the highest predicted average task speed or the greatest distance within the chosen target band. Adequate weather margins rank first.')
        origin_code=points[0].code if points else 'MYN'
        origin=next((p for p in points if p.code==origin_code),catalog.by_code.get(origin_code))
        cols[2].metric('Start point',origin_code,help='Use your current task’s start. With no task, the search starts at Long Mynd MYN.')
        if st.button('Optimise Route',key='optimise_route',disabled=origin is None,help='Screen up to 30 routes, check the best three using corridor forecasts, then plot the highest-ranked flyable route. This replaces the current task only when a complete flight is found.'):
            with st.spinner('Searching BGA routes and checking weather along each shortlisted corridor…'):
                screening=forecasts
                if not recorded and not any(f.location.key.startswith('map:') for f in forecasts):
                    grid=fetch_map_grid(model_key,bucket)
                    screening=(*forecasts,*grid.forecasts)
                candidates=shortlist_tasks(catalog,origin,target,screening,day,loading,preference=(FORECAST_MODELS[model_key].api_name,))
                checked=[];errors=[]
                for task in candidates:
                    try:
                        weather,failures=task_weather(task,forecasts,model_key,bucket,recorded=recorded)
                        result=assess_task(task,weather,day,loading,preference=(FORECAST_MODELS[model_key].api_name,))
                        checked.append((task,result));errors.extend(failures)
                    except (IngestionError,ValueError) as exc:errors.append(str(exc))
                winner=choose_optimised(checked,objective)
            if winner:
                task,result=winner;set_task(day,task,rerun=False);complete_task(day,task)
                st.session_state['optimiser_result']=f'Loaded {task_distance(task):.0f} km: {result.verdict}. Best {objective.lower()} among {len(checked)} checked candidates. This is a bounded search, not a global optimum.'
                st.rerun()
            else:st.session_state['optimiser_result']=f'No complete flight found among {len(checked)} checked candidates. Try another distance or day. '+('Some weather was unavailable.' if errors else '')
        if st.session_state.get('optimiser_result'):st.info(st.session_state['optimiser_result'])


@st.cache_data(ttl=900,show_spinner=False,max_entries=24)
def build_exports(points,assessment,day,loading,catalog):
    return export_bundle(points,assessment,day,loading,catalog)


def task_sheet_panel(points,assessment,day,loading,catalog,*,key='sheet'):
    from planning_ui import weather_table
    if not is_complete(day,points):
        if len(points)>=2 and st.button('Complete route & open task sheet',key=key+'_finish',help='Use the final selected point as Finish. Open the task briefing and export formats even when weather is unavailable.'):
            try:complete_task(day,points);st.rerun()
            except ValueError as exc:st.error(str(exc))
        return
    with st.expander('BGA / SeeYou Task Sheet',expanded=True):
        with st.spinner('Building your task sheet and instrument files…'):
            sheet,bundle=build_exports(tuple(points),assessment,day,loading,tuple(catalog.turnpoints))
        st.subheader(sheet.header['Aircraft'])
        cols=st.columns(3)
        for i,(name,value) in enumerate(list(sheet.header.items())[1:]):
            cols[i%3].metric(name,value,help='Calculated from your ordered task and selected forecast. Unavailable means the model cannot establish a complete flight.' if name not in ('Wing loading','Date') else 'Selected flight date or Duo Discus polar setting used for this briefing.')
        descriptions={name:{'Track true / magnetic':'Great-circle true track and WMM2025 magnetic track, referenced at the leg start. Degrees clockwise from north.',
            'Heading true °':'Wind-corrected glide heading from time-weighted leg wind and MacCready cruise airspeed. Conditions can vary within the leg.',
            'STF kt':'Time-weighted MacCready inter-thermal airspeed for the chosen polar and leg weather.',
            'XC km/h / kt':'Leg distance divided by modeled leg time, including climb time.',
            'Observation zone':'Explicit planning defaults embedded in both flight-computer formats. Confirm your event rules before declaring.',
            'Wind kt / from °':'Time-weighted wind along the modeled flight; direction is where the wind comes from.',
            'Leg time hh:mm':'Elapsed time of this leg from the same integrated flight solution as the verdict.'}.get(name,'Published waypoint data or great-circle leg geometry for the selected route. Missing values stay unavailable.') for name in sheet.rows[0]}
        weather_table(sheet.rows,descriptions,key=key+'_table')
        st.image(bundle['png'],caption='Task route snapshot. Geographic overview; check airspace and terrain separately.',width='stretch')
        for note in sheet.airfields:st.caption(note)
        cols=st.columns(2)
        formats=(('pdf','Download Printable PDF Task Sheet (.pdf)','application/pdf'),
                 ('docx','Download Editable Word Task Sheet (.docx)','application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
                 ('cup','Download SeeYou / Oudie Task (.cup)','text/plain'),('tsk','Download XCSoar Task (.tsk)','application/xml'))
        for i,(extension,label,mime) in enumerate(formats):
            cols[i%2].download_button(label,bundle[extension],f'G-CKML-{day}.{extension}',mime,key=key+'_'+extension,
                help='Download the complete task. CUP includes SeeYou Related Tasks and observation zones; TSK contains an XCSoar Task declaration. Verify the import on your instrument.')
        digest=hashlib.sha256(b''.join(bundle.values())).hexdigest()
        paths=st.session_state.setdefault('published_tasks',{})
        if digest not in paths:paths[digest]=publish_downloads(bundle)
        st.subheader('Direct PDA Download Link & Scannable QR Code')
        address=os.environ.get('DUO_PUBLIC_BASE_URL') or st.context.url or ''
        parsed=urlsplit(address);base=urlunsplit((parsed.scheme,parsed.netloc,parsed.path,'','')).rstrip('/')
        with st.expander('Download address settings'):
            base=st.text_input('Application address reachable by your phone',value=base,key=key+'_base',
                help='Use this website’s public address, including any deployment path. A localhost address only works on this computer. Your phone must be able to access the same application.')
        try:
            urls={ext:download_url(base,paths[digest],ext) for ext in ('cup','tsk')}
        except ValueError as exc:st.info(str(exc));urls={}
        for column,(extension,url) in zip(st.columns(2),urls.items()):
            with column:
                st.image(qr_bytes(url),width=190,caption=extension.upper()+' direct download')
                st.link_button('Open '+extension.upper()+' task file',url,help='Open the generated task file over HTTP(S). The file remains available while this deployment retains it; old files are cleaned when later exports are made.')
                st.code(url,language=None)
        st.caption('Your phone and instrument need access to this server. Links contain an unguessable token. Files older than 30 days are cleared when another export is made; anyone with a link can download that task.')
        for note in sheet.notes:st.caption(note)
