"""Owned session-state migration; saved tasks/preferences are never swept away."""
SCHEMA_VERSION=1
OBSOLETE_KEYS={'_uk_grid_hour','_weather_map_state','forecast_coverage_revision'}


def migrate(state):
    if state.get('session_schema_version')==SCHEMA_VERSION:return
    for key in list(state):
        if key in OBSOLETE_KEYS or key.startswith('forecast_fallback:'):
            state.pop(key,None)
    # Transport futures from the previous implementation must not pin a failed
    # 30-minute download. The engine owns new futures, not each user's session.
    state.pop('uk_map_grid_bundle',None)
    for name in ('_map_documents','_map_products'):state.pop(name,None)
    state['session_schema_version']=SCHEMA_VERSION
