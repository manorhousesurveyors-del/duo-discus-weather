"""Terrain-forced, linear stationary mountain-wave diagnostic.

Actual DEM slopes force w0=U dh/dx+V dh/dy. A damped Fourier/WKB response
propagates both phases through the forecast Scorer profile. It is a linear,
coarse-atmosphere estimate, not a validated mesoscale or rotor forecast.
"""
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import math
import numpy as np
from scipy.ndimage import gaussian_filter
from scipy.fft import fft2,ifft2,fftfreq
from config import MPS_TO_KNOTS,GeoPoint
from soaring_physics import distance_bearing,Profile

TERRAIN_PATH=Path(__file__).with_name('data')/'uk_wave_terrain.npz'
META_PATH=TERRAIN_PATH.with_suffix('.json')
# Each square samples real elevation; these are computational windows, not wave polygons.
DOMAINS=(
 ('snowdonia','Snowdonia / Yr Wyddfa',53.05,-4.02),
 ('clwyd','Denbigh / Clwydian Range / Ruthin',53.13,-3.25),
 ('berwyn','Berwyns / Llangollen',52.9,-3.35),
 ('cadair','Cadair Idris / Aran Fawddwy',52.77,-3.80),
 ('cambrian','Cambrian Mountains / Plynlimon',52.40,-3.75),
 ('mynd','Long Mynd / Stiperstones / Corndon / Clun',52.50,-2.95),
 ('clee','Brown Clee / Titterstone Clee',52.45,-2.60),
 ('brecon','Black Mountains / Talgarth / Brecon Beacons',51.94,-3.32),
 ('malvern','Malvern Hills',52.10,-2.34),
 ('peak','Peak District',53.35,-1.82),
 ('pennines','Pennines / Yorkshire Dales',54.35,-2.18),
 ('lakes','Lake District',54.55,-3.05),
 ('cheviots','Cheviots',55.48,-2.15),
 ('highlands','Scottish Highlands / Lochaber',56.90,-4.75),
 ('grampians','Grampians / Cairngorms / Aboyne',57.00,-3.35),
 ('fife','Portmoak / Ochils',56.23,-3.40),
)


def mercator_y(lat):return math.log(math.tan(math.pi/4+math.radians(lat)/2))


def inverse_y(value):return math.degrees(2*math.atan(math.exp(value))-math.pi/2)


@lru_cache(maxsize=2)
def _terrain(path,modified):
    metadata=json.loads(Path(path).with_suffix('.json').read_text())
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=metadata['sha256']:raise ValueError('Terrain checksum mismatch')
    with np.load(path,allow_pickle=False) as source:
        records=[]
        for entry in metadata['domains']:
            heights=source[entry['key']].astype(np.float64)
            if heights.shape!=(256,256) or not np.isfinite(heights).all() or heights.min()<-100 or heights.max()>1600:raise ValueError('Invalid UK terrain grid')
            records.append({**entry,'heights':heights})
    return records


def load_terrain():
    if not TERRAIN_PATH.exists() or not META_PATH.exists():return []
    return _terrain(str(TERRAIN_PATH),TERRAIN_PATH.stat().st_mtime_ns)


def atmosphere(profile,heading):
    levels=[l for l in profile.levels if l.u_mps is not None and l.v_mps is not None]
    if len(levels)<4:return None
    z=np.array([l.height_m for l in levels]);theta=np.array([l.theta_k for l in levels])
    if not np.all(np.diff(z)>0):return None
    az=math.radians(heading);cross=np.array([l.u_mps*math.sin(az)+l.v_mps*math.cos(az) for l in levels])
    n2=9.80665/theta*np.gradient(theta,z,edge_order=2)
    safe=np.where(np.abs(cross)>3,cross,np.nan)
    scorer=n2/safe**2-np.gradient(np.gradient(cross,z,edge_order=2),z,edge_order=2)/safe
    return {'z':z,'n2':n2,'cross':cross,'scorer':scorer,
            'u':np.array([l.u_mps for l in levels]),'v':np.array([l.v_mps for l in levels])}


def solve_field(heights,spacing_m,profile,altitudes_ft):
    """Return signed kt fields and diagnostics; unknown heights are null.

    Spatial frequencies 2–40 km retain ridge detail. A damped harmonic response
    favours the Scorer wavelength; no artificial windward/lee rectangle is used.
    Zero/unstable/reversing flow cannot create a positive wave field.
    """
    shape=heights.shape
    if heights.ndim!=2 or min(shape)<16 or not np.isfinite(heights).all() or spacing_m<=0:raise ValueError('Invalid wave terrain')
    low=next((l for l in profile.levels if l.height_m>max(profile.forecast.terrain_elevation_m+250,float(np.percentile(heights,90))) and l.u_mps is not None and l.v_mps is not None),None)
    if low is None:return [None]*len(altitudes_ft),{'reason':'No above-terrain wind profile'}
    u,v=low.u_mps,low.v_mps;speed=math.hypot(u,v);heading=math.degrees(math.atan2(u,v))%360
    a=atmosphere(profile,heading)
    if a is None or speed<3:return [None]*len(altitudes_ft),{'reason':'Insufficient wind/stability profile'}
    lower=np.interp(low.height_m,a['z'],a['scorer']);n2=np.interp(low.height_m,a['z'],a['n2'])
    if not math.isfinite(lower) or lower<=0 or n2<=0:
        return [np.zeros(shape) if a['z'][0]<=z*.3048<=a['z'][-1] else None for z in altitudes_ft],{'reason':'No stable cross-terrain flow','wind_to':heading,'speed_mps':speed}
    wavelength=2*math.pi/math.sqrt(lower)/1000
    # Keep diagnosed wavelengths, never force them into an attractive 5–18 km band.
    upper=float(np.interp(min(low.height_m+2500,a['z'][-1]),a['z'],a['scorer']))
    smooth=gaussian_filter(heights,.7)
    gy,gx=np.gradient(smooth,spacing_m)
    slope=np.hypot(gx,gy);normal=np.sqrt(np.mean((u*gx+v*gy)**2)/max(np.mean(slope**2),1e-12))
    pad=(shape[0]//2,shape[1]//2)
    h=np.pad(smooth,((pad[0],pad[0]),(pad[1],pad[1])),mode='reflect')
    h=(h-h.mean())*np.outer(np.hanning(h.shape[0]),np.hanning(h.shape[1]))
    ky=2*np.pi*fftfreq(h.shape[0],spacing_m)[:,None];kx=2*np.pi*fftfreq(h.shape[1],spacing_m)[None,:]
    # Rows run south -> north, so positive ky is geographic north.
    intrinsic=kx*u+ky*v;kh2=kx*kx+ky*ky;kh=np.sqrt(kh2);safe=np.abs(intrinsic)>1e-5
    kparallel=intrinsic/max(speed,1e-9);wavenumber=2*math.pi/max(wavelength*1000,1)
    # Damped lee response: causal poles yield downwind bounces, decaying over ~4 waves.
    damping=wavenumber/(8*math.pi)
    # scipy's inverse transform uses exp(+ikx): positive imaginary damping
    # places poles in the upper half-plane and the response downwind in x.
    response=1+(.25*wavenumber**2)/(wavenumber**2-kparallel**2+2j*damping*kparallel)
    band=(kh>=2*math.pi/40000)&(kh<=2*math.pi/2000)&safe
    forcing=1j*intrinsic*fft2(h)*band*response
    fields=[]
    for feet in altitudes_ft:
        height=feet*.3048
        if height<a['z'][0] or height>a['z'][-1] or height<=profile.forecast.terrain_elevation_m:
            fields.append(None);continue
        levels=np.linspace(low.height_m,max(low.height_m,height),max(2,int(abs(height-low.height_m)/250)+1))
        phase=np.zeros_like(kh);attenuation=np.zeros_like(kh);blocked=False
        for za,zb in zip(levels,levels[1:]):
            z=(za+zb)/2;flow=float(np.interp(z,a['z'],a['cross']));stability=float(np.interp(z,a['z'],a['n2']));scorer=float(np.interp(z,a['z'],a['scorer']))
            if flow<=3 or stability<=0 or not math.isfinite(scorer):blocked=True;break
            # Oblique terrain wavelengths use full horizontal wavenumber.
            ratio=np.divide(kh2,kparallel**2,out=np.zeros_like(kh2),where=safe)
            m2=scorer*ratio-kh2
            phase+=np.sqrt(np.maximum(m2,0))*(zb-za)*np.sign(intrinsic)
            attenuation+=np.sqrt(np.maximum(-m2,0))*(zb-za)
        if blocked:fields.append(np.zeros(shape));continue
        transfer=np.exp(1j*phase-np.minimum(attenuation,50))*math.exp(-max(0,height-low.height_m)/18000)
        field=np.real(ifft2(forcing*transfer))[pad[0]:pad[0]+shape[0],pad[1]:pad[1]+shape[1]]*MPS_TO_KNOTS
        field=np.where(heights<height,field,np.nan)
        fields.append(field)
    return fields,{'wind_to':round(heading,1),'speed_mps':round(speed,2),'cross_ridge_mps':round(normal,2),
        'n_s1':round(math.sqrt(n2),5),'scorer_lower_m2':lower,'scorer_upper_m2':upper,
        'wavelength_km':round(wavelength,2),'trapped':bool(math.isfinite(upper) and upper<lower),
        'bounce_decay':[round(math.exp(-n/4),3) for n in range(1,5)],
        'profile_heights_m':a['z'].round(1).tolist(),'profile_scorer_m2':[float(x) if math.isfinite(x) else None for x in a['scorer']]}


def profile_for_domain(forecasts,when,domain):
    centre=GeoPoint('terrain','Terrain',domain['latitude'],domain['longitude']);candidates=[]
    for f in forecasts:
        if not f.location.key.startswith('ridge:'):continue
        distance=distance_bearing(centre,f.location)[0]
        if distance>160:continue
        try:candidates.append((distance,Profile.from_forecast(f,when)))
        except KeyError:pass
    return min(candidates,key=lambda x:x[0]) if candidates else (None,None)
