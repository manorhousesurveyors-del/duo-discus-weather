"""Assistant discovery never blocks a page or caches a failed connection."""
from threading import Event
from time import perf_counter
from unittest import TestCase
from unittest.mock import patch,Mock
from app_cache import gemini_discovery
from briefing_jobs import connection
from gemini_rest import Connection
from weather_engine import ResourceStore

class BriefingJobsTests(TestCase):
    def test_connection_is_nonblocking_and_failure_retries_without_negative_cache(self):
        clock=[0.];release=Event();entered=Event();store=ResourceStore(clock=lambda:clock[0],workers=1)
        self.addCleanup(store.close);self.addCleanup(release.set);gemini_discovery.clear()
        def discover():entered.set();release.wait(2);return Connection((),'key','HTTP 429: temporary')
        with patch('briefing_jobs.jobs',return_value=store),patch('gemini_rest.GeminiREST') as api,patch('uk_map_grid.grid_progress'):
            api.return_value.connect.side_effect=discover
            start=perf_counter();value=connection('fingerprint','key','test-only-key')
            self.assertLess(perf_counter()-start,.1);self.assertFalse(value.models)
            self.assertTrue(entered.wait(1));release.set();store.queue.join()
            self.assertIn('429',connection('fingerprint','key','test-only-key').error)
            self.assertIsNone(store.previous(('models','fingerprint','key')))
            clock[0]=16;api.return_value.connect.side_effect=None
            api.return_value.connect.return_value=Connection(('gemini-test',),'key')
            connection('fingerprint','key','test-only-key');store.queue.join()
            self.assertEqual(connection('fingerprint','key','test-only-key').models,('gemini-test',))
            self.assertEqual(api.return_value.connect.call_count,2)
        gemini_discovery.clear()

    def test_keyless_navigation_never_starts_an_assistant_worker(self):
        with patch('briefing_jobs.jobs',side_effect=AssertionError('No worker needed')):
            self.assertEqual(connection('empty','auto','').models,())
