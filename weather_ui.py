"""Non-blocking presentation adapters; the weather engine owns I/O policy."""
from datetime import UTC,datetime
from pathlib import Path
import re
import streamlit as st
from config import FORECAST_MODELS,GeoPoint,convergence_grid
from data_ingestion import IngestionError, AviationWeatherClient
from alerts import load_bundle,GRID_VARIABLES
from soaring_physics import Profile


def background_value(key,loader,*,accept=lambda value:value is not None,ttl=300):
    """Ancillary weather reads use the same non-blocking resource policy."""
    from weather_engine import resources
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    from uk_map_grid import grid_progress
    future=resources().request(key,loader,accept=accept,ttl=ttl)
    if not future.done():
        if get_script_run_ctx(suppress_warning=True):grid_progress(future)
        return resources().previous(key)
    try:value=future.result()
    except IngestionError:value=None
    if not accept(value):
        if get_script_run_ctx(suppress_warning=True):
            grid_progress(future,'Weather source recovery is scheduled in the background.',
                          retry=lambda:resources().request(key,loader,accept=accept,ttl=ttl))
        return resources().previous(key)
    return value


@st.cache_resource(max_entries=2,show_spinner=False)
def load_recorded(path,modified,coverage_revision=3):
    return load_bundle(Path(path))


def load_sites(model_key: str, refresh_bucket: int, coverage_revision=3):
    from uk_map_grid import regional_future
    return regional_future(refresh_bucket,model_key)


def _refresh_weather():
    from weather_engine import resources
    resources().clear()


def forecasts_pending(points,model_key,*,fields=None):
    """Queue work without waiting; explicit actions can resume on the next run."""
    from weather_engine import request_forecasts,complete_batch
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    from uk_map_grid import grid_progress
    future=request_forecasts(points,model_key,fields=fields)
    if future.done():
        try:valid=complete_batch(future.result(),fields)
        except IngestionError:valid=False
        if not valid and get_script_run_ctx(suppress_warning=True):
            grid_progress(future,'Weather recovery is scheduled in the background.',
                          retry=lambda:request_forecasts(points,model_key,fields=fields))
        return False
    if get_script_run_ctx(suppress_warning=True):grid_progress(future)
    return True


load_sites.clear=_refresh_weather


def load_grid(model_key: str, refresh_bucket: int):
    from weather_engine import read_forecasts
    grid = convergence_grid()
    points=tuple(p for row in grid for p in row)
    forecasts_pending(points,model_key,fields=GRID_VARIABLES)
    return read_forecasts(points,model_key,fields=GRID_VARIABLES)


def load_coordinate(latitude: float, longitude: float, model_key: str, refresh_bucket: int):
    from weather_engine import read_forecasts
    point = GeoPoint('inspector',f'{latitude:.3f}, {longitude:.3f}',latitude,longitude)
    forecasts_pending((point,),model_key)
    batch=read_forecasts((point,),model_key)
    if not batch.forecasts:raise IngestionError('Point forecast is refreshing in the background')
    return batch.forecasts[0]


def load_aviation(refresh_bucket: int):
    from weather_engine import transport
    return background_value('aviation',lambda:AviationWeatherClient(http=transport()).fetch(),
                            accept=lambda v:v is not None and not v.failures,ttl=60)


def _fetch_uk_aviation():
    from uk_airfields import reporting_stations
    from data_ingestion import AviationBatch
    from weather_engine import transport
    stations=reporting_stations();client=AviationWeatherClient(http=transport())
    batches=[client.fetch(stations[i:i+100]) for i in range(0,len(stations),100)]
    return AviationBatch({k:v for b in batches for k,v in b.metars.items()},
        {k:v for b in batches for k,v in b.tafs.items()},
        tuple(s for b in batches for s in b.missing_metars),tuple(s for b in batches for s in b.missing_tafs),
        tuple(f for b in batches for f in b.failures),{k:v for b in batches for k,v in b.fetched_at.items()},
        tuple(k for b in batches for k in b.cached_products))


def load_uk_aviation(refresh_bucket):
    return background_value('uk-aviation',_fetch_uk_aviation,
                            accept=lambda v:v is not None and not v.failures,ttl=60)


def fetch_radar_metadata():
    from weather_engine import transport
    body = transport().get_json('https://api.rainviewer.com/public/weather-maps.json',{},ttl_seconds=0).body
    if not isinstance(body,dict) or body.get('host') != 'https://tilecache.rainviewer.com':
        raise IngestionError('Unexpected radar tile host')
    frames = body.get('radar',{}).get('past',[])
    if not isinstance(frames,list) or not frames:
        raise IngestionError('No observed radar frames available')
    if any(not isinstance(frame,dict) or not isinstance(frame.get('time'),int) for frame in frames):
        raise IngestionError('Invalid radar frame metadata')
    frame = max(frames,key=lambda item:item['time'])
    if not isinstance(frame.get('time'),int) or not re.fullmatch(r'/v2/radar/[A-Za-z0-9_/-]+',str(frame.get('path',''))):
        raise IngestionError('Invalid radar frame metadata')
    return {'time':datetime.fromtimestamp(frame['time'],UTC),
            'url':body['host']+frame['path']+'/512/{z}/{x}/{y}/2/1_1.png'}


def load_radar(refresh_bucket):
    return background_value('radar-metadata',fetch_radar_metadata)


load_radar.clear=_refresh_weather


def load_calibrated_radar(url,refresh_bucket):
    from radar_rates import load_rate_grid,rate_overlay
    return background_value(('calibrated-radar',url),lambda:rate_overlay(load_rate_grid(url)))


def load_radar_precipitation(latitude,longitude,when,model_key,refresh_bucket):
    from precipitation import fetch_point_precipitation
    from weather_engine import resources
    key=('precipitation',latitude,longitude,when.isoformat(),model_key)
    value=background_value(key,lambda:fetch_point_precipitation(latitude,longitude,when,FORECAST_MODELS[model_key]),
                           accept=lambda v:isinstance(v,dict) and v.get('rate') is not None,ttl=300)
    if value is not None:return value
    raise IngestionError('Point precipitation is refreshing in the background')


def radar_point_result(coordinate,when,model_key,refresh_bucket,*,recorded=False):
    """Return a fresh, coordinate-specific result or an explicit missing flag."""
    latitude,longitude=coordinate
    result={'latitude':latitude,'longitude':longitude,'rate':None,
            'valid_at':when.isoformat(),'source':'Open-Meteo',
            'model':FORECAST_MODELS[model_key].api_name}
    if recorded:
        return {**result,'error':'Recorded snapshot: live point queries are disabled.'}
    try:
        return load_radar_precipitation(latitude,longitude,when,model_key,refresh_bucket)
    except (IngestionError,ValueError) as exc:
        return {**result,'error':f'Point precipitation unavailable: {exc}'}


load_radar_precipitation.clear=_refresh_weather


def profiles_at(forecasts,when):
    profiles={}
    for forecast in forecasts:
        try:
            profile=Profile.from_forecast(forecast,when)
            if profile.values.get('temperature_2m') is not None:
                profiles[forecast.location.key]=profile
        except KeyError:
            continue
    return profiles


