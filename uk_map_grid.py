"""Synchronous five-day map products, reused until an explicit interaction.

Completed Future wrappers preserve the older public adapter API. They never
start workers, register timers, or cause the application to rerun.
"""
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import UTC, datetime
import numpy as np
import streamlit as st

from config import FORECAST_MODELS, LOCAL_TIMEZONE, default_forecast_model, forecast_locations
from data_ingestion import FetchFailure, ForecastBatch, IngestionError, OpenMeteoClient, utc_datetime
from forecast_coverage import COVERAGE_REVISION, complete_regional_forecasts
from map_weather import national_grid, wind_payload
from weather_engine import ForecastHttpClient

WIND_GRID_REVISION=3


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
        self.anchor=datetime.fromtimestamp(cycle*1800,UTC);self._models={}

    def request(self,model_key):
        from sync_weather import fetch_forecasts
        existing=self._models.get(model_key)
        if existing is not None:return existing
        batch=fetch_forecasts(national_grid(),model_key,self.cycle,product='wind')
        result=_completed(make_snapshot(batch.forecasts,batch.failures,self.anchor))
        # Failed attempts must not become a pinned 30-minute grid. A subsequent
        # user interaction may try again; no timer retries this request.
        if batch.forecasts and not batch.failures:self._models[model_key]=result
        return result

    def completed(self,model_key):
        result=self._models.get(model_key)
        return result.result() if result and result.done() and result.exception() is None else None


def transform(source,key,function):
    """Compatibility adapter: complete a pure product in the current pass."""
    result=Future()
    try:result.set_result(function(source.result(timeout=0)))
    except Exception as error:result.set_exception(error)
    return result


def _completed(value):
    future=Future();future.set_result(value)
    return future


@st.cache_resource(ttl=1800,max_entries=2,show_spinner=False)
def load_uk_map_grid(forecast_cycle_30min_bucket,coverage_revision=3,wind_grid_revision=3):
    return UKGridBundle(forecast_cycle_30min_bucket)


def session_grid(cycle):
    bundle=st.session_state.get('uk_map_grid_bundle')
    if bundle is None or bundle.cycle!=cycle or getattr(bundle,'wind_grid_revision',0)!=WIND_GRID_REVISION:
        bundle=load_uk_map_grid(cycle);st.session_state['uk_map_grid_bundle']=bundle
    return bundle


def synchronous_snapshot(model_key,cycle):
    """Return the complete grid in this render pass; also marks the new API."""
    return session_grid(cycle).request(model_key).result()


def regional_sources(cycle,model_key=None,coverage_revision=3):
    from sync_weather import fetch_forecasts
    return _completed(fetch_forecasts(forecast_locations(),model_key or default_forecast_model(),cycle))


def fetch_regional_sources(cycle):
    """CLI compatibility for the synchronous regional product."""
    return regional_sources(cycle).result()


def regional_future(cycle,model_key=None,coverage_revision=3):
    model_key=model_key or default_forecast_model()
    first_day=datetime.fromtimestamp(cycle*1800,UTC).astimezone(LOCAL_TIMEZONE).date()
    def product(batch):
        return ForecastBatch(complete_regional_forecasts(batch.forecasts,FORECAST_MODELS[model_key],first_day),batch.failures)
    return transform(regional_sources(cycle,model_key),('regional',cycle,model_key),product)


def clear():
    from sync_weather import cached_forecasts
    cached_forecasts.clear()
    load_uk_map_grid.clear()


regional_sources.clear=regional_future.clear=clear


def grid_progress(future,message='The requested calculation is still running.',retry=None):
    """Explicit result check for optional jobs; never poll or schedule a rerun.

    Weather readers do not call this adapter. It remains for optional assistant
    jobs, whose result can be checked by the user without refreshing the page
    on a timer.
    """
    st.caption(message)
    if st.button('Check result',key=f'job-check:{id(future)}',
                 help='Check this requested calculation once. The page does not refresh automatically.'):
        if retry:retry()
        st.rerun()
