"""Weather page: presentation consumes immutable background products."""
from datetime import datetime
import os,importlib
import streamlit as st
from config import FORECAST_MODELS,LOCAL_TIMEZONE,MONITORING_ZONES,WAVE_RIDGES,GeoPoint
from data_ingestion import IngestionError
from soaring_physics import Profile, evaluate_thermals, evaluate_wave, distance_bearing
from app_cache import forecast_run_id
import map_component
from map_view import build_map, cached_sounding, cached_meteogram

from planning_ui import active_task,process_map_event
from weather_ui import profiles_at,load_uk_aviation,load_radar,load_calibrated_radar,radar_point_result,load_grid,load_coordinate
from alerts import ForecastBundle,evaluate_convergence
from page_router import MODEL_LABELS

def render(ctx):
    errors=ctx.errors
    catalog = ctx.catalog
    recorded = ctx.recorded
    model_key = ctx.model_key
    points = ctx.points
    day = ctx.day
    day_index = ctx.day_index
    when = ctx.when
    loading = ctx.loading
    bucket = ctx.bucket
    now = ctx.now
    anchor = ctx.anchor
    visual_mode = ctx.visual_mode
    layers = ctx.layers
    mode = ctx.mode
    show_airfields = ctx.show_airfields
    forecasts = ctx.forecasts
    combined=map_forecasts=forecasts
    aviation=radar=convergence=rate_grid=radar_point=None
    # All other pages have returned. Only Weather can request the national grid.
    support=sorted({s for f in combined for s in f.supplemental_sources})
    if support:
        st.caption(MODEL_LABELS[model_key]+' · Forecast supported by '+', '.join(support)+'. Complete fallback profiles are used only when coverage improves.')
    wind_product=None
    if visual_mode in ('wind','thermal','pfd','xc','stf') and not recorded:
        from uk_map_grid import session_grid,grid_progress
        future=session_grid(bucket).request(model_key)
        if future.done():
            try:
                snapshot=future.result()
                map_forecasts=tuple({(f.location.key,f.model):f for f in (*combined,*snapshot.batch.forecasts)}.values())
                wind_product=snapshot.wind if snapshot.batch.forecasts else None
                errors+=tuple(f.message for f in snapshot.batch.failures)
                if snapshot.batch.failures or not snapshot.batch.forecasts:
                    grid_progress(future,'UK grid recovery continues in the background.',retry=lambda:session_grid(bucket).request(model_key))
            except (IngestionError,ValueError,OSError) as exc:
                grid_progress(future,'UK grid recovery continues in the background.',retry=lambda:session_grid(bucket).request(model_key))
        else:
            grid_progress(future)
        st.caption('UK grid: verified five-day data refreshes in the background every 30 minutes. Hour changes use stored data; gaps stay transparent.')
    # Airfield popups fetch reports in the browser on demand. Only the explicit
    # METAR overlay needs the server-wide report batch.
    if visual_mode=='extras' and 'METAR' in layers and not recorded:
        aviation=load_uk_aviation(int(now.timestamp()//60))
    if ((visual_mode=='extras' and 'Radar' in layers) or visual_mode=='radar') and not recorded:
        try: radar=load_radar(int(now.timestamp()//300))
        except (IngestionError,ValueError,KeyError) as exc: st.warning(f'Radar unavailable: {exc}')
    if visual_mode=='radar' and os.environ.get('DUO_RADAR_RATE_GRID_URL') and not recorded:
        try: rate_grid=load_calibrated_radar(os.environ['DUO_RADAR_RATE_GRID_URL'],int(now.timestamp()//300))
        except (IngestionError,ValueError) as exc: st.warning(f'Calibrated radar unavailable: {exc}')
    if visual_mode=='radar' and st.session_state.get('radar_coordinate'):
        with st.spinner('Loading Open-Meteo rain estimate at your map point…'):
            radar_point=radar_point_result(st.session_state['radar_coordinate'],when,model_key,
                                          int(now.timestamp()//300),recorded=bool(recorded))
    if visual_mode=='extras' and 'Convergence' in layers:
        if recorded: grid_forecasts=tuple(f for f in recorded.grid_forecasts if f.model==FORECAST_MODELS[model_key].api_name)
        else:
            with st.spinner('Loading the England and Wales convergence grid…'):
                batch=load_grid(model_key,bucket); grid_forecasts=batch.forecasts; errors+=tuple(f.message for f in batch.failures)
        convergence=evaluate_convergence(ForecastBundle(combined,grid_forecasts,.5,(),now),when,
                                         preference=(FORECAST_MODELS[model_key].api_name,))
        if convergence is None or convergence.valid_surface_cells<.8*convergence.total_cells:
            st.warning('Convergence coverage is incomplete at this hour.')
    profiles=profiles_at(combined,when)
    if combined:
        oldest=min(f.fetched_at for f in combined)
        age=max(0,(now-oldest).total_seconds()/60)
        st.caption(f'Weather retrieved {oldest.astimezone(LOCAL_TIMEZONE):%d %b %H:%M %Z} · {age:.0f} minutes old'+
                   (' · Stored forecast retained during recovery.' if age>60 else ''))
    local=profiles.get('region:shropshire'); thermal=evaluate_thermals(local,loading) if local else None
    metrics=st.columns(4)
    metrics[0].metric('LONG MYND · NET CLIMB','—' if thermal is None or thermal.net_climb_knots is None else f'{thermal.net_climb_knots:.1f} kt',
        help='Estimated core updraught after one polar-derived Duo Discus circling sink deduction, floored at zero. Task decisions use the conservative soil-moisture estimate, which may be lower.')
    metrics[1].metric('POTENTIAL CLOUD BASE','—' if thermal is None or thermal.cloud_base_ft_amsl is None else f'{thermal.cloud_base_ft_amsl:,.0f} ft',
        help='Hennig condensation-height estimate in feet AMSL. Compare with usable mixing top; cumulus may not form if mixing ends below this height.')
    metrics[2].metric('USABLE MIXING TOP','—' if thermal is None or thermal.usable_top_ft_amsl is None else f'{thermal.usable_top_ft_amsl:,.0f} ft',
        help='Lower of the condensation height and mixing top, in feet AMSL. Task assessment subtracts terrain to check usable depth AGL.')
    metrics[3].metric('VALID TIME',when.astimezone(LOCAL_TIMEZONE).strftime('%H:%M %Z'),when.astimezone(LOCAL_TIMEZONE).strftime('%a %d %b'),delta_color='off',
        help='Time of the selected forecast column. Radar and METAR have separate observed timestamps, and the task engine assesses the full soaring window.')
    origin=MONITORING_ZONES['shropshire']
    wave_signal=any(evaluate_wave(p,WAVE_RIDGES[key.split(':',1)[1]]).complete and
                    (evaluate_wave(p,WAVE_RIDGES[key.split(':',1)[1]]).score or 0)>=75
                    for key,p in profiles.items() if key.startswith('ridge:') and distance_bearing(origin,p.forecast.location)[0]<150)
    from app_cache import day_summary,forecast_run_id
    day_ratings,classifications=day_summary(forecast_run_id(combined),origin,anchor.astimezone(LOCAL_TIMEZONE).date(),loading,FORECAST_MODELS[model_key].api_name,_forecasts=combined)
    classification=classifications[day_index];rating=day_ratings[day_index]
    st.metric('Cross-country day estimate',classification.label if rating.stars is not None else 'Weather updating',
        help='Local weather capacity from conservative thermal climb, usable height and worst-direction wind with a 10% reserve. Check a complete route separately.')
    if rating.stars is not None:st.caption(f'{classification.scope} · '+classification.explanation)
    st.subheader(f'{rating.symbols} · {rating.label}' if rating.stars is not None else 'Five-day outlook · waiting for verified weather',help='Local PFD distance band capped by conservative thermal climb, usable height, sustained window and spread-out risk. Missing evidence stays unknown; wave does not inflate the thermal rating.')
    st.caption('UK RASP-style local day rating · '+origin.name+' · Independent distance bands with weather quality caps. '+
        (' '.join(rating.reasons) if rating.stars is not None else 'Planning tools remain available during recovery.')+(' Wave potential is shown separately.' if wave_signal else ''))
    cards=st.columns(5)
    for i,r in enumerate(day_ratings):
        with cards[i]:
            st.metric(datetime.fromisoformat(r.day).strftime('%a %d %b'),r.symbols if r.stars is not None else '—',
                help='Independent UK RASP-style rating at the task start / Long Mynd. Distances are local weather capacity; check your full route separately. '+' '.join(r.reasons))
            st.caption((r.label if r.stars is not None else 'Awaiting live weather')+(' · '+str(round(r.distance_km))+' km' if r.distance_km is not None else ''))
            if r.covered_hours:st.caption(f'{r.covered_hours}/8 local hours covered · Window '+('pending' if r.window_hours is None else f'{r.window_hours:g} h'))
    probe_enabled=st.toggle('Enable Point Forecast Probe',key='point_probe_enabled',
        help='Weather map only. Enable to inspect a two-day vertical meteogram using cached model samples. Disable to restore normal map clicks; no point forecasts are prepared while off.')
    if probe_enabled:
        st.caption('Point Forecast Probe active — click any location, airfield, or turnpoint on the Weather map to inspect its vertical meteogram.')
    probe_forecasts=map_forecasts
    if probe_enabled and not recorded:
        # Read an existing grid only. Enabling the probe cannot start one.
        bundle=st.session_state.get('uk_map_grid_bundle')
        completed=bundle.completed(model_key) if bundle is not None and bundle.cycle==bucket else None
        if completed is not None:
            probe_forecasts=tuple({(f.location.key,f.model):f for f in (*combined,*completed.batch.forecasts)}.values())
    probe=None;prepared=None;probe_ready=False
    if probe_enabled:
        try:
            probe=importlib.import_module('point_forecast_meteogram')
            if getattr(probe,'PROBE_API_REVISION',0)<6:probe=importlib.reload(probe)
            from weather_engine import products
            from uk_map_grid import grid_progress
            key=('probe',forecast_run_id(probe_forecasts),day.isoformat(),loading)
            job=products().request(key,lambda:probe.prepare_bundle(key[1],day,loading,_forecasts=tuple(probe_forecasts)))
            if job.done():prepared=job.result();probe_ready=True
            else:
                # The empty chart is interactive immediately; its real product
                # arrives on the next fragment tick, without blocking navigation.
                prepared=probe.prepare_bundle((),day,loading,_forecasts=())
                grid_progress(job,'Preparing the point forecast in the background.')
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Optional point probe unavailable')
            st.warning('Point Forecast Probe is temporarily unavailable. Normal map controls remain active.')
            probe=None
    from map_component import document_version
    document_key=document_version(map_forecasts,when,visual_mode,tuple(layers),loading,
        catalog.content_sha256,points,show_airfields,bool(recorded),
        repr((radar,rate_grid,radar_point,convergence,aviation)),
        st.session_state.get('airfield_popup:last_map_event'),probe_enabled,
        forecast_run_id(probe_forecasts) if probe_enabled else None,probe_ready)
    def weather_document():
        view=build_map(map_forecasts,when,layers=layers,aviation=aviation,radar=radar,convergence=convergence,
            task=points,turnpoints=catalog.turnpoints,visual_mode=visual_mode,anchor=anchor,loading=loading,rate_grid=rate_grid,radar_point=radar_point,show_airfields=show_airfields,live_airfields=not bool(recorded),selected_airfield=None if probe_enabled else st.session_state.get('airfield_popup:last_map_event'),wind_product=wind_product)[0]
        if probe_enabled and probe is not None:
            try:
                probe.add_probe(view,probe_forecasts,day,aviation=aviation,loading=loading,prepared=prepared)
            except Exception:
                # An optional chart or stale module must never prevent maps
                # or other pages loading. Keep the ordinary map operational.
                import logging
                logging.getLogger(__name__).exception('Optional point probe unavailable')
                st.warning('Point Forecast Probe is temporarily unavailable. Normal map controls remain active.')
        return view
    def on_map_change():
        event=st.session_state.get('soaring_map',{}) or {}
        if ((event.get('last_object_clicked') or {}).get('duo') or {}).get('kind')=='probe_close':
            st.session_state['_close_point_probe']=True
            st.session_state['last_map_event']=event
            return
        process_map_event(st.session_state.get('soaring_map',{}),catalog,day,st.session_state.get('map_click_mode','Inspect weather'))
    interaction=map_component.render_map(weather_document,document_key=document_key,height=720,use_container_width=True,pixelated=False,key='soaring_map',on_change=on_map_change,
        wrap_longitude=False,returned_objects=['last_object_clicked','last_clicked'])
    # AppTest and initial component defaults do not invoke the browser callback.
    if interaction and interaction!=st.session_state.get('last_map_event'):
        old=active_task(day)
        old_radar=st.session_state.get('radar_coordinate')
        process_map_event(interaction,catalog,day,mode)
        if active_task(day)!=old or st.session_state.get('radar_coordinate')!=old_radar: st.rerun()
    st.caption('Open Map modes to choose wind, radar, soaring contours or the route planner. These independent model estimates are inspired by Windy/SkySight; they are not their proprietary forecasts.')
    if radar:
        age=(now-radar['time']).total_seconds()/60
        st.caption(f'Observed radar {radar["time"].astimezone(LOCAL_TIMEZONE):%H:%M %Z} · {age:.0f} minutes old'+(' · STALE' if age>30 else '')+' · independent of the forecast slider.')
    with st.sidebar:
        st.divider()
        coordinate=st.session_state.get('inspector_coordinate')
        if coordinate is not None and coordinate!=st.session_state.get('_last_sounding_coordinate'):
            st.session_state['sounding_inspector_open']=True
            st.session_state['_last_sounding_coordinate']=coordinate
        with st.expander('Sounding inspector',expanded=False,key='sounding_inspector_open',on_change='rerun') as inspector:
            if not inspector.open:return
            choices=[f for f in combined if not f.location.key.startswith('corridor:')]
            if not choices:
                st.info('No sounding data for the selected model.'); return
            options=['Map coordinate']+[f.location.key for f in choices]
            labels={f.location.key:f.location.name for f in choices}
            if st.session_state.get('inspector_selection') not in options: st.session_state.pop('inspector_selection',None)
            initial=options.index('region:shropshire') if 'region:shropshire' in options else 1
            selection=st.selectbox('Waypoint or clicked coordinate',options,index=None if 'inspector_selection' in st.session_state else initial,
                format_func=lambda key:labels.get(key,'Map coordinate'),key='inspector_selection',
                help='Inspect a selected waypoint or the last map click. The plot shows pressure-level temperature/dewpoint and the local-day thermal, cloud, wind and rain evolution.')
            coordinate=st.session_state.get('inspector_coordinate',(52.518,-2.880))
            if selection=='Map coordinate':
                st.caption(f'{coordinate[0]:.3f}° N, {abs(coordinate[1]):.3f}° '+('W' if coordinate[1]<0 else 'E'))
                if not 49<=coordinate[0]<=61.5 or not -9<=coordinate[1]<=2.5:
                    st.info('Select a coordinate in the UK map area.'); return
                if recorded:
                    chosen=min(choices,key=lambda f:distance_bearing(GeoPoint('click','Click',*coordinate),f.location)[0])
                    st.caption('Recorded inspector: nearest stored waypoint, not a fresh coordinate forecast.')
                else:
                    try: chosen=load_coordinate(*coordinate,model_key,bucket)
                    except IngestionError as exc:
                        st.info(str(exc)); return
            elif selection is not None: chosen=next(f for f in choices if f.location.key==selection)
            else:
                st.info('Choose a sounding location.'); return
            try: selected_profile=Profile.from_forecast(chosen,when)
            except KeyError:
                st.info('No sounding at this hour.'); return
            if not selected_profile.levels: st.info('Pressure-level sounding unavailable.')
            else:
                st.plotly_chart(cached_sounding(forecast_run_id((chosen,)),when,_forecast=chosen),width='stretch',key='sounding')
                st.caption('Skew-T: temperature orange, dewpoint green. Hover for actual °C; dry/saturated adiabats are guides.')
            st.plotly_chart(cached_meteogram(forecast_run_id((chosen,)),day,loading,_forecast=chosen),width='stretch',key='meteogram')
            with st.expander('Profile assumptions'):
                for note in evaluate_thermals(selected_profile,loading).notes: st.write(note)

