"""Map construction and chart products, independent of page routing and HTTP."""
from __future__ import annotations
import html,math
import folium
from folium.utilities import mercator_transform
from branca.colormap import LinearColormap
from branca.element import MacroElement,Template
import numpy as np
from scipy.interpolate import griddata
from scipy.spatial import cKDTree
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from config import LOCAL_TIMEZONE,WAVE_RIDGES
from soaring_physics import CP, RD, Profile, dewpoint_c, evaluate_thermals, evaluate_wave, rank_wave_sites
from runtime_imports import optional_module
from data_ingestion import Forecast
CATEGORY_COLOURS={'VFR':'#15835d','MVFR':'#2674c5','IFR':'#d84949','LIFR':'#a342ac','UNKNOWN':'#6c7780'}


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


def meteogram_figure(forecast: Forecast, day, loading='dry') -> go.Figure:
    times = [t for t in forecast.times if t.astimezone(LOCAL_TIMEZONE).date()==day]
    thermal = [evaluate_thermals(Profile.from_forecast(forecast,t),loading) for t in times]
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


@st.cache_data(ttl=1800,max_entries=96,show_spinner=False)
def cached_sounding(run_id,when,*,_forecast):
    return sounding_figure(Profile.from_forecast(_forecast,when)).to_plotly_json()


@st.cache_data(ttl=1800,max_entries=32,show_spinner=False)
def cached_meteogram(run_id,day,loading='dry',*,_forecast):
    return meteogram_figure(_forecast,day,loading).to_plotly_json()


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
              centre=(53.3,-2.6), zoom=6, task=(), turnpoints=(), visual_mode=None, anchor=None, loading="dry", rate_grid=None, radar_point=None, map_identity="weather", show_airfields=False, live_airfields=True, include_modes=True, selected_airfield=None, wind_product=None,task_product=None):
    if visual_mode not in (None,'extras'):
        layers=['BGA turnpoints'] if visual_mode=='route' else []
    map_view=folium.Map(location=centre,zoom_start=zoom,tiles=None,control_scale=True,prefer_canvas=True)
    from map_bridge import add_click_bridge
    from client_map import install_canvas,add_waypoints,add_task_editor,add_shared_task,finish_layer_control
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
    for forecast in (forecasts if visual_mode is None or set(layers)&{'Thermals','Cloud base','Wave sites'} else ()):
        try: profile=Profile.from_forecast(forecast,when)
        except KeyError: continue
        t=evaluate_thermals(profile,loading); lat,lon=forecast.location.latitude,forecast.location.longitude
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
    if turnpoints and ('BGA turnpoints' in layers or map_identity in ('weather','wave','traffic')):
        add_waypoints(map_view,turnpoints,show=map_identity not in ('wave','traffic') and visual_mode not in ('wind','radar') and 'BGA turnpoints' in layers)
    if map_identity in ('weather','planner'):
        add_shared_task(map_view,task,when,editable=visual_mode=='route')
    if visual_mode=='route':add_task_editor(map_view,task,when,forecasts,loading,prepared=task_product)
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
        add_map_modes(map_view,forecasts,when,anchor or when,loading,mode=visual_mode,radar=radar,task=task,rate_grid=rate_grid,radar_point=radar_point,hide_menu=map_identity!="weather",wind_product=wind_product)
    if show_airfields:
        from uk_airfields import add_airfields
        add_airfields(map_view,aviation,live=live_airfields,route=map_identity=="planner" or visual_mode=="route",selected=selected_airfield,show=map_identity!='traffic',forecasts=forecasts,when=when)
    if map_identity in ('weather','traffic'):
        from client_map import script
        script(map_view,'reference_layers.js','installDuoReferenceControls',{'traffic':map_identity=='traffic'})
    ogn_traffic=optional_module('ogn_traffic')
    # A legacy OGN module has no activity lease. Leave its worker untouched and
    # omit its overlay until the current module is available.
    if callable(getattr(ogn_traffic,'set_worker_active',None)):
        getattr(ogn_traffic,'add_traffic',lambda *_args,**_kwargs:None)(
            map_view,show=map_identity=='traffic',recorded=not live_airfields)
    control=folium.LayerControl(collapsed=True,position='topright').add_to(map_view)
    finish_layer_control(map_view,control)
    return map_view,rank_wave_sites(waves)


@st.cache_data(ttl=1800,show_spinner=False,max_entries=12)
def wave_document(run_id,when,source_version,recorded,*,_forecasts,_turnpoints,_prepared=None):
    """Cache a session-independent map, including serialization and compression."""
    from wave_map import add_wave_map
    from map_component import compressed_document
    view,_=build_map(_forecasts,when,layers=[],visual_mode='clean',include_modes=False,
        map_identity='wave',turnpoints=_turnpoints,show_airfields=False,live_airfields=not recorded)
    add_wave_map(view,_forecasts,when,prepared=_prepared)
    return compressed_document(view)


