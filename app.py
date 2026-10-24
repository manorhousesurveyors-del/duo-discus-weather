"""Interactive UK soaring dashboard. Start with: streamlit run app.py.

Map clicks fetch a fresh sounding for the selected coordinate. All displayed
forecast columns remain model-specific; unavailable diagnostics stay unavailable.
"""
from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
import html
import math
import os
from pathlib import Path
import re

import folium
from folium.utilities import mercator_transform
from branca.colormap import LinearColormap
from branca.element import MacroElement, Template
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.interpolate import griddata
from scipy.spatial import cKDTree
import streamlit as st
from map_component import render_map as st_folium

from task_planning import task_distance

from alerts import (ForecastBundle, GRID_VARIABLES, evaluate_convergence, load_bundle)
from config import (FORECAST_MODELS, LOCAL_TIMEZONE, MONITORING_ZONES,
                    WAVE_RIDGES, GeoPoint, convergence_grid, forecast_locations)
from data_ingestion import (AviationWeatherClient, Forecast, IngestionError, JsonHttpClient,
                            OpenMeteoClient, utc_datetime)
from soaring_physics import (CP, RD, Profile, dewpoint_c, distance_bearing,
                             evaluate_thermals, evaluate_wave, rank_wave_sites)

MODEL_LABELS = {'ifs025':'ECMWF · medium range', 'ukmo':'Met Office · UK 2 km', 'icon_eu':'DWD ICON-EU · 7 km'}
CATEGORY_COLOURS = {'VFR':'#15835d','MVFR':'#2674c5','IFR':'#d84949','LIFR':'#a342ac','UNKNOWN':'#6c7780'}


@st.cache_data(ttl=900,max_entries=2,show_spinner=False)
def load_recorded(path,modified):
    return load_bundle(Path(path))


@st.cache_data(ttl=900,show_spinner=False)
def load_sites(model_key: str, refresh_bucket: int):
    return OpenMeteoClient().fetch_batched(forecast_locations(),(FORECAST_MODELS[model_key],))


@st.cache_data(ttl=900,show_spinner=False)
def load_grid(model_key: str, refresh_bucket: int):
    grid = convergence_grid()
    return OpenMeteoClient().fetch_batched([p for row in grid for p in row],(FORECAST_MODELS[model_key],),variables=GRID_VARIABLES)


@st.cache_data(ttl=900,show_spinner=False)
def load_map_forecasts(model_key: str, refresh_bucket: int, kind="soaring"):
    from planning_ui import fetch_map_grid
    return fetch_map_grid(model_key,refresh_bucket,kind)


@st.cache_data(ttl=900,show_spinner=False)
def load_coordinate(latitude: float, longitude: float, model_key: str, refresh_bucket: int):
    point = GeoPoint('inspector',f'{latitude:.3f}, {longitude:.3f}',latitude,longitude)
    return OpenMeteoClient().fetch(point,FORECAST_MODELS[model_key])


@st.cache_data(ttl=60,show_spinner=False)
def load_aviation(refresh_bucket: int):
    return AviationWeatherClient().fetch()


@st.cache_data(ttl=60,show_spinner=False)
def load_uk_aviation(refresh_bucket: int):
    from uk_airfields import reporting_stations
    from data_ingestion import AviationBatch
    stations=reporting_stations();client=AviationWeatherClient()
    batches=[client.fetch(stations[i:i+100]) for i in range(0,len(stations),100)]
    return AviationBatch({k:v for b in batches for k,v in b.metars.items()},
        {k:v for b in batches for k,v in b.tafs.items()},
        tuple(s for b in batches for s in b.missing_metars),tuple(s for b in batches for s in b.missing_tafs),
        tuple(f for b in batches for f in b.failures),{k:v for b in batches for k,v in b.fetched_at.items()},
        tuple(k for b in batches for k in b.cached_products))


@st.cache_data(ttl=300,show_spinner=False)
def load_radar(refresh_bucket: int):
    body = JsonHttpClient().get_json('https://api.rainviewer.com/public/weather-maps.json',{},ttl_seconds=300).body
    if not isinstance(body,dict) or body.get('host') != 'https://tilecache.rainviewer.com':
        raise IngestionError('Unexpected radar tile host')
    frames = body.get('radar',{}).get('past',[])
    if not isinstance(frames,list) or not frames:
        raise IngestionError('No observed radar frames available')
    if any(not isinstance(frame,dict) or not isinstance(frame.get('time'),int) for frame in frames):
        raise IngestionError('Invalid radar frame metadata')
    frame = max(frames,key=lambda item:item['time'])
    if not isinstance(frame.get('time'),int) or not re.fullmatch(r'/v2/radar/[A-Za-z0-9_/-]+',str(frame.get('path',''))):
        raise IngestionError('Invalid radar frame metadata')
    return {'time':datetime.fromtimestamp(frame['time'],UTC),
            'url':body['host']+frame['path']+'/512/{z}/{x}/{y}/2/1_1.png'}


@st.cache_data(ttl=300,show_spinner=False)
def load_calibrated_radar(url,refresh_bucket):
    from radar_rates import load_rate_grid,rate_overlay
    return rate_overlay(load_rate_grid(url))


@st.cache_data(ttl=300,max_entries=256,show_spinner=False)
def load_radar_precipitation(latitude,longitude,when,model_key,refresh_bucket):
    from precipitation import fetch_point_precipitation
    return fetch_point_precipitation(latitude,longitude,when,FORECAST_MODELS[model_key])


def radar_point_result(coordinate,when,model_key,refresh_bucket,*,recorded=False):
    """Return a fresh, coordinate-specific result or an explicit missing flag."""
    latitude,longitude=coordinate
    result={'latitude':latitude,'longitude':longitude,'rate':None,
            'valid_at':when.isoformat(),'source':'Open-Meteo',
            'model':FORECAST_MODELS[model_key].api_name}
    if recorded:
        return {**result,'error':'Recorded snapshot: live point queries are disabled.'}
    try:
        return load_radar_precipitation(latitude,longitude,when,model_key,refresh_bucket)
    except (IngestionError,ValueError) as exc:
        return {**result,'error':f'Point precipitation unavailable: {exc}'}


def sounding_figure(profile: Profile) -> go.Figure:
    """Skew-T coordinates: x=T+35 ln(1000/p), log-pressure vertical axis."""
    fig = go.Figure()
    pressures = np.linspace(1050,450,65)
    skew = lambda temperature,p: np.asarray(temperature)+35*np.log(1000/np.asarray(p))
    for temperature in range(-70,51,10):
        fig.add_trace(go.Scatter(x=skew(temperature,pressures),y=pressures,mode='lines',
                                line={'color':'#e0e6eb','width':1},hoverinfo='skip',showlegend=False))
    for theta in range(260,361,20):
        temperature = theta*(pressures/1000)**(RD/CP)-273.15
        fig.add_trace(go.Scatter(x=skew(temperature,pressures),y=pressures,mode='lines',
                                line={'color':'#e6ddd2','width':1,'dash':'dot'},hoverinfo='skip',showlegend=False))
    # Saturated pseudoadiabats integrated in log-pressure, constant latent heat.
    for initial in (0,10,20,30):
        p = np.linspace(1000,450,100)
        temperature = [initial+273.15]
        for before,after in zip(p,p[1:]):
            t = temperature[-1]
            vapour = 6.112*math.exp(17.67*(t-273.15)/(t-29.65))
            ratio = .622*vapour/(before-vapour)
            slope = RD*t/CP*(1+2.5e6*ratio/(RD*t))/(1+(2.5e6)**2*ratio/(CP*461.5*t*t))
            temperature.append(t+slope*math.log(after/before))
        fig.add_trace(go.Scatter(x=skew(np.array(temperature)-273.15,p),y=p,mode='lines',
                                line={'color':'#d7e5ef','width':1,'dash':'dot'},hoverinfo='skip',showlegend=False))
    levels = profile.levels
    for name,colour,values in (
        ('Temperature','#df7553',[l.temperature_c for l in levels]),
        ('Dewpoint','#249b8d',[dewpoint_c(l.temperature_c,l.rh_percent) if l.rh_percent is not None else None for l in levels]),
    ):
        p = [l.pressure_hpa for l in levels]
        x = [None if t is None else float(skew(t,pressure)) for t,pressure in zip(values,p)]
        fig.add_trace(go.Scatter(x=x,y=p,customdata=values,name=name,mode='lines+markers',connectgaps=False,
                                line={'color':colour,'width':3},hovertemplate='%{y:.0f} hPa · %{customdata:.1f} °C<extra>'+name+'</extra>'))
    fig.update_layout(height=390,margin={'l':35,'r':5,'t':20,'b':35},paper_bgcolor='rgba(0,0,0,0)',
                      plot_bgcolor='#f8fafb',legend={'orientation':'h','y':1.1},font={'size':10},
                      xaxis={'title':'Skew temperature coordinate','range':[-35,55]},
                      yaxis={'title':'hPa','type':'log','range':[math.log10(1050),math.log10(450)],
                             'tickvals':[1000,950,925,850,700,600,500]})
    return fig


def meteogram_figure(forecast: Forecast, day) -> go.Figure:
    times = [t for t in forecast.times if t.astimezone(LOCAL_TIMEZONE).date()==day]
    thermal = [evaluate_thermals(Profile.from_forecast(forecast,t)) for t in times]
    fig = make_subplots(rows=3,cols=1,shared_xaxes=True,vertical_spacing=.08,
                        subplot_titles=('Net climb · kt','Potential cloud base · ft AMSL','Wind / precipitation'),
                        specs=[[{}],[{}],[{'secondary_y':True}]])
    local = [t.astimezone(LOCAL_TIMEZONE) for t in times]
    fig.add_trace(go.Scatter(x=local,y=[t.net_climb_knots for t in thermal],name='Net climb',line={'color':'#de8350'},connectgaps=False),row=1,col=1)
    fig.add_trace(go.Scatter(x=local,y=[t.conservative_climb_knots for t in thermal],name='Conservative',line={'color':'#b86035','dash':'dot'}),row=1,col=1)
    fig.add_trace(go.Scatter(x=local,y=[t.cloud_base_ft_amsl for t in thermal],name='Cloud base',line={'color':'#377aac'}),row=2,col=1)
    fig.add_trace(go.Scatter(x=local,y=[t.usable_top_ft_amsl for t in thermal],name='Usable top',line={'color':'#66a0b9','dash':'dot'}),row=2,col=1)
    values = [forecast.at(t) for t in times]
    fig.add_trace(go.Scatter(x=local,y=[None if v.get('wind_speed_10m') is None else v['wind_speed_10m']*3600/1852 for v in values],name='Wind kt'),row=3,col=1)
    fig.add_trace(go.Bar(x=local,y=[v.get('precipitation') for v in values],name='Rain mm',marker_color='#89bacc'),row=3,col=1,secondary_y=True)
    fig.update_layout(height=430,margin={'l':30,'r':25,'t':25,'b':20},showlegend=False,paper_bgcolor='rgba(0,0,0,0)',font={'size':10})
    fig.update_xaxes(tickformat='%H:%M',row=3,col=1,title_text='Europe/London')
    return fig


def _destination(latitude,longitude,bearing,distance_km):
    angle, distance = math.radians(bearing),distance_km/6371.0088
    lat, lon = math.radians(latitude),math.radians(longitude)
    result_lat = math.asin(math.sin(lat)*math.cos(distance)+math.cos(lat)*math.sin(distance)*math.cos(angle))
    result_lon = lon+math.atan2(math.sin(angle)*math.sin(distance)*math.cos(lat),math.cos(distance)-math.sin(lat)*math.sin(result_lat))
    return math.degrees(result_lat),math.degrees(result_lon)


def wind_barb_svg(direction, speed_knots, colour):
    if speed_knots is None or direction is None:
        return f'<svg width="44" height="44"><circle cx="22" cy="22" r="6" fill="{colour}"/></svg>'
    if speed_knots < 2.5:
        return f'<svg width="44" height="44"><circle cx="22" cy="22" r="7" fill="none" stroke="{colour}" stroke-width="2"/></svg>'
    units = int((speed_knots+2.5)//5)*5
    shapes, y = [],3
    while units >= 50:
        shapes.append(f'<polygon points="22,{y} 35,{y+4} 22,{y+8}" fill="{colour}"/>')
        units -= 50
        y += 9
    while units >= 10:
        shapes.append(f'<path d="M22 {y} L34 {y+5}"/>')
        units -= 10
        y += 4
    if units >= 5:
        shapes.append(f'<path d="M22 {y} L28 {y+3}"/>')
    return (f'<svg width="44" height="44"><g transform="rotate({direction:.1f} 22 22)" stroke="{colour}" stroke-width="2" fill="none">'
            '<path d="M22 22 L22 2"/>'+''.join(shapes)+'</g></svg>')


def interpolate_map_samples(samples):
    """Linear interpolation inside the sample hull, capped at 75 km from a sample."""
    if len(samples) < 3:
        return []
    array = np.asarray(samples,float)
    projection = np.column_stack((array[:,1]*111*math.cos(math.radians(54)),array[:,0]*111))
    if np.linalg.matrix_rank(projection-projection.mean(axis=0)) < 2:
        return []
    lat, lon = np.meshgrid(np.arange(50,59.01,.18),np.arange(-6.5,2.01,.18),indexing='ij')
    query = np.column_stack((lon.ravel()*111*math.cos(math.radians(54)),lat.ravel()*111))
    values = griddata(projection,array[:,2],query,method='linear')
    distances,_ = cKDTree(projection).query(query)
    from global_land_mask import globe
    land = globe.is_land(lat.ravel(),lon.ravel())
    return [[float(a),float(b),float(v)] for a,b,v,d,on_land in zip(lat.ravel(),lon.ravel(),values,distances,land)
            if np.isfinite(v) and d <= 75 and on_land]


def add_colour_legend(map_view, caption, colours, minimum, maximum, layer=None):
    """Leaflet legend with inline CSS; no additional script/CDN dependency."""
    legend = MacroElement()
    legend._template = Template('''{% macro script(this, kwargs) %}
        var {{this.get_name()}} = L.control({position: 'bottomright'});
        {{this.get_name()}}.onAdd = function(map) {
            var div = L.DomUtil.create('div');
            div.innerHTML = {{this.content|tojson}};
            L.DomEvent.disableClickPropagation(div);
            return div;
        };
        {{this.get_name()}}.addTo({{this._parent.get_name()}});
        {% if this.layer %}
        function {{this.get_name()}}_update() {
            {{this.get_name()}}.getContainer().style.display =
                {{this._parent.get_name()}}.hasLayer({{this.layer.get_name()}}) ? '' : 'none';
        }
        {{this._parent.get_name()}}.on('layeradd layerremove', {{this.get_name()}}_update);
        {{this.get_name()}}_update();
        {% endif %}
    {% endmacro %}''')
    legend.layer = layer
    legend.content = ('<div style="background:rgba(255,255,255,.94);padding:9px 12px;border-radius:5px;'
                      'width:240px;font:11px sans-serif;color:#183042">'+html.escape(caption)+
                      '<div style="height:9px;margin-top:7px;background:linear-gradient(to right,'+
                      ','.join(colours)+')"></div><div style="display:flex;justify-content:space-between;margin-top:3px">'+
                      f'<span>{minimum:g}</span><span>{maximum:g}</span></div></div>')
    legend.add_to(map_view)


THERMAL_STOPS = ((0., '#2354cf'), (1., '#1d8df0'), (1.7, '#20d4e5'),
                 (3., '#f4e65a'), (4.25, '#f59335'), (5.5, '#d72932'))


def thermal_colour(value):
    positions = np.array([s[0] for s in THERMAL_STOPS])
    colours = np.array([[int(s[1][i:i+2],16) for i in (1,3,5)] for s in THERMAL_STOPS])
    rgb = [int(round(np.interp(value,positions,colours[:,i]))) for i in range(3)]
    return '#' + ''.join(f'{v:02x}' for v in rgb)


def thermal_overlay_data(samples):
    """Fixed-value RGBA field, independent of point density and map zoom."""
    if len(samples)<3:
        return None
    values={}
    for lat,lon,value in samples:
        values.setdefault((lat,lon),[]).append(value)
    array=np.array([[lat,lon,np.mean(v)] for (lat,lon),v in values.items()])
    projection=np.column_stack((array[:,1]*111*math.cos(math.radians(54)),array[:,0]*111))
    if len(array)<3 or np.linalg.matrix_rank(projection-projection.mean(axis=0))<2:
        return None
    south,north=max(49.,array[:,0].min()-.1),min(61.5,array[:,0].max()+.1)
    west,east=max(-9.,array[:,1].min()-.1),min(2.5,array[:,1].max()+.1)
    lats=np.linspace(south,north,max(3,math.ceil((north-south)/.04)))
    lons=np.linspace(west,east,max(3,math.ceil((east-west)/.04)))
    lat,lon=np.meshgrid(lats,lons,indexing='ij')
    query=np.column_stack((lon.ravel()*111*math.cos(math.radians(54)),lat.ravel()*111))
    field=griddata(projection,array[:,2],query,method='linear')
    distance,_=cKDTree(projection).query(query)
    from global_land_mask import globe
    mask=np.isfinite(field) & (distance<=75) & globe.is_land(lat.ravel(),lon.ravel())
    rgba=np.zeros((len(field),4),dtype=np.uint8)
    stops=np.array([v for v,_ in THERMAL_STOPS])
    for channel,offset in enumerate((1,3,5)):
        colours=np.array([int(c[offset:offset+2],16) for _,c in THERMAL_STOPS])
        rgba[mask,channel]=np.rint(np.interp(field[mask],stops,colours)).astype(np.uint8)
    rgba[mask,3]=155
    return np.flipud(rgba.reshape((*lat.shape,4))), [[south,west],[north,east]]


def build_map(forecasts, when, *, layers, aviation=None, radar=None, convergence=None,
              centre=(53.3,-2.6), zoom=6, task=(), turnpoints=(), visual_mode=None, anchor=None, loading="dry", rate_grid=None, radar_point=None, map_identity="weather", show_airfields=False, live_airfields=True, include_modes=True, selected_airfield=None):
    if visual_mode not in (None,'extras'):
        layers=['BGA turnpoints'] if visual_mode=='route' else []
    map_view=folium.Map(location=centre,zoom_start=zoom,tiles=None,control_scale=True,prefer_canvas=True)
    from map_bridge import add_click_bridge
    from client_map import install_canvas,add_waypoints,add_task_editor,finish_layer_control
    install_canvas(map_view)
    add_click_bridge(map_view,map_identity)
    map_view.default_js=[('leaflet','https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js'),
                         ('jquery','https://cdn.jsdelivr.net/npm/jquery@3.7.1/dist/jquery.min.js')]
    map_view.default_css=[('leaflet_css','https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css')]
    folium.TileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',name='Fast terrain · Esri',
        attr='Tiles © Esri, HERE, Garmin, USGS, OpenStreetMap contributors',max_zoom=19,show=map_identity!='wave').add_to(map_view)
    folium.TileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',name='Greyscale · Esri',
        attr='Tiles © Esri, HERE, Garmin, OpenStreetMap contributors',max_native_zoom=16,max_zoom=19,show=map_identity=='wave').add_to(map_view)
    folium.TileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Shaded_Relief/MapServer/tile/{z}/{y}/{x}',name='Shaded relief',
        attr='Tiles © Esri',class_name='duo-relief-tiles',max_native_zoom=13,max_zoom=19,overlay=True,opacity=.4,show=map_identity=='wave').add_to(map_view)
    folium.TileLayer('https://a.tile.opentopomap.org/{z}/{x}/{y}.png',name='Topography · OpenTopoMap',show=False,
        attr='Map data © OpenStreetMap contributors, SRTM | Map style © OpenTopoMap (CC-BY-SA)',max_zoom=17).add_to(map_view)
    folium.TileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',name='OpenStreetMap',
        attr='© OpenStreetMap contributors',max_zoom=19,show=False).add_to(map_view)
    thermal_samples,cloud_samples,waves=[],[],[]
    thermal_group=folium.FeatureGroup(name='Thermal strength · kt',show='Thermals' in layers)
    cloud_group=folium.FeatureGroup(name='Potential cloud base · ft AMSL',show='Cloud base' in layers,control='Cloud base' in layers)
    wave_group=folium.FeatureGroup(name='UK wave potential',show='Wave sites' in layers,control='Wave sites' in layers)
    for forecast in forecasts:
        try: profile=Profile.from_forecast(forecast,when)
        except KeyError: continue
        t=evaluate_thermals(profile); lat,lon=forecast.location.latitude,forecast.location.longitude
        if t.net_climb_knots is not None: thermal_samples.append([lat,lon,t.net_climb_knots])
        if t.cloud_base_ft_amsl is not None: cloud_samples.append([lat,lon,t.cloud_base_ft_amsl])
        label=html.escape(forecast.location.name)
        climb='Unavailable' if t.net_climb_knots is None else f'{t.net_climb_knots:.1f} kt'
        base='Unavailable' if t.cloud_base_ft_amsl is None else f'{t.cloud_base_ft_amsl:.0f} ft AMSL'
        folium.CircleMarker([lat,lon],radius=3,color=thermal_colour(t.net_climb_knots or 0),fill=True,
            tooltip=folium.Tooltip(f'{label}: {climb} net climb; potential base {base}. Radiation/parcel estimates; inspect the sounding before choosing a task.'),
            popup=f'{label}<br>Net climb estimate: {climb}<br>Cloud base: {base}').add_to(thermal_group)
        if forecast.location.key.startswith('ridge:'):
            ridge=WAVE_RIDGES[forecast.location.key.split(':',1)[1]]
            wave=evaluate_wave(profile,ridge); waves.append(wave)
            colour='#7653a1' if wave.complete and wave.score is not None and wave.score>=75 else '#82909a'
            score='Unavailable' if wave.score is None else f'{wave.score:.0f}/100'+('' if wave.complete else ' · provisional')
            folium.CircleMarker([lat,lon],radius=8,color=colour,fill=True,fill_opacity=.85,
                tooltip=folium.Tooltip(f'{label}: {score}. A heuristic wave score at an approximate ridge anchor; check stability and wind shear.'),
                popup=f'{label}<br>{score}<br>Approximate ridge anchor').add_to(wave_group)
            if wave.wind_from_deg is not None and wave.score is not None and wave.score>=50:
                spacing=min(15,max(3,wave.characteristic_scale_km or 8))
                for n in (1,2,3):
                    a,b=_destination(lat,lon,wave.wind_from_deg+180,n*spacing)
                    ends=[_destination(a,b,wave.wind_from_deg+side,4) for side in (-90,90)]
                    folium.PolyLine(ends,color=colour,weight=3,opacity=.55,dash_array='5,5',
                        tooltip=folium.Tooltip('Indicative downwind orientation and Scorer length scale. These bars do not locate resolved lift or rotor.')).add_to(wave_group)
    raster=thermal_overlay_data(thermal_samples) if visual_mode in (None,'extras') else None
    if raster is not None:
        image,bounds=raster
        # Folium's built-in reprojection returns floats that Branca normalizes
        # per channel. Cast explicitly to preserve the absolute colour scale.
        image=np.rint(mercator_transform(image,(bounds[0][0],bounds[1][0]),origin='upper')).astype(np.uint8)
        folium.raster_layers.ImageOverlay(image,bounds=bounds,origin='upper',mercator_project=False,
            opacity=1.,interactive=False,pixelated=False,zindex=200).add_to(thermal_group)
    # Always build the thermal layer so the map control can enable it while the sidebar starts off.
    thermal_group.add_to(map_view)
    add_colour_legend(map_view,'Net climb · kt: blue <1 · yellow 3 · red ≥5.5',
                      [f'{c} {v/5.5*100:.2f}%' for v,c in THERMAL_STOPS],0,5.5,layer=thermal_group)
    synchronizer=MacroElement()
    synchronizer.layer=thermal_group
    synchronizer._template=Template('''{% macro script(this, kwargs) %}
        {{this._parent.get_name()}}.on('overlayadd overlayremove', function(event) {
            if (event.layer !== {{this.layer.get_name()}} || !window.duoMapEvent) return;
            window.duoMapEvent('thermals',event.type === 'overlayadd' ? 'on' : 'off');
        });
    {% endmacro %}''')
    if map_identity=='weather' and visual_mode in (None,'extras'):synchronizer.add_to(map_view)
    cloud_colour=LinearColormap(['#e6f5f6','#77bdc8','#2b82a3','#344781'],vmin=1500,vmax=8000)
    if 'Cloud base' in layers:
        for lat,lon,value in interpolate_map_samples(cloud_samples):
            folium.Rectangle([[lat-.09,lon-.09],[lat+.09,lon+.09]],stroke=False,fill=True,fill_opacity=.38,
                fill_color=cloud_colour(value),tooltip=folium.Tooltip(f'Potential cloud base {value:.0f} ft AMSL. Compare with the mixing top; blue thermals may stop below this height.')).add_to(cloud_group)
        add_colour_legend(map_view,'Potential cloud base · ft AMSL',['#e6f5f6','#77bdc8','#2b82a3','#344781'],1500,8000)
    cloud_group.add_to(map_view); wave_group.add_to(map_view)
    if turnpoints and ('BGA turnpoints' in layers or map_identity=='wave'):
        add_waypoints(map_view,turnpoints,show=True)
    if visual_mode=='route':add_task_editor(map_view,task,when,forecasts,loading)
    if task and visual_mode!='route':
        active=folium.FeatureGroup(name=f'Active task · {task_distance(task):.0f} km')
        if len(task)>1:
            folium.PolyLine([(p.latitude,p.longitude) for p in task],color='#223c58',weight=4,
                tooltip=folium.Tooltip('Your selected task legs, in sequence. The task verdict uses sampled corridor weather; this line is not an airspace-cleared route.')).add_to(active)
        for a,b in zip(task,task[1:]):
            length,bearing=distance_bearing(a.point,b.point)
            folium.Marker([(a.latitude+b.latitude)/2,(a.longitude+b.longitude)/2],
                icon=folium.DivIcon(html=f'<span class="duo-leg-label">{length:.0f} km · {bearing:.0f}°T</span>',icon_size=(115,24)),
                tooltip=folium.Tooltip(f'{html.escape(a.code)} → {html.escape(b.code)}: {length:.1f} km, true track {bearing:.0f}°. Weather and airspace can require detours.')).add_to(active)
        for i,point in enumerate(task):
            role='Start' if i==0 else ('Finish' if i==len(task)-1 else 'Turnpoint')
            folium.Marker([point.latitude,point.longitude],
                icon=folium.DivIcon(html=f'<div style="background:#193d59;color:white;border:2px solid white;border-radius:50%;width:26px;height:26px;text-align:center;line-height:22px;font-weight:700">{i+1}</div>',icon_size=(28,28),icon_anchor=(14,14)),
                tooltip=folium.Tooltip(html.escape(f'{role} {i+1}: {point.code} · {point.name}. Reorder or remove this point in the task builder.'))).add_to(active)
        active.add_to(map_view)
    convergence_group=folium.FeatureGroup(name='Convergence ribbons',show='Convergence' in layers,control='Convergence' in layers)
    if convergence is not None:
        cells={(c.row,c.column):c for c in convergence.cells}
        for key,cell in cells.items():
            label=html.escape(f'{cell.classification} · {cell.surface_s1:.2e} s⁻¹ · ΔTd {cell.dewpoint_gradient_k_per_100km:.1f} K/100 km. A coarse-grid front candidate, not a resolved lift line.')
            folium.CircleMarker([cell.latitude,cell.longitude],radius=5,color='#d04489',fill=True,tooltip=folium.Tooltip(label)).add_to(convergence_group)
            for dr,dc in ((0,1),(1,-1),(1,0),(1,1)):
                neighbour=cells.get((key[0]+dr,key[1]+dc))
                if neighbour:
                    folium.PolyLine([(cell.latitude,cell.longitude),(neighbour.latitude,neighbour.longitude)],
                        color='#d04489',weight=8,opacity=.55,tooltip=folium.Tooltip(label)).add_to(convergence_group)
    convergence_group.add_to(map_view)
    metars=folium.FeatureGroup(name='METAR wind barbs / flight category',show='METAR' in layers,control='METAR' in layers)
    if aviation:
        for station,report in aviation.metars.items():
            if report.latitude is None or report.longitude is None: continue
            colour='#8a9298' if report.stale else CATEGORY_COLOURS[report.flight_category]
            tip=html.escape(f'{station} · {report.flight_category} · {report.observed_at:%H:%M UTC}'+(' · STALE' if report.stale else '')+'. Wind barb points into the wind; click for raw METAR/TAF and check report age.')
            taf=aviation.tafs.get(station)
            popup=html.escape(str(report.raw.get('rawOb','')))+('' if taf is None else '<hr>'+html.escape(str(taf.raw.get('rawTAF',''))))
            folium.Marker([report.latitude,report.longitude],
                icon=folium.DivIcon(html=wind_barb_svg(report.wind.direction_deg,report.wind.speed_knots,colour),icon_size=(44,44),icon_anchor=(22,22)),
                tooltip=folium.Tooltip(tip),popup=folium.Popup(popup,max_width=420)).add_to(metars)
    metars.add_to(map_view)
    if radar and visual_mode in (None,'extras'):
        folium.TileLayer(radar['url'],name='RainViewer observed radar',attr='Radar © RainViewer',overlay=True,
            opacity=.55,max_native_zoom=7,max_zoom=17,show='Radar' in layers).add_to(map_view)
    if visual_mode is not None and include_modes:
        from map_modes import add_map_modes
        add_map_modes(map_view,forecasts,when,anchor or when,loading,mode=visual_mode,radar=radar,task=task,rate_grid=rate_grid,radar_point=radar_point,hide_menu=map_identity!="weather")
    if show_airfields:
        from uk_airfields import add_airfields
        add_airfields(map_view,aviation,live=live_airfields,route=map_identity=="planner" or visual_mode=="route",selected=selected_airfield)
    from ogn_traffic import add_traffic
    add_traffic(map_view,show=map_identity=='traffic',recorded=not live_airfields)
    control=folium.LayerControl(collapsed=True,position='topright').add_to(map_view)
    finish_layer_control(map_view,control)
    return map_view,rank_wave_sites(waves)


def main():
    from planning_ui import (active_task, briefing_panel, catalog_panel, current_catalog,
                             process_map_event, task_builder, task_result, task_weather, weather_table)
    from task_planning import assess_task, classify_day, validate_task
    from map_modes import MODES
    from route_tools import optimizer_panel, task_sheet_panel
    from help_guide import render_help
    st.set_page_config(page_title='Duo · UK soaring weather',page_icon='⛅',layout='wide',initial_sidebar_state='expanded')
    st.markdown('<style>.block-container{padding-top:1.5rem}div[data-testid="stMetric"]{background:#fff;border:1px solid #dce5e8;border-radius:12px;padding:12px}'
                'h1{letter-spacing:-.04em}section[data-testid="stSidebar"]{border-right:1px solid #dce5e8}</style>',unsafe_allow_html=True)
    now=datetime.now(UTC)
    recorded_path=os.environ.get('DUO_DASHBOARD_SNAPSHOT')
    try: recorded=load_recorded(recorded_path,Path(recorded_path).stat().st_mtime_ns) if recorded_path else None
    except (OSError,ValueError,KeyError) as exc:
        st.error(f'Recorded forecast could not be read: {exc}'); return
    anchor=recorded.created_at if recorded else now
    st.title('Duo · UK soaring weather')
    st.caption('G-CKML  /  YOUR TASK · THERMALS · WAVE · CONVERGENCE')
    if recorded:
        st.info(f'Recorded forecast snapshot · retrieved {anchor.astimezone(LOCAL_TIMEZONE):%d %b %Y %H:%M %Z}. Task weather uses stored samples within 15 km; gaps remain unavailable.')
    catalog=current_catalog()
    default_thermal=st.query_params.get('thermals','off')=='on'
    if 'map_layers' not in st.session_state:
        st.session_state['map_layers']=[]+(['Thermals'] if default_thermal else [])
    if 'visual_mode' not in st.session_state: st.session_state['visual_mode']='thermal' if default_thermal else 'clean'
    def thermal_default_changed():
        enabled=st.session_state['thermal_default']
        st.session_state['visual_mode']='thermal' if enabled else 'clean'
        st.query_params['thermals']='on' if enabled else 'off'
        layers=[layer for layer in st.session_state['map_layers'] if layer!='Thermals']
        st.session_state['map_layers']=layers+(['Thermals'] if enabled else [])
    def extra_layers_changed():
        st.session_state['visual_mode']='extras' if st.session_state['map_layers'] else 'clean'
    with st.sidebar:
        st.subheader('Forecast controls')
        visual_mode=st.selectbox('Map mode',list(MODES),format_func=MODES.get,key='visual_mode',
            help='Choose one main map view. The floating Map modes menu offers the same choices; Clean Map hides weather overlays.')
        model_key=st.selectbox('Forecast model',list(MODEL_LABELS),format_func=MODEL_LABELS.get,key='forecast_model',
            help='Choose one model for the displayed weather and task calculations. UKMO covers about two days; ECMWF provides the longer-range fallback as an explicit selection.')
        if 'bot_forecast_day' in st.session_state:
            st.session_state['forecast_day']=st.session_state.pop('bot_forecast_day')
        default_day=1 if anchor.astimezone(LOCAL_TIMEZONE).hour>=13 else 0
        day_index=st.selectbox('Forecast day',list(range(5)),index=default_day,key='forecast_day',
            format_func=lambda d:f'Day {d} · {(anchor.astimezone(LOCAL_TIMEZONE).date()+timedelta(days=d)):%a %d %b}',
            help='Select the day to plan. Each date keeps its own saved task; a model that does not cover that day will show missing data.')
        hour=st.slider('Local time · Europe/London',9,19,13,format='%d:00',key='forecast_hour',
            help='Select the map and sounding hour, using BST in summer and GMT in winter. Task assessment still checks the whole 10:00–18:00 soaring window.')
        loading=st.radio('Wing loading',['dry','ballasted'],format_func=lambda v:'Dry · 34 kg/m²' if v=='dry' else 'Ballasted · 43 kg/m²',
            horizontal=True,key='wing_loading',help='Select the Duo Discus polar used for MacCready cruise and task ground speed. The thermal estimate retains the specified 1.6 kt circling sink.')
        with st.expander('More layers and click settings',expanded=False):
            st.toggle('Thermals on by default',value=default_thermal,key='thermal_default',on_change=thermal_default_changed,
                help='Choose whether the thermal overlay starts enabled. This preference is stored in the page URL, so bookmarking or reopening that URL preserves it.')
            layers=st.multiselect('Map layers',['Thermals','Cloud base','Convergence','Wave sites','BGA turnpoints','METAR','Radar'],key='map_layers',on_change=extra_layers_changed,
                help='Toggle weather and BGA layers. Blue-to-red thermals show fixed net-climb values; only your active task is drawn, and convergence requests a larger grid.')
            mode=st.radio('Map clicks',['Inspect weather','Add to task'],key='map_click_mode',
                help='Inspect changes the sounding only. Add mode appends a clicked BGA marker or custom coordinate using the builder’s Add as role.')
        show_airfields=st.checkbox('UK airfields and live reports',value=True,key='show_airfields',help='Show gliding clubs and UK aerodromes. Hover for current model weather; click for runway details and NOAA METAR/TAF reports. Turquoise marks gliding fields, purple reporting airfields and grey known closures.')
        if st.button('Refresh weather',width='stretch',help='Clear weather caches and request fresh data. Forecast retrieval is paced to respect provider quotas.'):
            st.cache_data.clear(); st.rerun()
        st.caption('Thermals are radiation/parcel estimates. Hover over controls and column headings for guidance.')
    day=anchor.astimezone(LOCAL_TIMEZONE).date()+timedelta(days=day_index)
    when=utc_datetime(datetime.combine(day,time(hour),LOCAL_TIMEZONE))
    bucket=int(now.timestamp()//900)
    with st.sidebar:
        with st.expander('Build / edit this day’s task',expanded=False): task_builder(catalog,day,location='sidebar')
        if st.session_state.get('task_message'): st.caption(st.session_state['task_message'])
        if st.session_state.get('task_error'): st.error(st.session_state.pop('task_error'))
    points=active_task(day)
    if recorded:
        forecasts=tuple(f for f in recorded.forecasts if f.model==FORECAST_MODELS[model_key].api_name); errors=recorded.errors
    else:
        with st.spinner('Loading regional and ridge forecasts…'):
            batch=load_sites(model_key,bucket)
            forecasts,errors=batch.forecasts,tuple(f.message for f in batch.failures)
    if not forecasts: st.warning('No regional forecast is available for this model. The task builder and exports remain available.')
    assessment=None; task_forecasts=()
    if len(points)>=2:
        try:
            validate_task(points)
            with st.spinner('Evaluating your task corridor and launch windows…'):
                task_forecasts,failures=task_weather(points,forecasts,model_key,bucket,recorded=bool(recorded))
                errors+=failures
                assessment=assess_task(points,task_forecasts,day,loading,preference=(FORECAST_MODELS[model_key].api_name,))
        except (ValueError,IngestionError) as exc: errors+=(f'Task: {exc}',)
    combined=tuple({(f.location.key,f.model):f for f in (*forecasts,*task_forecasts)}.values())
    map_forecasts=combined
    if visual_mode in ('wind','thermal','pfd','xc','stf') and not recorded:
        with st.spinner('Loading the UK map grid; cached for 15 minutes…'):
            map_batch=load_map_forecasts(model_key,bucket,"wind" if visual_mode=="wind" else "soaring")
            map_forecasts=tuple({(f.location.key,f.model):f for f in (*combined,*map_batch.forecasts)}.values())
            errors+=tuple(f.message for f in map_batch.failures)
        st.caption('Wind: regular UK grid including coastal water; terrain fallbacks are labelled. Soaring contours use land samples. Model interpolation does not increase forecast resolution.')
    aviation=radar=convergence=rate_grid=radar_point=None
    if show_airfields and not recorded:
        aviation=load_uk_aviation(int(now.timestamp()//60))
    elif visual_mode=='extras' and 'METAR' in layers and not recorded: aviation=load_aviation(int(now.timestamp()//60))
    if ((visual_mode=='extras' and 'Radar' in layers) or visual_mode=='radar') and not recorded:
        try: radar=load_radar(int(now.timestamp()//300))
        except (IngestionError,ValueError,KeyError) as exc: st.warning(f'Radar unavailable: {exc}')
    if visual_mode=='radar' and os.environ.get('DUO_RADAR_RATE_GRID_URL') and not recorded:
        try: rate_grid=load_calibrated_radar(os.environ['DUO_RADAR_RATE_GRID_URL'],int(now.timestamp()//300))
        except (IngestionError,ValueError) as exc: st.warning(f'Calibrated radar unavailable: {exc}')
    if visual_mode=='radar' and st.session_state.get('radar_coordinate'):
        with st.spinner('Loading Open-Meteo rain estimate at your map point…'):
            radar_point=radar_point_result(st.session_state['radar_coordinate'],when,model_key,
                                          int(now.timestamp()//300),recorded=bool(recorded))
    if visual_mode=='extras' and 'Convergence' in layers:
        if recorded: grid_forecasts=tuple(f for f in recorded.grid_forecasts if f.model==FORECAST_MODELS[model_key].api_name)
        else:
            with st.spinner('Loading the England and Wales convergence grid…'):
                batch=load_grid(model_key,bucket); grid_forecasts=batch.forecasts; errors+=tuple(f.message for f in batch.failures)
        convergence=evaluate_convergence(ForecastBundle(combined,grid_forecasts,.5,(),now),when,
                                         preference=(FORECAST_MODELS[model_key].api_name,))
        if convergence is None or convergence.valid_surface_cells<.8*convergence.total_cells:
            st.warning('Convergence coverage is incomplete at this hour.')
    profiles={}
    for forecast in combined:
        try:
            profile=Profile.from_forecast(forecast,when)
            if profile.values.get('temperature_2m') is not None: profiles[forecast.location.key]=profile
        except KeyError: continue
    local=profiles.get('region:shropshire'); thermal=evaluate_thermals(local) if local else None
    metrics=st.columns(4)
    metrics[0].metric('LONG MYND · NET CLIMB','—' if thermal is None or thermal.net_climb_knots is None else f'{thermal.net_climb_knots:.1f} kt',
        help='Estimated convective climb after the 1.6 kt Duo Discus circling sink. Task decisions use the conservative soil-moisture estimate, which may be lower.')
    metrics[1].metric('POTENTIAL CLOUD BASE','—' if thermal is None or thermal.cloud_base_ft_amsl is None else f'{thermal.cloud_base_ft_amsl:,.0f} ft',
        help='Hennig condensation-height estimate in feet AMSL. Compare with usable mixing top; cumulus may not form if mixing ends below this height.')
    metrics[2].metric('USABLE MIXING TOP','—' if thermal is None or thermal.usable_top_ft_amsl is None else f'{thermal.usable_top_ft_amsl:,.0f} ft',
        help='Lower of the condensation height and mixing top, in feet AMSL. Task assessment subtracts terrain to check usable depth AGL.')
    metrics[3].metric('VALID TIME',when.astimezone(LOCAL_TIMEZONE).strftime('%H:%M %Z'),when.astimezone(LOCAL_TIMEZONE).strftime('%a %d %b'),delta_color='off',
        help='Time of the selected forecast column. Radar and METAR have separate observed timestamps, and the task engine assesses the full soaring window.')
    origin=points[0].point if points and task_forecasts else MONITORING_ZONES['shropshire']
    wave_signal=any(evaluate_wave(p,WAVE_RIDGES[key.split(':',1)[1]]).complete and
                    (evaluate_wave(p,WAVE_RIDGES[key.split(':',1)[1]]).score or 0)>=75
                    for key,p in profiles.items() if key.startswith('ridge:') and distance_bearing(origin,p.forecast.location)[0]<150)
    classification=classify_day(combined,origin,day,loading,preference=(FORECAST_MODELS[model_key].api_name,),wave_available=wave_signal)
    st.metric('Cross-country day estimate',classification.label,
        help='Local weather capacity from conservative thermal climb, usable height and worst-direction wind with a 10% reserve. A 500 km label still requires a separately assessed 500 km corridor.')
    st.caption(f'{classification.scope} · '+classification.explanation)
    from day_rating import rate_day
    rating_key=(origin.key,loading,model_key,anchor.date().isoformat(),tuple((f.location.key,f.model,f.fetched_at.isoformat()) for f in combined))
    rating_cache=st.session_state.setdefault('_day_ratings',{})
    if rating_key not in rating_cache:
        rating_cache.clear()
        rating_cache[rating_key]=tuple(rate_day(combined,origin,anchor.astimezone(LOCAL_TIMEZONE).date()+timedelta(days=i),loading,
            preference=(FORECAST_MODELS[model_key].api_name,)) for i in range(5))
    day_ratings=rating_cache[rating_key];rating=day_ratings[day_index]
    st.subheader(f'{rating.symbols} · {rating.label}',help='Local PFD distance band capped by conservative thermal climb, usable height, sustained window and spread-out risk. Missing evidence stays unavailable; wave does not inflate the thermal rating.')
    st.caption('UK RASP-style local day rating · '+origin.name+' · Independent distance bands with weather quality caps. '+
        ' '.join(rating.reasons)+(' Wave potential is shown separately.' if wave_signal else ''))
    cards=st.columns(5)
    for i,r in enumerate(day_ratings):
        with cards[i]:
            st.metric(datetime.fromisoformat(r.day).strftime('%a %d %b'),r.symbols,
                help='Independent UK RASP-style rating at the task start / Long Mynd. Distances are local weather capacity; check your full route separately. '+' '.join(r.reasons))
            st.caption(r.label+(' · '+str(round(r.distance_km))+' km' if r.distance_km is not None else ''))
            st.caption('Window '+('unavailable' if r.window_hours is None else f'{r.window_hours:g} h'))
    if not profiles: st.warning('The selected model does not cover this hour. Choose another day, time or model.')
    waves=rank_wave_sites([evaluate_wave(p,WAVE_RIDGES[key.split(':',1)[1]]) for key,p in profiles.items() if key.startswith('ridge:')])
    map_tab,task_tab,help_tab,wave_tab,traffic_tab,bot_tab,quality_tab=st.tabs(['Weather map','Task planner','Help Guide','Wave watch','Live OGN Glider Radar','Help & Briefing Bot','Data quality'])
    with map_tab:
        map_view,waves=build_map(map_forecasts,when,layers=layers,aviation=aviation,radar=radar,convergence=convergence,
            task=points,turnpoints=catalog.turnpoints,visual_mode=visual_mode,anchor=anchor,loading=loading,rate_grid=rate_grid,radar_point=radar_point,show_airfields=show_airfields,live_airfields=not bool(recorded),selected_airfield=st.session_state.get('airfield_popup:last_map_event'))
        def on_map_change():
            process_map_event(st.session_state.get('soaring_map',{}),catalog,day,st.session_state.get('map_click_mode','Inspect weather'))
        interaction=st_folium(map_view,height=720,use_container_width=True,pixelated=False,key='soaring_map',on_change=on_map_change,
            wrap_longitude=False,returned_objects=['last_object_clicked','last_clicked'])
        # AppTest and initial component defaults do not invoke the browser callback.
        if interaction and interaction!=st.session_state.get('last_map_event'):
            old=active_task(day)
            old_radar=st.session_state.get('radar_coordinate')
            process_map_event(interaction,catalog,day,mode)
            if active_task(day)!=old or st.session_state.get('radar_coordinate')!=old_radar: st.rerun()
        st.caption('Open Map modes to choose wind, radar, soaring contours or the route planner. These independent model estimates are inspired by Windy/SkySight; they are not their proprietary forecasts.')
        if visual_mode=='route':
            task_result(points,assessment)
            optimizer_panel(catalog,day,loading,model_key,bucket,map_forecasts,recorded=bool(recorded))
            task_sheet_panel(points,assessment,day,loading,catalog,key='map_sheet')
        if radar:
            age=(now-radar['time']).total_seconds()/60
            st.caption(f'Observed radar {radar["time"].astimezone(LOCAL_TIMEZONE):%H:%M %Z} · {age:.0f} minutes old'+(' · STALE' if age>30 else '')+' · independent of the forecast slider.')
    with task_tab:
        from route_analysis import planner_panel
        planner_panel(catalog,day,when,loading,model_key,bucket,combined,points,assessment,aviation,map_builder=build_map,recorded=bool(recorded))
        task_result(points,assessment,key='planner')
        with st.expander('Build, reorder or clear task',expanded=not points): task_builder(catalog,day,location='planner')
        if visual_mode!='route': optimizer_panel(catalog,day,loading,model_key,bucket,map_forecasts,recorded=bool(recorded))
        task_sheet_panel(points,assessment,day,loading,catalog,key='planner_sheet')
    with help_tab:
        render_help(catalog,assessment,classification,waves,forecasts=combined,day=day,today=anchor.astimezone(LOCAL_TIMEZONE).date())
    with wave_tab:
        st.subheader('Wave Watch Map')
        from wave_map import add_wave_map
        wave_map,_=build_map(combined,when,layers=[],visual_mode='clean',include_modes=False,map_identity='wave',turnpoints=catalog.turnpoints,aviation=aviation,show_airfields=show_airfields,live_airfields=not bool(recorded),selected_airfield=st.session_state.get('airfield_popup:last_wave_event'))
        add_wave_map(wave_map,combined,when)
        def on_wave_click():process_map_event(st.session_state.get('wave_map',{}),catalog,day,'Inspect weather',event_key='last_wave_event',visual_override='wave')
        st_folium(wave_map,height=720,use_container_width=True,pixelated=False,key='wave_map',on_change=on_wave_click,wrap_longitude=False,returned_objects=['last_object_clicked','last_clicked'])
        st.subheader('Terrain-forced wave lift and sink')
        weather_table([{'Site':WAVE_RIDGES[w.site].point.name,'Score':w.score,'Evidence':'Complete' if w.complete else 'Provisional',
                        '850 hPa ascent kt':w.uplift_850_knots,'700 hPa ascent kt':w.uplift_700_knots,'N² s⁻²':w.brunt_vaisala_n2} for w in waves],
            {'Site':'Approximate named ridge anchor; it is not a surveyed wave entry point.','Score':'Heuristic score from flow alignment, shear, stability, Scorer decrease and model ascent.',
             'Evidence':'Only complete evidence can trigger a wave alert. Missing vertical motion remains provisional.',
             '850 hPa ascent kt':'Model vertical air motion, positive upward, converted using its returned units.',
             '700 hPa ascent kt':'Model vertical air motion at 700 hPa; a coarse model cannot resolve individual wave crests.',
             'N² s⁻²':'Brunt–Väisälä stability between 850 and 700 hPa. Positive values indicate static stability.'})
        st.caption('Wave signals inform the briefing but do not add assumed lift to a thermal task calculation.')
    with bot_tab:
        briefing_panel(catalog,day,loading,model_key,bucket,combined,assessment,classification,waves,recorded=bool(recorded),today=anchor.astimezone(LOCAL_TIMEZONE).date())
    with traffic_tab:
        st.subheader('Live OGN Glider Radar')
        st.caption('Current glider reports refresh independently of the forecast. Stale reports disappear after two minutes. Use the map’s Demo button for clearly labelled playback.')
        traffic_map,_=build_map(combined,when,layers=[],visual_mode='clean',include_modes=False,map_identity='traffic',
            turnpoints=catalog.turnpoints,aviation=aviation,show_airfields=True,live_airfields=not bool(recorded))
        st_folium(traffic_map,height=720,use_container_width=True,key='traffic_map',pixelated=False,wrap_longitude=False,
            returned_objects=['last_object_clicked','last_clicked'])
    with quality_tab:
        catalog_panel(catalog)
        st.divider(); st.subheader('Forecast quality')
        st.write(f'{len(profiles)}/{len(combined)} sites have surface temperature at the selected time.')
        weather_table([{'Location':f.location.name,'Retrieved UTC':f.fetched_at.isoformat(),'Available through':str(f.available_through),
                        'Unavailable fields':', '.join(f.unavailable_variables)} for f in combined],
            {'Location':'Requested regional, ridge or custom-task sample location.','Retrieved UTC':'API retrieval time, not the model initialization time.',
             'Available through':'Last non-null surface-temperature hour; other fields may have a shorter usable range.',
             'Unavailable fields':'Variables missing throughout this model response; calculations retain or explicitly estimate gaps as documented.'})
        if errors: st.warning('\n'.join(dict.fromkeys(errors)))
        if aviation:
            st.write('METAR unavailable:',', '.join(aviation.missing_metars) or 'None')
            st.write('TAF unavailable:',', '.join(aviation.missing_tafs) or 'None')
            if aviation.failures: st.warning('; '.join(f.message for f in aviation.failures))
        st.caption('Use official aviation forecasts, current airspace and the aircraft flight manual for flight decisions.')
    with st.sidebar:
        st.divider(); st.subheader('Sounding inspector')
        choices=[f for f in combined if not f.location.key.startswith('corridor:')]
        if not choices:
            st.info('No sounding data for the selected model.'); return
        options=['Map coordinate']+[f.location.key for f in choices]
        labels={f.location.key:f.location.name for f in choices}
        if st.session_state.get('inspector_selection') not in options: st.session_state.pop('inspector_selection',None)
        initial=options.index('region:shropshire') if 'region:shropshire' in options else 1
        selection=st.selectbox('Waypoint or clicked coordinate',options,index=None if 'inspector_selection' in st.session_state else initial,
            format_func=lambda key:labels.get(key,'Map coordinate'),key='inspector_selection',
            help='Inspect a selected waypoint or the last map click. The plot shows pressure-level temperature/dewpoint and the local-day thermal, cloud, wind and rain evolution.')
        coordinate=st.session_state.get('inspector_coordinate',(52.518,-2.880))
        if selection=='Map coordinate':
            st.caption(f'{coordinate[0]:.3f}° N, {abs(coordinate[1]):.3f}° '+('W' if coordinate[1]<0 else 'E'))
            if not 49<=coordinate[0]<=61.5 or not -9<=coordinate[1]<=2.5:
                st.info('Select a coordinate in the UK map area.'); return
            if recorded:
                chosen=min(choices,key=lambda f:distance_bearing(GeoPoint('click','Click',*coordinate),f.location)[0])
                st.caption('Recorded inspector: nearest stored waypoint, not a fresh coordinate forecast.')
            else:
                try: chosen=load_coordinate(*coordinate,model_key,bucket)
                except IngestionError as exc:
                    st.error(f'Coordinate forecast unavailable: {exc}'); return
        elif selection is not None: chosen=next(f for f in choices if f.location.key==selection)
        else:
            st.info('Choose a sounding location.'); return
        try: selected_profile=Profile.from_forecast(chosen,when)
        except KeyError:
            st.info('No sounding at this hour.'); return
        if not selected_profile.levels: st.info('Pressure-level sounding unavailable.')
        else:
            st.plotly_chart(sounding_figure(selected_profile),width='stretch',key='sounding')
            st.caption('Skew-T: temperature orange, dewpoint green. Hover for actual °C; dry/saturated adiabats are guides.')
        st.plotly_chart(meteogram_figure(chosen,day),width='stretch',key='meteogram')
        with st.expander('Profile assumptions'):
            for note in evaluate_thermals(selected_profile).notes: st.write(note)


if __name__=='__main__':
    main()
