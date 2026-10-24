"""Multi-altitude wave diagnostics from actual profiles, with explicit limits."""
from pathlib import Path
import base64
import io
import json
import math
import numpy as np
from PIL import Image
from folium.utilities import mercator_transform
from branca.element import MacroElement,Template
from config import WAVE_RIDGES,METRES_TO_FEET,MPS_TO_KNOTS
from soaring_physics import Profile,evaluate_wave,wind_direction

ALTITUDES_FT=(3000,10000,12000,16000,20000,25000)
COLOURS=('#ffe44a','#ffa12b','#ee3b26','#a31232','#7926ad')


def at_height(profile,height_m):
    """Interpolate only bracketed atmospheric heights; never extrapolate."""
    if height_m<=profile.forecast.terrain_elevation_m:return None
    for a,b in zip(profile.levels,profile.levels[1:]):
        if a.height_m<=height_m<=b.height_m:
            f=(height_m-a.height_m)/(b.height_m-a.height_m)
            def value(name):
                x,y=getattr(a,name),getattr(b,name)
                return None if x is None or y is None else x+f*(y-x)
            return {'u':value('u_mps'),'v':value('v_mps'),'w':value('w_mps'),
                    'pressure':math.exp(math.log(a.pressure_hpa)+f*math.log(b.pressure_hpa/a.pressure_hpa))}
    return None


def destination(lat,lon,bearing,km):
    angle=math.radians(bearing);phi=math.radians(lat);d=km/6371.
    latitude=math.asin(math.sin(phi)*math.cos(d)+math.cos(phi)*math.sin(d)*math.cos(angle))
    longitude=math.radians(lon)+math.atan2(math.sin(angle)*math.sin(d)*math.cos(phi),math.cos(d)-math.sin(phi)*math.sin(latitude))
    return math.degrees(latitude),math.degrees(longitude)


def wave_payload(forecasts,when):
    profiles={}
    for f in forecasts:
        if not f.location.key.startswith('ridge:'):continue
        try:profiles[f.location.key.split(':',1)[1]]=Profile.from_forecast(f,when)
        except KeyError:continue
    lat=np.linspace(49.8,59.,100);lon=np.linspace(-8.5,2.,100);xx,yy=np.meshgrid(lon,lat)
    from global_land_mask import globe
    land=globe.is_land(yy,xx);frames=[]
    for altitude in ALTITUDES_FT:
        total=np.zeros_like(xx);weights=np.zeros_like(xx);bars=[];sites=[]
        for key,ridge in WAVE_RIDGES.items():
            p=profiles.get(key);level=at_height(p,altitude/METRES_TO_FEET) if p else None
            wave=evaluate_wave(p,ridge) if p else None
            direction=wind_direction(level['u'],level['v']) if level and level['u'] is not None and level['v'] is not None else None
            uplift=level['w']*MPS_TO_KNOTS if level and level['w'] is not None else None
            supported=bool(wave and wave.components.get('cross_ridge_flow',0)>0 and wave.components.get('static_stability',0)>0)
            sites.append({'name':ridge.point.name,'lat':ridge.point.latitude,'lon':ridge.point.longitude,
                'uplift':uplift,'wind_from':direction,'score':wave.score if wave else None,'complete':bool(wave and wave.complete),
                'lower':wave.scorer_lower_m2 if wave else None,'upper':wave.scorer_upper_m2 if wave else None,
                'available':bool(level),'supported':supported})
            if supported and uplift is not None:
                distance=np.hypot((xx-ridge.point.longitude)*111*np.cos(np.deg2rad(yy)),(yy-ridge.point.latitude)*111)
                weight=np.exp(-.5*(distance/25)**2)*(distance<=65)
                total+=max(0,uplift)*weight;weights+=weight
            if supported and direction is not None and wave.characteristic_scale_km and wave.scorer_lower_m2>0:
                spacing=min(30,max(2,wave.characteristic_scale_km))
                for n in (1,2,3):
                    centre=destination(ridge.point.latitude,ridge.point.longitude,direction+180,n*spacing)
                    ends=[destination(*centre,direction+side,12) for side in (-90,90)]
                    bars.append({'points':ends,'name':ridge.point.name,'order':n,'spacing':round(spacing,1),
                        'resonance':wave.scorer_upper_m2 is not None and wave.scorer_upper_m2<wave.scorer_lower_m2})
        field=np.divide(total,weights,out=np.zeros_like(total),where=weights>0)
        rgba=np.zeros((*field.shape,4),dtype=np.uint8)
        for low,high,colour in zip((0,.5,1,2,4),(.5,1,2,4,float('inf')),COLOURS):
            selected=(weights>0)&land&(field>low)&(field<=high)
            rgba[selected,:3]=[int(colour[i:i+2],16) for i in (1,3,5)];rgba[selected,3]=165
        image=np.rint(mercator_transform(rgba,(49.8,59.),origin='lower')).astype(np.uint8)
        stream=io.BytesIO();Image.fromarray(np.flipud(image)).save(stream,format='PNG')
        frames.append({'altitude':altitude,'image':'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode(),
                       'bars':bars,'sites':sites,'positive_cells':int(np.count_nonzero(rgba[:,:,3]))})
    return {'when':when.isoformat(),'bounds':[[49.8,-8.5],[59,2]],'frames':frames,'colours':COLOURS,
        'source':'Open-Meteo ridge profiles · interpolated positive model vertical air motion where stable cross-ridge flow supports wave potential',
        'limits':'Regional diagnostic, not a resolved wave forecast. Bars show downwind Scorer-scale guides, not confirmed crests, rotor or clearance. Blank areas can mean no signal or missing data.'}


def add_wave_map(map_view,forecasts,when):
    import streamlit as st
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    key=(when.isoformat(),tuple((f.location.key,f.model,f.fetched_at.isoformat()) for f in forecasts))
    cache=st.session_state.setdefault('_wave_products',{}) if get_script_run_ctx(suppress_warning=True) else {}
    if key not in cache:
        cache.clear();cache[key]=wave_payload(forecasts,when)
    element=MacroElement();element.payload=json.dumps(cache[key],separators=(',',':'),allow_nan=False).replace('<','\\u003c')
    script=Path(__file__).with_name('assets').joinpath('wave_map.js').read_text()
    element._template=Template('{% macro script(this, kwargs) %}\n'+script+'\ninstallDuoWave({{this._parent.get_name()}},{{this.payload|safe}});\n{% endmacro %}')
    element.add_to(map_view)
