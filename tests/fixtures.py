"""Synthetic weather only; these profiles are not a forecast or flight validation."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from config import FORECAST_MODELS, HOURLY_VARIABLES, MONITORING_ZONES, PRESSURE_LEVELS_HPA
from data_ingestion import Forecast, JsonResponse, parse_forecast

NOW = datetime(2026, 6, 10, 12, tzinfo=UTC)


def forecast(point=None, *, start=NOW, hours=None, model='ecmwf_ifs025', changes=None):
    point = point or MONITORING_ZONES['shropshire']
    definition=next(m for m in FORECAST_MODELS.values() if m.api_name==model)
    hours=hours or definition.horizon_hours+1
    values = dict.fromkeys(HOURLY_VARIABLES, None)
    values.update(temperature_2m=28., dew_point_2m=12., surface_pressure=1000.,
                  boundary_layer_height=2400., direct_radiation=1000.,
                  shortwave_radiation=1100., soil_moisture_0_to_7cm=.05,
                  cloud_cover_low=20., wind_speed_10m=10., wind_direction_10m=270.,
                  precipitation=0.)
    heights = (110, 530, 760, 1450, 2000, 3000, 4200, 5600, 7200, 9200)
    temperatures = (27, 23, 21, 16, 13.5, 9, -2, -15, -26, -40)
    for p, height, t in zip(PRESSURE_LEVELS_HPA, heights, temperatures):
        values.update({f'temperature_{p}hPa':t, f'geopotential_height_{p}hPa':height,
                       f'relative_humidity_{p}hPa':70., f'wind_speed_{p}hPa':10.,
                       f'wind_direction_{p}hPa':270., f'vertical_velocity_{p}hPa':.2})
    values.update(changes or {})
    units = {}
    for name in values:
        if name.startswith(('temperature','dew_point')): unit='°C'
        elif name.startswith(('wind_speed','wind_gusts','vertical_velocity')): unit='m/s'
        elif name.startswith('wind_direction'): unit='°'
        elif name.startswith(('geopotential_height','boundary_layer')) or name=='visibility': unit='m'
        elif name.startswith(('cloud_cover','relative_humidity','precipitation_probability')): unit='%'
        elif name in ('surface_pressure','pressure_msl'): unit='hPa'
        elif name.endswith('radiation'): unit='W/m²'
        elif name.startswith('soil_moisture'): unit='m³/m³'
        elif name=='cape': unit='J/kg'
        elif name=='lifted_index': unit='K'
        else: unit='mm'
        units[name]=unit
    # Parse through the same boundary as a provider response.
    body={'latitude':point.latitude,'longitude':point.longitude,'elevation':100.,
          'utc_offset_seconds':0,'hourly_units':{'time':'unixtime',**units},
          'hourly':{'time':[int((start+timedelta(hours=i)).timestamp()) for i in range(hours)],
                    **{k: v if isinstance(v,list) else [v]*hours for k,v in values.items()}}}
    return parse_forecast(JsonResponse(body,start),point,definition)


def alter(source, **changes):
    hourly={**source.hourly, **{k:tuple(v) if isinstance(v,(list,tuple)) else (v,)*len(source.times)
                               for k,v in changes.items()}}
    return replace(source,hourly=hourly,
                   missing_counts={k:sum(v is None for v in values) for k,values in hourly.items()})
