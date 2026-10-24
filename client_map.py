"""Compact browser payloads for canvas waypoints and deliberate route commits."""
from pathlib import Path
from json_payload import browser_json
import hashlib
import json
from branca.element import MacroElement, Template
from config import LOCAL_TIMEZONE


def script(map_view, filename, call, payload=None):
    element=MacroElement()
    element.payload=browser_json(payload)
    source=Path(__file__).with_name('assets').joinpath(filename).read_text()
    element._template=Template('{% macro script(this, kwargs) %}\n'+source+'\n'+call+'({{this._parent.get_name()}},{{this.payload|safe}});\n{% endmacro %}')
    element.add_to(map_view)


def install_canvas(map_view):
    script(map_view,'canvas_points.js','installDuoCanvasAPI')


def add_waypoints(map_view,points,*,show=True):
    records=[{'code':p.code,'name':p.name,'title':('BGA ' if p.official else 'Imported ')+p.code+' · '+p.name,'lat':p.latitude,'lon':p.longitude,'elevation_m':p.elevation_m,
              'official':p.official,'kind':'bga','colour':'#264e69'} for p in points]
    script(map_view,'canvas_waypoints.js','installDuoWaypoints',{'points':records,'show':show})


def add_task_editor(map_view,points,when,forecasts,loading):
    from map_modes import map_products
    from map_weather import contour_features,BANDS
    samples,_,_=map_products(forecasts,when,when,loading,'stf')
    contours={key:contour_features(samples,key) for key in ('stf','thermal','pfd')}
    records=[{'code':p.code,'name':p.name,'lat':p.latitude,'lon':p.longitude,'elevation_m':p.elevation_m} for p in points]
    version=hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest()[:16]
    from route_tools import is_complete
    script(map_view,'task_canvas.js','installDuoTask',{'date':when.astimezone(LOCAL_TIMEZONE).date().isoformat(),
        'complete':is_complete(when.astimezone(LOCAL_TIMEZONE).date(),points),
        'points':records,'version':version,'samples':samples,'contours':contours,'bands':BANDS})


def finish_layer_control(map_view,control):
    element=MacroElement();element.control=control;element._duo_layer_footer=True
    element._template=Template('''{% macro script(this, kwargs) %}
    {{this._parent.get_name()}}._duoLayers={{this.control.get_name()}};
    ({{this._parent.get_name()}}._duoPendingLayers||[]).forEach(function(row){
       {{this.control.get_name()}}.addOverlay(row[1],row[0]);
    });
    {{this._parent.get_name()}}._duoPendingLayers=[];
    {% endmacro %}''')
    element.add_to(map_view)


def reorder_layer_control(map_view):
    import folium
    controls=[];footers=[]
    for key,child in list(map_view._children.items()):
        if isinstance(child,folium.LayerControl):controls.append(child);map_view._children.pop(key)
        elif getattr(child,'_duo_layer_footer',False):footers.append(child);map_view._children.pop(key)
    for child in controls+footers:child.add_to(map_view)
