"""Separate displayable estimates from evidence usable for flight decisions.

These provenance checks survive JSON persistence, interpolation and dataclass
copies. A complete numeric schema alone never makes an assumed profile a
verified forecast.
"""
from urllib.parse import urlsplit

DEMO_SOURCE = 'demo://uk-synoptic/v1'
DEMO_NOTE = ('DEMONSTRATION ONLY: synthetic seasonal weather, not observations '
             'or a live forecast. Do not use for flying decisions or alerts.')
SURFACE_ONLY_NOTE = ('Surface-only forecast: upper-air winds, radiation and '
                     'mixing depth are illustrative estimates, not verified flight evidence.')
EXCLUDED_NOTE = ('Demonstration and surface-only estimated profiles are excluded '
                 'from flight assessments and alert triggers.')


def is_demo(forecast):
    source = str(getattr(forecast, 'source', '') or '')
    return source.startswith('demo://') or any(
        'DEMONSTRATION ONLY' in str(note).upper()
        for note in getattr(forecast, 'warnings', ())) or any(
        str(name).startswith('demo://')
        for name, _ in getattr(forecast, 'source_runs', ())) or any(
        'DEMONSTRATION / SYNTHETIC' in str(origin).upper()
        for origins in getattr(forecast, 'field_sources', {}).values() for origin in set(origins) if origin)


def is_surface_estimate(forecast):
    source = str(getattr(forecast, 'source', '') or '')
    try:
        parsed=urlsplit(source)
        met_source = parsed.hostname == 'api.met.no'
        compact_source = parsed.path.startswith('/weatherapi/locationforecast/')
    except ValueError:met_source = compact_source = False
    return met_source and (compact_source or getattr(forecast,'model','')=='met_norway' or any(
        'surface-only' in str(note).lower() for note in getattr(forecast, 'warnings', ())))


def flight_evidence(forecast):
    """Provenance eligibility only; existing per-field/time checks still apply."""
    return forecast is not None and not is_demo(forecast) and not is_surface_estimate(forecast)


def evidence_label(forecast):
    if is_demo(forecast):return 'DEMONSTRATION · not a live forecast'
    if is_surface_estimate(forecast):return 'MET Norway surface forecast · estimated upper air'
    return 'Model forecast'
