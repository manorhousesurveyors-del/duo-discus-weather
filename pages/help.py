"""Help page; no weather fetching on navigation."""
from config import LOCAL_TIMEZONE
from help_guide import render_help

def render(ctx):
    catalog = ctx.catalog
    recorded = ctx.recorded
    model_key = ctx.model_key
    day = ctx.day
    bucket = ctx.bucket
    anchor = ctx.anchor
    forecasts = ctx.forecasts
    render_help(catalog,None,None,(),forecasts=forecasts,day=day,today=anchor.astimezone(LOCAL_TIMEZONE).date(),model_key=model_key,bucket=bucket,recorded=bool(recorded))
    return
