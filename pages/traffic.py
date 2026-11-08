"""Traffic page; independent renderer."""
import streamlit as st
import map_component
from map_view import build_map
from runtime_imports import optional_module


def render(ctx):
    catalog = ctx.catalog
    recorded = ctx.recorded
    when = ctx.when
    ogn_traffic=optional_module('ogn_traffic')
    traffic_ready=all(callable(getattr(ogn_traffic,name,None))
        for name in ('set_worker_active','traffic_heartbeat','add_traffic'))
    if not traffic_ready:
        st.info('Live traffic is temporarily unavailable while the app updates. Weather and task planning remain available.')
    st.subheader('Live OGN Glider Radar')
    if traffic_ready and not recorded:
        getattr(ogn_traffic,'traffic_heartbeat',lambda *_args,**_kwargs:None)()
    st.caption('Current glider reports refresh independently of the forecast. Recent reports remain for 20 minutes, with their position age shown. BGA turnpoints and airfields start off; use the switches at the top of the map. Yellow marks identified gliders; blue marks powered and unidentified targets.')
    from app_cache import file_version
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    def traffic_document():
        return build_map((),when,layers=[],visual_mode='clean',include_modes=False,map_identity='traffic',
            turnpoints=catalog.turnpoints,aviation=None,show_airfields=True,live_airfields=not bool(recorded))[0]
    map_component.render_map(traffic_document,document_key=('traffic',catalog.content_sha256,bool(recorded),file_version([root/'map_view.py',*root.glob('assets/*.js')])),
        height=720,use_container_width=True,key='traffic_map',pixelated=False,wrap_longitude=False,
        returned_objects=['last_object_clicked','last_clicked'])
    return
