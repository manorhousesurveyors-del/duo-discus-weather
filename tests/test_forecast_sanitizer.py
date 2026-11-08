"""Compact live-request diagnostics preserve evidence and label estimates."""
from dataclasses import replace
from datetime import timedelta
import math
import unittest

from config import FORECAST_MODELS
from day_rating import rate_day
from forecast_coverage import complete_forecasts, soaring_coverage
from forecast_sanitizer import (MIXING_ESTIMATE_SOURCE, SURFACE_RH_SOURCE,
                                has_core_coverage, sanitize)
from soaring_physics import Profile, evaluate_thermals
from tests.fixtures import NOW, alter, forecast


SURFACE = ('temperature_2m', 'dew_point_2m', 'wind_speed_10m', 'wind_direction_10m',
           'wind_gusts_10m', 'shortwave_radiation', 'cloud_cover', 'cloud_cover_low',
           'cloud_cover_mid', 'cloud_cover_high', 'precipitation', 'pressure_msl',
           'boundary_layer_height')
WINDS = tuple(f'{key}_{level}hPa' for level in (925, 850, 700, 300)
              for key in ('wind_speed', 'wind_direction'))
SOUNDING = tuple(f'{key}_{level}hPa' for level in (925, 850, 700)
                for key in ('temperature', 'geopotential_height', 'relative_humidity'))


def compact_forecast(model='icon_eu', *, sounding=True):
    original = forecast(start=NOW.replace(hour=0), hours=144, model=model,
                        changes={'pressure_msl':1012., 'boundary_layer_height':None,
                                 'cloud_cover':40., 'cloud_cover_mid':20.,
                                 'cloud_cover_high':10.})
    keys = SURFACE + WINDS + (SOUNDING if sounding else ())
    return replace(original, hourly={key:original.hourly[key] for key in keys},
                   units={key:original.units[key] for key in keys}, field_sources={})


class CompactSanitizerTests(unittest.TestCase):
    def test_three_models_have_five_days_of_usable_evidence_with_compact_schema(self):
        for model in FORECAST_MODELS.values():
            with self.subTest(model=model.api_name):
                raw = compact_forecast(model.api_name)
                result = complete_forecasts((raw,), model)[0]
                self.assertEqual(soaring_coverage(result, raw.times[0].date()), (8,) * 5)
                self.assertTrue(has_core_coverage(result))
                midday = evaluate_thermals(Profile.from_forecast(result, raw.times[12]))
                self.assertGreater(midday.net_climb_knots, 0)
                self.assertTrue(math.isfinite(midday.cloud_base_ft_amsl))
                self.assertGreater(midday.usable_top_ft_amsl, 0)
                for offset in range(5):
                    rating = rate_day((result,), result.location,
                                      raw.times[0].date() + timedelta(days=offset),
                                      preference=(model.api_name,))
                    self.assertEqual(rating.covered_hours, 8)
                    self.assertIn(rating.stars, range(1, 6))
                self.assertTrue(all(v is None for v in result.hourly['cape']))
                self.assertNotIn('surface_pressure', raw.hourly)

    def test_pressure_and_surface_humidity_are_derived_with_provenance(self):
        raw = compact_forecast()
        completed = sanitize(raw)
        pressure = completed.hourly['surface_pressure'][0]
        self.assertLess(pressure, raw.hourly['pressure_msl'][0])
        self.assertGreater(pressure, 990)
        self.assertIn('hypsometric', completed.field_sources['surface_pressure'][0])
        self.assertIn('6.5 K/km', completed.field_sources['surface_pressure'][0])
        self.assertGreater(completed.hourly['relative_humidity_2m'][0], 0)
        self.assertLess(completed.hourly['relative_humidity_2m'][0], 100)
        self.assertEqual(completed.field_sources['relative_humidity_2m'][0], SURFACE_RH_SOURCE)
        self.assertNotIn('relative_humidity_2m', raw.hourly)

    def test_surface_energy_proxy_is_labelled_and_does_not_claim_lcl_is_mixing_top(self):
        raw = compact_forecast(sounding=False)
        radiation = [0.] * len(raw.times)
        radiation[10:15] = [100., 250., 500., 350., 100.]
        raw = alter(raw, shortwave_radiation=radiation)
        result = sanitize(raw)
        self.assertIsNone(result.hourly['boundary_layer_height'][2])
        depth = result.hourly['boundary_layer_height'][12]
        self.assertGreaterEqual(depth, 150)
        self.assertLessEqual(depth, 1500)
        self.assertNotEqual(depth, 125 * (28 - 12))
        self.assertEqual(result.field_sources['boundary_layer_height'][12], MIXING_ESTIMATE_SOURCE)
        thermal = evaluate_thermals(Profile.from_forecast(result, raw.times[12]))
        self.assertIn(MIXING_ESTIMATE_SOURCE, thermal.notes)

    def test_native_zeroes_preserved_and_missing_essential_weather_not_invented(self):
        raw = alter(compact_forecast(sounding=False), boundary_layer_height=0.,
                    surface_pressure=999., relative_humidity_2m=0., precipitation=None)
        result = sanitize(raw)
        self.assertEqual(result.hourly['boundary_layer_height'], raw.hourly['boundary_layer_height'])
        self.assertEqual(result.hourly['surface_pressure'], raw.hourly['surface_pressure'])
        self.assertEqual(result.hourly['relative_humidity_2m'], raw.hourly['relative_humidity_2m'])
        self.assertTrue(all(value is None for value in result.hourly['precipitation']))
        self.assertFalse(has_core_coverage(result))

    def test_total_missing_surface_evidence_cannot_create_pressure_or_mixing_height(self):
        raw = compact_forecast(sounding=False)
        empty = replace(raw, hourly={key:(None,) * len(raw.times) for key in raw.hourly})
        result = sanitize(empty)
        self.assertTrue(all(value is None for value in result.hourly['boundary_layer_height']))
        self.assertNotIn('surface_pressure', result.hourly)
        self.assertFalse(has_core_coverage(result))


if __name__ == '__main__':
    unittest.main()
