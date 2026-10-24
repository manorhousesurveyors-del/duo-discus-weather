"""Open-Meteo point precipitation, kept separate from observed radar pixels."""
from datetime import timedelta
import math

from config import GeoPoint, OPEN_METEO_URL, ForecastModel
from data_ingestion import JsonHttpClient, SchemaError, utc_datetime


def fetch_point_precipitation(latitude: float, longitude: float, when,
                              model: ForecastModel, *, client=None):
    """Return the selected hour's mean mm/h at the requested coordinate.

    Open-Meteo precipitation is an accumulation over the preceding hour. Its
    numeric value in mm equals that hour's average mm/h, not an instantaneous
    rain rate. Request the nearest model cell directly; do not interpolate
    regional dashboard samples or extrapolate beyond the model's coverage.
    """
    point = GeoPoint('radar-click', 'Radar click', latitude, longitude)
    when = utc_datetime(when)
    if when.minute or when.second or when.microsecond:
        raise ValueError('Point precipitation requires a whole forecast hour')
    response = (client or JsonHttpClient()).get_json(OPEN_METEO_URL, {
        'latitude': point.latitude, 'longitude': point.longitude,
        'models': model.api_name, 'hourly': 'precipitation',
        'precipitation_unit': 'mm', 'timezone': 'GMT', 'timeformat': 'unixtime',
        'cell_selection': 'nearest',
        'start_date': when.date().isoformat(), 'end_date': when.date().isoformat(),
    }, ttl_seconds=300)
    body = response.body
    if not isinstance(body, dict):
        raise SchemaError('Invalid point precipitation response')
    units, hourly = body.get('hourly_units'), body.get('hourly')
    if (not isinstance(units, dict) or units.get('precipitation') != 'mm'
            or units.get('time') != 'unixtime' or body.get('utc_offset_seconds') != 0):
        raise SchemaError('Unexpected precipitation units or time zone')
    if not isinstance(hourly, dict):
        raise SchemaError('Missing hourly precipitation')
    times, values = hourly.get('time'), hourly.get('precipitation')
    if (not isinstance(times, list) or not isinstance(values, list)
            or len(times) != len(values) or not times
            or any(isinstance(t, bool) or not isinstance(t, (int, float))
                   or not math.isfinite(t) or t % 3600 for t in times)
            or any(b - a != 3600 for a, b in zip(times, times[1:]))):
        raise SchemaError('Invalid hourly precipitation time series')
    if any(v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(v) or v < 0) for v in values):
        raise SchemaError('Invalid precipitation amount')
    try:
        grid = GeoPoint('radar-grid', 'Open-Meteo model cell', body['latitude'], body['longitude'])
    except (KeyError, TypeError, ValueError) as exc:
        raise SchemaError('Invalid precipitation grid coordinates') from exc
    epoch = when.timestamp()
    rate = values[times.index(epoch)] if epoch in times else None
    return {
        'latitude': point.latitude, 'longitude': point.longitude,
        'grid_latitude': grid.latitude, 'grid_longitude': grid.longitude,
        'rate': rate, 'model': model.api_name, 'source': 'Open-Meteo',
        'valid_at': when.isoformat(),
        'interval_start': (when - timedelta(hours=1)).isoformat(),
        'fetched_at': utc_datetime(response.fetched_at).isoformat(),
        'error': None if rate is not None else 'This model has no precipitation value for the selected hour.',
    }
