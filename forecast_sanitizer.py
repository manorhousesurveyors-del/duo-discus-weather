"""Pure model-independent completion before any soaring validation.

Preserves native values and zeroes. Derivations require supporting evidence;
they do not fabricate rain, radiation, CAPE or favourable weather in an outage.
"""
from dataclasses import replace
from datetime import timedelta
import math
from config import PRESSURE_LEVELS_HPA
from atmospheric_moisture import complete_moisture, relative_humidity

SURFACE_PRESSURE_SOURCE = 'hypsometric surface pressure from model MSL pressure, temperature and terrain'
SURFACE_RH_SOURCE = 'Magnus surface RH from temperature/dewpoint'
MIXING_ESTIMATE_SOURCE = ('estimated surface-energy mixing depth; assumed 6 K/km stability, '
                          'not a diagnosed inversion')

CORE_FIELDS=('temperature_2m','dew_point_2m','precipitation','shortwave_radiation',
             'wind_speed_10m','wind_direction_10m','boundary_layer_height','cloud_cover_low','surface_pressure')


def COMPLETION_API():
    """Hot-reload capability marker for the compact-request completion API."""
    return 1


def has_core_coverage(forecast,fields=None):
    """One completion/admission contract, including overnight required values."""
    required=CORE_FIELDS if fields is None else tuple(name for name in CORE_FIELDS if name in fields)
    return len(forecast.times)>=120 and all(name in forecast.hourly and len(forecast.hourly[name])==len(forecast.times)
        and all(value is not None for value in forecast.hourly[name]) for name in required)


def _complete_surface(forecast, values, units, sources):
    """Recover diagnostic fields from actual surface values, never assumed rain.

    MSL pressure is reduced hydrostatically using mean column temperature. The
    standard 6.5 K/km lapse assumption is explicit in the source label. Surface
    humidity is computed only when both model temperature and dewpoint exist.
    """
    n = len(forecast.times)
    empty = (None,) * n
    pressure = list(values.get('surface_pressure', empty))
    humidity = list(values.get('relative_humidity_2m', empty))
    psource = list(sources.get('surface_pressure', empty))
    hsource = list(sources.get('relative_humidity_2m', empty))
    inputs = (values.get(key, empty) for key in ('temperature_2m', 'dew_point_2m', 'pressure_msl'))
    for i, (temperature, dewpoint, msl) in enumerate(zip(*inputs)):
        if pressure[i] is None and temperature is not None and msl is not None and msl > 0:
            elevation = forecast.terrain_elevation_m
            mean_kelvin = temperature + 273.15 + .00325 * elevation
            if mean_kelvin > 0:
                pressure[i] = msl * math.exp(-9.80665 * elevation / (287.05 * mean_kelvin))
                psource[i] = SURFACE_PRESSURE_SOURCE + ' (6.5 K/km lapse assumption)'
        if humidity[i] is None:
            estimate = relative_humidity(temperature, dewpoint)
            if estimate is not None:
                humidity[i], hsource[i] = estimate, SURFACE_RH_SOURCE
    for key, series, origins, unit in (
            ('surface_pressure', pressure, psource, 'hPa'),
            ('relative_humidity_2m', humidity, hsource, '%')):
        if key in values or any(origins):
            values[key] = tuple(series)
            if any(origins):
                sources[key], units[key] = tuple(origins), unit


def _surface_energy_depths(forecast, values):
    """Conservative daytime proxy when the model cannot diagnose a mixing top.

    Integrate sensible heating over each local solar day into an idealised
    stable column: z=sqrt(2*Q/(rho*cp*dtheta/dz)). The 6 K/km stability and
    1,500 m cap are modelling assumptions, not a measured inversion. This is
    deliberately distinct from 125*(T-Td), which estimates condensation level.
    Missing radiation/temperature/dewpoint/pressure yields no estimate. Night
    samples remain unknown: zero sunlight does not imply a zero boundary layer.
    """
    n = len(forecast.times)
    result = [None] * n
    energy = 0.
    solar_day = None
    empty = (None,) * n
    inputs = tuple(values.get(key, empty) for key in (
        'temperature_2m', 'dew_point_2m', 'shortwave_radiation', 'surface_pressure'))
    for i, (when, t, td, radiation, pressure) in enumerate(zip(forecast.times, *inputs)):
        # Longitude approximates local solar time without DST discontinuities.
        current_day = (when + timedelta(hours=forecast.location.longitude / 15.)).date()
        if current_day != solar_day:
            energy, solar_day = 0., current_day
        if any(value is None for value in (t, td, radiation, pressure)):
            continue
        if radiation <= 0 or pressure <= 0 or t <= -273.15:
            continue
        fraction = min(.8, max(.55, .55 + .025 * max(0., t - td)))
        sensible_flux = radiation * .8 * .7 * fraction * .85
        # Hourly radiation is the preceding-hour mean, hence one hour of heat.
        energy += sensible_flux * 3600.
        density = pressure * 100. / (287.05 * (t + 273.15))
        result[i] = min(1500., max(150., math.sqrt(2. * energy / (density * 1004. * .006))))
    return result


def sanitize(forecast):
    values=dict(forecast.hourly);units=dict(forecast.units);sources=dict(forecast.field_sources)
    _complete_surface(forecast, values, units, sources)
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
    if 'boundary_layer_height' in values or 'shortwave_radiation' in values:
        from soaring_physics import Profile,boundary_layer_height
        depths=list(values.get('boundary_layer_height',(None,)*n));origins=list(sources.get('boundary_layer_height',(None,)*n))
        surface_estimates = _surface_energy_depths(forecast, values)
        for index,when in enumerate(forecast.times):
            if depths[index] is not None:continue
            depth,origin=boundary_layer_height(Profile.from_forecast(result,when))
            if depth is None and surface_estimates[index] is not None:
                depth, origin = surface_estimates[index], MIXING_ESTIMATE_SOURCE
            if depth is not None:depths[index]=depth;origins[index]=origin
        values['boundary_layer_height']=tuple(depths)
        if any(origins):sources['boundary_layer_height']=tuple(origins);units['boundary_layer_height']='m'
    return replace(result,missing_counts={key:sum(v is None for v in series) for key,series in values.items()})
