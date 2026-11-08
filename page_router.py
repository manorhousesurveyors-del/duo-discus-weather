"""One active renderer per run; page imports and weather work are lazy."""
from dataclasses import dataclass
from importlib import import_module

MODEL_LABELS = {'ukmo':'Met Office · UKV / seamless', 'ifs025':'ECMWF · medium range', 'icon_eu':'DWD ICON-EU · 7 km'}

PAGES={
    'Weather map':('weather',True), 'Task planner':('task',True),
    'Help Guide':('help',False), 'Wave watch':('wave',True),
    'Live OGN Glider Radar':('traffic',False),
    'Help & Briefing Bot':('briefing',False), 'Data quality':('quality',False),
}

@dataclass(frozen=True)
class PageContext:
    catalog: object
    recorded: object
    model_key: object
    points: object
    day: object
    day_index: object
    when: object
    loading: object
    bucket: object
    now: object
    anchor: object
    visual_mode: object
    layers: object
    mode: object
    show_airfields: object
    forecasts: object
    errors: object


def dispatch(name,context):
    if name not in PAGES:raise ValueError('Unknown page')
    module,_=PAGES[name]
    return import_module('pages.'+module).render(context)
