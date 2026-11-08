"""Bounded MET Norway surface-forecast failover, with explicit estimated aloft data.

The compact product contains no pressure-level sounding. Its illustrative
vertical profiles must never be treated as verified soaring/task evidence.
No weather is generated when the provider response is empty or malformed.
"""
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import math

from atmospheric_moisture import dewpoint
from config import PRESSURE_LEVELS_HPA, Settings
from data_ingestion import (FetchFailure, Forecast, ForecastBatch, IngestionError,
                            JsonHttpClient, JsonResponse, SchemaError, utc_datetime)
from forecast_sanitizer import sanitize

MET_URL = 'https://api.met.no/weatherapi/locationforecast/2.0/compact'
USER_AGENT = 'DuoDiscusSoaringWeather/1.0 github.com/manorhousesurveyors-del/duo-discus-weather'
TOTAL_DEADLINE_SECONDS = 1.5
HTTP_TIMEOUT_SECONDS = 1.25
SURFACE_ONLY_WARNING = ('surface-only MET Norway forecast: solar heating and vertical profiles are '
                        'estimates, not verified upper-air or flight-planning evidence')

FIELDS = {
    'temperature_2m': ('air_temperature', 'celsius', '°C'),
    'dew_point_2m': ('dew_point_temperature', 'celsius', '°C'),
    'relative_humidity_2m': ('relative_humidity', '%', '%'),
    'wind_speed_10m': ('wind_speed', 'm/s', 'm/s'),
    'wind_direction_10m': ('wind_from_direction', 'degrees', '°'),
    'wind_gusts_10m': ('wind_speed_of_gust', 'm/s', 'm/s'),
    'cloud_cover': ('cloud_area_fraction', '%', '%'),
    'cloud_cover_low': ('cloud_area_fraction_low', '%', '%'),
    'cloud_cover_mid': ('cloud_area_fraction_medium', '%', '%'),
    'cloud_cover_high': ('cloud_area_fraction_high', '%', '%'),
    'pressure_msl': ('air_pressure_at_sea_level', 'hPa', 'hPa'),
}


class MetHttpClient(JsonHttpClient):
    """Verified HTTPS, identifying User-Agent, one 1.25-second attempt."""
    def __init__(self, **kwargs):
        super().__init__(replace(Settings.from_env(), http_timeout_seconds=HTTP_TIMEOUT_SECONDS,
                                 http_max_attempts=1, http_workers=9), **kwargs)
        opener = self.opener
        def identified(request, **options):
            request.add_header('User-Agent', USER_AGENT)
            return opener(request, **options)
        self.opener = identified

    def _pace(self, host, cost=1.):
        return None


def _number(value, name):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SchemaError('MET Norway has a nonnumeric '+name)
    return float(value)


def _timestamp(value):
    try:
        return utc_datetime(datetime.fromisoformat(value.replace('Z', '+00:00')))
    except (AttributeError, TypeError, ValueError) as exc:
        raise SchemaError('MET Norway has an invalid timestamp') from exc


def _solar(when, point, cloud):
    """Simple clear-sky transmission/cloud estimate, not provider radiation."""
    declination = math.radians(23.44 * math.sin(2 * math.pi * (284 + when.timetuple().tm_yday) / 365.))
    latitude = math.radians(point.latitude)
    hour = when.hour + when.minute/60. + point.longitude/15.
    angle = math.radians(15. * (hour-12.))
    elevation = max(0., math.sin(latitude)*math.sin(declination) +
                    math.cos(latitude)*math.cos(declination)*math.cos(angle))
    return 1361. * .75 * elevation * (1. - .75 * (min(100., max(0., cloud))/100.)**3.4)


def _illustrative_profile(forecast):
    """Explicit assumptions for visualisation; no geostrophic wind is measured."""
    values, units, sources = dict(forecast.hourly), dict(forecast.units), dict(forecast.field_sources)
    n = len(forecast.times)
    for pressure in PRESSURE_LEVELS_HPA:
        series = {name:[None]*n for name in ('wind_speed', 'wind_direction', 'temperature', 'dew_point',
                                            'geopotential_height', 'relative_humidity')}
        for i in range(n):
            t, p = values['temperature_2m'][i], values.get('surface_pressure', (None,)*n)[i]
            speed, direction = values['wind_speed_10m'][i], values['wind_direction_10m'][i]
            humidity = values['relative_humidity_2m'][i]
            if t is None or p is None or p <= 0:
                continue
            height_agl = 287.05 * (t+273.15)/9.80665 * math.log(p/pressure)
            series['geopotential_height'][i] = forecast.terrain_elevation_m + height_agl
            series['temperature'][i] = t - .0065 * min(height_agl, 11000.)
            series['relative_humidity'][i] = humidity
            series['dew_point'][i] = dewpoint(series['temperature'][i],humidity)
            if speed is not None and direction is not None:
                series['wind_speed'][i] = speed * (max(10., min(height_agl, 6000.))/10.)**.12
                series['wind_direction'][i] = direction
        for name, samples in series.items():
            key = f'{name}_{pressure}hPa'
            values[key] = tuple(samples)
            units[key] = {'wind_speed':'m/s','wind_direction':'°','temperature':'°C','dew_point':'°C',
                          'geopotential_height':'m','relative_humidity':'%'}[name]
            assumption = ('surface-scaled power-law wind; no measured upper-air data' if name.startswith('wind') else
                          'constant surface humidity aloft; no measured upper-air moisture' if name=='relative_humidity' else
                          'dewpoint from assumed upper-air temperature/humidity; not measured aloft' if name=='dew_point' else
                          'standard lapse/hydrostatic profile; no measured upper-air sounding')
            sources[key] = tuple('estimated '+assumption if v is not None else None for v in samples)
    return replace(forecast, hourly=values, units=units, field_sources=sources,
                   missing_counts={key:sum(v is None for v in series) for key,series in values.items()})


def parse_met_forecast(response: JsonResponse, point, anchor) -> Forecast:
    """Normalise instant fields and interval rain onto a 120-hour UTC axis.

    Missing coverage stays missing. Only bracketing samples at most six hours
    apart are interpolated; 6-hour rain is a period mean, not exact hourly rain.
    """
    body = response.body
    if not isinstance(body, dict):
        raise SchemaError('MET Norway response must be an object')
    properties = body.get('properties')
    geometry = body.get('geometry')
    if not isinstance(properties, dict) or not isinstance(geometry, dict):
        raise SchemaError('MET Norway response lacks properties or geometry')
    rows = properties.get('timeseries')
    metadata = properties.get('meta')
    units_in = metadata.get('units') if isinstance(metadata, dict) else None
    coordinates = geometry.get('coordinates')
    if not isinstance(rows, list) or not rows or not isinstance(units_in, dict):
        raise SchemaError('MET Norway response has no usable timeseries')
    if not isinstance(coordinates, list) or len(coordinates) < 3:
        raise SchemaError('MET Norway response lacks terrain coordinates')
    lon, lat, elevation = (_number(v, 'coordinate') for v in coordinates[:3])
    if any(v is None for v in (lon,lat,elevation)) or not -90<=lat<=90 or not -180<=lon<=180:
        raise SchemaError('MET Norway response has invalid coordinates')
    timestamps, details, rain = [], [], []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('data'), dict):
            raise SchemaError('MET Norway timeseries contains a malformed sample')
        stamp = _timestamp(row.get('time'))
        instant = row['data'].get('instant')
        detail = instant.get('details') if isinstance(instant, dict) else None
        if not isinstance(detail, dict):
            raise SchemaError('MET Norway instant sample has no details')
        if timestamps and stamp<=timestamps[-1]:
            raise SchemaError('MET Norway timestamps must be unique and ordered')
        timestamps.append(stamp)
        details.append({key:_number(detail.get(key), key) for key,_,_ in FIELDS.values()})
        for hours in (1,6):
            raw_period = row['data'].get(f'next_{hours}_hours', {})
            period = raw_period.get('details', {}) if isinstance(raw_period, dict) else None
            if not isinstance(period, dict):
                raise SchemaError('MET Norway precipitation interval is malformed')
            amount = _number(period.get('precipitation_amount'), 'precipitation_amount')
            if amount is not None:
                if amount<0 or units_in.get('precipitation_amount')!='mm':
                    raise SchemaError('MET Norway has invalid precipitation amount or units')
                rain.append((stamp, stamp+timedelta(hours=hours), hours, amount/hours))
    start = utc_datetime(anchor).replace(hour=0, minute=0, second=0, microsecond=0)
    times = tuple(start+timedelta(hours=i) for i in range(120))
    values, units, sources = {}, {}, {}
    for name,(key,expected,unit) in FIELDS.items():
        samples, provenance = [], []
        if any(row[key] is not None for row in details) and units_in.get(key)!=expected:
            raise SchemaError('MET Norway has unexpected units for '+key)
        for when in times:
            left = bisect_right(timestamps,when)-1
            value = origin = None
            if left>=0 and timestamps[left]==when:
                value = details[left][key]
            elif 0<=left<len(timestamps)-1:
                gap = (timestamps[left+1]-timestamps[left]).total_seconds()
                a,b = details[left][key], details[left+1][key]
                if gap<=21600 and a is not None and b is not None:
                    fraction = (when-timestamps[left]).total_seconds()/gap
                    change = (b-a+180)%360-180 if name=='wind_direction_10m' else b-a
                    value = a+fraction*change
                    if name=='wind_direction_10m':value%=360
                    origin = f'interpolated MET Norway {gap/3600:g}-hour instant samples'
            samples.append(value);provenance.append(origin)
        values[name], units[name] = tuple(samples), unit
        if any(provenance):sources[name]=tuple(provenance)
    if not any(v is not None for v in values['temperature_2m']):
        raise SchemaError('MET Norway has no temperature covering the requested days')
    rain_values, rain_sources = [], []
    for when in times:
        periods = [p for p in rain if p[0]<=when and when+timedelta(hours=1)<=p[1]]
        period = min(periods, key=lambda p:p[2], default=None)
        rain_values.append(period[3] if period else None)
        rain_sources.append(f'MET Norway {period[2]}-hour precipitation period mean (mm/h)' if period else None)
    values['precipitation'], units['precipitation'], sources['precipitation'] = tuple(rain_values), 'mm', tuple(rain_sources)
    dew = list(values['dew_point_2m']); dew_sources = list(sources.get('dew_point_2m',(None,)*120))
    radiation = []
    for i,when in enumerate(times):
        if dew[i] is None:
            dew[i] = dewpoint(values['temperature_2m'][i],values['relative_humidity_2m'][i])
            if dew[i] is not None:dew_sources[i]='estimated Magnus dewpoint from MET Norway temperature/RH'
        cloud = values['cloud_cover'][i]
        radiation.append(None if cloud is None or values['temperature_2m'][i] is None else _solar(when,point,cloud))
    values['dew_point_2m'], sources['dew_point_2m'] = tuple(dew), tuple(dew_sources)
    values['shortwave_radiation'], units['shortwave_radiation'] = tuple(radiation), 'W/m²'
    sources['shortwave_radiation'] = tuple('estimated solar elevation/cloud transmission; not model radiation'
                                         if value is not None else None for value in radiation)
    coverage = sum(v is not None for v in values['temperature_2m'])
    warnings = (SURFACE_ONLY_WARNING,)
    if coverage<120:
        warnings += (f'{coverage}/120 temperature hours covered; unbracketed past/future hours remain missing.',)
    forecast = Forecast(point,'met_norway',response.fetched_at,lat,lon,elevation,times,values,units,
                        {key:sum(v is None for v in series) for key,series in values.items()},
                        warnings,response.from_cache,119,source=MET_URL,
                        field_sources=sources,source_runs=(('met_norway',response.fetched_at.isoformat()),))
    # Derive pressure and mixing before adding illustrative upper levels. An
    # assumed lapse profile must not be mistaken for a real parcel sounding.
    forecast = sanitize(forecast)
    # Known zero solar heating supports zero convective mixing in this preview.
    # It says nothing about a nocturnal stable/residual atmospheric boundary.
    depth = list(forecast.hourly.get('boundary_layer_height', (None,)*120))
    depth_sources = list(forecast.field_sources.get('boundary_layer_height', (None,)*120))
    for i, solar in enumerate(forecast.hourly['shortwave_radiation']):
        if depth[i] is None and solar == 0 and forecast.hourly['temperature_2m'][i] is not None:
            depth[i] = 0.
            depth_sources[i] = 'estimated convective mixing depth after sunset; not atmospheric boundary layer'
    forecast = replace(forecast, hourly={**forecast.hourly,'boundary_layer_height':tuple(depth)},
                       field_sources={**forecast.field_sources,'boundary_layer_height':tuple(depth_sources)})
    return _illustrative_profile(forecast)


def fetch_met_forecasts(points, *, http, anchor) -> ForecastBatch:
    """At most nine concurrent GETs; return without waiting beyond 1.5 seconds."""
    points = tuple(points)
    if not points:return ForecastBatch((), ())
    if len(points)>9:raise ValueError('MET Norway failover accepts at most nine anchor points')
    def load(point):
        response = http.get_json(MET_URL, {'lat':round(point.latitude,4),'lon':round(point.longitude,4)},ttl_seconds=0)
        return parse_met_forecast(response,point,anchor)
    pool = ThreadPoolExecutor(max_workers=9,thread_name_prefix='met-norway')
    jobs = {pool.submit(load,point):point for point in points}
    done, pending = wait(jobs, timeout=TOTAL_DEADLINE_SECONDS)
    forecasts, failures = {}, {}
    for future in done:
        point = jobs[future]
        try:forecasts[point.key]=future.result()
        except (IngestionError,OSError,ValueError,TypeError,KeyError) as exc:
            failures[point.key]=FetchFailure(point.key,'MET Norway',str(exc))
    for future in pending:
        point = jobs[future];future.cancel()
        failures[point.key]=FetchFailure(point.key,'MET Norway','MET Norway request exceeded the 1.5-second deadline')
    pool.shutdown(wait=False,cancel_futures=True)
    return ForecastBatch(tuple(forecasts[p.key] for p in points if p.key in forecasts),
                         tuple(failures[p.key] for p in points if p.key in failures))
