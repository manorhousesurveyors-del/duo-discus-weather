"""Nine-anchor synchronous forecasts with live failover and explicit provenance.

No provider gate, cooldown state, background worker or scheduled rerun exists
in the dashboard transport. HTTP errors leave the live cache immediately.
"""
from dataclasses import replace
from datetime import UTC, datetime
import streamlit as st
from config import FORECAST_MODELS, Settings, forecast_locations
from data_ingestion import (OPEN_METEO_URL, FetchFailure, ForecastBatch, IngestionError,
                            JsonHttpClient, JsonResponse, SchemaError, parse_forecast)
from forecast_sanitizer import sanitize
from weather_anchors import ANCHORS, sample_anchors

API_REVISION = 3
SURFACE_VARIABLES = (
    'temperature_2m','dew_point_2m','wind_speed_10m','wind_direction_10m',
    'wind_gusts_10m','shortwave_radiation','cloud_cover','cloud_cover_low',
    'cloud_cover_mid','cloud_cover_high','precipitation','pressure_msl',
    'boundary_layer_height',
) + tuple(f'{v}_{p}hPa' for p in (925,850,700,300)
          for v in ('wind_speed','wind_direction'))
# These standard sounding fields are needed for real inversion/moisture
# evidence in the day rating. Surface dewpoint alone cannot describe them.
REGIONAL_VARIABLES = SURFACE_VARIABLES + tuple(
    f'{v}_{p}hPa' for p in (925,850,700)
    for v in ('temperature','geopotential_height','relative_humidity'))
WIND_VARIABLES = ('temperature_2m','pressure_msl','wind_speed_10m',
                  'wind_direction_10m') + tuple(
    f'{v}_{p}hPa' for p in (925,850,700,300)
    for v in ('wind_speed','wind_direction'))


class SyncHttpClient(JsonHttpClient):
    """One small request; no sleep, retry loop or provider cooldown gate."""
    def __init__(self, **kwargs):
        super().__init__(replace(Settings.from_env(), http_timeout_seconds=1.25,
                                 http_max_attempts=1), **kwargs)
        original=self.opener
        def identified(request, **options):
            request.add_header('User-Agent', 'DuoDiscusSoaringWeather/1.0 github.com/manorhousesurveyors-del/duo-discus-weather')
            return original(request, **options)
        self.opener=identified

    def _pace(self, host, cost=1.):
        return None


@st.cache_resource(show_spinner=False)
def transport():
    return SyncHttpClient()


def _request(points, model, fields):
    """One bounded batched GET, with the exact requested schema validated."""
    response = transport().get_json(OPEN_METEO_URL, {
        'latitude': ','.join(str(p.latitude) for p in points),
        'longitude': ','.join(str(p.longitude) for p in points),
        'models': model.api_name, 'hourly': ','.join(fields),
        'forecast_days': 5, 'timezone': 'GMT', 'timeformat': 'unixtime',
        'wind_speed_unit': 'ms', 'temperature_unit': 'celsius',
        'precipitation_unit': 'mm', 'cell_selection': 'nearest',
    }, ttl_seconds=0)
    bodies = [response.body] if isinstance(response.body, dict) else response.body
    if not isinstance(bodies, list) or len(bodies) != len(points):
        raise SchemaError('Multi-coordinate response count does not match request')
    forecasts = []
    for index, (point, body) in enumerate(zip(points, bodies)):
        if not isinstance(body, dict) or body.get('location_id', index) != index:
            raise SchemaError('Multi-coordinate response order is invalid')
        forecast = sanitize(parse_forecast(
            JsonResponse(body, response.fetched_at, response.from_cache), point, model, variables=fields))
        if len(forecast.times) < 120:
            raise SchemaError('Forecast response has fewer than five days of hourly samples')
        forecasts.append(forecast)
    if not forecasts:
        raise SchemaError('Forecast response contains no locations')
    return tuple(forecasts)


ESSENTIAL_REGIONAL_FIELDS = ('temperature_2m','dew_point_2m','shortwave_radiation',
                            'precipitation','wind_speed_10m','wind_direction_10m',
                            'pressure_msl','cloud_cover_low')


@st.cache_data(ttl=1800, show_spinner=False, max_entries=12)
def live_anchor_bundle(model_key, refresh_bucket):
    """Try the nine-coordinate primary, then one bounded MET Norway batch.

    Only real provider data is cached here. A failed primary never delays or
    disables the alternate provider or a later explicit Refresh action.
    """
    model=replace(FORECAST_MODELS[model_key],horizon_hours=119)
    anchor=datetime.fromtimestamp(refresh_bucket*1800,UTC)
    try:
        forecasts=_request(ANCHORS,model,REGIONAL_VARIABLES)
        missing={f.location:tuple(key for key in ESSENTIAL_REGIONAL_FIELDS
                 if any(v is None for v in f.hourly.get(key,(None,)))) for f in forecasts}
        affected=tuple(point for point in ANCHORS if missing[point])
        if affected:
            fields=tuple(dict.fromkeys(('temperature_2m',*(key for p in affected for key in missing[p]))))
            donor=replace(FORECAST_MODELS['icon_eu' if model_key=='ifs025' else 'ifs025'],horizon_hours=119)
            support=_request(affected,donor,fields)
            from forecast_coverage import complete_forecasts
            forecasts=complete_forecasts((*forecasts,*support),model)
        # Missing optional upper fields do not discard a usable surface map.
        if any(any(v is None for v in f.hourly.get(key,(None,)))
               for f in forecasts for key in ESSENTIAL_REGIONAL_FIELDS):
            raise IngestionError('Primary essential surface fields are incomplete')
    except (IngestionError,OSError) as primary_error:
        from met_norway import fetch_met_forecasts
        met=fetch_met_forecasts(ANCHORS,http=transport(),anchor=anchor)
        if not met.forecasts:
            raise IngestionError('Both live weather services could not be reached. '
                                 +str(primary_error)) from primary_error
        forecasts=met.forecasts
    from weather_disk_cache import save_live
    save_live(forecasts,model_key)
    return ForecastBatch(tuple(forecasts),())


def _resolve_bundle(model_key,refresh_bucket):
    try:
        return live_anchor_bundle(model_key,refresh_bucket)
    except (IngestionError,OSError) as exc:
        from weather_disk_cache import load_live
        anchor=datetime.fromtimestamp(refresh_bucket*1800,UTC)
        stored=load_live(model_key,anchor)
        if stored:
            return ForecastBatch(stored,(FetchFailure('weather','stored',
                'Using stored live forecast with its original retrieval time; live services did not respond.'),))
        from weather_demo import demo_forecasts
        return ForecastBatch(demo_forecasts(ANCHORS,anchor,model_key),
            (FetchFailure('weather','demonstration','Both live weather services could not be reached.'),))


def display_bundle(model_key,refresh_bucket):
    """Reuse the displayed snapshot until explicit Refresh or a new cycle.

    A demonstration is a labelled UI product, never a cached HTTP success. This
    prevents day/hour/mode changes from spending another request. Refresh always
    discards the display product and retries the live providers without a gate.
    """
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    if get_script_run_ctx(suppress_warning=True) is None:
        return _resolve_bundle(model_key,refresh_bucket)
    key='display_weather_bundle:'+model_key
    value=st.session_state.get(key)
    if value is None or value[0]!=refresh_bucket:
        value=(refresh_bucket,_resolve_bundle(model_key,refresh_bucket))
        st.session_state[key]=value
    return value[1]


@st.cache_data(ttl=1800,show_spinner=False,max_entries=48)
def _samples(run_id,points,product,_forecasts):
    return sample_anchors(_forecasts,points,wind_only=product=='wind')


def fetch_forecasts(points,model_key,refresh_bucket,product='regional'):
    points=tuple(sorted(points,key=lambda p:p.key))
    if not points:return ForecastBatch((),())
    bundle=display_bundle(model_key,refresh_bucket)
    from app_cache import forecast_run_id
    forecasts=_samples(forecast_run_id(bundle.forecasts),points,product,bundle.forecasts)
    return ForecastBatch(forecasts,bundle.failures)


def cached_forecasts(points,model_key,refresh_bucket,product='regional'):
    """Compatibility boundary; live bundle and spatial products own their caches."""
    return fetch_forecasts(points,model_key,refresh_bucket,product)


def regional_forecasts(model_key,refresh_bucket):
    return fetch_forecasts(forecast_locations(),model_key,refresh_bucket)


def clear():
    live_anchor_bundle.clear()
    _samples.clear()
    for key in list(st.session_state):
        if key.startswith('display_weather_bundle:'):
            st.session_state.pop(key,None)


cached_forecasts.clear=clear
