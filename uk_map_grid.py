"""UI adapters for the shared weather engine; no independent HTTP/cache policy."""
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
import numpy as np
import streamlit as st

from config import FORECAST_MODELS, LOCAL_TIMEZONE, default_forecast_model, forecast_locations
from data_ingestion import FetchFailure, ForecastBatch, IngestionError, OpenMeteoClient, utc_datetime
from forecast_coverage import COVERAGE_REVISION, complete_regional_forecasts
from map_weather import national_grid, wind_payload
from weather_engine import ForecastHttpClient, request_forecasts, resources, prefer_verified

WIND_GRID_REVISION=2
_pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='weather-products')
_derived={}
_lock=Lock()


def grid_variables():
    from config import forecast_variables
    return forecast_variables()


class GridHttpClient(ForecastHttpClient):
    """Compatibility alias; all production requests use weather_engine.transport."""
    def __init__(self,*,national=False,**kwargs):super().__init__(**kwargs)


def fetch_chunk(points,model,http):
    from dataclasses import replace
    good,bad=OpenMeteoClient(http=http)._fetch_chunk(points,replace(model,horizon_hours=119),grid_variables())
    if bad:raise IngestionError(bad[0].message)
    return tuple(good)


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
    """A lightweight cycle view of process-wide weather, shared across sessions."""
    def __init__(self,cycle):
        self.cycle=cycle;self.coverage_revision=COVERAGE_REVISION;self.wind_grid_revision=WIND_GRID_REVISION
        self.anchor=datetime.fromtimestamp(cycle*1800,UTC);self._models={};self._sources={}

    def request(self,model_key):
        source=request_forecasts(national_grid(),model_key)
        if self._sources.get(model_key) is not source:
            self._sources[model_key]=source
            self._models[model_key]=transform(source,('grid',self.cycle,model_key),
                lambda batch:self._snapshot(batch,model_key))
        return self._models[model_key]

    def _snapshot(self,batch,model_key):
        usable=prefer_verified(batch,national_grid(),model_key)
        return make_snapshot(usable.forecasts,usable.failures,self.anchor)

    def completed(self,model_key):
        result=self._models.get(model_key)
        return result.result() if result and result.done() and result.exception() is None else None


def transform(source,key,function):
    """CPU products run off the UI thread even when the source is already ready."""
    key=(key,source)
    with _lock:
        if key in _derived:return _derived[key]
        result=Future();_derived[key]=result
        if len(_derived)>48:
            for old in list(_derived):
                if old!=key and _derived[old].done():del _derived[old]
                if len(_derived)<=48:break
    def done(raw):
        def compute():
            try:result.set_result(function(raw.result()))
            except Exception as error:result.set_exception(error)
        _pool.submit(compute)
    source.add_done_callback(done)
    return result


@st.cache_resource(ttl=1800,max_entries=2,show_spinner=False)
def load_uk_map_grid(forecast_cycle_30min_bucket,coverage_revision=3,wind_grid_revision=2):
    return UKGridBundle(forecast_cycle_30min_bucket)


def session_grid(cycle):
    bundle=st.session_state.get('uk_map_grid_bundle')
    if bundle is None or bundle.cycle!=cycle or getattr(bundle,'wind_grid_revision',0)!=WIND_GRID_REVISION:
        bundle=load_uk_map_grid(cycle);st.session_state['uk_map_grid_bundle']=bundle
    return bundle


def regional_sources(cycle,model_key=None,coverage_revision=3):
    return request_forecasts(forecast_locations(),model_key or default_forecast_model())


def fetch_regional_sources(cycle):
    """CLI compatibility; UI callers use the non-blocking regional_future."""
    return regional_sources(cycle).result()


def regional_future(cycle,model_key=None,coverage_revision=3):
    model_key=model_key or default_forecast_model()
    first_day=datetime.fromtimestamp(cycle*1800,UTC).astimezone(LOCAL_TIMEZONE).date()
    def product(batch):
        usable=prefer_verified(batch,forecast_locations(),model_key)
        return ForecastBatch(complete_regional_forecasts(usable.forecasts,FORECAST_MODELS[model_key],first_day),usable.failures)
    return transform(regional_sources(cycle,model_key),('regional',cycle,model_key),product)


# Test/refresh compatibility. Invalidating TTLs does not discard live work or
# provider cooldowns, and is deliberately narrower than st.cache_data.clear().
regional_sources.clear=regional_future.clear=lambda:resources().clear()


@st.fragment(run_every='2s')
def grid_progress(future,message='Weather updates in the background. Maps and navigation remain usable.',retry=None):
    current=retry() if retry else future
    if current is not future or (future.done() and retry is None):st.rerun(scope='app')
    st.caption(message)
