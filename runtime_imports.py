"""Compatibility checks for Streamlit reruns during an in-place deployment.

Only already-loaded application modules with an outdated API are reloaded.
OGN is optional and owns a background thread, so it is never blindly reloaded.
"""
import importlib
import inspect
import logging
import sys
from threading import RLock

# Bump when new compatibility contracts must reach an already-running app.
API_REVISION = 5

_lock = RLock()
_log = logging.getLogger(__name__)

# Dependency order matters: a reloaded consumer must bind the current providers.
# Empty tuples check function presence; other tuples check newly added keywords.
LAZY_APIS = {
    'config': {'forecast_variables': (), 'duo_circling_sink_knots': ()},
    'data_ingestion': {'parse_forecast': ('variables',), 'split_model_response': ()},
    'soaring_physics': {'evaluate_thermals': ('loading',), 'thermal_usable_depth': ()},
    'task_planning': {'assess_task': ()},
    'map_weather': {'soaring_samples': ()},
    'day_rating': {'rate_day': ()},
    'alerts': {'load_bundle': ()},
    'uk_map_grid': {'grid_variables': ()},
    'json_payload': {'display_coordinates': ()},
    'app_cache': {'file_version': (), 'catalog_resource': (), 'forecast_run_id': ('physics_revision',),
                  'day_summary': (), 'assessed_task': (), 'gemini_discovery': ()},
    'terrain_wave': {'prepare_terrain': (), 'solve_field': ('prepared',)},
    'briefing_bot': {'site_evidence': ('maximum_km',)},
    'flight_briefing': {'build_context': ('resolved',)},
    'help_guide': {'render_help': ('model_key', 'bucket', 'recorded')},
    'planning_ui': {'task_payload': (), 'set_task': ('client_version',)},
    'client_map': {'add_shared_task': (), 'task_soaring_layers': ()},
    'wave_map': {'cached_wave_payload': ()},
    'map_modes': {'add_map_modes': ('wind_product',)},
    'map_component': {'compressed_document': (), 'render_document': ('browser_cache',), 'document_version': (), 'cached_document': ()},
    'route_tools': {'screening_forecasts': ()},
    'route_analysis': {'planner_panel': ('document_cache',)},
}


def _supports(module, api):
    for name, keywords in api.items():
        function = getattr(module, name, None)
        if not callable(function):
            return False
        if keywords:
            parameters = inspect.signature(function).parameters
            if not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
                if any(key not in parameters for key in keywords):
                    return False
    return True


def refresh_cached_modules():
    """Repair old sys.modules entries before app.py binds any local functions."""
    refreshed = []
    with _lock:
        for name, api in LAZY_APIS.items():
            module = sys.modules.get(name)
            # Consumers must rebind functions after the physics/config update.
            changed_physics=bool({'config','data_ingestion','soaring_physics'} & set(refreshed))
            if module is not None and (not _supports(module, api) or changed_physics):
                importlib.invalidate_caches()
                module = importlib.reload(module)
                if not _supports(module, api):
                    raise ImportError(f'{name} on disk lacks the current dashboard API')
                refreshed.append(name)
                _log.info('Refreshed cached application module: %s', name)
    return tuple(refreshed)


def optional_module(name):
    """An unavailable optional integration must not prevent dashboard startup."""
    try:
        return importlib.import_module(name)
    except ImportError:
        _log.warning('Optional integration %s could not be imported', name, exc_info=True)
        return None
