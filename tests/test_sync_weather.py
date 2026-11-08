"""Nine-anchor requests, live MET failover and honest offline display data."""
import json
from pathlib import Path
import tempfile
from threading import Lock
from time import perf_counter
from urllib.parse import parse_qs, urlsplit
import unittest
from unittest.mock import patch

from config import FORECAST_MODELS, forecast_locations
from forecast_coverage import soaring_coverage
from forecast_quality import flight_evidence, is_demo, is_surface_estimate
from map_weather import national_grid, wind_payload
from met_norway import MET_URL, USER_AGENT
from tests.provider_fixture import FailoverProvider, NOW
from tests.test_ingestion import Response
from weather_anchors import ANCHORS, sample_anchors
from weather_disk_cache import load_live
import sync_weather as weather

CYCLE=int(NOW.timestamp()//1800)


class SynchronousWeatherTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.disk=Path(self.tmp.name)/'weather.json'
        owner=patch.dict('os.environ',{'DUO_WEATHER_CACHE_PATH':str(self.disk)})
        owner.start();self.addCleanup(owner.stop)
        weather.clear();self.addCleanup(weather.clear)

    def client(self, scenario='healthy', *, ukmo_solar_gap=False, delay=0):
        provider=FailoverProvider(scenario,delay=delay)
        provider.hourly['pressure_msl']=[1012.]*len(provider.hourly['time'])
        active={'count':0,'maximum':0};lock=Lock()
        def open_request(request, **kwargs):
            self.assertEqual(kwargs['timeout'],1.25)
            self.assertEqual(request.get_header('User-agent'),USER_AGENT)
            parsed=urlsplit(request.full_url);query=parse_qs(parsed.query)
            if parsed.hostname=='api.met.no':
                with lock:
                    active['count']+=1
                    active['maximum']=max(active['maximum'],active['count'])
            try:
                with provider(request,**kwargs) as response:body=json.loads(response.read())
            finally:
                if parsed.hostname=='api.met.no':
                    with lock:active['count']-=1
            if parsed.hostname=='api.open-meteo.com':
                fields={'time',*query['hourly'][0].split(',')}
                for row in body:
                    if ukmo_solar_gap and query['models']==['ukmo_seamless'] and 'shortwave_radiation' in fields:
                        row['hourly']['shortwave_radiation'][72:]=[None]*(len(row['hourly']['time'])-72)
                    row['hourly']={k:v for k,v in row['hourly'].items() if k in fields}
                    row['hourly_units']={k:v for k,v in row['hourly_units'].items() if k in fields}
            return Response(json.dumps(body).encode())
        client=weather.SyncHttpClient(opener=open_request,clock=lambda:NOW,
            sleep=lambda _:self.fail('No retry sleeps or cooldown waits allowed'))
        self.assertFalse(hasattr(client,'gate'))
        return provider,client,active

    def test_first_pass_all_models_use_one_nine_anchor_request_and_complete_days(self):
        point=next(p for p in forecast_locations() if p.key=='region:shropshire')
        for key in FORECAST_MODELS:
            provider,client,_=self.client()
            with self.subTest(model=key),patch.object(weather,'transport',return_value=client):
                batch=weather.fetch_forecasts((point,),key,CYCLE)
                self.assertFalse(batch.failures)
                self.assertEqual(soaring_coverage(batch.forecasts[0],NOW.date()),(8,)*5)
                self.assertTrue(flight_evidence(batch.forecasts[0]))
                self.assertEqual(len(provider.calls),1)
                request=provider.calls[0][1]
                self.assertEqual(len(request['latitude'][0].split(',')),9)
                self.assertEqual(len(request['hourly'][0].split(',')),30)

    def test_regional_and_168_wind_points_share_nine_anchor_network_response(self):
        provider,client,_=self.client()
        with patch.object(weather,'transport',return_value=client):
            regional=weather.regional_forecasts('icon_eu',CYCLE)
            grid=weather.fetch_forecasts(national_grid(),'icon_eu',CYCLE,'wind')
            self.assertEqual(len(regional.forecasts),23)
            self.assertEqual(len(grid.forecasts),168)
            self.assertFalse(regional.failures or grid.failures)
            for _ in range(3):
                weather.regional_forecasts('icon_eu',CYCLE)
                weather.fetch_forecasts(national_grid(),'icon_eu',CYCLE,'wind')
            self.assertEqual(len(provider.calls),1)
            self.assertEqual(len(provider.calls[0][1]['latitude'][0].split(',')),9)
            self.assertTrue(all(f.times[0].date()==NOW.date() for f in grid.forecasts))

    def test_429_and_timeout_fail_over_to_nine_concurrent_met_calls_within_two_seconds(self):
        for scenario in ('429','timeout'):
            with self.subTest(scenario=scenario):
                weather.clear()
                provider,client,active=self.client(scenario,delay=.04)
                with patch.object(weather,'transport',return_value=client):
                    start=perf_counter()
                    batch=weather.fetch_forecasts(ANCHORS,'icon_eu',CYCLE)
                    self.assertLess(perf_counter()-start,2.)
                    self.assertEqual(len(batch.forecasts),9)
                    self.assertFalse(batch.failures)
                    self.assertTrue(all(f.source==MET_URL and is_surface_estimate(f) for f in batch.forecasts))
                    self.assertTrue(all(not flight_evidence(f) for f in batch.forecasts))
                    self.assertEqual(active['maximum'],9)
                    self.assertEqual([host for host,_ in provider.calls].count('api.open-meteo.com'),1)
                    self.assertEqual([host for host,_ in provider.calls].count('api.met.no'),9)
                    weather.fetch_forecasts(national_grid(),'icon_eu',CYCLE,'wind')
                    self.assertEqual(len(provider.calls),10)
                self.assertTrue(self.disk.exists())

    def test_both_services_offline_produce_numeric_demo_without_persisting_or_flight_claims(self):
        provider,client,_=self.client('offline')
        with patch.object(weather,'transport',return_value=client):
            batch=weather.fetch_forecasts(ANCHORS,'icon_eu',CYCLE)
        self.assertEqual(len(batch.forecasts),9)
        self.assertTrue(all(is_demo(f) and not flight_evidence(f) for f in batch.forecasts))
        self.assertFalse(self.disk.exists())
        self.assertEqual(len(provider.calls),10)
        self.assertEqual(batch.failures[0].provider,'demonstration')
        grid=sample_anchors(batch.forecasts,national_grid(),wind_only=True)
        self.assertEqual(len(grid),168)
        wind=wind_payload(grid,NOW)
        self.assertTrue(all(all(station['values'][12]) for station in wind['stations']))
        self.assertTrue(all(is_demo(f) for f in grid))

    def test_explicit_refresh_retries_immediately_after_failure_without_cooldown(self):
        provider,client,_=self.client('offline')
        with patch.object(weather,'transport',return_value=client):
            failed=weather.fetch_forecasts(ANCHORS,'icon_eu',CYCLE)
            self.assertTrue(all(is_demo(f) for f in failed.forecasts))
            provider.scenario='healthy'
            weather.clear()
            recovered=weather.fetch_forecasts(ANCHORS,'icon_eu',CYCLE)
        self.assertFalse(recovered.failures)
        self.assertTrue(all(flight_evidence(f) for f in recovered.forecasts))
        self.assertEqual(len(provider.calls),11)

    def test_disk_survives_memory_cache_clear_and_preserves_real_original_timestamp(self):
        _,client,_=self.client()
        with patch.object(weather,'transport',return_value=client):
            good=weather.fetch_forecasts(ANCHORS,'icon_eu',CYCLE)
        self.assertEqual(len(load_live('icon_eu',NOW,self.disk)),9)
        weather.clear()
        _,broken,_=self.client('offline')
        with patch.object(weather,'transport',return_value=broken):
            stored=weather.fetch_forecasts(ANCHORS,'icon_eu',CYCLE+1)
        self.assertEqual(stored.forecasts[0].fetched_at,NOW)
        self.assertEqual(stored.forecasts[0].hourly['temperature_2m'],good.forecasts[0].hourly['temperature_2m'])
        self.assertEqual(stored.failures[0].provider,'stored')
        self.assertTrue(all(flight_evidence(f) for f in stored.forecasts))

    def test_ukmo_solar_gap_keeps_selective_two_column_donor_to_nine_points(self):
        provider,client,_=self.client(ukmo_solar_gap=True)
        with patch.object(weather,'transport',return_value=client):
            batch=weather.fetch_forecasts(ANCHORS,'ukmo',CYCLE)
            weather.fetch_forecasts(national_grid(),'ukmo',CYCLE,'wind')
        self.assertFalse(batch.failures)
        self.assertEqual(len(provider.calls),2)
        donor=provider.calls[1][1]
        self.assertEqual(donor['models'],['ecmwf_ifs025'])
        self.assertEqual(set(donor['hourly'][0].split(',')),{'temperature_2m','shortwave_radiation'})
        self.assertEqual(len(donor['latitude'][0].split(',')),9)
        for forecast in batch.forecasts:
            self.assertEqual(soaring_coverage(forecast,NOW.date()),(8,)*5)
            self.assertEqual(forecast.field_sources['shortwave_radiation'][80],'ecmwf_ifs025')
            self.assertIsNone(forecast.field_sources['shortwave_radiation'][60])


if __name__=='__main__':unittest.main()
