"""Five-day dropdown coverage with provider-shaped gaps, not fabricated lift."""
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
import json
from unittest.mock import Mock, patch
import tempfile
import unittest

from config import FORECAST_MODELS, forecast_locations
from data_ingestion import (ForecastBatch, JsonResponse, OpenMeteoClient, Settings,
                            forecast_from_snapshot)
from forecast_coverage import complete_forecasts, PARCEL_SOURCE
from day_rating import rate_day
from soaring_physics import Profile, evaluate_thermals
from tests.fixtures import forecast, alter

START = datetime(2026, 10, 24, tzinfo=UTC)  # Includes the BST/GMT change.


def missing_model_fixture(point=None):
    """UK/ICON upper fields end at 48h; BLH absent from every provider."""
    point = point or next(p for p in forecast_locations() if p.key=='region:shropshire')
    result = []
    for key, model in FORECAST_MODELS.items():
        f = forecast(point, start=START, hours=144, model=model.api_name,
                     changes={'boundary_layer_height':None, 'cape':None if key=='ukmo' else 100.})
        if key in ('ukmo', 'icon_eu'):
            f = alter(f, **{name:list(series[:48]) + [None]*96 for name, series in f.hourly.items()
                           if name.endswith('hPa')})
            f = alter(f, cloud_cover_low=[20. if i%3 else None for i in range(144)])
        result.append(f)
    return tuple(result)


class ForecastCoverageTests(unittest.TestCase):
    def test_all_dropdown_models_five_days_have_eight_hours_map_and_probe(self):
        from map_weather import soaring_samples, wind_payload
        from point_forecast_meteogram import hour_sample, FIELDS
        raw = missing_model_fixture()
        original = tuple(asdict(f) for f in raw)
        for model in FORECAST_MODELS.values():
            with self.subTest(model=model.api_name):
                resolved = complete_forecasts(raw, model)
                f = resolved[0]
                for i in range(5):
                    day = START.date()+timedelta(days=i)
                    rating = rate_day(resolved, f.location, day, preference=(model.api_name,))
                    self.assertEqual(rating.covered_hours, 8, rating)
                    self.assertIn(rating.stars, range(1,6))
                    when = START+timedelta(days=i,hours=12)
                    thermal = evaluate_thermals(Profile.from_forecast(f,when))
                    self.assertGreater(thermal.net_climb_knots,0)
                    self.assertGreater(thermal.usable_top_ft_amsl-f.terrain_elevation_m/.3048,0)
                    sample = soaring_samples(resolved,when,'dry')[0]
                    self.assertGreater(sample['thermal'],0)
                    self.assertIsNotNone(sample['pfd'])
                    probe = hour_sample(f,when)
                    fields = dict(zip(FIELDS,probe['s']))
                    self.assertGreater(fields['climb'],0)
                    self.assertGreater(fields['depth'],0)
                    self.assertIn(PARCEL_SOURCE,probe['sources'])
                wind = wind_payload(resolved,START)
                # Midday on every day retains all six displayed wind levels.
                for i in range(5):self.assertTrue(all(wind['stations'][0]['values'][12+i*24]))
        self.assertEqual(tuple(asdict(f) for f in raw),original)

    def test_native_zeroes_preserved_and_fallback_uses_exact_valid_hour(self):
        uk, icon, ec = missing_model_fixture()
        uk = alter(uk, cape=[0.,None]+[None]*142,shortwave_radiation=0.)
        ec = replace(ec,times=tuple(t+timedelta(hours=1) for t in ec.times))
        ec = alter(ec,cape=[float(i+100) for i in range(144)])
        f = complete_forecasts((uk,ec),FORECAST_MODELS['ukmo'])[0]
        self.assertEqual(f.hourly['cape'][:3],(0.,100.,101.))
        self.assertIsNone(f.field_sources['cape'][0])
        self.assertEqual(f.field_sources['cape'][1],'ecmwf_ifs025')
        t=evaluate_thermals(Profile.from_forecast(f,START+timedelta(hours=12)))
        self.assertEqual(t.net_climb_knots,0.)
        self.assertEqual(t.usable_top_ft_amsl,f.terrain_elevation_m/.3048)
        # Primary values survive even if the donor predicts very different wind.
        self.assertEqual(f.hourly['wind_speed_850hPa'][12],uk.hourly['wind_speed_850hPa'][12])

    def test_cape_is_optional_and_total_outage_never_becomes_good_weather(self):
        ec=missing_model_fixture()[-1]
        ec=alter(ec,cape=None)
        for model in FORECAST_MODELS.values():
            f=complete_forecasts((ec,),model)[0]
            self.assertIn(rate_day((f,),f.location,START.date(),preference=(model.api_name,)).stars,range(1,6))
            self.assertTrue(all(v is None for v in f.hourly['cape']))
            self.assertEqual(complete_forecasts((),model),())
        bad=alter(ec,shortwave_radiation=None)
        f=complete_forecasts((bad,),FORECAST_MODELS['ifs025'])[0]
        rating=rate_day((f,),f.location,START.date())
        self.assertIsNone(rating.stars);self.assertEqual(rating.covered_hours,0)
        # A nearby/different requested coordinate must never donate its weather.
        different=replace(ec,location=replace(ec.location,latitude=ec.location.latitude+.1))
        fs=complete_forecasts((bad,different),FORECAST_MODELS['ifs025'])
        self.assertEqual(len(fs),2)
        self.assertIsNone(fs[0].hourly['shortwave_radiation'][12])

    def test_provenance_round_trips_through_alert_snapshot(self):
        f=complete_forecasts(missing_model_fixture(),FORECAST_MODELS['ukmo'])[0]
        restored=forecast_from_snapshot(json.loads(json.dumps(asdict(f),default=lambda v:v.isoformat())))
        self.assertEqual(restored.field_sources,f.field_sources)
        self.assertEqual(restored.sources_at(START),f.sources_at(START))
        self.assertEqual(restored.source_runs,f.source_runs)

    def test_new_donor_run_invalidates_cache_even_when_primary_retrieval_is_older(self):
        from app_cache import forecast_run_id
        uk,icon,ec=missing_model_fixture()
        before=complete_forecasts((uk,ec),FORECAST_MODELS['ukmo'])
        ec=replace(ec,fetched_at=ec.fetched_at+timedelta(minutes=30))
        after=complete_forecasts((uk,ec),FORECAST_MODELS['ukmo'])
        self.assertEqual(before[0].fetched_at,after[0].fetched_at)
        self.assertNotEqual(forecast_run_id(before),forecast_run_id(after))

    def test_bad_point_does_not_discard_other_points_in_a_batch(self):
        good=missing_model_fixture()[-1]
        body={'latitude':good.model_latitude,'longitude':good.model_longitude,'elevation':100,
              'utc_offset_seconds':0,'hourly_units':{'time':'unixtime',**good.units},
              'hourly':{'time':[int(t.timestamp()) for t in good.times],**{k:list(v) for k,v in good.hourly.items()}}}
        bad={**body,'hourly':{**body['hourly'],'temperature_2m':[None]*144},'location_id':1}
        http=Mock();http.get_json.return_value=JsonResponse([body,bad],START)
        batch=OpenMeteoClient(Settings(),http=http).fetch_batched(
            (good.location,replace(good.location,key='missing')), (FORECAST_MODELS['ifs025'],))
        self.assertEqual(len(batch.forecasts),1);self.assertEqual(len(batch.failures),1)
        self.assertEqual(batch.failures[0].target,'missing')

    def test_regional_and_grid_completion_share_raw_transport_on_model_switch(self):
        import uk_map_grid as grid
        raw=missing_model_fixture()
        grid.regional_sources.clear();grid.regional_future.clear()
        with patch.object(grid.OpenMeteoClient,'fetch_batched',return_value=ForecastBatch(raw,())) as fetch:
            for key in FORECAST_MODELS:
                batch=grid.regional_future(100,key).result(timeout=10)
                self.assertEqual(batch.forecasts[0].model,FORECAST_MODELS[key].api_name)
                self.assertTrue(batch.forecasts[0].supplemental_sources)
            fetch.assert_called_once()
        grid.regional_sources.clear();grid.regional_future.clear()
        with patch.object(grid,'national_grid',return_value=(raw[0].location,)), \
             patch.object(grid,'fetch_chunk',side_effect=lambda points,model,http:tuple(f for f in raw if f.model==model.api_name)) as fetch:
            bundle=grid.UKGridBundle(int(START.timestamp()//1800))
            for key in FORECAST_MODELS:
                snapshot=bundle.request(key).result(timeout=10)
                self.assertGreater(snapshot.hour(START)[0,list(snapshot.batch.forecasts[0].hourly).index('boundary_layer_height')],0)
            self.assertEqual(fetch.call_count,3)

    def test_real_app_dropdown_five_day_cards_and_probe_render_for_each_model(self):
        from streamlit.testing.v1 import AppTest
        from alerts import ForecastBundle,save_bundle
        import app
        with tempfile.TemporaryDirectory() as tmp:
            snapshot=Path(tmp)/'models.json'
            save_bundle(ForecastBundle(missing_model_fixture(),(),.5,(),START),snapshot)
            with patch.dict('os.environ',{'DUO_DASHBOARD_SNAPSHOT':str(snapshot),'DUO_TASK_STORE':tmp+'/tasks'}), \
                 patch('data_ingestion.JsonHttpClient.get_json',side_effect=AssertionError('Dropdown/probe must reuse cached data')):
                at=AppTest.from_file(str(Path(app.__file__)),default_timeout=90).run()
                for key in FORECAST_MODELS:
                    at.selectbox(key='forecast_model').select(key).run()
                    at.toggle(key='point_probe_enabled').set_value(True)
                    at.slider(key='forecast_hour').set_value(12).run()
                    self.assertFalse(at.exception)
                    self.assertFalse(any('unavailable' in w.value.lower() or 'does not cover' in w.value.lower() for w in at.warning))
                    cards=[m for m in at.metric if '★' in m.value]
                    self.assertEqual(len(cards),5)
                    self.assertEqual(sum('8/8 local hours covered' in c.value for c in at.caption),5)
                    self.assertTrue(at.get('component_instance'))
                    self.assertNotEqual(at.metric[0].value,'—',(key,[(m.label,m.value) for m in at.metric]))
                    self.assertGreater(float(at.metric[0].value.split()[0]),0)
                    for day in range(5):
                        at.selectbox(key='forecast_day').select(day).run()
                        self.assertFalse(at.exception)
                        self.assertTrue(at.get('component_instance'))
                        self.assertFalse(any('Point Forecast Probe is temporarily unavailable' in w.value for w in at.warning))


if __name__=='__main__':unittest.main()
