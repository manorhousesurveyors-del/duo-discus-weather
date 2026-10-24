"""Floating Leaflet controls and source-labelled visual layers."""
from pathlib import Path
import json
import folium
import streamlit as st
from branca.element import MacroElement, Template
from map_weather import BANDS, contour_features, soaring_samples, wind_payload

MODES = {'clean':'Off / Clean Map', 'wind':'Windy-style Wind Stream', 'radar':'Weather Radar',
         'thermal':'SkySight-style Thermal Lift / Sink', 'pfd':'Potential Flight Distance',
         'xc':'XC Speed', 'stf':'Speed to Fly · kt', 'route':'Route Forecast Planner','extras':'Other weather layers'}


def map_products(forecasts,when,anchor,loading,mode):
    """Keep expensive contour geometry and wind frames across click reruns."""
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    key=(mode,None if mode=='wind' else when.isoformat(),anchor.date().isoformat(),loading,
         tuple((f.location.key,f.model,f.fetched_at.isoformat(),len(f.times)) for f in forecasts))
    cache=st.session_state.setdefault('_map_products',{}) if get_script_run_ctx(suppress_warning=True) else {}
    if key in cache:return cache[key]
    samples=soaring_samples(forecasts,when,loading) if mode in ('thermal','pfd','xc','stf') else []
    contours=contour_features(samples,mode) if mode in BANDS else {'type':'FeatureCollection','features':[]}
    wind=wind_payload(forecasts,anchor) if mode=='wind' else None
    if len(cache)>=8:cache.pop(next(iter(cache)))
    cache[key]=(samples,contours,wind)
    return cache[key]


def add_map_modes(map_view,forecasts,when,anchor,loading,*,mode='clean',radar=None,task=(),rate_grid=None,radar_point=None,hide_menu=False):
    if mode not in MODES: mode='clean'
    samples,contours,wind=map_products(forecasts,when,anchor,loading,mode)
    payload={'mode':mode,'modes':MODES,'when':when.isoformat(),'wind':wind,'samples':samples,
             'contours':contours,'bands':BANDS.get(mode),'radar':radar,'points':len(task),
             'date':when.date().isoformat(),'loading':loading,'rate_grid':rate_grid,'radar_point':radar_point,'hide_menu':hide_menu}
    if radar: payload['radar']={**radar,'time':radar['time'].isoformat()}
    element=MacroElement()
    # No network script dependency: all controls and animation are bundled.
    javascript=Path(__file__).with_name('assets').joinpath('map_modes.js').read_text()
    element.payload=json.dumps(payload,separators=(',',':'),allow_nan=False).replace('<','\\u003c')
    element._template=Template('{% macro script(this, kwargs) %}\n'+javascript+
        '\ninstallDuoModes({{this._parent.get_name()}}, {{this.payload|safe}});\n{% endmacro %}')
    element.add_to(map_view)
