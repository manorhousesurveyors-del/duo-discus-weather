"""Optional Weather-only point probe. No network, workers, or UI at import time.

The browser spatially interpolates cached samples, never inventing unavailable
hours/levels. Thermal diagnostics use the existing Duo Discus physics engine.
"""
from datetime import UTC, datetime, time, timedelta
import math
import streamlit as st

from config import LOCAL_TIMEZONE, METRES_TO_FEET, MPS_TO_KNOTS
from soaring_physics import Profile, evaluate_thermals, wind_components

PROBE_API_REVISION = 3
LEVELS = (950, 900, 850, 700, 600, 500, 400, 300, 250, 200)
FIELDS = ('temperature', 'dewpoint', 'depth', 'climb', 'heat', 'solar',
          'u', 'v', 'gust', 'mslp', 'rain', 'cloud_low', 'cloud_mid', 'cloud_high', 'core', 'sink')


def number(value):
    return round(float(value), 3) if value is not None and math.isfinite(value) else None


def pressure_cloud(profile, pressure):
    values=profile.values
    direct=values.get(f'cloud_cover_{pressure}hPa')
    if direct is not None:return number(direct)
    for lower,upper in zip(profile.levels,profile.levels[1:]):
        if upper.pressure_hpa<pressure<lower.pressure_hpa:
            a=values.get(f'cloud_cover_{int(lower.pressure_hpa)}hPa');b=values.get(f'cloud_cover_{int(upper.pressure_hpa)}hPa')
            if a is None or b is None:return None
            weight=math.log(pressure/lower.pressure_hpa)/math.log(upper.pressure_hpa/lower.pressure_hpa)
            return number(a+weight*(b-a))
    return None


def pressure_sample(profile, pressure):
    """900 hPa is log-pressure interpolated; supplied upper levels stay usable
    even when Streamlit retains an older Profile class during a deployment.
    """
    level=profile.level(pressure)
    if level is not None:
        return [number(v) for v in (level.height_m*METRES_TO_FEET,level.rh_percent,
                                    level.u_mps,level.v_mps)]+[pressure_cloud(profile,pressure)]
    values=profile.values;suffix=f'_{pressure}hPa'
    height=values.get('geopotential_height'+suffix)
    if height is None or height<=profile.forecast.terrain_elevation_m+2:return [None]*5
    speed,direction=values.get('wind_speed'+suffix),values.get('wind_direction'+suffix)
    u,v=wind_components(speed,direction) if speed is not None and direction is not None else (None,None)
    return [number(height*METRES_TO_FEET),number(values.get('relative_humidity'+suffix)),number(u),number(v),pressure_cloud(profile,pressure)]


def hour_sample(forecast, when, loading='dry'):
    try:
        profile=Profile.from_forecast(forecast,when)
        thermal=evaluate_thermals(profile,loading);values=profile.values
        speed,direction=values.get('wind_speed_10m'),values.get('wind_direction_10m')
        u,v=wind_components(speed,direction) if speed is not None and direction is not None else (None,None)
        row=[values.get('temperature_2m'),values.get('dew_point_2m'),
             None if thermal.boundary_layer_m_agl is None else thermal.boundary_layer_m_agl*METRES_TO_FEET,
             thermal.net_climb_knots,thermal.sensible_heat_flux_wm2,values.get('shortwave_radiation'),
             u,v,None if values.get('wind_gusts_10m') is None else values['wind_gusts_10m']*MPS_TO_KNOTS,
             values.get('pressure_msl'),values.get('precipitation'),values.get('cloud_cover_low'),
             values.get('cloud_cover_mid'),values.get('cloud_cover_high'),thermal.core_updraught_knots,thermal.circling_sink_knots]
        return {'s':[number(v) for v in row],'p':[pressure_sample(profile,p) for p in LEVELS]}
    except (KeyError,ValueError,TypeError):
        return {'s':[None]*len(FIELDS),'p':[[None]*5 for _ in LEVELS]}


@st.cache_resource(ttl=1800,max_entries=12,show_spinner=False)
def prepare_bundle(run_id,day,loading='dry',*,_forecasts):
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
    preferred=('ukmo_seamless','ukmo_uk_deterministic_2km','ecmwf_ifs025')
    if len(models)>1 and not models<=set(preferred):
        raise ValueError('Point probe requires a single named model or the explicit UKMO/ECMWF pair')
    grouped={}
    for forecast in _forecasts:
        grouped.setdefault((forecast.location.latitude,forecast.location.longitude),{})[forecast.model]=forecast
    for coordinate,choices in grouped.items():
        ordered=sorted(choices.values(),key=lambda f:preferred.index(f.model) if f.model in preferred else 99)
        primary=ordered[0]
        stations.append({'lat':coordinate[0],'lon':coordinate[1],
            'name':primary.location.name,'terrain':number(primary.terrain_elevation_m*METRES_TO_FEET),
            'retrieved':primary.fetched_at.isoformat(),
            'series':[{'model':f.model,'retrieved':f.fetched_at.isoformat(),
                       'hours':[hour_sample(f,when,loading) for when in hours]} for f in ordered]})
    return {'columns':columns,'stations':stations,'fields':FIELDS,'levels':LEVELS,
            'model':'UKMO seamless → ECMWF fallback' if len(models)>1 else next(iter(models),'Unavailable'),
            'loading':loading,'max_distance_km':120,
            'cycle_note':'Model run time is not supplied by this API. Retrieval time is shown.',
            'notes':'Cached grid interpolation; no click-time request. Mixing depth uses model BLH where available, otherwise a dry-parcel estimate. RH/cloud shading is a proxy, not a measured cloud cross-section. Missing hours and levels stay unavailable. 10 m wind is supplied; 2 m wind is unavailable.'}


def observation_payload(aviation):
    """Use only already-loaded METARs. Never label a model sample as an observation."""
    reports=[]
    for report in getattr(aviation,'metars',{}).values():
        if any(value is None or not math.isfinite(value) for value in
               (report.latitude,report.longitude,report.temperature_c)):
            continue
        reports.append({'station':report.station,
            'name':str(report.raw.get('name') or report.station),
            'lat':report.latitude,'lon':report.longitude,
            'time':report.observed_at.isoformat(),'temperature':report.temperature_c,
            'stale':report.stale})
    return reports


def add_probe(map_view,forecasts,day,*,aviation=None,loading='dry'):
    """Attach only to an enabled Weather map. A failure remains local to it."""
    from app_cache import forecast_run_id
    from client_map import script
    payload={**prepare_bundle(forecast_run_id(forecasts),day,loading,_forecasts=tuple(forecasts)),
             'observations':observation_payload(aviation)}
    script(map_view,'point_forecast_meteogram.js','installDuoPointProbe',payload)
