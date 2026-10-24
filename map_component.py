"""Self-contained Leaflet component with lightweight fragment state updates.

The local component keeps Streamlit's supported two-way component protocol.
It uses a 720 px iframe and a 680 px map instead of an HTML-only iframe, so
Every point edit saves shared route state while cursor movement stays local.
"""
from pathlib import Path
import re
import hashlib
import json,zlib,base64
import streamlit as st
import streamlit.components.v1 as components

_component = components.declare_component('duo_map', path=str(Path(__file__).with_name('map_component')))


def map_document(view):
    view.get_root().render()
    root = view.get_root()
    parts = [root.html.render(), root.script.render(), root.header.render()]
    # Stable element names prevent fresh Folium UUIDs from resetting drafts on
    # unrelated Python reruns. Payload changes still update the map.
    identifiers = list(dict.fromkeys(re.findall(r'\b\w+_[0-9a-f]{32}\b', '\n'.join(parts))))
    mapping = {name: f'duo_element_{i}' for i, name in enumerate(identifiers)}
    mapping[view.get_name()] = 'map_div'
    pattern = re.compile('|'.join(re.escape(name) for name in mapping))
    body, script, header = [pattern.sub(lambda m: mapping[m[0]], p) for p in parts]
    header = re.sub(r'<script\b[^>]*>.*?</script>|<link\b[^>]*>', '', header, flags=re.S | re.I)
    return {'html': body, 'script': script + '\nwindow.map_div=map_div;', 'header': header}


@st.fragment
def render_document(document,*,key,on_change=None):
    from planning_ui import task_payload
    def changed():
        st.session_state[key]=st.session_state.get(key+'_component',{})
        if on_change:on_change()
        event=st.session_state[key] or {}
        kind=(event.get('last_object_clicked') or {}).get('duo',{}).get('kind')
        if kind not in ('task_sync','briefing_point'):st.session_state['_map_full_refresh']=True
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    ctx=get_script_run_ctx(suppress_warning=True)
    fragment_only=bool(ctx and ctx.fragment_ids_this_run)
    # A fragment save sends only route state. The iframe already owns its map.
    content={'document_id':document['document_id']} if fragment_only else document
    result=_component(**content,canonical_task=task_payload(),height=720,scrolling=False,
        key=key+'_component',default=None,on_change=changed)
    task_refresh=st.session_state.pop('_task_full_refresh',False)
    map_refresh=st.session_state.pop('_map_full_refresh',False)
    if task_refresh or map_refresh:st.rerun(scope='app')
    return result


def compressed_document(view):
    document=map_document(view)
    raw=json.dumps(document,separators=(',',':')).encode()
    return {'document_id':hashlib.sha256(raw).hexdigest(),
            'compressed_document':base64.b64encode(zlib.compress(raw,6)).decode()}


def document_version(forecasts,when,*options):
    from app_cache import forecast_run_id,file_version
    # Session-specific task content is included by callers. File versions also
    # invalidate an existing browser session after a deployment or data import.
    root=Path(__file__).parent
    versions=file_version([*root.glob('*.py'),*root.glob('assets/*.js'),
                           *root.glob('data/*.json'),root/'map_component/index.html'])
    return (forecast_run_id(forecasts),when.isoformat(),versions,options)


def cached_document(key, version, build):
    """Reuse finished per-session documents without rebuilding Folium objects."""
    cache=st.session_state.setdefault('_map_documents',{})
    lookup=(key,version)
    if lookup not in cache:
        if len(cache)>=12:cache.pop(next(iter(cache)))
        cache[lookup]=compressed_document(build())
    return cache[lookup]


def render_map(view,*,key,on_change=None,document_key=None,**_):
    document=(cached_document(key,document_key,view) if callable(view)
              else compressed_document(view))
    return render_document(document,key=key,on_change=on_change)
