from datetime import UTC, datetime
import math
import unittest

from config import (
    DUO_DISCUS_POLARS, FORECAST_MODELS, HOURLY_VARIABLES, ICAO_STATIONS,
    LOCAL_TIMEZONE, MONITORING_ZONES, POLAR_REFERENCE_LOADING_KG_M2,
    POLAR_REFERENCE_POINTS, ROUTES, Settings, WAVE_RIDGES, GeoPoint,
    convergence_grid, forecast_locations,
)


class ConfigurationTests(unittest.TestCase):
    def test_specification_locations_routes_and_stations(self):
        self.assertEqual(len(MONITORING_ZONES), 9)
        self.assertEqual(len(WAVE_RIDGES), 14)
        self.assertEqual(len(set(ICAO_STATIONS)), 15)
        self.assertEqual((MONITORING_ZONES['shropshire'].latitude,
                          MONITORING_ZONES['shropshire'].longitude), (52.518, -2.88))
        self.assertEqual(len(ROUTES['A'].legs), 3)
        self.assertEqual(ROUTES['A'].waypoints[0], ROUTES['A'].waypoints[-1])
        self.assertEqual(len(ROUTES['B'].legs), 1)
        self.assertEqual(len(forecast_locations()), 23)
        self.assertEqual(len({p.key for p in forecast_locations()}), 23)

    def test_ridge_azimuths_and_provenance(self):
        expected = [280, 250, 270, 270, 240, 310, 270, 260, 250, 240, 250, 260, 230, 270]
        self.assertEqual([r.perpendicular_wind_azimuths_deg[0] for r in WAVE_RIDGES.values()], expected)
        for ridge in WAVE_RIDGES.values():
            self.assertFalse(ridge.survey_verified)
            self.assertTrue(ridge.provenance)
            self.assertGreater(ridge.crest_elevation_ft, 1000)

    def test_scaled_polars_reproduce_reference_data_and_glide_ratio(self):
        for polar in DUO_DISCUS_POLARS.values():
            scale = math.sqrt(polar.wing_loading_kg_m2 / POLAR_REFERENCE_LOADING_KG_M2)
            for speed, sink in POLAR_REFERENCE_POINTS:
                self.assertAlmostEqual(polar.sink_mps(speed * scale), sink * scale, places=10)
            speed = math.sqrt(polar.c / polar.a)
            self.assertAlmostEqual(speed / polar.sink_mps(speed), 44.76986834, places=6)
        with self.assertRaises(ValueError):
            DUO_DISCUS_POLARS['dry'].sink_mps(100)

    def test_london_clock_uses_dst_without_collapsing_repeated_hour(self):
        first = datetime(2026, 10, 25, 0, 30, tzinfo=UTC).astimezone(LOCAL_TIMEZONE)
        second = datetime(2026, 10, 25, 1, 30, tzinfo=UTC).astimezone(LOCAL_TIMEZONE)
        self.assertEqual(first.hour, second.hour)
        self.assertNotEqual(first.fold, second.fold)
        self.assertNotEqual(first.utcoffset(), second.utcoffset())
        self.assertEqual(datetime(2026, 1, 1, 12, tzinfo=UTC).astimezone(LOCAL_TIMEZONE).hour, 12)

    def test_grid_orientation_and_budget(self):
        grid = convergence_grid(south=50, north=51, west=-3, east=-2, step_degrees=.5)
        self.assertEqual((len(grid), len(grid[0])), (3, 3))
        self.assertEqual(grid[-1][-1].latitude, 51)
        self.assertEqual(grid[-1][-1].longitude, -2)
        self.assertLess(grid[0][0].latitude, grid[1][0].latitude)
        with self.assertRaises(ValueError):
            convergence_grid(step_degrees=.01)
        with self.assertRaises(ValueError):
            convergence_grid(south=51, north=50)

    def test_settings_validation(self):
        self.assertEqual(Settings.from_env({'DUO_HTTP_WORKERS': '4'}).http_workers, 4)
        for values in ({'DUO_HTTP_WORKERS': '0'}, {'DUO_HTTP_TIMEOUT_SECONDS': 'nan'},
                       {'DUO_HTTP_MAX_ATTEMPTS': '500'}, {'DUO_HTTP_WORKERS': 'many'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Settings.from_env(values)
        with self.assertRaises(ValueError):
            GeoPoint('bad', 'Bad', float('nan'), 0)

    def test_model_names_and_inclusive_horizons(self):
        self.assertEqual(FORECAST_MODELS['ifs025'].api_name, 'ecmwf_ifs025')
        self.assertEqual(FORECAST_MODELS['icon_eu'].horizon_hours, 120)
        self.assertIn('vertical_velocity_850hPa', HOURLY_VARIABLES)
        self.assertIn('soil_moisture_0_to_7cm', HOURLY_VARIABLES)
        self.assertEqual(len(HOURLY_VARIABLES), len(set(HOURLY_VARIABLES)))
