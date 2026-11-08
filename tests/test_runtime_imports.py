"""Regression coverage for a Streamlit process retaining pre-deployment modules."""
import ast
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import runtime_imports


class RuntimeImportTests(unittest.TestCase):
    def test_activity_hook_is_public_and_never_starts_a_worker(self):
        import ogn_traffic
        self.assertTrue(callable(ogn_traffic.set_worker_active))
        self.assertEqual(ogn_traffic.set_worker_active.__annotations__, {'active': bool, 'return': None})
        if hasattr(ogn_traffic, '__all__'):
            self.assertIn('set_worker_active', ogn_traffic.__all__)
        with patch.object(ogn_traffic, '_worker', None), patch.object(ogn_traffic, 'ensure_worker') as start:
            ogn_traffic.set_worker_active(True)
            ogn_traffic.set_worker_active(False)
            start.assert_not_called()
        worker = SimpleNamespace(active_until=0)
        with patch.object(ogn_traffic, '_worker', worker), patch.object(ogn_traffic.time, 'time', return_value=100):
            ogn_traffic.set_worker_active(True)
            self.assertEqual(worker.active_until, 0)
            ogn_traffic.set_worker_active(False)
            self.assertEqual(worker.active_until, 0)

    def test_optional_import_failure_has_no_startup_exception(self):
        with patch.object(runtime_imports.importlib, 'import_module', side_effect=ImportError('optional module unavailable')):
            with self.assertLogs('runtime_imports', level='WARNING'):
                self.assertIsNone(runtime_imports.optional_module('ogn_traffic'))

    def test_old_function_signature_is_detected(self):
        legacy = SimpleNamespace(render_help=lambda catalog: None)
        self.assertFalse(runtime_imports._supports(legacy, {'render_help': ('model_key',)}))
        current = SimpleNamespace(render_help=lambda catalog, *, model_key='ifs025': None)
        self.assertTrue(runtime_imports._supports(current, {'render_help': ('model_key',)}))

    def test_real_cached_modules_are_repaired_once_without_reloading_ogn(self):
        # An isolated interpreter avoids replacing functions used by other tests.
        script = '''
import importlib
from unittest.mock import patch
from runtime_imports import LAZY_APIS, refresh_cached_modules
import ogn_traffic
worker = object()
ogn_traffic._worker = worker
modules = {name: importlib.import_module(name) for name in LAZY_APIS}
for name, api in LAZY_APIS.items():
    module = modules[name]
    delattr(module, next(iter(api)))
assert set(refresh_cached_modules()) == set(LAZY_APIS)
assert ogn_traffic._worker is worker
with patch('runtime_imports.importlib.reload', side_effect=AssertionError('Unnecessary reload')):
    assert refresh_cached_modules() == ()
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_app_refreshes_the_compatibility_registry_itself_after_deployment(self):
        script = """
import runtime_imports,map_component,map_modes
runtime_imports.API_REVISION=0
runtime_imports.LAZY_APIS={}
del map_component.cached_document
map_modes.add_map_modes=lambda view:None
import app
assert runtime_imports.API_REVISION==15
assert callable(map_component.cached_document)
assert runtime_imports._supports(map_modes,{'add_map_modes':('wind_product',)})
"""
        result=subprocess.run([sys.executable,'-c',script],cwd=Path(__file__).resolve().parents[1],
                              text=True,capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_deployment_reload_keeps_anchor_live_met_and_demo_forecasts_pickleable(self):
        # A real reload replaces dataclass identities. Consumers holding old
        # GeoPoint/Forecast classes used to fail inside st.cache_data afterward.
        script = """
import pickle
from datetime import datetime,UTC
import config,data_ingestion,weather_anchors,weather_demo,met_norway,sync_weather,app_cache,bga_turnpoints
import runtime_imports
from tests.fixtures import forecast
from tests.provider_fixture import met_compact
before=type(weather_anchors.ANCHORS[0])
saved_turnpoint=bga_turnpoints.Turnpoint('MYN','Long Mynd',52.518,-2.88,430.)
saved_turnpoint_class=type(saved_turnpoint)
refreshed=runtime_imports.refresh_cached_modules(deployment_changed=True)
assert 'weather_anchors' in refreshed and 'weather_demo' in refreshed
point=weather_anchors.ANCHORS[0]
assert type(point) is config.GeoPoint and type(point) is not before
assert type(pickle.loads(pickle.dumps(point))) is config.GeoPoint
assert type(saved_turnpoint) is saved_turnpoint_class is bga_turnpoints.Turnpoint
assert bga_turnpoints.GeoPoint is config.GeoPoint
assert type(saved_turnpoint.point) is config.GeoPoint
assert type(pickle.loads(pickle.dumps(saved_turnpoint))) is saved_turnpoint_class
assert type(pickle.loads(pickle.dumps(saved_turnpoint.point))) is config.GeoPoint
anchor=datetime(2026,11,8,tzinfo=UTC)
live=forecast(point,start=anchor,hours=120,model='icon_eu')
met=met_norway.parse_met_forecast(data_ingestion.JsonResponse(met_compact(point.latitude,point.longitude),anchor),point,anchor)
demo=weather_demo.demo_forecasts((point,),anchor,'icon_eu')[0]
for source in (live,met,demo):
    assert type(source) is data_ingestion.Forecast
    assert type(source.location) is config.GeoPoint
    restored=pickle.loads(pickle.dumps(source))
    assert restored.source==source.source and restored.field_sources==source.field_sources
    # Cross the actual Streamlit data-cache serialization boundary twice.
    run=app_cache.forecast_run_id((source,))
    first=sync_weather._samples(run,(point,saved_turnpoint.point),'regional',(source,))
    second=sync_weather._samples(run,(point,saved_turnpoint.point),'regional',(source,))
    assert first[0].source==second[0].source==source.source
    assert type(second[0]) is data_ingestion.Forecast
"""
        result=subprocess.run([sys.executable,'-c',script],cwd=Path(__file__).resolve().parents[1],
                              text=True,capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_every_local_symbol_import_in_audited_modules_exists(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ('app.py', 'briefing_bot.py', 'client_map.py', 'ogn_traffic.py'):
            for node in ast.walk(ast.parse((root / filename).read_text())):
                if isinstance(node, ast.ImportFrom) and node.module and not node.level and (root / (node.module + '.py')).is_file():
                    module = importlib.import_module(node.module)
                    for symbol in node.names:
                        self.assertTrue(hasattr(module, symbol.name), (filename, node.module, symbol.name))

    def test_startup_and_navigation_with_legacy_or_unavailable_ogn(self):
        from streamlit.testing.v1 import AppTest
        from alerts import ForecastBundle, save_bundle
        from tests.fixtures import NOW
        root = Path(__file__).resolve().parents[1]
        legacy = ModuleType('ogn_traffic')
        legacy.add_traffic = Mock(side_effect=AssertionError('Legacy OGN must not start a worker'))
        with tempfile.TemporaryDirectory() as folder:
            snapshot = Path(folder) / 'snapshot.json'
            save_bundle(ForecastBundle((), (), .5, (), NOW), snapshot)
            with patch.dict(os.environ, {'DUO_DASHBOARD_SNAPSHOT': str(snapshot), 'DUO_TASK_STORE': folder + '/tasks'}), \
                 patch.dict(sys.modules, {'ogn_traffic': legacy}), patch('map_component.render_map', return_value=None):
                at = AppTest.from_file(str(root / 'app.py'), default_timeout=30).run()
                self.assertFalse(at.exception)
                for page in ('Task planner', 'Help Guide', 'Live OGN Glider Radar'):
                    at.radio(key='active_page').set_value(page).run()
                    self.assertFalse(at.exception)
                self.assertTrue(any('traffic is temporarily unavailable' in row.value for row in at.info))
                legacy.add_traffic.assert_not_called()
                with patch('runtime_imports.optional_module', return_value=None):
                    at.radio(key='active_page').set_value('Weather map').run()
                    self.assertFalse(at.exception)
