"""Regressions for the synchronous first-render weather boundary."""
from datetime import timedelta
import unittest
from unittest.mock import patch

from config import FORECAST_MODELS, forecast_locations
from data_ingestion import ServiceError
from forecast_coverage import soaring_coverage
from map_weather import national_grid, wind_payload
from sync_weather import SyncHttpClient
import sync_weather as weather
from tests.provider_fixture import Provider, NOW

CYCLE = int(NOW.timestamp()//1800)


class SynchronousWeatherTests(unittest.TestCase):
    def setUp(self):
        weather.cached_forecasts.clear()
        weather._last_good.clear()
        self.addCleanup(weather.cached_forecasts.clear)
        self.addCleanup(weather._last_good.clear)

    def client(self, scenario='healthy', *, ukmo_solar_gap=False, donor_error=False):
        provider = Provider(scenario)
        provider.hourly['pressure_msl'] = [1012.] * len(provider.hourly['time'])
        # Return precisely the variables requested, including true null UKMO
        # fields. A broad fixture must not conceal an incomplete request schema.
        import json
        from urllib.parse import urlsplit, parse_qs
        from tests.test_ingestion import Response
        def open_request(request, **kwargs):
            self.assertEqual(kwargs['timeout'], 4.0)
            query = parse_qs(urlsplit(request.full_url).query)
            if donor_error and query['models']==['ecmwf_ifs025']:
                raise TimeoutError('Synthetic donor timeout')
            with provider(request, **kwargs) as response:
                body = json.loads(response.read())
            fields = {'time', *query['hourly'][0].split(',')}
            for row in body:
                if ukmo_solar_gap and query['models']==['ukmo_seamless'] and 'shortwave_radiation' in fields:
                    row['hourly']['shortwave_radiation'][72:] = [None] * (len(row['hourly']['time'])-72)
                row['hourly'] = {k:v for k,v in row['hourly'].items() if k in fields}
                row['hourly_units'] = {k:v for k,v in row['hourly_units'].items() if k in fields}
            return Response(json.dumps(body).encode())
        client = SyncHttpClient(opener=open_request, clock=lambda:NOW,
                                sleep=lambda _:self.fail('No weather retry sleeps allowed'))
        return provider, client

    def test_first_pass_all_models_complete_without_donor_or_pending_state(self):
        point = next(p for p in forecast_locations() if p.key == 'region:shropshire')
        for key in FORECAST_MODELS:
            provider, client = self.client()
            with self.subTest(model=key), patch.object(weather, 'transport', return_value=client):
                batch = weather.fetch_forecasts((point,), key, CYCLE)
                self.assertFalse(batch.failures)
                self.assertEqual(soaring_coverage(batch.forecasts[0], NOW.date()), (8,)*5)
                self.assertEqual(len(provider.calls), 1)
                self.assertEqual(len(provider.calls[0][1]['hourly'][0].split(',')), 30)

    def test_regional_and_entire_wind_grid_fit_real_default_budget_in_two_gets(self):
        provider, client = self.client()
        with patch.object(weather, 'transport', return_value=client):
            regional = weather.regional_forecasts('icon_eu', CYCLE)
            grid = weather.fetch_forecasts(national_grid(), 'icon_eu', CYCLE, 'wind')
            self.assertEqual(len(regional.forecasts), 23)
            self.assertEqual(len(grid.forecasts), 168)
            self.assertFalse(regional.failures or grid.failures)
            self.assertEqual(client.gate.budget, 300)
            self.assertLessEqual(sum(cost for _,cost in client.gate.costs), 300)
            self.assertEqual(len(provider.calls), 2)
            for _ in range(3):
                weather.regional_forecasts('icon_eu', CYCLE)
                weather.fetch_forecasts(national_grid(), 'icon_eu', CYCLE, 'wind')
            self.assertEqual(len(provider.calls), 2)
            self.assertTrue(all(f.times[0].date()==NOW.date() for f in grid.forecasts))

    def test_rate_limit_and_timeout_have_no_retries_no_negative_weather_cache(self):
        point = forecast_locations()[0]
        for scenario in ('429', 'timeout'):
            with self.subTest(scenario=scenario):
                provider, client = self.client(scenario)
                with patch.object(weather, 'transport', return_value=client):
                    failed = weather.fetch_forecasts((point,), 'icon_eu', CYCLE)
                self.assertFalse(failed.forecasts)
                self.assertIn('no stored forecast', failed.failures[0].message)
                self.assertEqual(len(provider.calls), 1)
                # A later explicit attempt after recovery must not see a cached
                # failure. The fresh transport represents expiry of cooldown.
                healthy, client = self.client()
                with patch.object(weather, 'transport', return_value=client):
                    ready = weather.fetch_forecasts((point,), 'icon_eu', CYCLE)
                self.assertTrue(ready.forecasts)
                self.assertEqual(len(healthy.calls), 1)
                weather.cached_forecasts.clear(); weather._last_good.clear()

    def test_failed_explicit_refresh_retains_real_data_and_original_timestamp(self):
        point = forecast_locations()[0]
        _, client = self.client()
        with patch.object(weather, 'transport', return_value=client):
            good = weather.fetch_forecasts((point,), 'icon_eu', CYCLE)
        weather.clear()
        _, broken = self.client('429')
        with patch.object(weather, 'transport', return_value=broken):
            stored = weather.fetch_forecasts((point,), 'icon_eu', CYCLE+1)
        self.assertEqual(stored.forecasts, good.forecasts)
        self.assertEqual(stored.forecasts[0].fetched_at, NOW)
        self.assertIn('Using stored forecast', stored.failures[0].message)

    def test_ukmo_late_solar_gap_uses_one_selective_donor_and_fits_full_grid_budget(self):
        provider, client = self.client(ukmo_solar_gap=True)
        with patch.object(weather, 'transport', return_value=client):
            regional = weather.regional_forecasts('ukmo', CYCLE)
            grid = weather.fetch_forecasts(national_grid(), 'ukmo', CYCLE, 'wind')
            self.assertFalse(regional.failures or grid.failures)
            self.assertEqual(len(provider.calls), 3)
            support = provider.calls[1][1]
            self.assertEqual(support['models'], ['ecmwf_ifs025'])
            self.assertEqual(set(support['hourly'][0].split(',')), {'temperature_2m','shortwave_radiation'})
            self.assertEqual(len(support['latitude'][0].split(',')), 23)
            self.assertAlmostEqual(sum(cost for _,cost in client.gate.costs), 293.6)
            for forecast in regional.forecasts:
                self.assertEqual(soaring_coverage(forecast, NOW.date()), (8,)*5)
                self.assertEqual(forecast.field_sources['shortwave_radiation'][80], 'ecmwf_ifs025')
                self.assertIsNone(forecast.field_sources['shortwave_radiation'][60])
            weather.regional_forecasts('ukmo', CYCLE)
            self.assertEqual(len(provider.calls), 3)

    def test_failed_selective_donor_retains_primary_without_caching_partial_weather(self):
        point = forecast_locations()[0]
        _, client = self.client(ukmo_solar_gap=True, donor_error=True)
        with patch.object(weather, 'transport', return_value=client):
            partial = weather.fetch_forecasts((point,), 'ukmo', CYCLE)
        self.assertEqual(len(partial.forecasts), 1)
        self.assertIn('donor request failed', partial.failures[0].message)
        self.assertIsNotNone(partial.forecasts[0].hourly['shortwave_radiation'][60])
        self.assertIsNone(partial.forecasts[0].hourly['shortwave_radiation'][80])
        self.assertFalse(weather._last_good()[0])
        # The exact same cache key can recover on the next explicit interaction.
        provider, client = self.client(ukmo_solar_gap=True)
        with patch.object(weather, 'transport', return_value=client):
            ready = weather.fetch_forecasts((point,), 'ukmo', CYCLE)
        self.assertFalse(ready.failures)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(soaring_coverage(ready.forecasts[0], NOW.date()), (8,)*5)


if __name__ == '__main__':
    unittest.main()
