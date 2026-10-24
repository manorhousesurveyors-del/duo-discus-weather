"""Grid fetch concurrency, cycle caching and real warm-page timing regressions.

Transport is deterministic; page rendering, compression and physics are real.
These limits measure a warm server, not provider or browser network latency.
"""
from concurrent.futures import Future
from datetime import timedelta
from pathlib import Path
from threading import Event, Lock
from time import perf_counter
from unittest.mock import Mock, patch
from urllib.error import HTTPError
import tempfile
import unittest
import numpy as np

from config import FORECAST_MODELS, HOURLY_VARIABLES, OPEN_METEO_URL, forecast_locations
from data_ingestion import ForecastBatch, IngestionError, JsonResponse
from tests.fixtures import NOW, forecast
import uk_map_grid as grid


class UKGridTests(unittest.TestCase):
    def test_transport_has_one_short_attempt_and_never_sleeps_on_timeout_or_429(self):
        for error in (TimeoutError(), HTTPError(OPEN_METEO_URL,429,'full',{'Retry-After':'60'},None)):
            opener=Mock(side_effect=error);sleep=Mock(side_effect=AssertionError('Retry/pacing sleep'))
            client=grid.GridHttpClient(opener=opener,sleep=sleep)
            with self.assertRaises(IngestionError):client.get_json(OPEN_METEO_URL,{},ttl_seconds=0)
            self.assertEqual(opener.call_count,1)
            self.assertEqual(opener.call_args.kwargs['timeout'],3.5)
            sleep.assert_not_called()

    def test_one_batch_contains_all_five_days_and_keeps_missing_hours(self):
        point=forecast_locations()[0];source=forecast(point,hours=144)
        body={'latitude':point.latitude,'longitude':point.longitude,'elevation':100,
              'utc_offset_seconds':0,'hourly_units':{'time':'unixtime',**source.units},
              'hourly':{'time':[int(t.timestamp()) for t in source.times],
                        **{k:list(v) for k,v in source.hourly.items()}}}
        body['hourly']['temperature_2m'][-1]=None
        http=Mock();http.get_json.return_value=JsonResponse([body],NOW)
        result=grid.fetch_chunk((point,),FORECAST_MODELS['ukmo'],http)
        params=http.get_json.call_args.args[1]
        self.assertEqual(params['forecast_hours'],120)
        self.assertEqual(set(params['hourly'].split(',')),set(HOURLY_VARIABLES))
        self.assertIsNone(result[0].at(source.times[-1])['temperature_2m'])
        self.assertEqual(result[0].model,FORECAST_MODELS['ukmo'].api_name)

    def test_twelve_concurrent_chunks_schedule_once_and_keep_model_isolation(self):
        entered=Event();release=Event();lock=Lock();count=0
        points=grid.national_grid()
        def fetch(chunk,model,http):
            nonlocal count
            with lock:
                count+=1
                if count==12:entered.set()
            if not release.wait(5):raise AssertionError('Test did not release transport')
            raise IngestionError('Deterministic missing coverage')
        with patch.object(grid,'fetch_chunk',side_effect=fetch) as request:
            bundle=grid.UKGridBundle(int(NOW.timestamp()//1800))
            try:
                start=perf_counter();pending=bundle.request('ifs025')
                self.assertLess(perf_counter()-start,.3)
                self.assertTrue(entered.wait(2),'All twelve requests must start concurrently')
                for _ in range(120):self.assertIs(bundle.request('ifs025'),pending)
                self.assertEqual(request.call_count,12)
                self.assertFalse(pending.done())
            finally:release.set()
            result=pending.result(timeout=10)
            self.assertEqual(len(result.batch.failures),len(points)*3)
            self.assertEqual(result.batch.forecasts,())
            self.assertIs(bundle.request('ifs025'),pending)  # no retries on failure
            other=bundle.request('ukmo');self.assertIsNot(other,pending)
            self.assertEqual(other.result(timeout=10).batch.failures,result.batch.failures)
            self.assertEqual(request.call_count,36)  # Same raw data across model switches.

    def test_task_optimizer_can_only_read_an_already_finished_grid(self):
        from route_tools import screening_forecasts
        bundle=grid.UKGridBundle(100)
        with patch.object(grid.st,'session_state',{'uk_map_grid_bundle':bundle}), \
             patch.object(grid,'GridHttpClient',side_effect=AssertionError('Task must not fetch national grid')):
            self.assertEqual(screening_forecasts((),'ifs025',100),())
            pending=Future();bundle._models['ifs025']=pending
            self.assertEqual(screening_forecasts((),'ifs025',100),())
            self.assertFalse(pending.done())
            source=forecast();job=Future();job.set_result((source,))
            pending.set_result(grid.assemble((job,),((source.location,),),FORECAST_MODELS['ifs025'],NOW))
            self.assertEqual(screening_forecasts((),'ifs025',100),(source,))
            self.assertEqual(screening_forecasts((),'ukmo',100),())
            self.assertEqual(screening_forecasts((),'ifs025',101),())

    def test_session_resource_only_changes_on_cycle_and_hour_views_are_constant_time(self):
        source=forecast(hours=120);job=Future();job.set_result((source,))
        snapshot=grid.assemble((job,),((source.location,),),FORECAST_MODELS['ifs025'],NOW)
        with patch.object(grid.st,'session_state',{}):
            grid.load_uk_map_grid.clear()
            one=grid.session_grid(100)
            for day in range(5):
                for hour in range(24):
                    self.assertIs(grid.session_grid(100),one)
                    start=perf_counter();view=snapshot.hour(NOW+timedelta(days=day,hours=hour))
                    self.assertLess(perf_counter()-start,.005)
                    self.assertTrue(np.shares_memory(view,snapshot.values))
                    self.assertFalse(view.flags.writeable)
            self.assertIsNone(snapshot.hour(NOW+timedelta(minutes=1)))
            self.assertIsNot(grid.session_grid(101),one)
            self.assertIs(grid.st.session_state['uk_map_grid_bundle'],grid.load_uk_map_grid(101))
            grid.load_uk_map_grid.clear()
        with self.assertRaises(KeyError):source.at(NOW+timedelta(minutes=1))
        with self.assertRaises(KeyError):source.at(NOW-timedelta(hours=1))


class PageNavigationTests(unittest.TestCase):
    def test_real_warm_pages_and_day_hour_changes_do_not_restart_grid(self):
        from streamlit.testing.v1 import AppTest
        from datetime import UTC,datetime
        import app
        start=datetime.now(UTC).replace(hour=0,minute=0,second=0,microsecond=0)
        forecasts=tuple(forecast(p,start=start,hours=144) for p in forecast_locations())
        regional=Future();regional.set_result(ForecastBatch(forecasts,()))
        data=Future();data.set_result(forecasts)
        snapshot=grid.assemble((data,),(tuple(f.location for f in forecasts),),FORECAST_MODELS['ifs025'],start)
        # National transport remains pending while we navigate away, then finishes.
        pending=Future();bundle=Mock(cycle=int(datetime.now(UTC).timestamp()//1800),coverage_revision=2);bundle.request.return_value=pending
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict('os.environ',{'DUO_DASHBOARD_SNAPSHOT':'','DUO_TASK_STORE':tmp,'GEMINI_API_KEY':''}), \
             patch.object(grid,'regional_future',return_value=regional), \
             patch.object(grid,'load_uk_map_grid',return_value=bundle) as loader, \
             patch('ogn_traffic.ensure_worker'),patch('ogn_traffic.traffic_heartbeat'), \
             patch.object(grid.st,'spinner',wraps=grid.st.spinner) as spinner:
            app.load_sites.clear()
            at=AppTest.from_file(str(Path(app.__file__)),default_timeout=90).run()
            at.selectbox(key='visual_mode').select('wind').run()
            self.assertFalse(at.exception)
            self.assertFalse(pending.done())
            self.assertTrue(any('background' in x.value for x in at.caption))
            held=at.session_state['uk_map_grid_bundle']
            # A pending fetch does not obstruct help. No map constructor is mocked.
            count=bundle.request.call_count
            clock=perf_counter();at.radio(key='active_page').set_value('Help Guide').run()
            self.assertLess(perf_counter()-clock,.3)
            self.assertEqual(bundle.request.call_count,count)
            self.assertFalse(at.get('component_instance'))
            pending.set_result(snapshot)
            pages=at.radio(key='active_page').options
            timings={}
            for iteration in range(2):
                for page in pages:
                    count=bundle.request.call_count
                    clock=perf_counter();at.radio(key='active_page').set_value(page).run()
                    elapsed=perf_counter()-clock
                    self.assertFalse(at.exception,(page,at.exception))
                    headings=[x.value for x in at.subheader]
                    self.assertEqual('Task planning map' in headings,page=='Task planner')
                    self.assertEqual('Wave Watch Map' in headings,page=='Wave watch')
                    self.assertFalse(at.tabs)
                    if page!='Weather map':
                        self.assertEqual(bundle.request.call_count,count,page)
                        self.assertNotIn('Cross-country day estimate',[m.label for m in at.metric],page)
                        self.assertFalse(any('★' in str(m.value) for m in at.metric),page)
                        self.assertFalse(at.get('plotly_chart'),page)
                    if page in ('Help Guide','Help & Briefing Bot','Data quality'):
                        self.assertFalse(at.get('component_instance'))
                    if iteration:
                        timings[page]=round(elapsed*1000,1)
                        self.assertLess(elapsed,.3,(page,elapsed))
            at.radio(key='active_page').set_value('Weather map').run()
            for day,hour in ((0,10),(1,15),(4,19),(0,10)):
                # New hour documents may do cold layout work; transport and cube
                # selection must reuse the exact same five-day resource.
                at.selectbox(key='forecast_day').select(day)
                at.slider(key='forecast_hour').set_value(hour).run()
                self.assertFalse(at.exception)
                self.assertIs(at.session_state['uk_map_grid_bundle'],held)
            # Repeat the already-built selection with full Streamlit execution.
            clock=perf_counter();at.run()
            self.assertLess(perf_counter()-clock,.3)
            loader.assert_called_once()
            self.assertFalse(any('Loading the UK map grid' in str(call) for call in spinner.call_args_list))
            print('\nWarm real-page timings (ms):',timings)
            app.load_sites.clear()
