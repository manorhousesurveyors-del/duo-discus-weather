"""Exercise the shared scheduler against slow, empty, partial and limited I/O."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Event
from time import perf_counter
from unittest.mock import Mock,patch
from urllib.error import HTTPError
import unittest

from config import OPEN_METEO_URL,FORECAST_MODELS
from data_ingestion import ForecastBatch,OpenMeteoClient,ServiceError
from forecast_sanitizer import sanitize
from weather_engine import ResourceStore,ProviderGate,ForecastHttpClient,complete_batch,DeferredWeather,relabel
from tests.fixtures import NOW,forecast,alter
from tests.test_ingestion import Response


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.time=0.
        self.store=ResourceStore(clock=lambda:self.time,workers=2,max_entries=8)
        self.addCleanup(self.store.close)

    def test_single_flight_across_sessions_returns_immediately_and_caches_success(self):
        release=Event();entered=Event();self.addCleanup(release.set)
        def load():entered.set();release.wait(2);return 'verified'
        start=perf_counter();future=self.store.request('same-grid',load)
        self.assertLess(perf_counter()-start,.02);self.assertTrue(entered.wait(1))
        with ThreadPoolExecutor(max_workers=8) as pool:
            replies=list(pool.map(lambda _:self.store.request('same-grid',load),range(50)))
        self.assertTrue(all(f is future for f in replies))
        release.set();self.assertEqual(future.result(1),'verified')
        self.assertEqual(self.store.previous('same-grid'),'verified')
        self.assertIs(self.store.request('same-grid',Mock(side_effect=AssertionError('duplicate'))),future)

    def test_failed_and_partial_results_are_not_success_cache_and_retry_in_same_cycle(self):
        partial=ForecastBatch((alter(forecast(),precipitation=None),),())
        for value in (ForecastBatch((),()),partial):
            future=self.store.request(str(value),lambda:value,accept=complete_batch,retry=5)
            self.assertEqual(future.result(1),value)
            self.assertIsNone(self.store.previous(str(value)))
            self.time+=6
            good=ForecastBatch((forecast(),),())
            new=self.store.request(str(value),lambda:good,accept=complete_batch)
            self.assertIsNot(new,future);self.assertEqual(new.result(1),good)
        future=self.store.request('timeout',Mock(side_effect=ServiceError('timeout')),retry=5)
        with self.assertRaises(ServiceError):future.result(1)
        self.assertIsNone(self.store.previous('timeout'))
        self.time+=6
        self.assertEqual(self.store.request('timeout',lambda:'recovered').result(1),'recovered')

    def test_refresh_retains_last_good_value_and_cannot_erase_cooldown(self):
        self.store.request('a',lambda:'good',ttl=1).result(1)
        self.time=2
        failed=self.store.request('a',Mock(side_effect=ServiceError('429',status=429,retry_after=120)))
        with self.assertRaises(ServiceError):failed.result(1)
        self.assertEqual(self.store.previous('a'),'good')
        self.store.clear();self.time=30
        self.assertIs(self.store.request('a',Mock(side_effect=AssertionError('cooldown bypass'))),failed)
        self.time=123
        self.assertEqual(self.store.request('a',lambda:'new').result(1),'new')

    def test_known_zeroes_are_cacheable_and_coordinate_roles_are_relabelled(self):
        f=forecast(changes={'precipitation':0,'shortwave_radiation':0,'wind_speed_10m':0})
        self.assertTrue(complete_batch(ForecastBatch((f,),())))
        point=replace(f.location,key='task-start')
        result=relabel(ForecastBatch((f,),()),(point,))
        self.assertEqual(result.forecasts[0].location,point)
        self.assertEqual(f.location.key,'shropshire')


class ProviderTests(unittest.TestCase):
    def test_429_retry_after_applies_to_every_consumer_without_sleeping(self):
        now=[0.];gate=ProviderGate(clock=lambda:now[0]);opener=Mock(side_effect=HTTPError(OPEN_METEO_URL,429,'limited',{'Retry-After':'90'},None))
        sleep=Mock(side_effect=AssertionError('UI/worker sleep'))
        first=ForecastHttpClient(gate=gate,opener=opener,sleep=sleep)
        second=ForecastHttpClient(gate=gate,opener=opener,sleep=sleep)
        with self.assertRaises(ServiceError):first.get_json(OPEN_METEO_URL,{},ttl_seconds=1800)
        now[0]=60
        with self.assertRaises(DeferredWeather):second.get_json(OPEN_METEO_URL,{},ttl_seconds=1800)
        opener.assert_called_once();sleep.assert_not_called();self.assertEqual(gate.until,90)
        now[0]=91;opener.side_effect=lambda *a,**k:Response(b'{"ok":true}')
        self.assertEqual(second.get_json(OPEN_METEO_URL,{},ttl_seconds=1800).body,{'ok':True})

    def test_even_http_200_empty_or_partial_json_is_never_cached(self):
        opener=Mock(side_effect=lambda *a,**k:Response(b'{"hourly":{}}'))
        http=ForecastHttpClient(opener=opener)
        for _ in range(2):http.get_json(OPEN_METEO_URL,{},ttl_seconds=1800)
        self.assertEqual(opener.call_count,2);self.assertFalse(http._cache)

    def test_transport_single_flight_deduplicates_overlapping_readers(self):
        release=Event();entered=Event();self.addCleanup(release.set)
        def open(*args,**kwargs):entered.set();release.wait(2);return Response(b'{"ok":true}')
        opener=Mock(side_effect=open);http=ForecastHttpClient(opener=opener)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(http.get_json,OPEN_METEO_URL,{},ttl_seconds=0)
            self.assertTrue(entered.wait(1))
            shared=next(iter(http._inflight.values()));read=shared.result;waiting=Event()
            def join(*args,**kwargs):waiting.set();return read(*args,**kwargs)
            with patch.object(shared,'result',side_effect=join):
                other=pool.submit(http.get_json,OPEN_METEO_URL,{},ttl_seconds=0)
                self.assertTrue(waiting.wait(1));release.set()
                self.assertEqual(first.result().body,other.result().body)
        self.assertEqual(opener.call_count,1)

    def test_successful_primary_does_not_download_other_models(self):
        f=forecast(start=NOW.replace(hour=0),hours=144,model='icon_eu')
        http=Mock();http.gate=None
        client=OpenMeteoClient(http=http)
        with patch.object(client,'fetch_batched',return_value=ForecastBatch((f,),())) as fetch:
            batch=client.fetch_supported((f.location,),FORECAST_MODELS['icon_eu'])
        self.assertTrue(batch.forecasts);fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[1],(FORECAST_MODELS['icon_eu'],))

    def test_sanitizer_brackets_vectors_preserves_native_fields_and_never_invents_rain(self):
        f=forecast(changes={'boundary_layer_height':None,'wind_speed_900hPa':None,
                           'wind_direction_900hPa':None,'precipitation':None})
        completed=sanitize(f)
        self.assertIsNotNone(completed.hourly['boundary_layer_height'][0])
        self.assertIsNotNone(completed.hourly['wind_speed_900hPa'][0])
        self.assertEqual(completed.hourly['temperature_2m'],f.hourly['temperature_2m'])
        self.assertIsNone(completed.hourly['precipitation'][0])
        self.assertIsNone(f.hourly['boundary_layer_height'][0])
        self.assertEqual(completed.field_sources['wind_speed_900hPa'][0],'log-pressure wind interpolation')


if __name__=='__main__':unittest.main()

class EngineIntegrationTests(unittest.TestCase):
    def test_verified_chunks_resume_without_restarting_successful_requests(self):
        import weather_engine as engine
        from config import GeoPoint
        from data_ingestion import FetchFailure
        now=[0.];store=ResourceStore(clock=lambda:now[0],workers=2);self.addCleanup(store.close)
        points=tuple(GeoPoint(str(i),str(i),52+i*.01,-3) for i in range(12))
        calls=[]
        def fetch(group,model,**kwargs):
            calls.append(tuple(p.key for p in group))
            if group[0].key=='8' and now[0]==0:
                return ForecastBatch((),tuple(FetchFailure(p.key,model.api_name,'temporary 429') for p in group))
            return ForecastBatch(tuple(forecast(p) for p in group),())
        with patch.object(engine,'_store',store),patch.object(OpenMeteoClient,'fetch_supported',side_effect=fetch):
            first=engine.request_forecasts(points,'ifs025');self.assertEqual(len(first.result(5).forecasts),8)
            self.assertIs(engine.request_forecasts(points,'ifs025'),first)
            now[0]=16
            second=engine.request_forecasts(points,'ifs025');self.assertEqual(len(second.result(5).forecasts),12)
            self.assertEqual(len(calls),3)
            self.assertEqual(sum(group[0]=='0' for group in calls),1)
            # An alias at exactly the same coordinates must preserve caller roles.
            aliases=tuple(replace(p,key='alias:'+p.key) for p in points)
            self.assertEqual({f.location.key for f in engine.read_forecasts(aliases,'ifs025').forecasts},
                             {p.key for p in aliases})

    def test_three_models_cross_real_http_parse_and_completion_boundaries(self):
        from tests.provider_fixture import Provider,NOW as fixture_now,START
        from forecast_coverage import soaring_coverage
        from day_rating import rate_day
        from config import forecast_locations
        point=forecast_locations()[0]
        provider=Provider();http=ForecastHttpClient(opener=provider,clock=lambda:fixture_now,gate=ProviderGate(budget=10000))
        for model in FORECAST_MODELS.values():
            with self.subTest(model=model.api_name):
                batch=OpenMeteoClient(http=http).fetch_supported((point,),model)
                self.assertEqual(len(batch.forecasts),1)
                self.assertEqual(soaring_coverage(batch.forecasts[0],START.date()),(8,)*5)
                for offset in range(5):
                    rating=rate_day(batch.forecasts,point,(START+timedelta(days=offset)).date(),preference=(model.api_name,))
                    self.assertIsNotNone(rating.stars)
                self.assertFalse(http._cache)

    def test_absent_required_key_cannot_be_admitted_as_a_complete_forecast(self):
        f=forecast();values=dict(f.hourly);values.pop('precipitation')
        self.assertFalse(complete_batch(ForecastBatch((replace(f,hourly=values),),())))

    def test_session_migration_preserves_tasks_and_preferences(self):
        from session_state import migrate
        state={'active_task_points':('MYN','BIE'),'forecast_model':'ukmo','_uk_grid_hour':'stale',
               '_weather_map_state':'stale','forecast_fallback:ukmo':'stale','briefing_messages':['keep']}
        migrate(state)
        self.assertNotIn('_uk_grid_hour',state);self.assertNotIn('_weather_map_state',state)
        self.assertNotIn('forecast_fallback:ukmo',state)
        self.assertEqual(state['forecast_model'],'ukmo');self.assertEqual(state['active_task_points'],('MYN','BIE'))
        self.assertEqual(state['briefing_messages'],['keep'])

    def test_overnight_core_gap_gets_donor_even_when_eight_soaring_hours_are_present(self):
        from tests.provider_fixture import START
        primary=forecast(start=START,hours=144,model='icon_eu')
        night=list(primary.hourly['precipitation']);night[2]=None
        primary=alter(primary,precipitation=night)
        donor=forecast(start=START,hours=144,model='ecmwf_ifs025')
        with patch.object(OpenMeteoClient,'fetch_batched',side_effect=[ForecastBatch((primary,),()),ForecastBatch((donor,),())]) as fetch:
            batch=OpenMeteoClient().fetch_supported((primary.location,),FORECAST_MODELS['icon_eu'])
            self.assertEqual(fetch.call_count,2);self.assertTrue(complete_batch(batch))
            self.assertEqual(batch.forecasts[0].field_sources['precipitation'][2],'ecmwf_ifs025')
