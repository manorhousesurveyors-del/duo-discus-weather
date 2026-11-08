"""Task planning page; only corridor requests, never a national-grid fetch."""
import streamlit as st
from config import FORECAST_MODELS
from data_ingestion import IngestionError
from app_cache import forecast_run_id
from map_view import build_map

from planning_ui import task_weather,task_result,task_builder
from task_planning import validate_task
from route_tools import optimizer_panel,task_sheet_panel

def render(ctx):
    errors=ctx.errors
    catalog = ctx.catalog
    recorded = ctx.recorded
    model_key = ctx.model_key
    points = ctx.points
    day = ctx.day
    when = ctx.when
    loading = ctx.loading
    bucket = ctx.bucket
    forecasts = ctx.forecasts
    assessment=None; task_forecasts=()
    if len(points)>=2:
        try:
            validate_task(points)
            with st.spinner('Evaluating your task corridor and launch windows…'):
                task_forecasts,failures=task_weather(points,forecasts,model_key,bucket,recorded=bool(recorded))
                errors+=failures
                from app_cache import assessed_task,forecast_run_id
                assessment=assessed_task(forecast_run_id(task_forecasts),points,day,loading,FORECAST_MODELS[model_key].api_name,_forecasts=task_forecasts)
        except (ValueError,IngestionError) as exc: errors+=(f'Task: {exc}',)
    combined=tuple({(f.location.key,f.model):f for f in (*forecasts,*task_forecasts)}.values())
    map_forecasts=combined
    aviation=radar=convergence=rate_grid=radar_point=None
    from route_analysis import planner_panel
    planner_panel(catalog,day,when,loading,model_key,bucket,combined,points,assessment,aviation,map_builder=build_map,recorded=bool(recorded))
    task_result(points,assessment,key='planner')
    with st.expander('Build, reorder or clear task',expanded=not points): task_builder(catalog,day,location='planner')
    optimizer_panel(catalog,day,loading,model_key,bucket,map_forecasts,recorded=bool(recorded))
    task_sheet_panel(points,assessment,day,loading,catalog,key='planner_sheet')
    return
