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

from config import DUO_DISCUS_POLARS, LOCAL_TIMEZONE, METRES_TO_FEET, MPS_TO_KNOTS, GeoPoint
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


def national_grid():
    """~85 km spaced land samples, not a high-resolution meteorological model."""
    from global_land_mask import globe
    return tuple(GeoPoint(f'map:{lat:.2f}:{lon:.2f}', f'UK map {lat:.2f}, {lon:.2f}', float(lat), float(lon))
                 for lat in np.arange(49.8, 61.01, .8) for lon in np.arange(-8.2, 2.01, 1.1)
                 if globe.is_land(lat, lon))


def wind_vector(profile, pressure):
    if pressure == 0:
        speed, direction = profile.values.get('wind_speed_10m'), profile.values.get('wind_direction_10m')
        return None if speed is None or direction is None else (*wind_components(speed, direction), False)
    level = profile.level(pressure)
    if level is None or level.u_mps is None or level.v_mps is None:
        return None
    return level.u_mps, level.v_mps, level.interpolated


def wind_payload(forecasts, anchor, *, days=5):
    """Hourly UTC timeline; missing samples remain null (including short horizons)."""
    start = datetime.combine(anchor.astimezone(LOCAL_TIMEZONE).date(), time(), LOCAL_TIMEZONE)
    times = [(start.astimezone(UTC) + timedelta(hours=h)).astimezone(LOCAL_TIMEZONE) for h in range(days*24)]
    stations = []
    for f in forecasts:
        series = []
        for when in times:
            try: profile = Profile.from_forecast(f, when)
            except KeyError:
                series.append([None]*len(WIND_LEVELS)); continue
            series.append([None if (v := wind_vector(profile, pressure)) is None else
                           [round(v[0], 2), round(v[1], 2), int(v[2])] for pressure, _ in WIND_LEVELS])
        stations.append({'lat': f.location.latitude, 'lon': f.location.longitude, 'values': series})
    return {'times': [t.isoformat() for t in times], 'labels': [t.strftime('%a %d %b · %H:%M %Z') for t in times],
            'levels': [label for _, label in WIND_LEVELS], 'stations': stations,
            'coverage_km': 120, 'source': 'Open-Meteo model wind · interpolated between samples'}


def soaring_samples(forecasts, when, loading):
    samples = []
    for f in forecasts:
        try: profile = Profile.from_forecast(f, when)
        except KeyError: continue
        thermal = evaluate_thermals(profile)
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
