"""Independent forecast map products; no proprietary Windy/SkySight data.

Pressure winds are interpolated in log pressure, never beyond available levels.
Soaring contours mask water, unsupported coverage and blocked flying conditions.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, UTC
import math
import numpy as np
from scipy.interpolate import griddata
from scipy.spatial import cKDTree
import contourpy

from config import DUO_DISCUS_POLARS, LOCAL_TIMEZONE, METRES_TO_FEET, MPS_TO_KNOTS, PRESSURE_LEVELS_HPA, GeoPoint
from soaring_physics import Profile, cross_country_speed, evaluate_thermals, wind_components
from task_planning import classify_day

WIND_LEVELS = ((0, 'Surface · 10 m'), (950, '950 hPa · ≈1,800 ft'),
               (850, '850 hPa · ≈4,800 ft'), (800, '800 hPa · ≈6,400 ft / 2,000 m'),
               (700, '700 hPa · ≈10,000 ft'), (500, '500 hPa · ≈18,000 ft'))
SOARING_COLOURS = ('#603b91','#3348a4','#2684d2','#39ccda','#63be62','#e4d74f','#f39b38','#d74234')
BANDS = {'thermal': (0, .5, 1, 1.5, 2.5, 3.5, 4.5, 5.5, 20),
         'pfd': (0, 50, 100, 150, 200, 250, 300, 500, 1500),
         'xc': (0, 30, 45, 60, 75, 90, 105, 120, 300),
         'stf': (0, 50, 55, 60, 65, 70, 75, 85, 150)}


WIND_LATITUDES = tuple(round(float(v),6) for v in np.linspace(49.8,59.,13))
WIND_LONGITUDES = tuple(round(float(v),6) for v in np.linspace(-8.5,2.,15))
WIND_VARIABLES = ('temperature_2m','surface_pressure','wind_speed_10m','wind_direction_10m',
                  'wind_speed_80m','wind_direction_80m','wind_speed_100m','wind_direction_100m') + tuple(
    f'{v}_{p}hPa' for p in (1000,950,925,850,800,700,500)
    for v in ('wind_speed','wind_direction','geopotential_height'))


def national_grid():
    """Regular 195-node UK grid, including coastal water; no distance cutoff mask."""
    return tuple(GeoPoint(f'map:{lat:.6f}:{lon:.6f}',f'UK grid {lat:.3f}, {lon:.3f}',lat,lon)
                 for lat in WIND_LATITUDES for lon in WIND_LONGITUDES)


def soaring_grid():
    """~85 km spaced land samples, not a high-resolution meteorological model."""
    from global_land_mask import globe
    return tuple(GeoPoint(f'map:{lat:.2f}:{lon:.2f}', f'UK map {lat:.2f}, {lon:.2f}', float(lat), float(lon))
                 for lat in np.arange(49.8, 61.01, .8) for lon in np.arange(-8.2, 2.01, 1.1)
                 if globe.is_land(lat, lon))


def wind_vector(profile, pressure):
    if pressure == 0:
        speed, direction = profile.values.get('wind_speed_10m'), profile.values.get('wind_direction_10m')
        return None if speed is None or direction is None else (*wind_components(speed, direction), False)
    # Wind fields do not require temperature/humidity at each pressure level.
    # Interpolate vector components, avoiding the 359°/1° direction wrap error.
    values=profile.values; terrain=profile.forecast.terrain_elevation_m
    nodes=[]; surface_pressure=values.get('surface_pressure')
    if surface_pressure:
        for height in (10,80,100):
            speed,direction=values.get(f'wind_speed_{height}m'),values.get(f'wind_direction_{height}m')
            if speed is not None and direction is not None:
                p=surface_pressure*math.exp(-height/8434.5)
                nodes.append((p,*wind_components(speed,direction),0))
        if nodes and pressure>=nodes[0][0]:
            # At/below the lowest near-surface sample (including the small
            # surface-to-10 m interval), display explicitly labelled surface
            # wind. Never invent an underground pressure-level wind.
            return nodes[0][1],nodes[0][2],2
    for p in PRESSURE_LEVELS_HPA:
        speed,direction=values.get(f'wind_speed_{p}hPa'),values.get(f'wind_direction_{p}hPa')
        height=values.get(f'geopotential_height_{p}hPa')
        if speed is None or direction is None or (height is not None and height<=terrain+2):continue
        if height is None and surface_pressure and p>=surface_pressure:continue
        if height is None and not surface_pressure:continue
        nodes.append((p,*wind_components(speed,direction),0))
    nodes.sort(reverse=True)
    for p,u,v,flag in nodes:
        if p==pressure:return u,v,flag
    for a,b in zip(nodes,nodes[1:]):
        if a[0]>pressure>b[0]:
            fraction=math.log(pressure/a[0])/math.log(b[0]/a[0])
            return a[1]+fraction*(b[1]-a[1]),a[2]+fraction*(b[2]-a[2]),1
    return None


def wind_payload(forecasts, anchor, *, days=5):
    """Hourly UTC timeline; missing samples remain null (including short horizons)."""
    start = datetime.combine(anchor.astimezone(LOCAL_TIMEZONE).date(), time(), LOCAL_TIMEZONE)
    times = [(start.astimezone(UTC) + timedelta(hours=h)).astimezone(LOCAL_TIMEZONE) for h in range(days*24)]
    stations = []
    forecasts=tuple(forecasts)
    for f in forecasts:
        series = []
        for when in times:
            try: profile = Profile.from_forecast(f, when)
            except KeyError:
                series.append([None]*len(WIND_LEVELS)); continue
            series.append([None if (v := wind_vector(profile, pressure)) is None else
                           [round(v[0], 2), round(v[1], 2), int(v[2])] for pressure, _ in WIND_LEVELS])
        stations.append({'lat': f.location.latitude, 'lon': f.location.longitude, 'values': series})
    lookup={(round(s['lat'],6),round(s['lon'],6)):i for i,s in enumerate(stations)}
    grid=[[lookup.get((lat,lon)) for lon in WIND_LONGITUDES] for lat in WIND_LATITUDES]
    return {'times': [t.isoformat() for t in times], 'labels': [t.strftime('%a %d %b · %H:%M %Z') for t in times],
            'levels': [label for _, label in WIND_LEVELS], 'stations': stations,
            'grid':{'latitudes':WIND_LATITUDES,'longitudes':WIND_LONGITUDES,'indices':grid},
            'source': 'Open-Meteo '+(', '.join(sorted({f.model for f in forecasts})) or 'unavailable')+' · regular UK grid'+
                (' · gap support: '+', '.join(sorted({s for f in forecasts for s in f.supplemental_sources})) if any(f.supplemental_sources for f in forecasts) else ''),
            'notes':'Bilinear horizontal and log-pressure vector interpolation. Terrain fallback uses surface wind; missing forecast hours stay unavailable.'}


def soaring_samples(forecasts, when, loading):
    samples = []
    for f in forecasts:
        try: profile = Profile.from_forecast(f, when)
        except KeyError: continue
        thermal = evaluate_thermals(profile,loading)
        climb, top = thermal.conservative_climb_knots, thermal.usable_top_ft_amsl
        wind = wind_vector(profile, 925) or wind_vector(profile, 850)
        xc = stf = None
        if climb is not None and top is not None and wind:
            speeds = [cross_country_speed(climb/MPS_TO_KNOTS, bearing, wind[0], wind[1], DUO_DISCUS_POLARS[loading])
                      for bearing in (0, 90, 180, 270)]
            xc = min(speed[0] for speed in speeds)
            stf = sum(speed[1] for speed in speeds)/4*MPS_TO_KNOTS
            if top/METRES_TO_FEET-f.terrain_elevation_m < 500 or (profile.values.get('precipitation') or 0) >= 1:
                xc = stf = 0.
            if xc <= 0: stf = 0.
        pfd = classify_day((f,), f.location, when.astimezone(LOCAL_TIMEZONE).date(), loading,
                           preference=(f.model,)).distance_km
        samples.append({'lat': f.location.latitude, 'lon': f.location.longitude, 'thermal': climb,
                        'pfd': pfd, 'xc': xc, 'stf': stf,
                        'base': thermal.cloud_base_ft_amsl, 'sink': None if climb is None else min(0., climb)})
    return samples


def contour_features(samples, field):
    """Black-outlined filled isobands, holes preserved; zero and unknown stay clear."""
    if field not in BANDS: raise ValueError('Unknown contour field')
    unique = {(s['lat'], s['lon']): (0. if field=='thermal' and s.get('xc')==0 else s[field])
              for s in samples if s.get(field) is not None}
    if len(unique) < 3: return {'type': 'FeatureCollection', 'features': []}
    coords = np.asarray(list(unique)); values = np.asarray(list(unique.values()))
    if np.linalg.matrix_rank(coords-coords.mean(axis=0)) < 2:
        return {'type': 'FeatureCollection', 'features': []}
    x = np.arange(max(-9, coords[:,1].min()-.1), min(2.5, coords[:,1].max()+.1)+.04, .08)
    y = np.arange(max(49, coords[:,0].min()-.1), min(61.5, coords[:,0].max()+.1)+.04, .08)
    xx, yy = np.meshgrid(x, y)
    # Interpolate values only within the hull; known zero stations stay zero.
    z = griddata(coords[:, ::-1], values, (xx, yy), method='linear')
    scale = np.array([66., 111.])
    distance, nearest = cKDTree(coords[:, ::-1]*scale).query(np.column_stack([xx.ravel(), yy.ravel()])*scale)
    from global_land_mask import globe
    mask = (~np.isfinite(z)) | (distance.reshape(z.shape)>120) | ~globe.is_land(yy,xx)
    mask |= (values[nearest].reshape(z.shape)<=0) | (z<=0)
    cg = contourpy.contour_generator(x=x, y=y, z=np.ma.masked_array(z,mask),
                                     fill_type='OuterOffset', corner_mask=False)
    features = []
    for i, (lower, upper) in enumerate(zip(BANDS[field], BANDS[field][1:])):
        vertices, offsets = cg.filled(lower, upper)
        for polygon, boundaries in zip(vertices, offsets):
            rings = [polygon[a:b].round(5).tolist() for a,b in zip(boundaries,boundaries[1:])]
            features.append({'type':'Feature', 'geometry':{'type':'Polygon','coordinates':rings},
                             'properties':{'low':lower, 'high':upper, 'colour':SOARING_COLOURS[i], 'field':field}})
    return {'type':'FeatureCollection','features':features}


def point_values(samples, latitude, longitude):
    """Nearest supported source, explicit distance; never claim an exact grid value."""
    if not samples: return None
    def distance(s): return math.hypot((s['lat']-latitude)*111, (s['lon']-longitude)*111*math.cos(math.radians(latitude)))
    point=min(samples,key=distance)
    return {**point,'distance_km':distance(point)} if distance(point)<=120 else None
