"""Optional Weather-only point probe. No network, workers, or UI at import time.

The browser spatially interpolates cached samples, never inventing unavailable
hours/levels. Thermal diagnostics use the existing Duo Discus physics engine.
"""
from datetime import UTC, datetime, time, timedelta
import math
import streamlit as st

from config import LOCAL_TIMEZONE, METRES_TO_FEET, MPS_TO_KNOTS
from soaring_physics import Profile, evaluate_thermals, wind_components

LEVELS = (950, 900, 850, 700, 600, 500, 400, 300, 250, 200)
FIELDS = ('temperature', 'dewpoint', 'depth', 'climb', 'heat', 'solar',
          'u', 'v', 'gust', 'mslp', 'rain', 'cloud_low', 'cloud_mid', 'cloud_high')


def number(value):
    return round(float(value), 3) if value is not None and math.isfinite(value) else None


def pressure_sample(profile, pressure):
    """900 hPa is log-pressure interpolated; supplied upper levels stay usable
    even when Streamlit retains an older Profile class during a deployment.
    """
    level=profile.level(pressure)
    if level is not None:
        return [number(v) for v in (level.height_m*METRES_TO_FEET,level.rh_percent,
                                    level.u_mps,level.v_mps)]
    values=profile.values;suffix=f'_{pressure}hPa'
    height=values.get('geopotential_height'+suffix)
    if height is None or height<=profile.forecast.terrain_elevation_m+2:return [None]*4
    speed,direction=values.get('wind_speed'+suffix),values.get('wind_direction'+suffix)
    u,v=wind_components(speed,direction) if speed is not None and direction is not None else (None,None)
    return [number(height*METRES_TO_FEET),number(values.get('relative_humidity'+suffix)),number(u),number(v)]


def hour_sample(forecast, when):
    try:
        profile=Profile.from_forecast(forecast,when)
        thermal=evaluate_thermals(profile);values=profile.values
        speed,direction=values.get('wind_speed_10m'),values.get('wind_direction_10m')
        u,v=wind_components(speed,direction) if speed is not None and direction is not None else (None,None)
        row=[values.get('temperature_2m'),values.get('dew_point_2m'),
             None if thermal.boundary_layer_m_agl is None else thermal.boundary_layer_m_agl*METRES_TO_FEET,
             thermal.conservative_climb_knots,thermal.sensible_heat_flux_wm2,values.get('shortwave_radiation'),
             u,v,None if values.get('wind_gusts_10m') is None else values['wind_gusts_10m']*MPS_TO_KNOTS,
             values.get('pressure_msl'),values.get('precipitation'),values.get('cloud_cover_low'),
             values.get('cloud_cover_mid'),values.get('cloud_cover_high')]
        return {'s':[number(v) for v in row],'p':[pressure_sample(profile,p) for p in LEVELS]}
    except (KeyError,ValueError,TypeError):
        return {'s':[None]*len(FIELDS),'p':[[None]*4 for _ in LEVELS]}


@st.cache_resource(ttl=1800,max_entries=12,show_spinner=False)
def prepare_bundle(run_id,day,*,_forecasts):
    """Prepare only after opt-in; immutable-by-convention session-free payload.

    Local 07:30–18:30 columns use two bracketing UTC hours, not timezone-naive
    arithmetic. No day/hour is ever passed to the national grid fetch function.
    """
    columns=[];hours=[]
    for offset in range(2):
        date=day+timedelta(days=offset)
        for hour in range(7,19):
            local=datetime.combine(date,time(hour,30),LOCAL_TIMEZONE)
            lower=local.astimezone(UTC).replace(minute=0)
            indices=[]
            for when in (lower,lower+timedelta(hours=1)):
                if when not in hours:hours.append(when)
                indices.append(hours.index(when))
            columns.append({'label':local.strftime('%H:%M'),'day':local.strftime('%a %d %b'),
                            'zone':local.tzname(),'iso':local.isoformat(),'indices':indices})
    stations=[]
    models={f.model for f in _forecasts}
    if len(models)>1:raise ValueError('Point probe requires a single named forecast model')
    for forecast in _forecasts:
        stations.append({'lat':forecast.location.latitude,'lon':forecast.location.longitude,
            'name':forecast.location.name,'terrain':number(forecast.terrain_elevation_m*METRES_TO_FEET),
            'retrieved':forecast.fetched_at.isoformat(),
            'hours':[hour_sample(forecast,when) for when in hours]})
    return {'columns':columns,'stations':stations,'fields':FIELDS,'levels':LEVELS,
            'model':next(iter(models),'Unavailable'),'max_distance_km':120,
            'cycle_note':'Model run time is not supplied by this API. Retrieval time is shown.',
            'notes':'Cached grid interpolation; no click-time request. RH/cloud shading is a proxy, not a measured cloud cross-section. Missing hours and levels stay unavailable. 10 m wind is supplied; 2 m wind is unavailable.'}


def add_probe(map_view,forecasts,day):
    """Attach only to an enabled Weather map. A failure remains local to it."""
    from app_cache import forecast_run_id
    from client_map import script
    payload=prepare_bundle(forecast_run_id(forecasts),day,_forecasts=tuple(forecasts))
    script(map_view,'point_forecast_meteogram.js','installDuoPointProbe',payload)
