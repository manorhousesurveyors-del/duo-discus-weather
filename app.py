"""Interactive UK soaring dashboard. Start with: streamlit run app.py.

The requested model is preferred; labelled support fills missing fields or
replaces unusable regional profiles. Point probes reuse cached samples.
"""
from __future__ import annotations

from datetime import UTC,datetime,time,timedelta
import importlib
import os
from pathlib import Path
import streamlit as st
import runtime_imports

if getattr(runtime_imports,'API_REVISION',0)<12:
    importlib.reload(runtime_imports)
runtime_imports.refresh_cached_modules()
optional_module=runtime_imports.optional_module

from config import LOCAL_TIMEZONE,default_forecast_model
from data_ingestion import ForecastBatch,IngestionError,utc_datetime
from page_router import MODEL_LABELS
from weather_ui import (load_recorded,load_sites,_refresh_weather,load_grid,load_coordinate,
    load_aviation,load_uk_aviation,load_radar,load_calibrated_radar,load_radar_precipitation,
    radar_point_result,profiles_at)

# Compatibility for scripts/importers; chart libraries load only on first use.
_MAP_API={'THERMAL_STOPS','sounding_figure','meteogram_figure','cached_sounding',
    'cached_meteogram','_destination','wind_barb_svg','interpolate_map_samples',
    'add_colour_legend','thermal_colour','thermal_overlay_data','build_map','wave_document'}

def __getattr__(name):
    if name in _MAP_API:return getattr(importlib.import_module('map_view'),name)
    raise AttributeError(name)


def _main():
    from planning_ui import active_task,current_catalog,task_builder
    from map_modes import MODES
    st.set_page_config(page_title='Duo · UK soaring weather',page_icon='⛅',layout='wide',initial_sidebar_state='expanded')
    from session_state import migrate
    migrate(st.session_state)
    st.markdown('<style>.block-container{padding-top:1.5rem}div[data-testid="stMetric"]{background:#fff;border:1px solid #dce5e8;border-radius:12px;padding:12px}'
                'h1{letter-spacing:-.04em}section[data-testid="stSidebar"]{border-right:1px solid #dce5e8}</style>',unsafe_allow_html=True)
    now=datetime.now(UTC)
    recorded_path=os.environ.get('DUO_DASHBOARD_SNAPSHOT')
    try: recorded=load_recorded(recorded_path,Path(recorded_path).stat().st_mtime_ns) if recorded_path else None
    except (OSError,ValueError,KeyError) as exc:
        st.error(f'Recorded forecast could not be read: {exc}'); return
    anchor=recorded.created_at if recorded else now
    st.title('Duo · UK soaring weather')
    st.caption('G-CKML  /  YOUR TASK · THERMALS · WAVE · CONVERGENCE')
    if recorded:
        st.info(f'Recorded forecast snapshot · retrieved {anchor.astimezone(LOCAL_TIMEZONE):%d %b %Y %H:%M %Z}. Task weather uses stored samples within 15 km; gaps remain unavailable.')
    from page_router import PAGES
    pages=list(PAGES)
    st.markdown('<style>.st-key-active_page [role="radiogroup"]{gap:0;border-bottom:1px solid #dce5e8;flex-wrap:wrap}.st-key-active_page [role="radiogroup"] label{padding:10px 12px;margin:0;border-bottom:2px solid transparent}.st-key-active_page [role="radiogroup"] label:has(input:checked){border-bottom-color:#267d84;color:#267d84}.st-key-active_page [data-testid="stRadioOption"]>div>div:first-child{display:none}.st-key-active_page [data-testid="stRadioOption"]:focus-within{outline:2px solid #267d84;outline-offset:-2px}</style>',unsafe_allow_html=True)
    active_page=st.radio('Page',pages,horizontal=True,key='active_page',label_visibility='collapsed',help='Only this page loads its maps and calculations. Task edits are shared across Weather and Task pages.')
    close_probe=st.session_state.pop('_close_point_probe',False)
    if active_page!='Weather map' or close_probe:st.session_state['point_probe_enabled']=False
    ogn_traffic=optional_module('ogn_traffic')
    if not recorded:
        getattr(ogn_traffic,'start_background',lambda:None)()
    catalog=current_catalog()
    default_thermal=st.query_params.get('thermals','off')=='on'
    if 'map_layers' not in st.session_state:
        st.session_state['map_layers']=[]+(['Thermals'] if default_thermal else [])
    if 'visual_mode' not in st.session_state: st.session_state['visual_mode']='thermal' if default_thermal else 'clean'
    def thermal_default_changed():
        enabled=st.session_state['thermal_default']
        st.session_state['visual_mode']='thermal' if enabled else 'clean'
        st.query_params['thermals']='on' if enabled else 'off'
        layers=[layer for layer in st.session_state['map_layers'] if layer!='Thermals']
        st.session_state['map_layers']=layers+(['Thermals'] if enabled else [])
    def extra_layers_changed():
        st.session_state['visual_mode']='extras' if st.session_state['map_layers'] else 'clean'
    with st.sidebar:
        st.subheader('Forecast controls')
        visual_mode=st.selectbox('Map mode',list(MODES),format_func=MODES.get,key='visual_mode',
            help='Choose one main map view. The floating Map modes menu offers the same choices; Clean Map hides weather overlays.')
        if 'forecast_model' not in st.session_state:st.session_state['forecast_model']=default_forecast_model()
        model_key=st.selectbox('Forecast model',list(MODEL_LABELS),format_func=MODEL_LABELS.get,key='forecast_model',
            help='Fresh sessions start with ICON-EU. Missing fields use labelled model support; an unusable five-day profile falls back to a better-covered ICON/ECMWF sounding. Missing evidence is never treated as good weather.')
        if 'bot_forecast_day' in st.session_state:
            st.session_state['forecast_day']=st.session_state.pop('bot_forecast_day')
        default_day=1 if anchor.astimezone(LOCAL_TIMEZONE).hour>=13 else 0
        day_index=st.selectbox('Forecast day',list(range(5)),index=default_day,key='forecast_day',
            format_func=lambda d:f'Day {d} · {(anchor.astimezone(LOCAL_TIMEZONE).date()+timedelta(days=d)):%a %d %b}',
            help='Select the day to plan. Each date keeps its own saved task; a model that does not cover that day will show missing data.')
        hour=st.slider('Local time · Europe/London',9,19,13,format='%d:00',key='forecast_hour',
            help='Select the map and sounding hour, using BST in summer and GMT in winter. Task assessment still checks the whole 10:00–18:00 soaring window.')
        loading=st.radio('Wing loading',['dry','ballasted'],format_func=lambda v:'Dry · 34 kg/m²' if v=='dry' else 'Ballasted · 43 kg/m²',
            horizontal=True,key='wing_loading',help='Select the Duo Discus polar used for MacCready cruise and task ground speed. The same polar gives the bank-adjusted circling sink, deducted once from estimated thermal-core lift.')
        with st.expander('More layers and click settings',expanded=False):
            st.toggle('Thermals on by default',value=default_thermal,key='thermal_default',on_change=thermal_default_changed,
                help='Choose whether the thermal overlay starts enabled. This preference is stored in the page URL, so bookmarking or reopening that URL preserves it.')
            layers=st.multiselect('Map layers',['Thermals','Cloud base','Convergence','Wave sites','BGA turnpoints','METAR','Radar'],key='map_layers',on_change=extra_layers_changed,
                help='Toggle weather and BGA layers. Blue-to-red thermals show fixed net-climb values; only your active task is drawn, and convergence requests a larger grid.')
            mode=st.radio('Map clicks',['Inspect weather','Add to task'],key='map_click_mode',
                help='Inspect changes the sounding only. Add mode appends a clicked BGA marker or custom coordinate using the builder’s Add as role.')
        show_airfields=st.checkbox('UK airfields and live reports',value=True,key='show_airfields',help='Show gliding clubs and UK aerodromes. Hover for current model weather; click for runway details and NOAA METAR/TAF reports. Turquoise marks gliding fields, purple reporting airfields and grey known closures.')
        if st.button('Refresh weather',width='stretch',help='Clear stored forecasts and restart background updates. UK grid requests make one attempt per model and cycle.'):
            _refresh_weather()
            st.session_state.pop('_map_documents',None)
            st.rerun()
        st.caption('Thermals are radiation/parcel estimates. Hover over controls and column headings for guidance.')
    day=anchor.astimezone(LOCAL_TIMEZONE).date()+timedelta(days=day_index)
    when=utc_datetime(datetime.combine(day,time(hour),LOCAL_TIMEZONE))
    bucket=int(now.timestamp()//1800)
    with st.sidebar:
        if active_page in ('Weather map','Task planner'):
            with st.expander('Build / edit this day’s task',expanded=False,key='sidebar_task_builder_open',on_change='rerun') as task_panel:
                if task_panel.open:task_builder(catalog,day,location='sidebar')
        if st.session_state.get('task_message'): st.caption(st.session_state['task_message'])
        if st.session_state.get('task_error'): st.error(st.session_state.pop('task_error'))
    points=active_task(day)
    page_body=st.empty()
    page_body.empty()
    with page_body.container(key='page_body_'+active_page):
        _render_page(active_page,catalog,recorded,model_key,points,day,day_index,when,
                     loading,bucket,now,anchor,visual_mode,layers,mode,show_airfields)


def _render_page(active_page,catalog,recorded,model_key,points,day,day_index,when,
                 loading,bucket,now,anchor,visual_mode,layers,mode,show_airfields):
    from page_router import PageContext,PAGES,dispatch
    from app_cache import forecast_run_id,covered_forecasts
    regional_loading=False
    if recorded:
        forecasts=covered_forecasts(forecast_run_id(recorded.forecasts),model_key,_forecasts=recorded.forecasts); errors=recorded.errors
    elif not PAGES[active_page][1]:
        forecasts=st.session_state.get('forecast_bundle:'+model_key,())
        errors=()
    else:
        from uk_map_grid import grid_progress
        pending=load_sites(model_key,bucket)
        if pending.done():
            try:
                batch=pending.result()
                forecasts=batch.forecasts or st.session_state.get('forecast_bundle:'+model_key,())
                errors=tuple(f.message for f in batch.failures)
                if batch.failures or not batch.forecasts:
                    grid_progress(pending,'Forecast recovery continues in the background.',retry=lambda:load_sites(model_key,bucket))
            except (IngestionError,OSError,ValueError) as exc:
                forecasts=st.session_state.get('forecast_bundle:'+model_key,())
                errors=(str(exc),)
                grid_progress(pending,'Weather recovery continues in the background.',retry=lambda:load_sites(model_key,bucket))
        else:
            regional_loading=True
            forecasts=st.session_state.get('forecast_bundle:'+model_key,())
            errors=()
            grid_progress(pending,'Regional forecasts are updating in the background. Stored data retains its retrieval time.')
    from weather_engine import complete_batch
    if forecasts and complete_batch(ForecastBatch(forecasts,())):
        st.session_state['forecast_bundle:'+model_key]=forecasts
    if regional_loading and not forecasts and active_page=='Weather map':
        st.info('Loading the five-day regional forecast and checking ICON-EU / ECMWF coverage…')
        # Pending is not a forecast failure. Do not create false 0/8 ratings.
        # Controls and the terrain map still render while the worker runs.
    context=PageContext(catalog,recorded,model_key,points,day,day_index,when,loading,bucket,now,anchor,visual_mode,layers,mode,show_airfields,forecasts,errors)
    dispatch(active_page,context)


def main():
    from time import perf_counter
    started=perf_counter()
    st.session_state['_full_render_count']=st.session_state.get('_full_render_count',0)+1
    try:_main()
    finally:st.session_state['_page_render_ms']=round((perf_counter()-started)*1000,2)


if __name__=='__main__':
    main()
