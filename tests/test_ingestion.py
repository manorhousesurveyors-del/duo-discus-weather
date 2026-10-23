from dataclasses import replace
from datetime import UTC, datetime, timedelta
import io
import json
from pathlib import Path
import ssl
import tempfile
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

from config import FORECAST_MODELS, HOURLY_VARIABLES, MONITORING_ZONES, Settings
from data_ingestion import (
    AviationWeatherClient, JsonHttpClient, JsonResponse, OpenMeteoClient,
    IngestionError, SchemaError, ServiceError, Visibility, _HttpsRedirectHandler, _write_json, flight_category,
    parse_clouds, parse_forecast, parse_metar, parse_taf, parse_visibility, parse_wind,
)

NOW = datetime(2026, 6, 10, 12, tzinfo=UTC)
POINT = MONITORING_ZONES['shropshire']


def forecast_fixture():
    # Deliberately partial synthetic fixture: absent fields must stay missing.
    return {'latitude': 52.52, 'longitude': -2.88, 'elevation': 480,
            'utc_offset_seconds': 0,
            'hourly_units': {'time': 'unixtime', 'temperature_2m': '°C',
                             'wind_speed_10m': 'm/s', 'vertical_velocity_850hPa': 'm/s'},
            'hourly': {'time': [int((NOW + timedelta(hours=h)).timestamp()) for h in range(3)],
                       'temperature_2m': [20.1, 21.3, None], 'wind_speed_10m': [0, 5, None],
                       'vertical_velocity_850hPa': [0.12, -0.2, None]}}


def metar_fixture():
    return {'icaoId': 'EGBB', 'obsTime': int(NOW.timestamp()), 'receiptTime': NOW.isoformat(),
            'temp': 21, 'dewp': 12, 'wdir': 270, 'wspd': 10, 'wgst': 18,
            'visib': '6+', 'altim': 1018, 'lat': 52.45, 'lon': -1.75, 'elev': 99,
            'clouds': [{'cover': 'SCT', 'base': 4000}], 'fltCat': 'VFR', 'rawOb': 'synthetic test'}


def taf_fixture():
    return {'icaoId': 'EGBB', 'issueTime': NOW.isoformat(),
            'validTimeFrom': int(NOW.timestamp()), 'validTimeTo': int((NOW + timedelta(hours=24)).timestamp()),
            'rawTAF': 'synthetic test', 'fcsts': [
                {'timeFrom': int(NOW.timestamp()), 'timeTo': int((NOW + timedelta(hours=24)).timestamp()),
                 'wdir': 270, 'wspd': 10, 'visib': '6+', 'clouds': [{'cover': 'SCT', 'base': 4000}]},
                {'timeFrom': int((NOW + timedelta(hours=3)).timestamp()),
                 'timeTo': int((NOW + timedelta(hours=6)).timestamp()),
                 'fcstChange': 'TEMPO', 'probability': 30, 'visib': 2,
                 'clouds': [{'cover': 'BKN', 'base': 800}], 'wxString': 'SHRA'},
            ]}


class Response(io.BytesIO):
    def __init__(self, body=b'{}', *, status=200, content_type='application/json'):
        super().__init__(body)
        self.status = status
        self.headers = {'Content-Type': content_type}


class FakeClock:
    def __init__(self):
        self.seconds = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.seconds

    def now(self):
        return NOW + timedelta(seconds=self.seconds)

    def sleep(self, duration):
        self.sleeps.append(duration)
        self.seconds += duration


class HttpTests(unittest.TestCase):
    def client(self, outcomes, **kwargs):
        self.calls = []
        self.clock = FakeClock()
        def open_request(request, **options):
            self.calls.append((request, options))
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return JsonHttpClient(Settings(), opener=open_request, sleep=self.clock.sleep,
                              monotonic=self.clock.monotonic, clock=self.clock.now, **kwargs)

    def get(self, client, params=None, ttl=60):
        return client.get_json('https://example.test/data', params or {'x': 'a b'}, ttl_seconds=ttl)

    def test_cache_keeps_fetch_time_isolation_and_expires(self):
        client = self.client([Response(b'{"values":[1]}'), Response(b'{"values":[2]}')])
        first = self.get(client)
        first.body['values'].append(999)
        self.clock.seconds += 10
        second = self.get(client)
        self.assertTrue(second.from_cache)
        self.assertEqual(second.body, {'values': [1]})
        self.assertEqual(second.fetched_at, first.fetched_at)
        self.clock.seconds += 51
        self.assertEqual(self.get(client).body, {'values': [2]})
        self.assertEqual(len(self.calls), 2)

    def test_real_request_encoding_timeout_and_tls_verification(self):
        client = self.client([Response()])
        self.get(client)
        request, options = self.calls[0]
        self.assertEqual(parse_qs(urlsplit(request.full_url).query), {'x': ['a b']})
        self.assertEqual(options['timeout'], 20)
        self.assertEqual(options['context'].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(options['context'].check_hostname)

    def test_transient_failure_then_success(self):
        client = self.client([HTTPError('https://example.test', 503, 'Unavailable', {}, None), Response(b'{"ok":true}')])
        self.assertEqual(self.get(client).body, {'ok': True})
        self.assertEqual(len(self.calls), 2)
        self.assertGreaterEqual(sum(self.clock.sleeps), 1)

    def test_retry_after_respected(self):
        client = self.client([HTTPError('https://example.test', 429, 'Throttled', {'Retry-After': '7'}, None), Response()])
        self.get(client)
        self.assertIn(7, self.clock.sleeps)

    def test_long_retry_after_does_not_retry_too_early(self):
        client = self.client([HTTPError('https://example.test', 429, 'Throttled', {'Retry-After': '120'}, None)])
        with self.assertRaises(ServiceError):
            self.get(client)
        self.assertEqual(len(self.calls), 1)

    def test_permanent_error_is_not_retried_or_leaked(self):
        client = self.client([HTTPError('https://example.test?secret=hidden', 403, 'Forbidden', {}, None)])
        with self.assertRaises(ServiceError) as caught:
            self.get(client)
        self.assertNotIn('hidden', str(caught.exception))
        self.assertEqual(len(self.calls), 1)

    def test_tls_failure_is_never_bypassed(self):
        client = self.client([URLError(ssl.SSLCertVerificationError('certificate failure'))])
        with self.assertRaises(ServiceError):
            self.get(client)
        self.assertEqual(len(self.calls), 1)

    def test_retry_exhaustion(self):
        client = self.client([URLError('offline') for _ in range(3)])
        with self.assertRaises(ServiceError):
            self.get(client)
        self.assertEqual(len(self.calls), 3)

    def test_empty_aviation_response(self):
        self.assertEqual(self.get(self.client([Response(status=204)])).body, [])

    def test_invalid_json_html_nonfinite_and_oversized_rejected(self):
        for response in (Response(b'broken'), Response(b'{"x":NaN}'), Response(b'null'),
                         Response(b'<html>error</html>', content_type='text/html')):
            with self.subTest(response=response), self.assertRaises(SchemaError):
                self.get(self.client([response]))
        with self.assertRaises(SchemaError):
            self.get(self.client([Response(b'{"long":"1234567890"}')], max_response_bytes=5))

    def test_api_error_and_insecure_url_rejected(self):
        with self.assertRaises(ServiceError):
            self.get(self.client([Response(b'{"error":true,"reason":"bad model"}')]))
        client = self.client([])
        with self.assertRaises(ValueError):
            client.get_json('http://example.test', {}, ttl_seconds=1)
        with self.assertRaises(ValueError):
            client.get_json('https://user:password@example.test', {}, ttl_seconds=1)

    def test_https_redirect_cannot_downgrade_tls(self):
        with self.assertRaises(ServiceError):
            _HttpsRedirectHandler().redirect_request(Request('https://example.test'), None,
                                                     301, 'Redirect', {}, 'http://example.test')

    def test_rate_limit_and_cache_eviction(self):
        client = self.client([Response(), Response(), Response()], cache_entries=1)
        self.get(client, {'x': 1})
        self.get(client, {'x': 2})
        self.get(client, {'x': 1})
        self.assertEqual(len(self.calls), 3)
        self.assertGreaterEqual(self.clock.seconds, 2.4)

    def test_weighted_multilocation_pacing_accounts_for_provider_quota(self):
        client=self.client([Response(),Response()])
        params={'latitude':','.join(['52']*20),'hourly':','.join(HOURLY_VARIABLES),
                'models':'icon_eu','forecast_hours':121}
        for _ in range(2):
            client.get_json('https://api.open-meteo.com/v1/forecast',params,ttl_seconds=0)
        self.assertAlmostEqual(self.clock.seconds,20*len(HOURLY_VARIABLES)/10*60/300)

    def test_unspecified_429_cooldown_waits_one_minute(self):
        client=self.client([HTTPError('https://api.open-meteo.com',429,'quota',{},None),Response()])
        client.get_json('https://api.open-meteo.com/v1/forecast',{},ttl_seconds=0)
        self.assertGreaterEqual(self.clock.seconds,60)


class ForecastTests(unittest.TestCase):
    def parse(self, body=None):
        return parse_forecast(JsonResponse(body or forecast_fixture(), NOW), POINT, FORECAST_MODELS['ifs025'])

    def test_units_missingness_and_exact_time_lookup(self):
        result = self.parse()
        self.assertEqual(result.at(NOW)['temperature_2m'], 20.1)
        self.assertEqual(result.at(NOW)['wind_speed_10m'], 0)
        self.assertIsNone(result.at(NOW)['boundary_layer_height'])
        self.assertIn('boundary_layer_height', result.unavailable_variables)
        self.assertEqual(result.units['vertical_velocity_850hPa'], 'm/s')
        self.assertEqual(result.available_through, NOW + timedelta(hours=1))
        self.assertEqual(result.terrain_elevation_m, 480)
        with self.assertRaises(KeyError):
            result.at(NOW + timedelta(minutes=30))
        with self.assertRaises(ValueError):
            result.at(NOW.replace(tzinfo=None))

    def test_unexpected_units_never_silently_converted(self):
        for unit in ('km/h', None):
            body = forecast_fixture()
            body['hourly_units']['wind_speed_10m'] = unit
            with self.subTest(unit=unit), self.assertRaises(SchemaError):
                self.parse(body)

    def test_pressure_velocity_units_preserved(self):
        body = forecast_fixture()
        body['hourly_units']['vertical_velocity_850hPa'] = 'Pa/s'
        result = self.parse(body)
        self.assertEqual(result.units['vertical_velocity_850hPa'], 'Pa/s')
        self.assertEqual(result.at(NOW)['vertical_velocity_850hPa'], .12)

    def test_malformed_axes_arrays_and_nonfinite_rejected(self):
        bodies = []
        body = forecast_fixture(); body['hourly']['time'][1] = body['hourly']['time'][0]; bodies.append(body)
        body = forecast_fixture(); body['hourly']['temperature_2m'] = [1]; bodies.append(body)
        body = forecast_fixture(); body['hourly']['temperature_2m'][0] = float('inf'); bodies.append(body)
        body = forecast_fixture(); body['hourly']['temperature_2m'] = [None] * 3; bodies.append(body)
        body = forecast_fixture(); body['utc_offset_seconds'] = 3600; bodies.append(body)
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(SchemaError):
                self.parse(body)

    def test_requests_all_fields_and_120_hour_endpoint(self):
        calls = []
        class Http:
            def get_json(self, endpoint, params, **kwargs):
                calls.append(params)
                return JsonResponse(forecast_fixture(), NOW)
        OpenMeteoClient(http=Http()).fetch(POINT, FORECAST_MODELS['icon_eu'])
        self.assertEqual(calls[0]['forecast_hours'], 121)
        self.assertEqual(calls[0]['wind_speed_unit'], 'ms')
        self.assertEqual(calls[0]['hourly'].split(','), list(HOURLY_VARIABLES))

    def test_parallel_batch_preserves_success_and_reports_failure(self):
        class Http:
            def get_json(self, endpoint, params, **kwargs):
                if params['models'] == 'icon_eu':
                    raise ServiceError('Synthetic service unavailable', status=503)
                return JsonResponse(forecast_fixture(), NOW)
        batch = OpenMeteoClient(http=Http()).fetch_many([POINT])
        self.assertEqual(len(batch.forecasts), 2)
        self.assertEqual(len(batch.failures), 1)
        self.assertEqual(batch.failures[0].provider, 'icon_eu')
        with self.assertRaises(IngestionError):
            batch.require_success()

    def test_duplicate_location_keys_and_request_budget_rejected(self):
        client = OpenMeteoClient(settings=replace(Settings(), max_grid_points=1))
        with self.assertRaises(ValueError):
            client.fetch_many([POINT, POINT])

    def test_multicoordinate_batch_validates_response_count_and_order(self):
        points=(POINT,MONITORING_ZONES['humberside'])
        bodies=[forecast_fixture(),forecast_fixture()]
        for i,body in enumerate(bodies): body['location_id']=i
        class Http:
            def get_json(self,*args,**kwargs): return JsonResponse(bodies,NOW)
        client=OpenMeteoClient(http=Http())
        result=client.fetch_batched(points,[FORECAST_MODELS['ifs025']],variables=('temperature_2m',))
        self.assertEqual(len(result.forecasts),2)
        self.assertEqual({f.location.key for f in result.forecasts},{p.key for p in points})
        bodies.reverse()
        self.assertEqual(len(client.fetch_batched(points,[FORECAST_MODELS['ifs025']]).failures),2)
        bodies.pop()
        self.assertEqual(len(client.fetch_batched(points,[FORECAST_MODELS['ifs025']]).failures),2)


class AviationTests(unittest.TestCase):
    def test_wind_from_west_north_variable_and_calm(self):
        wind = parse_wind({'wdir': 270, 'wspd': 10, 'wgst': 15})
        self.assertAlmostEqual(wind.u_mps, 5.144444444, places=7)
        self.assertAlmostEqual(wind.v_mps, 0, places=7)
        self.assertLess(parse_wind({'wdir': 360, 'wspd': 10}).v_mps, 0)
        self.assertIsNone(parse_wind({'wdir': 'VRB', 'wspd': 3}).u_mps)
        self.assertEqual(parse_wind({'wspd': 0}).u_mps, 0)
        with self.assertRaises(SchemaError):
            parse_wind({'wspd': -1})

    def test_visibility_censoring_fractions_and_missing_values(self):
        self.assertEqual(parse_visibility('6+'), Visibility(6 * 1609.344, 'GT'))
        self.assertAlmostEqual(parse_visibility('1 1/2SM').metres, 1.5 * 1609.344)
        self.assertEqual(parse_visibility('M1/4SM').qualifier, 'LT')
        self.assertEqual(parse_visibility('CAVOK').metres, 10000)
        self.assertIsNone(parse_visibility(None).metres)
        with self.assertRaises(SchemaError):
            parse_visibility('missing')

    def test_flight_category_boundaries_and_unknowns(self):
        cases = [(10, 4000, 'VFR'), (5, 4000, 'MVFR'), (3, 3000, 'MVFR'),
                 (2.9, 4000, 'IFR'), (10, 999, 'IFR'), (10, 499, 'LIFR'), (.9, 4000, 'LIFR')]
        for visibility, base, category in cases:
            with self.subTest(visibility=visibility, base=base):
                self.assertEqual(flight_category(parse_visibility(visibility), parse_clouds([{'cover': 'BKN', 'base': base}])), category)
        self.assertEqual(flight_category(parse_visibility('6+'), ()), 'UNKNOWN')
        self.assertEqual(flight_category(parse_visibility('6+'), parse_clouds([{'cover': 'BKN'}])), 'UNKNOWN')

    def test_metar_normalization_freshness_and_pressure(self):
        report = parse_metar(metar_fixture(), now=NOW)
        self.assertFalse(report.stale)
        self.assertEqual(report.qnh_hpa, 1018)
        self.assertEqual(report.clouds[0].base_ft_agl, 4000)
        self.assertEqual(report.flight_category, 'VFR')
        self.assertTrue(parse_metar(metar_fixture(), now=NOW + timedelta(hours=2)).stale)
        self.assertTrue(parse_metar(metar_fixture(), now=NOW - timedelta(hours=1)).stale)
        body = metar_fixture(); body['altim'] = 29.92
        with self.assertRaises(SchemaError):
            parse_metar(body, now=NOW)

    def test_taf_retains_overlapping_conditional_periods(self):
        taf = parse_taf(taf_fixture(), now=NOW)
        self.assertTrue(taf.currently_valid)
        self.assertEqual(len(taf.periods), 2)
        self.assertEqual(taf.periods[1].change_indicator, 'TEMPO')
        self.assertEqual(taf.periods[1].probability_percent, 30)
        self.assertIsNone(taf.periods[1].wind.speed_knots)  # Never invented from base period.
        self.assertFalse(parse_taf(taf_fixture(), now=NOW + timedelta(days=2)).currently_valid)

    def test_taf_invalid_interval_rejected(self):
        body = taf_fixture()
        body['fcsts'][0]['timeTo'] = body['validTimeTo'] + 3600
        with self.assertRaises(SchemaError):
            parse_taf(body, now=NOW)

    def test_taf_empty_visibility_in_conditional_group_is_unspecified(self):
        # Observed in live EGOS/EGTC/EGXW TEMPO groups; do not reject the TAF.
        body = taf_fixture()
        body['fcsts'][1]['visib'] = ''
        taf = parse_taf(body, now=NOW)
        self.assertEqual(taf.periods[1].visibility, Visibility(None, 'UNKNOWN'))
        self.assertEqual(taf.periods[0].visibility.qualifier, 'GT')

    def test_poll_reports_missing_stations_and_keeps_latest_metar(self):
        earlier = metar_fixture(); earlier['obsTime'] -= 3600
        invalid = metar_fixture(); invalid['icaoId'] = 'EGOS'; invalid['altim'] = 29.92
        class Http:
            def get_json(self, endpoint, params, **kwargs):
                return JsonResponse([metar_fixture(), earlier, invalid] if endpoint.endswith('/metar') else [taf_fixture()], NOW)
        batch = AviationWeatherClient(http=Http(), clock=lambda: NOW).fetch(['EGBB', 'EGOS', 'EGCV'])
        self.assertEqual(batch.metars['EGBB'].observed_at, NOW)
        self.assertEqual(batch.missing_metars, ('EGOS', 'EGCV'))
        self.assertEqual(batch.missing_tafs, ('EGOS', 'EGCV'))
        self.assertEqual(len(batch.failures), 1)

    def test_one_product_failure_does_not_erase_other(self):
        class Http:
            def get_json(self, endpoint, params, **kwargs):
                if endpoint.endswith('/taf'):
                    raise ServiceError('Unavailable')
                return JsonResponse([metar_fixture()], NOW)
        batch = AviationWeatherClient(http=Http(), clock=lambda: NOW).fetch(['EGBB'])
        self.assertIn('EGBB', batch.metars)
        self.assertEqual(batch.missing_tafs, ('EGBB',))
        self.assertEqual(len(batch.failures), 1)

    def test_empty_product_responses_are_missing_not_fabricated(self):
        class Http:
            def get_json(self, *args, **kwargs):
                return JsonResponse([], NOW)
        batch = AviationWeatherClient(http=Http()).fetch(['EGCV'])
        self.assertEqual(batch.missing_metars, ('EGCV',))
        self.assertFalse(batch.metars)
        self.assertFalse(batch.failures)

    def test_snapshot_atomic_write_and_failed_write_preserves_prior_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot.json'
            _write_json(path, {'valid_at': NOW, 'value': 1})
            with self.assertRaises(ValueError):
                _write_json(path, {'value': float('nan')})
            self.assertEqual(json.loads(path.read_text())['value'], 1)
            self.assertEqual(list(Path(directory).iterdir()), [path])
