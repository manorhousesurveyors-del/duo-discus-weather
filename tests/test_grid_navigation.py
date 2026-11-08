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

    def test_cycle_view_returns_completed_grid_and_retries_only_on_request(self):
        points=grid.national_grid();source=forecast()
        failure=ForecastBatch((),())
        success=ForecastBatch((source,),())
        with patch('sync_weather.fetch_forecasts',side_effect=(failure,success)) as request:
            bundle=grid.UKGridBundle(int(NOW.timestamp()//1800))
            failed=bundle.request('ifs025')
            self.assertTrue(failed.done())
            self.assertEqual(failed.result().batch.forecasts,())
            self.assertIsNone(bundle.completed('ifs025'))
            self.assertEqual(request.call_args.args,(points,'ifs025',bundle.cycle))
            self.assertEqual(request.call_args.kwargs,{'product':'wind'})
            recovered=bundle.request('ifs025')
            self.assertTrue(recovered.done())
            self.assertTrue(recovered.result().batch.forecasts)
            for _ in range(120):self.assertIs(bundle.request('ifs025'),recovered)
            self.assertEqual(request.call_count,2)

    def test_progress_adapter_never_reruns_without_a_user_click(self):
        future=Future()
        retry=Mock()
        with patch.object(grid.st,'caption'),patch.object(grid.st,'button',return_value=False), \
             patch.object(grid.st,'rerun',side_effect=AssertionError('Automatic rerun')):
            grid.grid_progress(future,retry=retry)
            future.set_result(None)
            grid.grid_progress(future,retry=retry)
        retry.assert_not_called()

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
        fixed_now=start+timedelta(hours=12)
        class FrozenClock(datetime):
            @classmethod
            def now(cls,tz=None):return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)
        forecasts=tuple(forecast(p,start=start,hours=144) for p in forecast_locations())
        regional=ForecastBatch(forecasts,())
        data=Future();data.set_result(forecasts)
        snapshot=grid.assemble((data,),(tuple(f.location for f in forecasts),),FORECAST_MODELS['ifs025'],start)
        # The first pass receives the entire completed national product.
        completed=Future();completed.set_result(snapshot)
        bundle=Mock(cycle=int(fixed_now.timestamp()//1800),coverage_revision=3,wind_grid_revision=grid.WIND_GRID_REVISION);bundle.request.return_value=completed
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict('os.environ',{'DUO_DASHBOARD_SNAPSHOT':'','DUO_TASK_STORE':tmp,'GEMINI_API_KEY':''}), \
             patch.object(app,'datetime',FrozenClock), \
             patch.object(app,'load_sites',return_value=regional), \
             patch.object(grid,'load_uk_map_grid',return_value=bundle) as loader, \
             patch('ogn_traffic.ensure_worker'),patch('ogn_traffic.traffic_heartbeat'), \
             patch.object(grid.st,'spinner',wraps=grid.st.spinner) as spinner:
            app.load_sites.clear()
            at=AppTest.from_string('import app\napp.main()',default_timeout=90).run()
            at.selectbox(key='visual_mode').select('wind').run()
            self.assertFalse(at.exception)
            self.assertTrue(completed.done())
            self.assertFalse(any('background' in x.value for x in at.caption))
            held=at.session_state['uk_map_grid_bundle']
            # Help never requests the national grid. No map constructor is mocked.
            count=bundle.request.call_count
            clock=perf_counter();at.radio(key='active_page').set_value('Help Guide').run()
            self.assertLess(perf_counter()-clock,.3)
            self.assertEqual(bundle.request.call_count,count)
            self.assertFalse(at.get('component_instance'))
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
