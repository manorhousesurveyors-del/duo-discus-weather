"""Only deliberate map clicks cross the Streamlit component boundary."""
from pathlib import Path
from branca.element import MacroElement, Template

RETURNED_OBJECTS = ['last_object_clicked','last_clicked']


def add_click_bridge(map_view, identity='weather'):
    element=MacroElement(); element.identity=identity
    source=Path(__file__).with_name('assets').joinpath('map_bridge.js').read_text()
    element._template=Template('{% macro script(this, kwargs) %}\n'+source+
        '\ninstallDuoBridge({{this._parent.get_name()}}, "{{this.identity}}");\n{% endmacro %}')
    element.add_to(map_view)
