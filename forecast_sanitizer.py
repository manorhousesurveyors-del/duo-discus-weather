"""Pure model-independent completion before any soaring validation.

Preserves native values and zeroes. Derivations require supporting evidence;
they do not fabricate rain, radiation, CAPE or favourable weather in an outage.
"""
from dataclasses import replace
import math
from config import PRESSURE_LEVELS_HPA
from atmospheric_moisture import complete_moisture

CORE_FIELDS=('temperature_2m','dew_point_2m','precipitation','shortwave_radiation',
             'wind_speed_10m','wind_direction_10m','boundary_layer_height','cloud_cover_low','surface_pressure')


def has_core_coverage(forecast,fields=None):
    """One completion/admission contract, including overnight required values."""
    required=CORE_FIELDS if fields is None else tuple(name for name in CORE_FIELDS if name in fields)
    return len(forecast.times)>=120 and all(name in forecast.hourly and len(forecast.hourly[name])==len(forecast.times)
        and all(value is not None for value in forecast.hourly[name]) for name in required)


def sanitize(forecast):
    values=dict(forecast.hourly);units=dict(forecast.units);sources=dict(forecast.field_sources)
    complete_moisture(values,units,sources)
    # Interpolate vectors, never angles (359° and 1° must not become 180°).
    n=len(forecast.times)
    for pressure in PRESSURE_LEVELS_HPA:
        speed=f'wind_speed_{pressure}hPa';direction=f'wind_direction_{pressure}hPa'
        if speed not in values or direction not in values:continue
        speeds=list(values[speed]);directions=list(values[direction])
        ss=list(sources.get(speed,(None,)*n));ds=list(sources.get(direction,(None,)*n))
        for hour in range(n):
            if speeds[hour] is not None and directions[hour] is not None:continue
            nodes=[]
            for p in PRESSURE_LEVELS_HPA:
                v=forecast.hourly.get(f'wind_speed_{p}hPa',(None,)*n)[hour]
                d=forecast.hourly.get(f'wind_direction_{p}hPa',(None,)*n)[hour]
                height=forecast.hourly.get(f'geopotential_height_{p}hPa',(None,)*n)[hour]
                if v is not None and d is not None and (height is None or height>forecast.terrain_elevation_m):
                    a=math.radians(d);nodes.append((p,-v*math.sin(a),-v*math.cos(a)))
            nodes.sort(reverse=True)
            for a,b in zip(nodes,nodes[1:]):
                if a[0]>pressure>b[0]:
                    fraction=math.log(pressure/a[0])/math.log(b[0]/a[0])
                    u=a[1]+fraction*(b[1]-a[1]);v=a[2]+fraction*(b[2]-a[2])
                    if speeds[hour] is None:speeds[hour]=math.hypot(u,v);ss[hour]='log-pressure wind interpolation'
                    if directions[hour] is None:directions[hour]=math.degrees(math.atan2(-u,-v))%360;ds[hour]='log-pressure wind interpolation'
                    break
        for key,series,origins,unit in ((speed,speeds,ss,'m/s'),(direction,directions,ds,'°')):
            values[key]=tuple(series)
            if any(origins):sources[key]=tuple(origins);units[key]=unit
    result=replace(forecast,hourly=values,units=units,field_sources=sources)
    if 'boundary_layer_height' in values:
        from soaring_physics import Profile,boundary_layer_height
        depths=list(values['boundary_layer_height']);origins=list(sources.get('boundary_layer_height',(None,)*n))
        for index,when in enumerate(forecast.times):
            if depths[index] is not None:continue
            depth,origin=boundary_layer_height(Profile.from_forecast(result,when))
            if depth is not None:depths[index]=depth;origins[index]=origin
        values['boundary_layer_height']=tuple(depths)
        if any(origins):sources['boundary_layer_height']=tuple(origins);units['boundary_layer_height']='m'
    return replace(result,missing_counts={key:sum(v is None for v in series) for key,series in values.items()})
