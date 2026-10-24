from copy import deepcopy
from datetime import timedelta
import json
import unittest
from unittest.mock import Mock, patch

import app
from config import FORECAST_MODELS, OPEN_METEO_URL
from data_ingestion import IngestionError, JsonResponse
from precipitation import fetch_point_precipitation
from tests.fixtures import NOW


class PointPrecipitationTests(unittest.TestCase):
    def setUp(self):
        self.body = {'latitude': 52.5, 'longitude': -3.0, 'utc_offset_seconds': 0,
                     'hourly_units': {'time': 'unixtime', 'precipitation': 'mm'},
                     'hourly': {'time': [int((NOW+timedelta(hours=i)).timestamp()) for i in range(3)],
                                'precipitation': [0, 1.25, None]}}
        self.client = Mock()

    def fetch(self, when=NOW, body=None):
        self.client.get_json.return_value = JsonResponse(self.body if body is None else body, NOW)
        return fetch_point_precipitation(52.518, -2.88, when, FORECAST_MODELS['ifs025'], client=self.client)

    def test_exact_coordinate_selected_model_and_hourly_mean_units(self):
        result = self.fetch(NOW+timedelta(hours=1))
        self.assertEqual(result['rate'], 1.25)
        self.assertEqual(result['latitude'], 52.518)
        self.assertEqual(result['grid_latitude'], 52.5)
        self.assertEqual(result['interval_start'], NOW.isoformat())
        self.assertEqual(result['valid_at'], (NOW+timedelta(hours=1)).isoformat())
        self.assertEqual(result['source'], 'Open-Meteo')
        self.assertEqual(result['model'], 'ecmwf_ifs025')
        endpoint, params = self.client.get_json.call_args.args
        self.assertEqual(endpoint, OPEN_METEO_URL)
        self.assertEqual(params['latitude'], 52.518)
        self.assertEqual(params['longitude'], -2.88)
        self.assertEqual(params['models'], 'ecmwf_ifs025')
        self.assertEqual(params['cell_selection'], 'nearest')
        self.assertEqual(params['hourly'], 'precipitation')
        self.assertEqual(params['precipitation_unit'], 'mm')
        self.assertEqual(params['start_date'], NOW.date().isoformat())
        self.assertEqual(self.client.get_json.call_args.kwargs['ttl_seconds'], 300)
        json.dumps(result, allow_nan=False)

    def test_dry_zero_missing_and_outside_horizon_are_distinct(self):
        self.assertEqual(self.fetch()['rate'], 0)
        for hours in (-1, 2, 3, 120):
            result = self.fetch(NOW+timedelta(hours=hours))
            self.assertIsNone(result['rate'])
            self.assertIn('no precipitation value', result['error'])

    def test_invalid_units_times_values_and_coordinates_are_rejected(self):
        edits = [('hourly_units', 'precipitation', 'inch'), ('hourly_units', 'time', 'iso8601'),
                 ('hourly', 'time', [NOW.timestamp()]*3), ('hourly', 'time', [True, 2, 3]),
                 ('hourly', 'time', [NOW.timestamp(), NOW.timestamp()+7200, NOW.timestamp()+10800]),
                 ('hourly', 'precipitation', [0]), ('hourly', 'precipitation', [0, -1, None]),
                 ('hourly', 'precipitation', [0, float('nan'), None]),
                 ('hourly', 'precipitation', [0, True, None]),
                 ('hourly', 'precipitation', [0, '1.2', None])]
        bodies = [None, [], {}, {**self.body, 'latitude': 95}, {**self.body, 'utc_offset_seconds': 3600}]
        for section, key, value in edits:
            body = deepcopy(self.body); body[section][key] = value; bodies.append(body)
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(IngestionError):
                self.fetch(body={} if body is None else body)

    def test_subhour_and_invalid_requested_coordinates_do_not_send_requests(self):
        with self.assertRaises(ValueError): self.fetch(NOW+timedelta(minutes=30))
        for latitude, longitude in ((91, 0), (0, 181), (float('nan'), 0)):
            with self.assertRaises(ValueError):
                fetch_point_precipitation(latitude, longitude, NOW, FORECAST_MODELS['ifs025'], client=self.client)
        self.client.get_json.assert_not_called()

    def test_point_cache_separates_coordinates_hours_and_models(self):
        app.load_radar_precipitation.clear()
        with patch('precipitation.fetch_point_precipitation', return_value={'rate': 1.2}) as fetch:
            app.load_radar_precipitation(52, -3, NOW, 'ifs025', 1)
            app.load_radar_precipitation(52, -3, NOW, 'ifs025', 1)
            self.assertEqual(fetch.call_count, 1)
            app.load_radar_precipitation(53, -3, NOW, 'ifs025', 1)
            app.load_radar_precipitation(52, -3, NOW+timedelta(hours=1), 'ifs025', 1)
            app.load_radar_precipitation(52, -3, NOW, 'ukmo', 1)
            app.load_radar_precipitation(52, -3, NOW, 'ifs025', 2)
            self.assertEqual(fetch.call_count, 5)
        app.load_radar_precipitation.clear()

    def test_outage_and_recorded_mode_do_not_reuse_a_previous_value(self):
        with patch('app.load_radar_precipitation', side_effect=IngestionError('Timed out')) as fetch:
            result = app.radar_point_result((53, -2), NOW, 'ukmo', 1)
            self.assertIsNone(result['rate']); self.assertIn('Timed out', result['error'])
            self.assertEqual(result['latitude'], 53)
            self.assertEqual(result['model'], 'ukmo_seamless')
            fetch.reset_mock()
            result = app.radar_point_result((53, -2), NOW, 'ukmo', 1, recorded=True)
            self.assertIsNone(result['rate']); self.assertIn('Recorded snapshot', result['error'])
            fetch.assert_not_called()
