"""Non-blocking, cycle-scoped five-day UK grid; no widget values in cache keys."""
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import Lock
import math
import numpy as np
import streamlit as st

from config import FORECAST_MODELS, OPEN_METEO_URL, Settings, forecast_locations
from data_ingestion import (FetchFailure, ForecastBatch, IngestionError, JsonHttpClient,
                            JsonResponse, SchemaError, OpenMeteoClient, parse_forecast, utc_datetime)
from map_weather import national_grid, wind_payload
from forecast_coverage import complete_forecasts

_pool = ThreadPoolExecutor(max_workers=12, thread_name_prefix='uk-map-grid')


def grid_variables():
    """Current schema, including high-cloud levels, also after a hot deployment."""
    from config import forecast_variables
    return forecast_variables()


class GridHttpClient(JsonHttpClient):
    """One attempt per chunk, without the alert client's pacing/retry sleeps.

    A cycle schedules each model once. A 429 is retained as missing coverage;
    it never triggers another request within the cycle.
    """
    def __init__(self, **kwargs):
        super().__init__(replace(Settings.from_env(), http_timeout_seconds=3.5,
                                 http_max_attempts=1, http_workers=12), **kwargs)

    def _pace(self, host, cost=1.0):
        pass


def fetch_chunk(points, model, http):
    response = http.get_json(OPEN_METEO_URL, {
        'latitude': ','.join(str(p.latitude) for p in points),
        'longitude': ','.join(str(p.longitude) for p in points),
        'models': model.api_name, 'hourly': ','.join(grid_variables()),
        'forecast_hours': 120, 'past_hours': 24, 'timezone': 'GMT', 'timeformat': 'unixtime',
        'wind_speed_unit': 'ms', 'temperature_unit': 'celsius', 'precipitation_unit': 'mm',
        'cell_selection': 'land',
    }, ttl_seconds=0)
    bodies = [response.body] if isinstance(response.body, dict) else response.body
    if not isinstance(bodies,list) or len(bodies) != len(points):
        raise SchemaError('UK grid response count does not match request')
    forecasts = []
    for index, (point, body) in enumerate(zip(points, bodies)):
        if not isinstance(body, dict) or body.get('location_id', index) != index:
            raise SchemaError('UK grid response order is invalid')
        # Request five days even for short-range models; null tails stay unknown.
        forecasts.append(parse_forecast(JsonResponse(body, response.fetched_at), point,
                                        replace(model, horizon_hours=119)))
    return tuple(forecasts)


@dataclass(frozen=True)
class GridSnapshot:
    batch: ForecastBatch
    time_index: dict
    values: np.ndarray
    wind: dict

    def hour(self, when):
        """O(1) read-only NumPy view, indexed by exact UTC valid time."""
        index = self.time_index.get(utc_datetime(when))
        return None if index is None else self.values[index]


def assemble(futures, chunks, model, anchor):
    forecasts, failures = [], []
    for future, points in zip(futures, chunks):
        try:
            forecasts.extend(future.result())
        except (IngestionError, OSError, ValueError) as exc:
            failures.extend(FetchFailure(p.key, model.api_name, str(exc)) for p in points)
    return make_snapshot(forecasts, failures, anchor)


def make_snapshot(forecasts, failures, anchor):
    forecasts = tuple(sorted(forecasts, key=lambda f: f.location.key))
    times = tuple(sorted({t for f in forecasts for t in f.times}))
    index = {t: i for i, t in enumerate(times)}
    values = np.full((len(times), len(forecasts), len(grid_variables())), np.nan, dtype=np.float32)
    for column, forecast in enumerate(forecasts):
        positions = [index[t] for t in forecast.times]
        values[positions, column, :] = np.array([forecast.hourly.get(v,(None,)*len(forecast.times)) for v in grid_variables()], dtype=np.float32).T
    values.flags.writeable = False
    return GridSnapshot(ForecastBatch(forecasts, tuple(failures)), index, values,
                        wind_payload(forecasts, anchor))


class UKGridBundle:
    """One resource per cycle, with separate lazy results for each named model."""
    def __init__(self, cycle):
        self.coverage_revision = 2
        self.cycle = cycle
        self.anchor = datetime.fromtimestamp(cycle * 1800, UTC)
        self._lock = Lock()
        self._models = {}
        self._raw = None

    def completed(self,model_key):
        """Read an existing result without scheduling or waiting for any work."""
        with self._lock:
            result=self._models.get(model_key)
        if result is None or not result.done() or result.exception() is not None:
            return None
        return result.result()

    def request(self, model_key):
        """Schedule once, return immediately. Day/hour/page/task are never inputs."""
        with self._lock:
            if model_key in self._models:
                return self._models[model_key]
            result = Future()
            self._models[model_key] = result
            if self._raw is None:
                self._start_raw()
            def completed(raw):
                def publish():
                    try:
                        batch = raw.result()
                        forecasts = complete_forecasts(batch.forecasts, FORECAST_MODELS[model_key])
                        result.set_result(make_snapshot(forecasts, batch.failures, self.anchor))
                    except Exception as exc:
                        result.set_exception(exc)
                _pool.submit(publish)
            self._raw.add_done_callback(completed)
            return result

    def _start_raw(self):
        """One shared cycle of independent requests, at most 12 in flight.

        Switching the selected model reuses these raw arrays. An unavailable
        provider/chunk cannot discard a healthy fallback provider's response.
        """
        self._raw = Future()
        points = national_grid()
        size = math.ceil(len(points) / 12)
        chunks = tuple(points[i:i+size] for i in range(0, len(points), size))
        http = GridHttpClient()
        jobs = [(_pool.submit(fetch_chunk, chunk, model, http), chunk, model)
                for chunk in chunks for model in FORECAST_MODELS.values()]
        remaining = len(jobs)
        done_lock = Lock()
        def completed(_):
            nonlocal remaining
            with done_lock:
                remaining -= 1
                if remaining:
                    return
            forecasts, failures = [], []
            for job, chunk, model in jobs:
                try:
                    forecasts.extend(job.result())
                except (IngestionError, OSError, ValueError) as exc:
                    failures.extend(FetchFailure(p.key, model.api_name, str(exc)) for p in chunk)
            self._raw.set_result(ForecastBatch(tuple(forecasts), tuple(failures)))
        for job, _, _ in jobs:
            job.add_done_callback(completed)


@st.cache_resource(ttl=1800, max_entries=2, show_spinner=False)
def load_uk_map_grid(forecast_cycle_30min_bucket, coverage_revision=2):
    return UKGridBundle(forecast_cycle_30min_bucket)


def session_grid(cycle):
    bundle = st.session_state.get('uk_map_grid_bundle')
    if bundle is None or bundle.cycle != cycle or getattr(bundle,'coverage_revision',0) != 2:
        bundle = load_uk_map_grid(cycle)
        st.session_state['uk_map_grid_bundle'] = bundle
    return bundle


@st.cache_resource(ttl=1800,max_entries=2,show_spinner=False)
def regional_sources(cycle, coverage_revision=2):
    def fetch():
        http=GridHttpClient()
        client=OpenMeteoClient(http.settings,http=http)
        return client.fetch_batched(forecast_locations(),FORECAST_MODELS.values(),batch_size=8)
    return _pool.submit(fetch)


@st.cache_resource(ttl=1800,max_entries=6,show_spinner=False)
def regional_future(cycle,model_key, coverage_revision=2):
    result = Future()
    def completed(raw):
        def publish():
            try:
                batch = raw.result()
                result.set_result(ForecastBatch(complete_forecasts(batch.forecasts,FORECAST_MODELS[model_key]),batch.failures))
            except Exception as exc:
                result.set_exception(exc)
        _pool.submit(publish)
    regional_sources(cycle).add_done_callback(completed)
    return result


@st.fragment(run_every='500ms')
def grid_progress(future,message="UK weather grid is updating in the background. You can keep using the app."):
    """The active page polls only its pending work; navigation stays usable."""
    if future.done():
        st.rerun(scope='app')
    st.caption(message)
