"""A clean live-mode session must not confuse pending/partial UKMO with no data."""
from concurrent.futures import Future
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from config import FORECAST_MODELS, default_forecast_model, forecast_locations
from data_ingestion import FetchFailure, ForecastBatch
from day_rating import rate_day
from forecast_coverage import complete_regional_forecasts, soaring_coverage
from tests.fixtures import alter, forecast

START = datetime(2026, 11, 6, tzinfo=UTC)
NOW = START+timedelta(hours=8)
CYCLE = int(NOW.timestamp()//1800)
POINT = next(p for p in forecast_locations() if p.key=='region:shropshire')


def sources():
    forecasts = []
    for key, model in FORECAST_MODELS.items():
        f = forecast(POINT, start=START, hours=144, model=model.api_name)
        if key in ('ukmo', 'icon_eu'):
            f = alter(f, boundary_layer_height=None, cape=None, precipitation=None, cloud_cover_low=None)
            f = alter(f, **{name:list(series[:48])+[None]*96 for name,series in f.hourly.items() if name.endswith('hPa')})
        forecasts.append(f)
    return tuple(forecasts)


class FreshForecastTests(unittest.TestCase):
    def setUp(self):
        import app
        import uk_map_grid as grid
        self.app,self.grid=app,grid
        import weather_engine
        from unittest.mock import Mock
        store=weather_engine.ResourceStore(workers=2)
        owner=patch.object(weather_engine,'_store',store);owner.start();self.addCleanup(owner.stop);self.addCleanup(store.close)
        http=weather_engine.ForecastHttpClient(opener=Mock(side_effect=AssertionError('Unexpected network')))
        owner=patch.object(weather_engine,'_transport',http);owner.start();self.addCleanup(owner.stop)
        app.load_sites.clear();grid.regional_sources.clear();grid.regional_future.clear()
        self.addCleanup(app.load_sites.clear)
        self.addCleanup(grid.regional_sources.clear)
        self.addCleanup(grid.regional_future.clear)

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
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(os.environ,{'DUO_DASHBOARD_SNAPSHOT':'','DUO_TASK_STORE':tmp,'GEMINI_API_KEY':''}), \
             patch.object(self.app,'datetime',wraps=datetime) as clock, \
             patch.object(self.grid,'forecast_locations',return_value=(POINT,)), \
             patch.object(self.grid.OpenMeteoClient,'fetch_batched',return_value=ForecastBatch(sources(),())) as fetch, \
             patch('watchdog_weather.fetch_weather',side_effect=AssertionError('Healthy donors need no extra network')):
            clock.now.return_value=NOW
            at=AppTest.from_string('import app\napp._main()',default_timeout=30).run()
            self.assertEqual(at.selectbox(key='forecast_model').value,'icon_eu')
            self.assertEqual(default_forecast_model(),'icon_eu')
            self.assertFalse(any('Unavailable' in m.value or 'unavailable' in m.value for m in at.metric))
            self.grid.regional_future(CYCLE).result(timeout=10)
            at.run();self.assert_ready(at)
            # A user's explicit UKMO choice persists, with support from the same
            # raw bundle before ratings are calculated. Switching does no I/O.
            for key in ('ukmo','ifs025'):
                at.selectbox(key='forecast_model').select(key).run()
                self.grid.regional_future(CYCLE,key).result(timeout=10)
                at.run();self.assert_ready(at)
                self.assertEqual(at.selectbox(key='forecast_model').value,key)
            self.assertEqual(fetch.call_count,3)  # Each newly selected model loads once; days/hours reuse it.

    def test_pending_cold_start_keeps_help_usable_and_does_not_render_zero_cards_or_load_grid(self):
        from streamlit.testing.v1 import AppTest
        pending=Future()
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(os.environ,{'DUO_DASHBOARD_SNAPSHOT':'','DUO_TASK_STORE':tmp,'GEMINI_API_KEY':''}), \
             patch.object(self.grid,'regional_future',return_value=pending), \
             patch.object(self.grid,'load_uk_map_grid',side_effect=AssertionError('No grid before regional coverage')):
            at=AppTest.from_string('import app\napp._main()',default_timeout=30).run()
            self.assertFalse(at.exception);self.assertFalse(at.warning)
            self.assertEqual(len(at.metric),10)
            self.assertTrue(at.get('component_instance'))
            self.assertTrue(all('Unavailable' not in str(m.value) for m in at.metric))
            self.assertTrue(any('Loading the five-day regional forecast' in i.value for i in at.info))
            at.radio(key='active_page').set_value('Help Guide').run()
            self.assertFalse(at.exception);self.assertFalse(at.get('component_instance'))

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

    def test_failed_primary_uses_targeted_donor_before_validation(self):
        empty=ForecastBatch((),(FetchFailure(POINT.key,'icon_eu','HTTP timeout'),))
        recovered=ForecastBatch((sources()[-1],),())
        with patch.object(self.grid,'forecast_locations',return_value=(POINT,)), \
             patch.object(self.grid.OpenMeteoClient,'fetch_batched',side_effect=(empty,recovered)) as fetch:
            batch=self.grid.regional_future(CYCLE,'icon_eu').result(timeout=10)
            self.assertEqual(soaring_coverage(batch.forecasts[0],START.date()),(8,)*5)
            self.assertEqual(fetch.call_count,2)
            self.assertEqual(fetch.call_args.args[0],(POINT,))
            self.assertEqual(fetch.call_args.args[1],(FORECAST_MODELS['ifs025'],))


if __name__=='__main__':unittest.main()
