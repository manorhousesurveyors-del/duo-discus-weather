"""Deterministic demonstration weather for a disconnected map.

This is a seasonal/diurnal illustration, never a substitute aviation forecast.
Every generated field carries demo provenance. Flight assessments and alerts
must reject this source using forecast_quality.flight_evidence.
"""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import math

from atmospheric_moisture import dewpoint
from config import FORECAST_MODELS, HOURLY_VARIABLES, PRESSURE_LEVELS_HPA
from data_ingestion import JsonResponse, parse_forecast, utc_datetime
from forecast_quality import DEMO_NOTE, DEMO_SOURCE

_RD, _G, _LAPSE = 287.05, 9.80665, .0065


def _clamp(value, low, high):return min(high, max(low, value))


def _unit(name):
    if name.startswith(('temperature_', 'dew_point_')):return '°C'
    if name.startswith(('wind_speed_', 'wind_gusts_', 'vertical_velocity_')):return 'm/s'
    if name.startswith('wind_direction_'):return '°'
    if name.startswith(('relative_humidity_', 'cloud_cover')) or name == 'precipitation_probability':return '%'
    if name.startswith('geopotential_height_') or name in ('boundary_layer_height', 'visibility'):return 'm'
    if name in ('surface_pressure', 'pressure_msl'):return 'hPa'
    if name.endswith('radiation'):return 'W/m²'
    if name == 'cape':return 'J/kg'
    if name == 'lifted_index':return 'K'
    if name.startswith('soil_moisture_'):return 'm³/m³'
    if name in ('precipitation', 'showers'):return 'mm'
    raise ValueError('No demonstration unit defined for '+name)


def _hour(point, when):
    # The 100 m reference is synthetic too; no terrain dataset is implied.
    terrain = 100.0
    doy = when.timetuple().tm_yday
    season = math.cos(2*math.pi*(doy-200)/365.2425)
    phase = 2*math.pi*((doy % 11)/11+when.hour/264+point.longitude/80)
    declination = math.radians(23.44)*math.sin(2*math.pi*(doy-81)/365.2425)
    latitude = math.radians(point.latitude)
    hour_angle = math.radians(15*(when.hour+point.longitude/15-12))
    sun = max(0., math.sin(latitude)*math.sin(declination)+
              math.cos(latitude)*math.cos(declination)*math.cos(hour_angle))
    low = _clamp(40+23*math.sin(phase)+12*(1-sun), 5, 90)
    mid = _clamp(20+14*math.cos(phase-.8), 3, 55)
    high = _clamp(25+18*math.sin(phase+1.4), 2, 65)
    cloud = 100*(1-(1-low/100)*(1-mid/100)*(1-high/100))
    radiation = 850*sun*(1-.65*cloud/100)
    temperature = (11+6*season-.4*(point.latitude-53.5)+
                   2.5*math.cos(2*math.pi*(when.hour-14)/24)+1.2*math.sin(phase))
    spread = 2.2+5*sun+.7*(1+math.sin(phase))
    dew = temperature-spread
    msl = 1014+7*math.cos(phase)-.22*(point.latitude-54)
    sea_temperature_k = temperature+273.15+_LAPSE*terrain
    surface = msl*(1-_LAPSE*terrain/sea_temperature_k)**(_G/(_RD*_LAPSE))
    speed = 4.5+2.2*(1+math.sin(phase))+.1*(point.latitude-53)
    direction = (245+22*math.sin(phase+.5)+.6*point.longitude) % 360
    rain = max(0., (low-64)/18)*max(0., math.sin(phase+.4))
    depth = 120+1.9*radiation+65*sun
    values = {
        'temperature_2m':temperature, 'dew_point_2m':dew,
        'surface_pressure':surface, 'pressure_msl':msl,
        'cloud_cover':cloud, 'cloud_cover_low':low, 'cloud_cover_mid':mid,
        'cloud_cover_high':high, 'precipitation':rain, 'showers':.2*rain,
        'precipitation_probability':min(95., rain*70),
        'shortwave_radiation':radiation, 'direct_radiation':radiation*(1-.8*cloud/100),
        'cape':max(0., radiation-350)*.5, 'lifted_index':5-3*sun,
        'boundary_layer_height':depth,
        'wind_speed_10m':speed, 'wind_direction_10m':direction,
        'wind_gusts_10m':speed*1.35+1, 'soil_moisture_0_to_7cm':.22,
        'visibility':max(5000., 30000-rain*12000),
    }
    for height in (80, 100):
        values[f'wind_speed_{height}m'] = speed*(height/10)**.12
        values[f'wind_direction_{height}m'] = (direction+4*math.log(height/10)) % 360
    for pressure in PRESSURE_LEVELS_HPA:
        height = sea_temperature_k/_LAPSE*(1-(pressure/msl)**(_RD*_LAPSE/_G))
        agl = max(0., height-terrain)
        temp = sea_temperature_k-273.15-_LAPSE*height
        layer_cloud = low if agl<2000 else mid if agl<6000 else high
        humidity = _clamp(55+.35*layer_cloud-.0015*agl, 20, 96)
        values.update({
            f'temperature_{pressure}hPa':temp,
            f'dew_point_{pressure}hPa':dewpoint(temp, humidity),
            f'relative_humidity_{pressure}hPa':humidity,
            f'cloud_cover_{pressure}hPa':layer_cloud,
            f'geopotential_height_{pressure}hPa':height,
            f'wind_speed_{pressure}hPa':speed*(max(10., agl)/10)**.12,
            f'wind_direction_{pressure}hPa':(direction+28*(1-math.exp(-agl/2000))) % 360,
            f'vertical_velocity_{pressure}hPa':0.,
        })
    return values


def demo_forecasts(points, anchor: datetime, model_key):
    """Return 120 labelled hourly samples for each point, without any I/O."""
    anchor = utc_datetime(anchor)
    start = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
    model = FORECAST_MODELS.get(model_key)
    if model is None:
        model = next((m for m in FORECAST_MODELS.values() if m.api_name==model_key), None)
    if model is None:raise ValueError('Unknown forecast model: '+str(model_key))
    model = replace(model, horizon_hours=119)
    times = tuple(start+timedelta(hours=i) for i in range(120))
    units = {key:_unit(key) for key in HOURLY_VARIABLES}
    forecasts = []
    for point in points:
        rows = tuple(_hour(point, when) for when in times)
        hourly = {key:[row[key] for row in rows] for key in HOURLY_VARIABLES}
        body = {'latitude':point.latitude, 'longitude':point.longitude, 'elevation':100.,
                'utc_offset_seconds':0, 'hourly_units':{'time':'unixtime', **units},
                'hourly':{'time':[int(t.timestamp()) for t in times], **hourly}}
        parsed = parse_forecast(JsonResponse(body, anchor), point, model)
        forecasts.append(replace(parsed, source=DEMO_SOURCE, warnings=(DEMO_NOTE,),
            field_sources={key:('DEMONSTRATION / synthetic seasonal illustration',)*120
                           for key in parsed.hourly},
            source_runs=((DEMO_SOURCE, start.isoformat()),)))
    return tuple(forecasts)
