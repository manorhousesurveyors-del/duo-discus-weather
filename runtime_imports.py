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
API_REVISION = 15

_lock = RLock()
_log = logging.getLogger(__name__)

# Dependency order matters: a reloaded consumer must bind the current providers.
# Empty tuples check function presence; other tuples check newly added keywords.
LAZY_APIS = {
    'config': {'forecast_variables': (), 'moisture_variables': (), 'duo_circling_sink_knots': (), 'default_forecast_model': ()},
    'atmospheric_moisture': {'complete_moisture': ()},
    'data_ingestion': {'parse_forecast': ('variables',), 'split_model_response': (), 'weather_transport': (), 'Forecast': ('field_sources','source_runs')},
    'weather_engine': {'ProviderGate': (), 'ForecastHttpClient': ()},
    'soaring_physics': {'evaluate_thermals': ('loading',), 'thermal_usable_depth': ()},
    'forecast_coverage': {'complete_forecasts': (), 'complete_regional_forecasts': ()},
    'task_planning': {'assess_task': ()},
    'map_weather': {'soaring_samples': (), 'fill_wind_grid': ()},
    'day_rating': {'rate_day': ()},
    'alerts': {'load_bundle': ()},
    'forecast_sanitizer': {'sanitize': (), 'COMPLETION_API': ()},
    'weather_anchors': {'sample_anchors': ()},
    'weather_demo': {'demo_forecasts': ()},
    'met_norway': {'fetch_met_forecasts': ()},
    'weather_disk_cache': {'save_live': (), 'load_live': ()},
    'sync_weather': {'fetch_forecasts': ('product',), 'display_bundle': (), 'met_transport': (), '_http_resource': ('revision',)},
    'weather_ui': {'source_value': ()},
    'session_state': {'migrate': ('synchronous',)},
    'uk_map_grid': {'grid_variables': (), 'transform': (), 'synchronous_snapshot': (), 'load_uk_map_grid': ('wind_grid_revision',)},
    'json_payload': {'display_coordinates': ()},
    'app_cache': {'file_version': (), 'catalog_resource': (), 'forecast_run_id': ('physics_revision',),
                  'day_summary': (), 'assessed_task': (), 'gemini_discovery': (), 'covered_forecasts': ('coverage_revision',)},
    'terrain_wave': {'prepare_terrain': (), 'solve_field': ('prepared',)},
    'briefing_bot': {'site_evidence': ('maximum_km',)},
    'flight_briefing': {'build_context': ('resolved',)},
    'help_guide': {'render_help': ('model_key', 'bucket', 'recorded')},
    'place_resolver': {'local_forecasts': ()},
    'planning_ui': {'task_payload': (), 'set_task': ('client_version',)},
    'client_map': {'add_shared_task': (), 'task_soaring_layers': (), 'install_wind_canvas': (), 'surface_payload': (), 'add_task_editor': ('prepared',)},
    'uk_airfields': {'add_airfields': ('show','forecasts')},
    'wave_map': {'cached_wave_payload': (), 'add_wave_map': ('prepared',)},
    'map_modes': {'add_map_modes': ('wind_product',), 'install_wind_canvas': ()},
    'map_view': {'build_map': ('task_product',), 'wave_document': ('_prepared',)},
    'map_component': {'compressed_document': (), 'render_document': ('browser_cache',), 'document_version': (), 'cached_document': ()},
    'route_tools': {'screening_forecasts': ()},
    'route_analysis': {'planner_panel': ('document_cache',)},
    'pages.weather': {'synchronous_render': ()},
    'pages.wave': {'render': ()},
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


def refresh_cached_modules(*, deployment_changed=False):
    """Repair old sys.modules entries before app.py binds any local functions."""
    refreshed = []
    with _lock:
        for name, api in LAZY_APIS.items():
            module = sys.modules.get(name)
            # Consumers must rebind functions after the physics/config update.
            changed_physics=bool({'config','data_ingestion','soaring_physics','forecast_sanitizer','weather_ui','uk_map_grid'} & set(refreshed))
            if module is not None and (deployment_changed or not _supports(module, api) or changed_physics):
                importlib.invalidate_caches()
                module = importlib.reload(module)
                if not _supports(module, api):
                    raise ImportError(f'{name} on disk lacks the current dashboard API')
                refreshed.append(name)
                _log.info('Refreshed cached application module: %s', name)
        if 'config' in refreshed:
            # Keep saved Turnpoint instances valid: rebind their coordinate
            # dependency without replacing the catalog's dataclass identity.
            catalog_module = sys.modules.get('bga_turnpoints')
            if catalog_module is not None:
                catalog_module.GeoPoint = sys.modules['config'].GeoPoint
    return tuple(refreshed)


def optional_module(name):
    """An unavailable optional integration must not prevent dashboard startup."""
    try:
        module=importlib.import_module(name)
        if name=='ogn_traffic' and getattr(module,'__file__',None) and getattr(module,'TRAFFIC_API_REVISION',0)<2:
            # Stop the superseded worker before replacing its implementation.
            # Never leave old and new persistent receivers running together.
            with _lock:
                if getattr(module,'TRAFFIC_API_REVISION',0)<2:
                    worker=getattr(module,'_worker',None)
                    if worker is not None:
                        stop=getattr(worker,'stop',None)
                        if stop is not None:stop.set()
                        receiver=getattr(worker,'aprs',None)
                        if getattr(receiver,'stop',None) is not None:receiver.stop.set()
                    provider=sys.modules.get('ogn_live')
                    if provider is not None and not hasattr(provider,'MirrorSession'):importlib.reload(provider)
                    importlib.invalidate_caches();module=importlib.reload(module)
        return module
    except ImportError:
        _log.warning('Optional integration %s could not be imported', name, exc_info=True)
        return None
