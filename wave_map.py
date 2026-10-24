"""Multi-altitude wave diagnostics from actual profiles, with explicit limits."""
import base64
import io
import math
import numpy as np
from PIL import Image

ALTITUDES_FT=(3000,7000,10000,12000,13000,16000,20000,23000,25000)
WAVE_BANDS=((-1000,-4,'#153c92'),(-4,-2,'#277ad1'),(-2,-.5,'#5acde2'),(.5,2,'#fff4a1'),(2,4,'#ffe234'),(4,6,'#ff992b'),(6,8,'#e8392e'),(8,1000,'#7a164b'))
COLOURS=tuple(b[2] for b in WAVE_BANDS)


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


def _png(rgba):
    stream=io.BytesIO();Image.fromarray(np.flipud(rgba)).save(stream,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()


def _packed(array):
    import zlib
    values=np.where(np.isfinite(array),np.clip(np.rint(array),-32767,32767),-32768).astype('<i2')
    return base64.b64encode(zlib.compress(values.tobytes(),6)).decode()


def legacy_wave_payload(forecasts,when,*,terrain=None):
    from terrain_wave import load_terrain,profile_for_domain,solve_field,mercator_y,inverse_y
    terrain=load_terrain() if terrain is None else terrain
    frames=[{'altitude':a,'tiles':[],'positive_cells':0,'negative_cells':0,'peak':None,'bars':[],'sites':[]} for a in ALTITUDES_FT]
    domains=[]
    for d in terrain:
        distance,profile=profile_for_domain(forecasts,when,d)
        heights=d['heights'];south,west=d['bounds'][0];north,east=d['bounds'][1]
        lat=np.array([inverse_y(y) for y in np.linspace(mercator_y(south),mercator_y(north),heights.shape[0])])
        lon=np.linspace(west,east,heights.shape[1]);xx,yy=np.meshgrid(lon,lat)
        mine=((yy-d['latitude'])*111)**2+((xx-d['longitude'])*66)**2;owned=np.ones(heights.shape,dtype=bool)
        for other in terrain:
            if other['key']==d['key']:continue
            os,ow=other['bounds'][0];on,oe=other['bounds'][1]
            closer=((yy-other['latitude'])*111)**2+((xx-other['longitude'])*66)**2<mine
            owned &= ~(closer&(yy>=os)&(yy<=on)&(xx>=ow)&(xx<=oe))
        fields,diag=solve_field(heights,d['spacing_m'],profile,ALTITUDES_FT) if profile else ([None]*len(ALTITUDES_FT),{'reason':'No nearby atmospheric profile'})
        gy,gx=np.gradient(heights,d['spacing_m']);shade=np.clip((.7+.5*(-gx+gy))/np.sqrt(1+gx*gx+gy*gy),0,1)
        grey=np.rint(150+95*shade).astype(np.uint8);relief=np.zeros((*heights.shape,4),dtype=np.uint8)
        relief[:,:,:3]=grey[:,:,None];relief[:,:,3]=np.where(owned&(heights>0),190,0)
        def coarse_grid(array):
            # Cell-centred 1 km samples; keep unsupported cells unavailable.
            return array.reshape(array.shape[0]//2,2,array.shape[1]//2,2).mean(axis=(1,3))
        encoded=[];coarse=coarse_grid(heights)
        for i,field in enumerate(fields):
            rgba=np.zeros((*heights.shape,4),dtype=np.uint8);frame=frames[i]
            if field is not None:
                selected=owned&np.isfinite(field)
                for low,high,colour in WAVE_BANDS:
                    mask=selected&(field>=low)&(field<high)
                    rgba[mask,:3]=[int(colour[j:j+2],16) for j in (1,3,5)];rgba[mask,3]=180
                frame['positive_cells']+=int(np.count_nonzero(selected&(field>=.5)))
                frame['negative_cells']+=int(np.count_nonzero(selected&(field<=-.5)))
                peak=float(np.nanmax(np.where(selected,field,np.nan))) if selected.any() else None
                if peak is not None:frame['peak']=max(frame['peak'] or 0,peak)
                encoded.append(_packed(coarse_grid(field)*100))
            else:peak=None;encoded.append(None)
            wind=at_height(profile,frame['altitude']*.3048) if profile else None
            tile={'key':d['key'],'image':_png(rgba),'bounds':d['bounds'],'peak':None if peak is None else round(max(0,peak),1),
                  'wind':None if not wind or wind['u'] is None or wind['v'] is None else [wind['u'],wind['v']]}
            frame['tiles'].append(tile)
            frame['sites'].append({'name':d['name'],'lat':d['latitude'],'lon':d['longitude'],'available':field is not None,'peak':tile['peak']})
        domains.append({k:d[k] for k in ('key','name','latitude','longitude','bounds','spacing_m')}|{'relief':_png(relief),
            'rows':coarse.shape[0],'cols':coarse.shape[1],'terrain':_packed(coarse),'fields':encoded,'diagnostics':diag,
            'weather_distance_km':None if distance is None else round(distance,1),'weather_model':profile.forecast.model if profile else None})
    return {'when':when.isoformat(),'frames':frames,'domains':domains,'bands':WAVE_BANDS,'encoding':'zlib-i16le-base64',
        'source':'500 m terrain-forced linear wave estimate · Mapzen elevation / Open-Meteo atmospheric profiles',
        'limits':'Both lift and sink are modelled, not observed. Atmospheric spacing is much coarser than the terrain. Trapping, rotor, nonlinear flow and exact crest positions are uncertain. Cross-sections interpolate solved height bands.',
        'terrain_status':'Verified elevation grids loaded' if terrain else 'Terrain unavailable: run scripts/update_wave_terrain.py; no hill-specific field is fabricated.'}


def wave_payload(forecasts,when,*,terrain=None):
    """One continuous national canvas; no site clipping or ownership masks."""
    if terrain is not None: return legacy_wave_payload(forecasts,when,terrain=terrain)
    from terrain_wave import load_continuous_terrain,continuous_field
    try:grid=load_continuous_terrain()
    except (OSError,ValueError,KeyError):
        return {'when':when.isoformat(),'frames':[{'altitude':a,'tiles':[],'peak':None,'sites':[],'positive_cells':0,'negative_cells':0} for a in ALTITUDES_FT],
            'domains':[],'bands':WAVE_BANDS,'source':'Continuous terrain unavailable','limits':'No wave field fabricated.',
            'terrain_status':'Verified continuous terrain is missing; run scripts/update_continuous_terrain.py.'}
    fields,winds,profiles=continuous_field(grid,forecasts,when,ALTITUDES_FT)
    h=grid['heights'];gy,gx=np.gradient(h,grid['spacing_m']);shade=np.clip((.7+.5*(-gx+gy))/np.sqrt(1+gx*gx+gy*gy),0,1)
    relief=np.zeros((*h.shape,4),dtype=np.uint8);relief[:,:,:3]=np.rint(150+95*shade)[:,:,None];relief[:,:,3]=np.where(h>0,125,0)
    frames=[];encoded=[]
    for i,(alt,field) in enumerate(zip(ALTITUDES_FT,fields)):
        rgba=np.zeros((*h.shape,4),dtype=np.uint8);valid=np.isfinite(field)
        # Continuous colour interpolation between physical velocity stops.
        stops=[-8,-4,-2,-.5,0,.5,2,4,6,8,12]
        colours=['#153c92','#153c92','#277ad1','#5acde2','#ffffff','#fff4a1','#ffe234','#ff992b','#e8392e','#7a164b','#7a164b']
        values=np.nan_to_num(field)
        for c,j in enumerate((1,3,5)):rgba[:,:,c]=np.interp(values,stops,[int(col[j:j+2],16) for col in colours]).astype(np.uint8)
        rgba[:,:,3]=np.where(valid,np.minimum(165,np.maximum(0,(np.abs(values)-.35)*300)),0).astype(np.uint8)
        peak=float(np.nanmax(field)) if valid.any() else None
        tile={'key':'uk','bounds':grid['bounds'],'image':_png(rgba),'peak':None if peak is None else round(max(0,peak),1),'wind':None,
            'wind_u':_packed(winds[i][0]*100),'wind_v':_packed(winds[i][1]*100)}
        encoded.append(_packed(field*100))
        frames.append({'altitude':alt,'tiles':[tile],'peak':peak,'sites':[],
            'positive_cells':int(np.count_nonzero(field>=.5)),'negative_cells':int(np.count_nonzero(field<=-.5))})
    domain={'key':'uk','name':'Continuous UK / eastern Ireland','latitude':54.5,'longitude':-3.,'bounds':grid['bounds'],
        'rows':h.shape[0],'cols':h.shape[1],'spacing_m':grid['spacing_m'],'terrain':_packed(h),'fields':encoded,'relief':_png(relief),
        'diagnostics':{},'profiles':profiles,'weather_model':'Blended loaded profiles','weather_distance_km':None}
    return {'when':when.isoformat(),'frames':frames,'domains':[domain],'bands':WAVE_BANDS,'encoding':'zlib-i16le-base64',
        'source':'Continuous ~2 km Mapzen terrain · regional spectral wave solutions + available Open-Meteo background ascent',
        'limits':'Independent linear estimate, not SkySight. Smooth atmospheric blending; variable map scale, unresolved rotor and uncertain amplitudes/crest positions. Unsupported heights or profiles stay transparent.',
        'terrain_status':'Verified continuous elevation mosaic loaded'}


def add_wave_map(map_view,forecasts,when):
    import streamlit as st
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    from client_map import script,reorder_layer_control
    from airspace import load_airspace,display_airspace
    from config import LOCAL_TIMEZONE
    import folium,html
    key=(when.isoformat(),tuple((f.location.key,f.model,f.fetched_at.isoformat()) for f in forecasts if f.location.key.startswith(('region:','ridge:'))))
    cache=st.session_state.setdefault('_wave_products',{}) if get_script_run_ctx(suppress_warning=True) else {}
    if key not in cache:cache.clear();cache[key]=wave_payload(forecasts,when)
    script(map_view,'wave_map.js','installDuoWave',cache[key])
    body=load_airspace(when.astimezone(LOCAL_TIMEZONE).date())
    if body:
        display=display_airspace(body)
        display={**display,'features':[{**f,'properties':{k:html.escape(v) if isinstance(v,str) else v for k,v in f['properties'].items()}} for f in display['features']]}
        folium.GeoJson(display,name='Airspace boundaries · simplified display',show=True,
            style_function=lambda _:dict(color='#a52a49',weight=.8,fillOpacity=.02),
            tooltip=folium.GeoJsonTooltip(fields=['name','floor','ceiling'],aliases=['Name','Lower','Upper'])).add_to(map_view)
    reorder_layer_control(map_view)
