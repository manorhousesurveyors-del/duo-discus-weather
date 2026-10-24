"""Lazy shared resources and bounded forecast-derived caches for the dashboard."""
from pathlib import Path
import streamlit as st


def file_version(paths):
    return tuple((str(p),p.stat().st_mtime_ns,p.stat().st_size) for p in paths if p.exists())


@st.cache_resource(show_spinner=False,max_entries=4)
def catalog_resource(version):
    import bga_turnpoints
    return bga_turnpoints.active_catalog()


def forecast_run_id(forecasts):
    # Retrieval identity changes on refresh; location/model prevent cross-grid reuse.
    return tuple((f.location.key,f.location.latitude,f.location.longitude,f.model,
                  f.fetched_at.isoformat(),len(f.times)) for f in forecasts)


@st.cache_data(ttl=1800,show_spinner=False,max_entries=48)
def day_summary(run_id,origin,first_day,loading,model,_forecasts):
    from datetime import timedelta
    from day_rating import rate_day
    from task_planning import classify_day
    ratings=tuple(rate_day(_forecasts,origin,first_day+timedelta(days=i),loading,preference=(model,)) for i in range(5))
    classes=tuple(classify_day(_forecasts,origin,first_day+timedelta(days=i),loading,preference=(model,)) for i in range(5))
    return ratings,classes


@st.cache_data(ttl=1800,show_spinner=False,max_entries=32)
def assessed_task(run_id,points,day,loading,model,_forecasts):
    from task_planning import assess_task
    return assess_task(points,_forecasts,day,loading,preference=(model,))


@st.cache_data(ttl=3600,show_spinner=False,max_entries=32)
def gemini_discovery(fingerprint,auth,_credential):
    # Only a one-way credential fingerprint is a cache key. Cached output contains
    # model names and redacted diagnostics, never the key or an authenticated URL.
    from gemini_rest import GeminiREST
    return GeminiREST(_credential,auth).connect()
