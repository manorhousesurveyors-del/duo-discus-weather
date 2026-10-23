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
from folium.plugins import HeatMap
from branca.colormap import LinearColormap
from branca.element import MacroElement, Template
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.interpolate import griddata
from scipy.spatial import cKDTree
import streamlit as st
from streamlit_folium import st_folium

from alerts import (ForecastBundle, GRID_VARIABLES, evaluate_convergence, load_bundle)
from config import (FORECAST_MODELS, LOCAL_TIMEZONE, MONITORING_ZONES, ROUTES,
                    WAVE_RIDGES, GeoPoint, convergence_grid, forecast_locations)
from data_ingestion import (AviationWeatherClient, Forecast, IngestionError, JsonHttpClient,
                            OpenMeteoClient, utc_datetime)
from soaring_physics import (CP, RD, Profile, dewpoint_c, distance_bearing,
                             evaluate_task_day, evaluate_thermals, evaluate_wave, rank_wave_sites)

MODEL_LABELS = {'ifs025':'ECMWF · medium range', 'ukmo':'Met Office · UK 2 km', 'icon_eu':'DWD ICON-EU · 7 km'}
CATEGORY_COLOURS = {'VFR':'#15835d','MVFR':'#2674c5','IFR':'#d84949','LIFR':'#a342ac','UNKNOWN':'#6c7780'}


@st.cache_data(ttl=900,show_spinner=False)
def load_sites(model_key: str, refresh_bucket: int):
    return OpenMeteoClient().fetch_batched(forecast_locations(),(FORECAST_MODELS[model_key],))


@st.cache_data(ttl=900,show_spinner=False)
def load_grid(model_key: str, refresh_bucket: int):
    grid = convergence_grid()
    return OpenMeteoClient().fetch_batched([p for row in grid for p in row],(FORECAST_MODELS[model_key],),variables=GRID_VARIABLES)


@st.cache_data(ttl=900,show_spinner=False)
def load_coordinate(latitude: float, longitude: float, model_key: str, refresh_bucket: int):
    point = GeoPoint('inspector',f'{latitude:.3f}, {longitude:.3f}',latitude,longitude)
    return OpenMeteoClient().fetch(point,FORECAST_MODELS[model_key])


@st.cache_data(ttl=60,show_spinner=False)
def load_aviation(refresh_bucket: int):
    return AviationWeatherClient().fetch()


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
            'url':body['host']+frame['path']+'/256/{z}/{x}/{y}/2/1_1.png'}


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


def add_colour_legend(map_view, caption, colours, minimum, maximum):
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
    {% endmacro %}''')
    legend.content = ('<div style="background:rgba(255,255,255,.94);padding:9px 12px;border-radius:5px;'
                      'width:240px;font:11px sans-serif;color:#183042">'+html.escape(caption)+
                      '<div style="height:9px;margin-top:7px;background:linear-gradient(to right,'+
                      ','.join(colours)+')"></div><div style="display:flex;justify-content:space-between;margin-top:3px">'+
                      f'<span>{minimum:g}</span><span>{maximum:g}</span></div></div>')
    legend.add_to(map_view)


def build_map(forecasts, when, *, layers, aviation=None, radar=None, convergence=None,
              centre=(53.3,-2.6), zoom=6):
    map_view = folium.Map(location=centre,zoom_start=zoom,tiles=None,control_scale=True,prefer_canvas=True)
    # Leaflet/heatmap assets use jsDelivr, already part of the environment preset.
    map_view.default_js = [('leaflet','https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js'),
                           ('jquery','https://cdn.jsdelivr.net/npm/jquery@3.7.1/dist/jquery.min.js')]
    map_view.default_css = [('leaflet_css','https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css')]
    folium.TileLayer('https://a.tile.opentopomap.org/{z}/{x}/{y}.png',name='Topography',
                     attr='Map data © OpenStreetMap contributors, SRTM | Map style © OpenTopoMap (CC-BY-SA)',max_zoom=17).add_to(map_view)
    folium.TileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',name='OpenStreetMap',
                     attr='© OpenStreetMap contributors',max_zoom=19,show=False).add_to(map_view)
    thermal_samples, cloud_samples, waves = [],[],[]
    thermal_group = folium.FeatureGroup(name='Thermal strength · kt',show='Thermals' in layers,control='Thermals' in layers)
    cloud_group = folium.FeatureGroup(name='Potential cloud base · ft AMSL',show='Cloud base' in layers,control='Cloud base' in layers)
    wave_group = folium.FeatureGroup(name='UK wave sites and indicative lee bars',show='Wave sites' in layers,control='Wave sites' in layers)
    for forecast in forecasts:
        try:
            profile = Profile.from_forecast(forecast,when)
        except KeyError:
            continue
        t = evaluate_thermals(profile)
        lat,lon = forecast.location.latitude,forecast.location.longitude
        if t.net_climb_knots is not None:
            thermal_samples.append([lat,lon,max(0,t.net_climb_knots)])
        if t.cloud_base_ft_amsl is not None:
            cloud_samples.append([lat,lon,t.cloud_base_ft_amsl])
        label = html.escape(forecast.location.name)
        climb = 'Unavailable' if t.net_climb_knots is None else f'{t.net_climb_knots:.1f} kt'
        base = 'Unavailable' if t.cloud_base_ft_amsl is None else f'{t.cloud_base_ft_amsl:.0f} ft AMSL'
        folium.CircleMarker([lat,lon],radius=4,color='#d7783f',fill=True,tooltip=f'{label}: {climb}',
                            popup=f'{label}<br>Net climb estimate: {climb}<br>Cloud base: {base}').add_to(thermal_group)
        if forecast.location.key.startswith('ridge:'):
            ridge = WAVE_RIDGES[forecast.location.key.split(':',1)[1]]
            wave = evaluate_wave(profile,ridge)
            waves.append(wave)
            colour = '#7653a1' if wave.complete and wave.score is not None and wave.score>=75 else '#82909a'
            score = 'Unavailable' if wave.score is None else f'{wave.score:.0f}/100'+('' if wave.complete else ' · provisional')
            folium.CircleMarker([lat,lon],radius=8,color=colour,fill=True,fill_opacity=.85,
                                tooltip=f'{label}: {score}',popup=f'{label}<br>{score}<br>Approximate ridge anchor').add_to(wave_group)
            if wave.wind_from_deg is not None and wave.score is not None and wave.score >= 50:
                spacing = min(15,max(3,(wave.characteristic_scale_km or 8)))
                for n in (1,2,3):
                    a,b = _destination(lat,lon,wave.wind_from_deg+180,n*spacing)
                    ends = [_destination(a,b,wave.wind_from_deg+side,4) for side in (-90,90)]
                    folium.PolyLine(ends,color=colour,weight=3,opacity=.55,dash_array='5,5',
                                    tooltip='Indicative lee orientation / Scorer scale; not predicted lift bands').add_to(wave_group)
    heat = interpolate_map_samples(thermal_samples) if 'Thermals' in layers else []
    if heat:
        heat_layer = HeatMap([[a,b,min(v,6)/6] for a,b,v in heat],radius=18,blur=20,min_opacity=.15,
                              gradient={.2:'#e1c35e',.55:'#df8e43',1:'#a33d2f'})
        heat_layer.default_js = [('leaflet-heat','https://cdn.jsdelivr.net/npm/leaflet.heat@0.2.0/dist/leaflet-heat.js')]
        heat_layer.add_to(thermal_group)
    cloud_colour = LinearColormap(['#e6f5f6','#77bdc8','#2b82a3','#344781'],vmin=1500,vmax=8000)
    if 'Cloud base' in layers:
        for lat,lon,value in interpolate_map_samples(cloud_samples):
            folium.Rectangle([[lat-.09,lon-.09],[lat+.09,lon+.09]],stroke=False,fill=True,fill_opacity=.38,
                              fill_color=cloud_colour(value),tooltip=f'Interpolated potential base {value:.0f} ft AMSL').add_to(cloud_group)
        add_colour_legend(map_view,'Potential cloud base · ft AMSL',
                          ['#e6f5f6','#77bdc8','#2b82a3','#344781'],1500,8000)
    for group in (thermal_group,cloud_group,wave_group):
        group.add_to(map_view)
    if 'Thermals' in layers:
        add_colour_legend(map_view,'Net thermal climb estimate · kt',
                          ['#e1c35e','#df8e43','#a33d2f'],0,6)
    routes = folium.FeatureGroup(name='Duo Discus task templates',show='Routes' in layers,control='Routes' in layers)
    for route in ROUTES.values():
        coordinates = [(p.latitude,p.longitude) for p in route.waypoints]
        if route.key == 'A':
            folium.Polygon(coordinates,color='#243f5a',weight=3,fill_opacity=.03,tooltip=route.name).add_to(routes)
        else:
            _,bearing = distance_bearing(*route.waypoints)
            polygon = [_destination(*coordinates[0],bearing-90,2),_destination(*coordinates[1],bearing-90,2),
                       _destination(*coordinates[1],bearing+90,2),_destination(*coordinates[0],bearing+90,2)]
            folium.Polygon(polygon,color='#2c8c83',fill_opacity=.12,tooltip='Snowdonia–South Wales: schematic corridor').add_to(routes)
            folium.PolyLine(coordinates,color='#2c8c83',weight=3,tooltip=route.name).add_to(routes)
    routes.add_to(map_view)
    convergence_group = folium.FeatureGroup(name='Convergence ribbons',show='Convergence' in layers,control='Convergence' in layers)
    if convergence is not None:
        cells = {(c.row,c.column):c for c in convergence.cells}
        for key,cell in cells.items():
            label = html.escape(f'{cell.classification} · {cell.surface_s1:.2e} s⁻¹ · ΔTd {cell.dewpoint_gradient_k_per_100km:.1f} K/100 km')
            folium.CircleMarker([cell.latitude,cell.longitude],radius=5,color='#d04489',fill=True,tooltip=label).add_to(convergence_group)
            for dr,dc in ((0,1),(1,-1),(1,0),(1,1)):
                neighbour = cells.get((key[0]+dr,key[1]+dc))
                if neighbour:
                    folium.PolyLine([(cell.latitude,cell.longitude),(neighbour.latitude,neighbour.longitude)],
                                    color='#d04489',weight=8,opacity=.55,tooltip=label).add_to(convergence_group)
    convergence_group.add_to(map_view)
    metars = folium.FeatureGroup(name='METAR wind barbs / flight category',show='METAR' in layers,control='METAR' in layers)
    if aviation:
        for station,report in aviation.metars.items():
            if report.latitude is None or report.longitude is None:
                continue
            colour = '#8a9298' if report.stale else CATEGORY_COLOURS[report.flight_category]
            text = html.escape(f'{station} · {report.flight_category} · {report.observed_at:%H:%M UTC}'+(' · STALE' if report.stale else ''))
            raw = html.escape(str(report.raw.get('rawOb','')))
            taf = aviation.tafs.get(station)
            taf_text = '' if taf is None else '<hr>'+html.escape(str(taf.raw.get('rawTAF','')))
            folium.Marker([report.latitude,report.longitude],icon=folium.DivIcon(html=wind_barb_svg(report.wind.direction_deg,report.wind.speed_knots,colour),icon_size=(44,44),icon_anchor=(22,22)),
                          tooltip=text,popup=folium.Popup(raw+taf_text,max_width=420)).add_to(metars)
    metars.add_to(map_view)
    if radar:
        folium.TileLayer(radar['url'],name='RainViewer observed radar',attr='Radar © RainViewer',
                         overlay=True,opacity=.55,max_native_zoom=7,max_zoom=17,show='Radar' in layers).add_to(map_view)
    folium.LayerControl(collapsed=False).add_to(map_view)
    return map_view,rank_wave_sites(waves)


def main():
    st.set_page_config(page_title='Duo · UK soaring weather',page_icon='⛅',layout='wide',initial_sidebar_state='expanded')
    st.markdown('<style>.block-container{padding-top:1.5rem}div[data-testid="stMetric"]{background:#fff;border:1px solid #dce5e8;border-radius:12px;padding:12px}'
                'h1{letter-spacing:-.04em}section[data-testid="stSidebar"]{border-right:1px solid #dce5e8}</style>',unsafe_allow_html=True)
    now = datetime.now(UTC)
    recorded_path = os.environ.get('DUO_DASHBOARD_SNAPSHOT')
    recorded = load_bundle(Path(recorded_path)) if recorded_path else None
    anchor = recorded.created_at if recorded else now
    st.title('Duo · UK soaring weather')
    st.caption('G-CKML  /  THERMALS · WAVE · CONVERGENCE')
    if recorded:
        st.info(f'Recorded forecast snapshot · retrieved {anchor.astimezone(LOCAL_TIMEZONE):%d %b %Y %H:%M %Z}. Inspector uses recorded sites.')
    with st.sidebar:
        st.subheader('Forecast controls')
        model_key = st.selectbox('Forecast model',list(MODEL_LABELS),format_func=MODEL_LABELS.get)
        default_day = 1 if anchor.astimezone(LOCAL_TIMEZONE).hour >= 13 else 0
        day_index = st.selectbox('Forecast day',list(range(6)),index=default_day,
                                format_func=lambda d:f'Day {d} · {(anchor.astimezone(LOCAL_TIMEZONE).date()+timedelta(days=d)):%a %d %b}')
        hour = st.slider('Local time · Europe/London',9,19,13,format='%d:00')
        loading = st.radio('Wing loading',['dry','ballasted'],format_func=lambda s:'Dry · 34 kg/m²' if s=='dry' else 'Ballasted · 43 kg/m²',horizontal=True)
        layers = st.multiselect('Map layers',['Thermals','Cloud base','Convergence','Wave sites','METAR','Routes','Radar'],
                                default=['Thermals','Wave sites','METAR','Routes','Radar'])
        if st.button('Refresh weather',width='stretch'):
            st.cache_data.clear()
            st.rerun()
        st.caption('Thermals are radiation/parcel estimates. Missing data stays unavailable.')
    day = anchor.astimezone(LOCAL_TIMEZONE).date()+timedelta(days=day_index)
    when = utc_datetime(datetime.combine(day,time(hour),LOCAL_TIMEZONE))
    bucket = int(now.timestamp()//900)
    if recorded:
        forecasts = tuple(f for f in recorded.forecasts if f.model==FORECAST_MODELS[model_key].api_name)
        errors = recorded.errors
    else:
        with st.spinner('Loading regional and ridge forecasts…'):
            batch = load_sites(model_key,bucket)
            forecasts,errors = batch.forecasts,tuple(f.message for f in batch.failures)
    if not forecasts:
        st.error('No forecast data is available. '+('; '.join(errors[:3]) if errors else 'Choose another model or refresh.'))
        return
    aviation = radar = convergence = None
    if 'METAR' in layers and not recorded:
        aviation = load_aviation(int(now.timestamp()//60))
    if 'Radar' in layers and not recorded:
        try:
            radar = load_radar(int(now.timestamp()//300))
        except (IngestionError,ValueError,KeyError) as exc:
            st.warning(f'Radar unavailable: {exc}')
    if 'Convergence' in layers:
        if recorded:
            grid_forecasts = tuple(f for f in recorded.grid_forecasts if f.model==FORECAST_MODELS[model_key].api_name)
        else:
            with st.spinner('Loading the England and Wales convergence grid…'):
                batch = load_grid(model_key,bucket)
                grid_forecasts = batch.forecasts
                errors += tuple(f.message for f in batch.failures)
        convergence = evaluate_convergence(ForecastBundle(forecasts,grid_forecasts,.5,(),now),when,
                                           preference=(FORECAST_MODELS[model_key].api_name,))
        if convergence is None or convergence.valid_surface_cells < .8*convergence.total_cells:
            st.warning('Convergence coverage is incomplete at this hour.')
    profile_by_key = {}
    for forecast in forecasts:
        try:
            p = Profile.from_forecast(forecast,when)
            if p.values.get('temperature_2m') is not None:
                profile_by_key[forecast.location.key] = p
        except KeyError:
            continue
    local_profile = profile_by_key.get('region:shropshire')
    local_thermal = evaluate_thermals(local_profile) if local_profile else None
    metrics = st.columns(4)
    metrics[0].metric('LONG MYND · NET CLIMB','—' if local_thermal is None or local_thermal.net_climb_knots is None else f'{local_thermal.net_climb_knots:.1f} kt')
    metrics[1].metric('POTENTIAL CLOUD BASE','—' if local_thermal is None or local_thermal.cloud_base_ft_amsl is None else f'{local_thermal.cloud_base_ft_amsl:,.0f} ft')
    metrics[2].metric('USABLE MIXING TOP','—' if local_thermal is None or local_thermal.usable_top_ft_amsl is None else f'{local_thermal.usable_top_ft_amsl:,.0f} ft')
    metrics[3].metric('VALID TIME',when.astimezone(LOCAL_TIMEZONE).strftime('%H:%M %Z'),when.astimezone(LOCAL_TIMEZONE).strftime('%a %d %b'),delta_color='off')
    if not profile_by_key:
        st.warning('The selected model does not cover this hour. Choose another forecast day, time or model.')
    map_tab,task_tab,wave_tab,quality_tab = st.tabs(['Weather map','Task planner','Wave watch','Data quality'])
    with map_tab:
        centre = st.session_state.get('map_centre',(53.3,-2.6))
        map_view,waves = build_map(forecasts,when,layers=layers,aviation=aviation,radar=radar,convergence=convergence,
                                   centre=centre,zoom=st.session_state.get('map_zoom',6))
        interaction = st_folium(map_view,height=650,use_container_width=True,key='soaring_map',
                                returned_objects=['last_clicked','center','zoom'])
        if interaction.get('last_clicked'):
            clicked = interaction['last_clicked']
            coordinate = (round(clicked['lat'],3),round(clicked['lng'],3))
            if st.session_state.get('inspector_coordinate') != coordinate:
                st.session_state['inspector_coordinate'] = coordinate
                st.session_state['inspector_selection'] = 'Map coordinate'
        if interaction.get('center'):
            st.session_state['map_centre'] = (interaction['center']['lat'],interaction['center']['lng'])
        if interaction.get('zoom'):
            st.session_state['map_zoom'] = interaction['zoom']
        st.caption('Select layers in the sidebar; click the map to inspect a coordinate. Heatmaps interpolate samples within 75 km; wave bars show indicative lee orientation, not predicted lift. Cloud heights are AMSL.')
        st.caption('METAR colours: VFR green · MVFR blue · IFR red · LIFR purple · unknown/stale grey. These categories do not replace UK VMC criteria.')
        if radar:
            age = (now-radar['time']).total_seconds()/60
            st.caption(f'Observed radar {radar["time"].astimezone(LOCAL_TIMEZONE):%H:%M %Z} · {age:.0f} minutes old'+(' · STALE' if age>30 else '')+' · radar time is independent of the forecast slider.')
    with task_tab:
        for route in ROUTES.values():
            result = evaluate_task_day(route,forecasts,day,loading,preference=(FORECAST_MODELS[model_key].api_name,))
            st.subheader(route.name)
            st.caption(f'{result.route_distance_km:.0f} km actual route · estimated continuous soaring between 10:00 and 18:00 local')
            st.dataframe(pd.DataFrame([{'Task':f'{km} km','Verdict':status.replace('_',' ')} for km,status in result.viability.items()]),hide_index=True,width='stretch')
            with st.expander('Hourly leg speeds and assumptions'):
                st.dataframe(pd.DataFrame([{'Time':r.valid_at.astimezone(LOCAL_TIMEZONE).strftime('%H:%M'),'Leg':r.leg,
                                           'Ground km/h':r.groundspeed_kmh,'Cruise km/h':r.cruise_airspeed_kmh,'Net climb kt':r.net_climb_knots,
                                           'Notes':'; '.join(r.notes)} for r in result.hourly_legs]),hide_index=True)
                st.caption(' '.join(result.notes))
    with wave_tab:
        st.subheader('National wave potential')
        st.dataframe(pd.DataFrame([{'Site':WAVE_RIDGES[w.site].point.name,'Score':w.score,
                                   'Evidence':'Complete' if w.complete else 'Provisional','850 hPa ascent kt':w.uplift_850_knots,
                                   '700 hPa ascent kt':w.uplift_700_knots,'N² s⁻²':w.brunt_vaisala_n2} for w in waves]),hide_index=True,width='stretch')
        st.caption('A score is a heuristic potential index. Ridge anchors are approximate; low-resolution profiles cannot resolve rotor or individual wave crests.')
    with quality_tab:
        st.write(f'{len(profile_by_key)}/{len(forecasts)} sites have surface temperature at the selected time.')
        st.dataframe(pd.DataFrame([{'Location':f.location.name,'Retrieved UTC':f.fetched_at.isoformat(),
                                   'Available through':str(f.available_through),'Unavailable fields':', '.join(f.unavailable_variables)} for f in forecasts]),hide_index=True)
        if errors:
            st.warning('\n'.join(dict.fromkeys(errors)))
        if aviation:
            st.write('METAR unavailable:',', '.join(aviation.missing_metars) or 'None')
            st.write('TAF unavailable:',', '.join(aviation.missing_tafs) or 'None')
            if aviation.failures:
                st.warning('; '.join(f.message for f in aviation.failures))
        st.caption('Use official aviation forecasts, current airspace and the aircraft flight manual for flight decisions.')
    with st.sidebar:
        st.divider()
        st.subheader('Sounding inspector')
        options = ['Map coordinate']+[f.location.key for f in forecasts]
        labels = {f.location.key:f.location.name for f in forecasts}
        initial = options.index('region:shropshire') if 'region:shropshire' in options else 1
        selection = st.selectbox('Waypoint or clicked coordinate',options,
                                  index=None if 'inspector_selection' in st.session_state else initial,
                                  format_func=lambda key:labels.get(key,'Map coordinate'),key='inspector_selection')
        coordinate = st.session_state.get('inspector_coordinate',(52.518,-2.880))
        if selection == 'Map coordinate':
            st.caption(f'{coordinate[0]:.3f}° N, {abs(coordinate[1]):.3f}° '+('W' if coordinate[1]<0 else 'E'))
            if not 49 <= coordinate[0] <= 61 or not -9 <= coordinate[1] <= 3:
                st.info('Select a coordinate in the UK map area.')
                return
            if recorded:
                chosen = min(forecasts,key=lambda f:(f.location.latitude-coordinate[0])**2+(f.location.longitude-coordinate[1])**2)
                st.caption('Recorded mode: nearest stored waypoint, not a fresh coordinate forecast.')
            else:
                try:
                    chosen = load_coordinate(*coordinate,model_key,bucket)
                except IngestionError as exc:
                    st.error(f'Coordinate forecast unavailable: {exc}')
                    return
        else:
            chosen = next(f for f in forecasts if f.location.key==selection)
        try:
            selected_profile = Profile.from_forecast(chosen,when)
        except KeyError:
            st.info('No sounding at this hour.')
            return
        if not selected_profile.levels:
            st.info('Pressure-level sounding unavailable.')
        else:
            st.plotly_chart(sounding_figure(selected_profile),width='stretch',key='sounding')
            st.caption('Skew-T: temperature orange, dewpoint green. Dry/saturated adiabats are guides; hover for actual °C.')
        st.plotly_chart(meteogram_figure(chosen,day),width='stretch',key='meteogram')
        diagnostic = evaluate_thermals(selected_profile)
        with st.expander('Profile assumptions'):
            for note in diagnostic.notes:
                st.write(note)


if __name__ == '__main__':
    main()
