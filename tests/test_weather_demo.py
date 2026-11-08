"""Offline display is complete, deterministic, and never flight evidence."""
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
import json
import math
import unittest
from unittest.mock import patch

from config import FORECAST_MODELS, HOURLY_VARIABLES, MONITORING_ZONES, PRESSURE_LEVELS_HPA, forecast_locations
from data_ingestion import _json_default, forecast_from_snapshot
from forecast_quality import (DEMO_SOURCE, EXCLUDED_NOTE, SURFACE_ONLY_NOTE,
                              evidence_label, flight_evidence, is_demo, is_surface_estimate)
from weather_demo import demo_forecasts

ANCHOR=datetime(2026,11,8,8,tzinfo=UTC)
ORIGIN=MONITORING_ZONES['shropshire']


class DemoForecastTests(unittest.TestCase):
    def test_five_days_have_complete_canonical_arrays_and_pressure_profiles(self):
        from soaring_physics import Profile,evaluate_thermals
        from map_weather import WIND_LEVELS,wind_vector
        for model in FORECAST_MODELS:
            with self.subTest(model=model):
                f=demo_forecasts((ORIGIN,),ANCHOR,model)[0]
                self.assertEqual(f.source,DEMO_SOURCE)
                self.assertTrue(is_demo(f));self.assertFalse(flight_evidence(f))
                self.assertEqual(len(f.times),120)
                self.assertEqual(f.times[0],ANCHOR.replace(hour=0))
                self.assertEqual(f.times[-1],ANCHOR.replace(hour=23)+timedelta(days=4))
                self.assertTrue(set(HOURLY_VARIABLES).issubset(f.hourly))
                for key,series in f.hourly.items():
                    self.assertEqual(len(series),120,key)
                    self.assertTrue(all(value is not None and math.isfinite(value) for value in series),key)
                    self.assertEqual(f.missing_counts[key],0)
                for offset in range(5):
                    profile=Profile.from_forecast(f,ANCHOR.replace(hour=13)+timedelta(days=offset))
                    values=profile.values;thermal=evaluate_thermals(profile)
                    self.assertLessEqual(values['dew_point_2m'],values['temperature_2m'])
                    self.assertGreater(values['pressure_msl'],values['surface_pressure'])
                    self.assertGreaterEqual(values['shortwave_radiation'],values['direct_radiation'])
                    self.assertGreaterEqual(values['wind_gusts_10m'],values['wind_speed_10m'])
                    self.assertIsNotNone(thermal.net_climb_knots)
                    self.assertGreaterEqual(thermal.net_climb_knots,0)
                    self.assertGreater(thermal.cloud_base_ft_amsl,0)
                    self.assertGreater(thermal.usable_top_ft_amsl,0)
                    for pressure,_ in WIND_LEVELS:
                        self.assertIsNotNone(wind_vector(profile,pressure),(model,pressure))
                    heights=[values[f'geopotential_height_{p}hPa'] for p in PRESSURE_LEVELS_HPA]
                    self.assertEqual(heights,sorted(heights))
                self.assertEqual(f.hourly['shortwave_radiation'][0],0)

    def test_dataset_is_repeatable_spatial_and_seasonal_without_io(self):
        from config import GeoPoint
        south=GeoPoint('south','South',50,-5);north=GeoPoint('north','North',58,-4)
        with patch('urllib.request.urlopen',side_effect=AssertionError('No network for demo')):
            first=demo_forecasts((south,north),ANCHOR,'icon_eu')
            second=demo_forecasts((south,north),ANCHOR,'icon_eu')
            summer=demo_forecasts((south,),ANCHOR.replace(month=7),'icon_eu')[0]
        self.assertEqual(first,second)
        self.assertNotEqual(first[0].hourly['wind_speed_10m'],first[1].hourly['wind_speed_10m'])
        self.assertGreater(summer.hourly['temperature_2m'][13],first[0].hourly['temperature_2m'][13])

    def test_probe_header_identifies_demo_and_met_estimates(self):
        from point_forecast_meteogram import prepare_bundle
        from app_cache import forecast_run_id
        from tests.provider_fixture import FailoverProvider
        from met_norway import parse_met_forecast
        from data_ingestion import JsonResponse
        import json
        from urllib.request import Request
        demo=demo_forecasts((ORIGIN,),ANCHOR,'icon_eu')[0]
        preview=prepare_bundle(forecast_run_id((demo,)),ANCHOR.date(),_forecasts=(demo,))
        self.assertIn('DEMONSTRATION ONLY',preview['model'])
        provider=FailoverProvider('429')
        with provider(Request('https://api.met.no/weatherapi/locationforecast/2.0/compact?lat=52.518&lon=-2.880')) as response:
            met=parse_met_forecast(JsonResponse(json.loads(response.read()),ANCHOR),ORIGIN,ANCHOR)
        preview=prepare_bundle(forecast_run_id((met,)),ANCHOR.date(),_forecasts=(met,))
        self.assertIn('MET Norway',preview['model'])
        self.assertIn('assumed upper profile',preview['model'])

    def test_late_met_card_uses_known_surface_values_without_inventing_morning(self):
        from pages.weather import remaining_surface_temperature
        from config import GeoPoint
        f=demo_forecasts((GeoPoint('region:shropshire','Long Mynd',52.518,-2.880),),ANCHOR,'icon_eu')[0]
        values=dict(f.hourly)
        values['temperature_2m']=(None,)*18+(9.,)*6+values['temperature_2m'][24:]
        late=replace(f,hourly=values)
        self.assertEqual(remaining_surface_temperature((late,),ANCHOR.date().isoformat()),'9.0 °C')
        self.assertTrue(all(v is None for v in late.hourly['temperature_2m'][:18]))

    def test_snapshot_roundtrip_retains_demo_quality(self):
        source=demo_forecasts((ORIGIN,),ANCHOR,'icon_eu')[0]
        saved=json.loads(json.dumps(asdict(source),default=_json_default))
        restored=forecast_from_snapshot(saved)
        self.assertTrue(is_demo(restored))
        self.assertFalse(flight_evidence(restored))
        self.assertEqual(restored.source,source.source)
        self.assertEqual(restored.warnings,source.warnings)
        self.assertEqual(restored.hourly,source.hourly)
        self.assertEqual(restored.units,source.units)
        self.assertIn('DEMONSTRATION',evidence_label(restored))

    def test_all_five_cards_can_preview_numbers_but_normal_ratings_reject_demo(self):
        from day_rating import rate_day
        from task_planning import classify_day
        forecasts=demo_forecasts((ORIGIN,),ANCHOR,'icon_eu')
        for offset in range(5):
            day=ANCHOR.date()+timedelta(days=offset)
            display=rate_day(forecasts,ORIGIN,day,preview=True)
            self.assertEqual(display.covered_hours,8)
            self.assertIn(display.stars,(1,2,3,4,5))
            self.assertTrue(math.isfinite(display.distance_km))
            self.assertTrue(any('DISPLAY PREVIEW ONLY' in r for r in display.reasons))
            self.assertIsNone(rate_day(forecasts,ORIGIN,day).stars)
            self.assertIsNone(classify_day(forecasts,ORIGIN,day).distance_km)


class ForecastQualityGuards(unittest.TestCase):
    def test_surface_only_and_native_source_contracts(self):
        from tests.fixtures import forecast
        native=forecast()
        self.assertTrue(flight_evidence(native))
        met=replace(native,source='https://api.met.no/weatherapi/locationforecast/2.0/compact',warnings=(SURFACE_ONLY_NOTE,))
        self.assertTrue(is_surface_estimate(met));self.assertFalse(flight_evidence(met))
        self.assertFalse(is_demo(met))
        self.assertIn('MET Norway',evidence_label(met))
        self.assertFalse(is_surface_estimate(replace(met,source='https://api.met.no.example.invalid/')))
        self.assertFalse(flight_evidence(None))

    def test_met_snapshot_cannot_lose_surface_only_provenance(self):
        from data_ingestion import SchemaError
        from tests.fixtures import forecast
        met=replace(forecast(hours=120),model='met_norway',requested_horizon_hours=119,
            source='https://api.met.no/weatherapi/locationforecast/2.0/compact',warnings=(SURFACE_ONLY_NOTE,))
        record=json.loads(json.dumps(asdict(met),default=_json_default))
        restored=forecast_from_snapshot(record)
        self.assertEqual(restored.model,'met_norway')
        self.assertTrue(is_surface_estimate(restored))
        self.assertFalse(flight_evidence(restored))
        for invalid in (None,'javascript:alert(1)','https://user:secret@api.met.no/'):
            with self.assertRaises(SchemaError):forecast_from_snapshot({**record,'source':invalid})
        with self.assertRaises(SchemaError):forecast_from_snapshot({**record,'warnings':{'surface-only':True}})

    def test_complete_demo_and_met_estimates_cannot_make_a_task_achievable(self):
        from tests.test_task_planning import TASK,DAY,weather
        from task_planning import assess_task
        original=weather()
        self.assertEqual(assess_task(TASK,original,DAY).verdict,'FULLY ACHIEVABLE')
        for source,notes in ((DEMO_SOURCE,()),('https://api.met.no/weatherapi/locationforecast/2.0/compact',(SURFACE_ONLY_NOTE,))):
            data=tuple(replace(f,source=source,warnings=notes) for f in original)
            result=assess_task(TASK,data,DAY)
            self.assertEqual(result.verdict,'INSUFFICIENT DATA')
            self.assertIsNone(result.best)
            self.assertIn(EXCLUDED_NOTE,result.warnings)

    def test_demo_and_surface_estimates_never_trigger_alerts_or_convergence(self):
        from alerts import ForecastBundle,build_briefing,evaluate_convergence
        from tests.fixtures import NOW,forecast
        original=tuple(forecast(point) for point in forecast_locations())
        for source,notes in ((DEMO_SOURCE,()),('https://api.met.no/weatherapi/locationforecast/2.0/compact',(SURFACE_ONLY_NOTE,))):
            data=tuple(replace(f,source=source,warnings=notes) for f in original)
            bundle=ForecastBundle(data,data,.5,(),NOW)
            report=build_briefing(bundle,2,now=NOW)
            self.assertFalse(report.triggers)
            self.assertTrue(all(r.stars is None for r in report.ratings))
            self.assertIn(EXCLUDED_NOTE,report.warnings)
            self.assertIsNone(evaluate_convergence(bundle,NOW))
