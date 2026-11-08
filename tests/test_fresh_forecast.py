"""First-pass live rendering uses synchronous, evidence-preserving forecasts."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from config import FORECAST_MODELS, default_forecast_model, forecast_locations
from data_ingestion import JsonResponse, ServiceError
from day_rating import rate_day
from forecast_coverage import complete_regional_forecasts, soaring_coverage
from tests.fixtures import alter, forecast

START = datetime(2026, 11, 6, tzinfo=UTC)
NOW = START+timedelta(hours=8)
CYCLE = int(NOW.timestamp()//1800)
POINT = next(p for p in forecast_locations() if p.key=='region:shropshire')


class FreshForecastTests(unittest.TestCase):
    def setUp(self):
        import app
        import uk_map_grid as grid
        import sync_weather
        self.app,self.grid,self.sync=app,grid,sync_weather
        sync_weather.clear();sync_weather._last_good()[0].clear()
        grid.load_uk_map_grid.clear()
        self.addCleanup(sync_weather.clear)
        self.addCleanup(sync_weather._last_good()[0].clear)
        self.http=Mock()
        self.http.get_json.side_effect=AssertionError('Unexpected network')
        owner=patch.object(sync_weather,'transport',return_value=self.http)
        owner.start();self.addCleanup(owner.stop)

    def healthy_response(self, endpoint, params, **kwargs):
        """Return only fields requested by production, with absent model BLH."""
        f=forecast(POINT,start=START,hours=144,model=params['models'],
                   changes={'pressure_msl':1012.,'boundary_layer_height':None,'cape':None})
        fields=params['hourly'].split(',')
        rows=[]
        for lat,lon in zip(params['latitude'].split(','),params['longitude'].split(',')):
            rows.append({'latitude':float(lat),'longitude':float(lon),'elevation':100.,
                'utc_offset_seconds':0,'hourly_units':{'time':'unixtime',**{k:f.units[k] for k in fields}},
                'hourly':{'time':[int(t.timestamp()) for t in f.times],
                          **{k:list(f.hourly[k]) for k in fields}}})
        return JsonResponse(rows,NOW)

    def assert_ready(self, at):
        self.assertFalse(at.exception)
        self.assertEqual(sum('8/8 local hours covered' in c.value for c in at.caption),5)
        self.assertEqual(len([m for m in at.metric if '★' in m.value]),5)
        values=[x.value for group in (at.warning,at.error,at.metric,at.caption) for x in group]
        for message in ('Day classification unavailable','Insufficient forecast coverage','No regional forecast','Only 0/8'):
            self.assertFalse(any(message in str(value) for value in values),message)
        self.assertTrue(at.get('component_instance'))

    def test_fresh_live_startup_default_renders_five_valid_days_without_unavailable_flash(self):
        from streamlit.testing.v1 import AppTest
        self.http.get_json.side_effect=self.healthy_response
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(os.environ,{'DUO_DASHBOARD_SNAPSHOT':'','DUO_TASK_STORE':tmp,'GEMINI_API_KEY':''}), \
             patch.object(self.app,'datetime',wraps=datetime) as clock, \
             patch.object(self.sync,'forecast_locations',return_value=(POINT,)), \
             patch('watchdog_weather.fetch_weather',side_effect=AssertionError('Healthy donors need no extra network')):
            clock.now.return_value=NOW
            at=AppTest.from_string('import app\napp._main()',default_timeout=30).run()
            self.assertEqual(at.selectbox(key='forecast_model').value,'icon_eu')
            self.assertEqual(default_forecast_model(),'icon_eu')
            self.assertFalse(any('Unavailable' in m.value or 'unavailable' in m.value for m in at.metric))
            self.assert_ready(at)  # The very first run, without warm-up or polling.
            # Each explicit model choice is fetched once. Day/hour changes then
            # reuse its complete synchronous five-day response.
            for key in ('ukmo','ifs025'):
                at.selectbox(key='forecast_model').select(key).run()
                self.assert_ready(at)
                self.assertEqual(at.selectbox(key='forecast_model').value,key)
            at.selectbox(key='forecast_day').select(4).run()
            at.slider(key='forecast_hour').set_value(14).run()
            self.assert_ready(at)
            self.assertEqual(self.http.get_json.call_count,3)

    def test_failed_cold_start_keeps_help_usable_without_waiting_or_loading_grid(self):
        from streamlit.testing.v1 import AppTest
        self.http.get_json.side_effect=ServiceError('HTTP 429',status=429)
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(os.environ,{'DUO_DASHBOARD_SNAPSHOT':'','DUO_TASK_STORE':tmp,'GEMINI_API_KEY':''}), \
             patch.object(self.grid,'load_uk_map_grid',side_effect=AssertionError('Clean mode needs no national grid')):
            at=AppTest.from_string('import app\napp._main()',default_timeout=30).run()
            self.assertFalse(at.exception)
            self.assertEqual(len(at.metric),10)
            self.assertTrue(at.get('component_instance'))
            self.assertTrue(any('no stored forecast' in w.value for w in at.warning))
            self.assertFalse(any('★' in m.value for m in at.metric))
            text=' '.join(str(x.value) for group in (at.info,at.caption,at.metric) for x in group)
            for message in ('Weather updating','Awaiting live weather','waiting for verified weather','background'):
                self.assertNotIn(message,text)
            calls=self.http.get_json.call_count
            at.radio(key='active_page').set_value('Help Guide').run()
            self.assertFalse(at.exception);self.assertFalse(at.get('component_instance'))
            self.assertEqual(self.http.get_json.call_count,calls)

    def test_incomplete_primary_sounding_falls_back_before_day_validation(self):
        # Non-null but unusable surface pressure defeats a simple null merge.
        pressure=[1000.]*144;pressure[12]=0.
        uk=forecast(POINT,start=START,hours=144,model='ukmo_seamless',changes={'surface_pressure':pressure})
        icon=forecast(POINT,start=START,hours=144,model='icon_eu')
        self.assertEqual(soaring_coverage(uk,START.date()),(7,8,8,8,8))
        result=complete_regional_forecasts((uk,icon),FORECAST_MODELS['ukmo'],START.date())[0]
        self.assertEqual(soaring_coverage(result,START.date()),(8,)*5)
        self.assertEqual(result.model,'ukmo_seamless')
        self.assertEqual(result.field_sources['surface_pressure'][12],'icon_eu')
        self.assertTrue(any('Coverage fallback to icon_eu' in note for note in result.warnings))
        self.assertEqual(uk.hourly['surface_pressure'][12],0.)  # Source stays immutable.

    def test_poor_weather_zeroes_are_kept_and_complete_provider_outage_stays_unknown(self):
        uk=forecast(POINT,start=START,hours=144,model='ukmo_seamless',changes={'shortwave_radiation':0.,'cape':0.})
        good=forecast(POINT,start=START,hours=144,model='ecmwf_ifs025')
        result=complete_regional_forecasts((uk,good),FORECAST_MODELS['ukmo'],START.date())[0]
        self.assertEqual(result.hourly['shortwave_radiation'][12],0.)
        self.assertEqual(rate_day((result,),POINT,START.date(),preference=('ukmo_seamless',)).stars,1)
        self.assertFalse(any('Coverage fallback' in note for note in result.warnings))
        bad=alter(uk,shortwave_radiation=None)
        result=complete_regional_forecasts((bad,),FORECAST_MODELS['ukmo'],START.date())[0]
        self.assertEqual(soaring_coverage(result,START.date()),(0,)*5)
        self.assertEqual(complete_regional_forecasts((),FORECAST_MODELS['ukmo'],START.date()),())
        nearby=replace(good,location=replace(POINT,latitude=POINT.latitude+.1))
        results=complete_regional_forecasts((bad,nearby),FORECAST_MODELS['ukmo'],START.date())
        self.assertEqual(soaring_coverage(next(f for f in results if f.location==POINT),START.date()),(0,)*5)

    def test_failed_explicit_refresh_retains_original_real_forecast_without_retry_loop(self):
        self.http.get_json.side_effect=self.healthy_response
        with patch.object(self.sync,'forecast_locations',return_value=(POINT,)):
            first=self.sync.regional_forecasts('icon_eu',CYCLE)
            self.assertEqual(soaring_coverage(first.forecasts[0],START.date()),(8,)*5)
            self.sync.clear()
            self.http.get_json.side_effect=ServiceError('HTTP timeout')
            stored=self.sync.regional_forecasts('icon_eu',CYCLE)
            self.assertEqual(stored.forecasts,first.forecasts)
            self.assertEqual(stored.forecasts[0].fetched_at,NOW)
            self.assertIn('Using stored forecast',stored.failures[0].message)
            self.assertEqual(self.http.get_json.call_count,2)


if __name__=='__main__':unittest.main()
