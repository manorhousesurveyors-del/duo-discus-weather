"""Self-contained Leaflet component: explicit commits, reliable hidden-tab sizing.

The local component keeps Streamlit's supported two-way component protocol.
It uses a 720 px iframe and a 680 px map instead of an HTML-only iframe, so
Finish/Analyse can save a route while cursor movement remains browser-only.
"""
from pathlib import Path
import re
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


def render_map(view, *, key, on_change=None, **_):
    def changed():
        st.session_state[key] = st.session_state.get(key + '_component', {})
        if on_change:
            on_change()
    return _component(**map_document(view), height=720, scrolling=False, key=key + '_component',
                      default=None, on_change=changed)
