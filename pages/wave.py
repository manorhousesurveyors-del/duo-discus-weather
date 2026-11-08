"""Wave page; independent renderer."""
from pathlib import Path
import streamlit as st
from config import WAVE_RIDGES
from soaring_physics import evaluate_wave, rank_wave_sites
from app_cache import forecast_run_id
from map_view import wave_document

from planning_ui import process_map_event,weather_table
from weather_ui import profiles_at

def render(ctx):
    catalog = ctx.catalog
    recorded = ctx.recorded
    day = ctx.day
    when = ctx.when
    forecasts = ctx.forecasts
    combined=forecasts
    waves=rank_wave_sites([evaluate_wave(p,WAVE_RIDGES[key.split(':',1)[1]]) for key,p in profiles_at(combined,when).items() if key.startswith('ridge:')])
    st.subheader('Wave Watch Map')
    from app_cache import forecast_run_id,file_version
    from airspace import DEFAULT_PATH,NEXT_PATH,LOCAL_PATH
    from map_component import render_document
    versions=file_version([DEFAULT_PATH,NEXT_PATH,LOCAL_PATH,*Path('data').glob('*turnpoint*'),*Path('assets').glob('*.js')])
    from wave_map import cached_wave_payload,empty_wave_payload
    from weather_engine import products
    from uk_map_grid import grid_progress
    run_id=forecast_run_id(combined)
    job=products().request(('wave-field',run_id,when),lambda:cached_wave_payload(run_id,when,_forecasts=combined))
    ready=job.done()
    prepared=job.result() if ready else empty_wave_payload(when)
    if not ready:grid_progress(job,'Wave layers are being prepared in the background.')
    # Uploaded catalogs participate in the key; no task/session state is cached.
    document=wave_document(run_id,when,(versions,catalog.content_sha256,ready),bool(recorded),
        _forecasts=combined,_turnpoints=catalog.turnpoints,_prepared=prepared)
    def on_wave_click():process_map_event(st.session_state.get('wave_map',{}),catalog,day,'Inspect weather',event_key='last_wave_event',visual_override='wave')
    render_document(document,key='wave_map',on_change=on_wave_click)
    st.subheader('Terrain-forced wave lift and sink')
    weather_table([{'Site':WAVE_RIDGES[w.site].point.name,'Score':w.score,'Evidence':'Complete' if w.complete else 'Provisional',
                    '850 hPa ascent kt':w.uplift_850_knots,'700 hPa ascent kt':w.uplift_700_knots,'N² s⁻²':w.brunt_vaisala_n2} for w in waves],
        {'Site':'Approximate named ridge anchor; it is not a surveyed wave entry point.','Score':'Heuristic score from flow alignment, shear, stability, Scorer decrease and model ascent.',
         'Evidence':'Only complete evidence can trigger a wave alert. Missing vertical motion remains provisional.',
         '850 hPa ascent kt':'Model vertical air motion, positive upward, converted using its returned units.',
         '700 hPa ascent kt':'Model vertical air motion at 700 hPa; a coarse model cannot resolve individual wave crests.',
         'N² s⁻²':'Brunt–Väisälä stability between 850 and 700 hPa. Positive values indicate static stability.'})
    st.caption('Wave signals inform the briefing but do not add assumed lift to a thermal task calculation.')
    return
