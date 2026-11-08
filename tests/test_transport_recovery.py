"""A stale/cached transport exception must never bypass the alternate provider."""
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
from types import SimpleNamespace
from urllib.error import HTTPError
import unittest
from unittest.mock import Mock, patch

from config import forecast_locations
from data_ingestion import IngestionError, JsonResponse
from forecast_quality import is_demo, is_surface_estimate
from map_weather import national_grid, wind_payload
from met_norway import MetHttpClient
from tests.provider_fixture import FailoverProvider, NOW
from weather_engine import DeferredWeather
import sync_weather as weather

CYCLE=int(NOW.timestamp()//1800)


class ObsoleteIngestionError(RuntimeError):
    """Simulate a class object retained before importlib.reload replaced it."""


class ObsoleteDeferredWeather(ObsoleteIngestionError):
    pass


ERRORS=(lambda:DeferredWeather('old local gate'),
        lambda:HTTPError('https://api.open-meteo.com/v1/forecast',429,'Too Many Requests',{},None),
        lambda:ObsoleteDeferredWeather('obsolete cached transport class'),
        lambda:RuntimeError('unexpected stale provider state'))


class TransportRecoveryTests(unittest.TestCase):
    def setUp(self):
        # Bind the current application modules before injecting transports.
        import app
        import uk_map_grid
        self.app=app;self.grid=uk_map_grid
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'weather.json'
        env=patch.dict('os.environ',{'DUO_DASHBOARD_SNAPSHOT':'','DUO_WEATHER_CACHE_PATH':str(self.path),
            'DUO_TASK_STORE':str(Path(self.tmp.name)/'tasks'),'DUO_OGN_ENABLED':'0','GEMINI_API_KEY':''})
        env.start();self.addCleanup(env.stop)
        weather.clear();self.addCleanup(weather.clear)
        uk_map_grid.load_uk_map_grid.clear();self.addCleanup(uk_map_grid.load_uk_map_grid.clear)

    def alternate(self, offline=False):
        provider=FailoverProvider('offline' if offline else '429')
        client=MetHttpClient(opener=provider,clock=lambda:NOW,
                             sleep=lambda _:self.fail('Fallback must not sleep'))
        return provider,client

    def assert_numeric(self,batch):
        self.assertEqual(len(batch.forecasts),len(forecast_locations()))
        for forecast in batch.forecasts:
            for day in range(5):
                values=forecast.at(NOW.replace(hour=13)+timedelta(days=day))
                for field in ('temperature_2m','dew_point_2m','surface_pressure','shortwave_radiation','boundary_layer_height'):
                    self.assertIsNotNone(values[field],(forecast.location.key,day,field))

    def test_versioned_resources_cannot_reuse_an_old_gated_client_or_share_providers(self):
        weather._http_resource.clear();self.addCleanup(weather._http_resource.clear)
        obsolete=Mock(name='cached old ForecastHttpClient')
        obsolete.gate=Mock()
        with patch.object(weather,'SyncHttpClient',return_value=obsolete):
            prior=weather._http_resource('open-meteo',weather.HTTP_TRANSPORT_REVISION-1)
        self.assertIs(prior,obsolete)
        primary=weather.transport();alternate=weather.met_transport()
        self.assertIsNot(primary,prior)
        self.assertIsInstance(primary,weather.SyncHttpClient)
        self.assertFalse(hasattr(primary,'gate'))
        self.assertIsInstance(alternate,MetHttpClient)
        self.assertIsNot(primary,alternate)
        self.assertIs(weather.transport(),primary)
        self.assertIs(weather._transport(),primary)
        self.assertIs(weather.met_transport(),alternate)

    def test_hot_reload_rejects_old_api_and_migrates_stale_display_without_losing_tasks(self):
        import runtime_imports
        from session_state import SCHEMA_VERSION,migrate
        stale=SimpleNamespace(fetch_forecasts=lambda *args,**kwargs:None,
                              display_bundle=lambda *args:None)
        self.assertFalse(runtime_imports._supports(stale,runtime_imports.LAZY_APIS['sync_weather']))
        self.assertTrue(runtime_imports._supports(weather,runtime_imports.LAZY_APIS['sync_weather']))
        state={'session_schema_version':SCHEMA_VERSION-1,
               'display_weather_bundle:icon_eu':('old','failed gated client'),
               'forecast_bundle:icon_eu':('old Forecast instances',),
               'uk_map_grid_bundle':'old future','_map_documents':{'stale':True},
               '_map_products':{'stale':True},'active_task_points':['MYN','BIE'],
               'forecast_model':'ukmo','briefing_messages':['keep this conversation']}
        migrate(state)
        self.assertEqual(state['session_schema_version'],SCHEMA_VERSION)
        for key in ('display_weather_bundle:icon_eu','forecast_bundle:icon_eu','uk_map_grid_bundle','_map_documents','_map_products'):
            self.assertNotIn(key,state)
        self.assertEqual(state['active_task_points'],['MYN','BIE'])
        self.assertEqual(state['forecast_model'],'ukmo')
        self.assertEqual(state['briefing_messages'],['keep this conversation'])

    def test_all_primary_exception_classes_reach_independent_met_transport(self):
        from weather_ui import load_sites
        self.assertNotIsInstance(ObsoleteDeferredWeather(),IngestionError)
        for factory in ERRORS:
            with self.subTest(error=type(factory()).__name__):
                weather.clear()
                primary=Mock(get_json=Mock(side_effect=factory()))
                provider,alternate=self.alternate()
                with patch.object(weather,'transport',return_value=primary), \
                     patch.object(weather,'met_transport',return_value=alternate,create=True):
                    batch=load_sites('icon_eu',CYCLE)
                    self.assert_numeric(batch)
                    self.assertTrue(all(is_surface_estimate(f) for f in batch.forecasts))
                    grid=weather.fetch_forecasts(national_grid(),'icon_eu',CYCLE,'wind')
                    field=wind_payload(grid.forecasts,NOW)
                    for day in range(5):
                        self.assertTrue(all(all(station['values'][13+24*day]) for station in field['stations']))
                primary.get_json.assert_called_once()
                self.assertEqual(len(provider.calls),9)
                self.assertTrue(all(host=='api.met.no' for host,_ in provider.calls))

    def test_empty_and_incomplete_primary_responses_enter_met_failover(self):
        from weather_ui import load_sites
        sample={'latitude':52.5,'longitude':-2.8,'elevation':100.,'utc_offset_seconds':0,
                'hourly_units':{'time':'unixtime','temperature_2m':'°C'},
                'hourly':{'time':[int((NOW.replace(hour=0)+timedelta(hours=i)).timestamp()) for i in range(24)],
                          'temperature_2m':[15.]*24}}
        for response in (None,JsonResponse([],NOW),JsonResponse([sample]*9,NOW)):
            with self.subTest(response=type(response).__name__):
                weather.clear()
                provider,alternate=self.alternate()
                primary=Mock(get_json=Mock(return_value=response))
                with patch.object(weather,'transport',return_value=primary), \
                     patch.object(weather,'met_transport',return_value=alternate,create=True):
                    self.assert_numeric(load_sites('icon_eu',CYCLE))
                self.assertEqual(len(provider.calls),9)

    def test_failed_disk_read_and_write_cannot_crash_available_or_demo_weather(self):
        from weather_ui import load_sites
        for offline in (False,True):
            with self.subTest(offline=offline):
                weather.clear()
                primary=Mock(get_json=Mock(side_effect=ObsoleteDeferredWeather('stale transport')))
                _,alternate=self.alternate(offline)
                with patch.object(weather,'transport',return_value=primary), \
                     patch.object(weather,'met_transport',return_value=alternate,create=True), \
                     patch('weather_disk_cache.save_live',side_effect=RuntimeError('broken cache writer')), \
                     patch('weather_disk_cache.load_live',side_effect=RuntimeError('broken cache reader')):
                    batch=load_sites('icon_eu',CYCLE)
                self.assert_numeric(batch)
                if offline:self.assertTrue(all(is_demo(f) for f in batch.forecasts))
                else:self.assertTrue(all(is_surface_estimate(f) for f in batch.forecasts))

    def test_main_first_pass_survives_gate_429_and_obsolete_exceptions_without_polling(self):
        from streamlit.testing.v1 import AppTest
        for factory,offline in ((ERRORS[0],False),(ERRORS[1],False),(ERRORS[2],True),(ERRORS[3],True)):
            with self.subTest(error=type(factory()).__name__,offline=offline):
                weather.clear();self.grid.load_uk_map_grid.clear();self.path.unlink(missing_ok=True)
                primary=Mock(get_json=Mock(side_effect=factory()))
                provider,alternate=self.alternate(offline)
                with patch.object(weather,'transport',return_value=primary), \
                     patch.object(weather,'met_transport',return_value=alternate,create=True), \
                     patch.object(self.app,'datetime',wraps=datetime) as clock:
                    clock.now.return_value=NOW
                    at=AppTest.from_string('import app\napp.main()',default_timeout=45)
                    at.session_state['visual_mode']='wind'
                    at.run()
                    self.assertFalse(at.exception)
                    self.assertFalse(at.error)
                    self.assertEqual(at.session_state['_full_render_count'],1)
                    self.assertTrue(at.get('component_instance'))
                    metrics={metric.label:metric.value for metric in at.metric}
                    for label in ('LONG MYND · NET CLIMB','POTENTIAL CLOUD BASE','USABLE MIXING TOP'):
                        self.assertRegex(metrics[label],r'\d')
                        self.assertNotIn('—',metrics[label])
                    for day in range(5):
                        self.assertRegex(metrics[(NOW+timedelta(days=day)).strftime('%a %d %b')],r'\d')
                    text=' '.join(str(item.value) for group in (at.caption,at.warning,at.info,at.metric) for item in group)
                    for forbidden in ('Weather provider cooling down','background recovery','Weather updating','Awaiting live weather'):
                        self.assertNotIn(forbidden,text)
                    self.assertEqual(primary.get_json.call_count,1)
                    self.assertEqual(len(provider.calls),9)
                    # No timed fragment is registered by the weather page.
                    from pathlib import Path
                    for module in (self.app,self.grid):
                        self.assertNotIn("run_every=",Path(module.__file__).read_text())


if __name__=='__main__':unittest.main()
