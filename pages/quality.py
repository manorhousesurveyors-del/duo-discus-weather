"""Quality page; no weather fetching on navigation."""
import streamlit as st
from planning_ui import catalog_panel,weather_table

def render(ctx):
    catalog = ctx.catalog
    forecasts = ctx.forecasts
    errors = ctx.errors
    catalog_panel(catalog)
    st.divider();st.subheader('Forecast quality')
    weather_table([{'Location':f.location.name,'Retrieved UTC':f.fetched_at.isoformat(),'Available through':str(f.available_through),'Gap support':', '.join(f.supplemental_sources) or 'None','Unavailable fields':', '.join(f.unavailable_variables)} for f in forecasts],
        {'Location':'Requested forecast coordinate.','Retrieved UTC':'Oldest contributing response retrieval time.','Available through':'Last non-null surface-temperature hour.','Gap support':'Models or parcel estimates filling missing fields, or a complete fallback sounding when coverage improves. Exact field/hour sources are retained.','Unavailable fields':'Fields still absent after model support. CAPE is optional for thermal ratings.'})
    if errors:st.warning('\n'.join(dict.fromkeys(errors)))
    return
