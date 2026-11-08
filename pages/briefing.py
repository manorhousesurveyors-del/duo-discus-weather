"""Briefing page; no weather fetching on navigation."""
from config import LOCAL_TIMEZONE
from planning_ui import briefing_panel

def render(ctx):
    catalog = ctx.catalog
    recorded = ctx.recorded
    model_key = ctx.model_key
    day = ctx.day
    loading = ctx.loading
    bucket = ctx.bucket
    anchor = ctx.anchor
    forecasts = ctx.forecasts
    briefing_panel(catalog,day,loading,model_key,bucket,forecasts,None,None,(),recorded=bool(recorded),today=anchor.astimezone(LOCAL_TIMEZONE).date())
    return
