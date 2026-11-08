"""One-pass dashboard forecasts, with no workers, polling or scheduled reruns.

Only successful parsed datasets enter Streamlit's 30-minute cache. Optional
model fields do not determine whether the map can render. A failed refresh can
reuse real, timestamped data; it never manufactures a flying forecast.
"""
from collections import OrderedDict
from dataclasses import replace
from datetime import UTC, datetime
from threading import RLock

import streamlit as st

from config import FORECAST_MODELS, forecast_locations
from data_ingestion import (OPEN_METEO_URL, FetchFailure, ForecastBatch,
                            IngestionError, JsonResponse, SchemaError, parse_forecast)
from forecast_sanitizer import sanitize
from weather_engine import ForecastHttpClient

API_REVISION = 2
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


class SyncHttpClient(ForecastHttpClient):
    """Four-second socket timeout, one attempt, zero retry/pacing sleeps."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.settings = replace(self.settings, http_timeout_seconds=4.0)


@st.cache_resource(show_spinner=False)
def transport():
    return SyncHttpClient()


@st.cache_resource(show_spinner=False)
def _last_good():
    return OrderedDict(), RLock()


class IncompleteForecast(IngestionError):
    """Carry usable primary data out of cache_data without caching a failure."""
    def __init__(self, forecasts, model, message):
        super().__init__(message)
        self.batch = ForecastBatch(tuple(forecasts), (FetchFailure('weather', model, message),))


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


@st.cache_data(ttl=1800, show_spinner=False, max_entries=48)
def cached_forecasts(points, model_key, refresh_bucket, product='regional'):
    """Five-day batch, with at most one selective synchronous donor request.

    A healthy primary uses one GET. Model-specific essential-column gaps (for
    example UKMO shortwave beyond 72 hours) request only those columns at the
    affected coordinates, plus the parser's temperature field. Failed support
    returns usable primary data outside this cache; no retries or timers run.
    """
    model = replace(FORECAST_MODELS[model_key], horizon_hours=119)
    fields = WIND_VARIABLES if product == 'wind' else REGIONAL_VARIABLES
    forecasts = _request(points, model, fields)
    if product == 'wind':
        return ForecastBatch(forecasts, ())
    missing = {f.location:tuple(key for key in ESSENTIAL_REGIONAL_FIELDS
                               if any(v is None for v in f.hourly.get(key, (None,))))
               for f in forecasts}
    affected = tuple(point for point in points if missing[point])
    if not affected:
        return ForecastBatch(forecasts, ())
    donor_fields = tuple(dict.fromkeys(('temperature_2m', *(
        key for point in affected for key in missing[point]))))
    donor = replace(FORECAST_MODELS['icon_eu' if model_key=='ifs025' else 'ifs025'], horizon_hours=119)
    try:
        support = _request(affected, donor, donor_fields)
    except (IngestionError, OSError) as exc:
        raise IncompleteForecast(forecasts, model.api_name,
                                 'Some forecast fields remain missing; donor request failed: '+str(exc)) from exc
    from forecast_coverage import complete_forecasts
    completed = complete_forecasts((*forecasts, *support), model)
    if any(any(v is None for v in f.hourly.get(key, (None,)))
           for f in completed for key in ESSENTIAL_REGIONAL_FIELDS):
        raise IncompleteForecast(completed, model.api_name,
                                 'Some essential forecast fields remain missing after one donor request.')
    return ForecastBatch(completed, ())


def fetch_forecasts(points, model_key, refresh_bucket, product='regional'):
    """Resolve now, or return stored real data with an explicit failure note."""
    points = tuple(sorted(points, key=lambda p: p.key))
    if not points:
        return ForecastBatch((), ())
    key = (points, model_key, product)
    previous, lock = _last_good()
    try:
        batch = cached_forecasts(points, model_key, refresh_bucket, product)
    except IncompleteForecast as exc:
        return exc.batch
    except (IngestionError, OSError) as exc:
        with lock:
            saved = previous.get(key)
        # Do not extend the stored forecast's valid times into a new day.
        stored = tuple(f for f in (saved.forecasts if saved else ())
                       if f.times[-1] >= datetime.fromtimestamp(refresh_bucket*1800, UTC))
        note = ('Using stored forecast with its original retrieval time. ' if stored else
                'Weather request failed; no stored forecast covers this period. ')
        return ForecastBatch(stored, (FetchFailure('weather', FORECAST_MODELS[model_key].api_name,
                                                   note + str(exc)),))
    with lock:
        previous[key] = batch
        previous.move_to_end(key)
        while len(previous) > 48:
            previous.popitem(last=False)
    return batch


def regional_forecasts(model_key, refresh_bucket):
    return fetch_forecasts(forecast_locations(), model_key, refresh_bucket)


def clear():
    """Explicit user refresh: invalidate successes, retain honest outage backup."""
    cached_forecasts.clear()
