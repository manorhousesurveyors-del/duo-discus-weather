"""Nine real forecast anchors; smooth, labelled spatial display estimates."""
from dataclasses import replace
import numpy as np
from scipy.interpolate import RBFInterpolator
from config import GeoPoint

ANCHORS = (
    GeoPoint('region:shropshire','Long Mynd',52.518,-2.880),
    GeoPoint('anchor:exeter','South West / Exeter',50.73,-3.53),
    GeoPoint('anchor:cambridge','South East / Cambridge',52.20,.12),
    GeoPoint('anchor:denbigh','North Wales / Denbigh',53.18,-3.42),
    GeoPoint('anchor:sutton_bank','Pennines / Sutton Bank',54.23,-1.21),
    GeoPoint('anchor:borders','Scotland South / Borders',55.60,-2.75),
    GeoPoint('anchor:aboyne','Scotland Highlands / Aboyne',57.08,-2.84),
    GeoPoint('anchor:belfast','Northern Ireland / Belfast',54.60,-5.93),
    GeoPoint('anchor:dublin','Ireland Central / Dublin',53.35,-6.26),
)
SPATIAL_SOURCE = 'nine-anchor radial-basis spatial estimate'


def sample_anchors(forecasts, points, *, wind_only=False):
    """Interpolate scalar fields and wind vectors; never substitute another hour.

    Linear radial basis interpolation uses kilometres, clamps scalar overshoot
    to the available anchor range, and falls back to inverse-distance weights
    if fewer than three anchors support a column. Missing columns stay unknown.
    """
    forecasts, points = tuple(forecasts), tuple(points)
    if not forecasts or not points:return ()
    times = forecasts[0].times
    forecasts = tuple(f for f in forecasts if f.times == times)
    xy = np.array([[f.location.longitude*65.,f.location.latitude*111.] for f in forecasts])
    targets = np.array([[p.longitude*65.,p.latitude*111.] for p in points])
    distances = np.linalg.norm(targets[:,None,:]-xy[None,:,:],axis=2)
    nearest = distances.argmin(axis=1)
    fields = sorted(set.intersection(*(set(f.hourly) for f in forecasts)))
    if wind_only:
        fields = [k for k in fields if k.startswith(('wind_speed_','wind_direction_'))
                  or k in ('temperature_2m','surface_pressure','pressure_msl')]

    def interpolate(values):
        out = np.full((len(points),values.shape[1]),np.nan)
        # Reuse one solve for all hours with the same missing-anchor pattern.
        masks = np.isfinite(values)
        for mask in np.unique(masks.T,axis=0):
            columns = np.flatnonzero(np.all(masks == mask[:,None],axis=0))
            if not mask.any():continue
            data = values[mask][:,columns]
            if mask.sum() >= 3 and np.linalg.matrix_rank(xy[mask]-xy[mask][0]) == 2:
                result = RBFInterpolator(xy[mask],data,kernel='linear',smoothing=.01)(targets)
            else:
                weights=1/np.maximum(distances[:,mask],.1)**2
                result=(weights/weights.sum(axis=1,keepdims=True))@data
            out[:,columns] = np.clip(result,data.min(axis=0),data.max(axis=0))
        return out

    values={}
    for key in fields:
        if key.startswith('wind_direction_'):continue
        raw=np.array([f.hourly[key] for f in forecasts],dtype=float)
        if key.startswith('wind_speed_') and key.replace('wind_speed_','wind_direction_') in fields:
            direction=key.replace('wind_speed_','wind_direction_')
            angles=np.radians(np.array([f.hourly[direction] for f in forecasts],dtype=float))
            u,v=interpolate(-raw*np.sin(angles)),interpolate(-raw*np.cos(angles))
            values[key]=np.hypot(u,v)
            values[direction]=np.degrees(np.arctan2(-u,-v))%360
        else:values[key]=interpolate(raw)
    result=[]
    for i,point in enumerate(points):
        base=forecasts[nearest[i]]
        if distances[i,nearest[i]] < .001:
            result.append(replace(base,location=point));continue
        hourly={k:tuple(None if not np.isfinite(v) else float(v) for v in rows[i]) for k,rows in values.items()}
        provenance={k:tuple(None if v is None else SPATIAL_SOURCE for v in row) for k,row in hourly.items()}
        note=f'Spatial approximation: nine forecast anchors; nearest {base.location.name}, {distances[i,nearest[i]]:.0f} km.'
        result.append(replace(base,location=point,model_latitude=point.latitude,model_longitude=point.longitude,
            hourly=hourly,units={k:base.units.get(k) for k in hourly},field_sources=provenance,
            missing_counts={k:sum(v is None for v in row) for k,row in hourly.items()},
            warnings=base.warnings+(note,)))
    return tuple(result)
