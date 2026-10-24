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
            self.assertEqual(worker.active_until, 135)
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
