"""Optional point probe: strict opt-in, cached physics and honest missing data."""
from datetime import date, timedelta
from pathlib import Path
from dataclasses import replace
import json
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from tests.fixtures import NOW, forecast
from config import FORECAST_MODELS, HOURLY_VARIABLES, forecast_locations
from app_cache import forecast_run_id
from soaring_physics import Profile, evaluate_thermals
import point_forecast_meteogram as probe


class PointDataTests(unittest.TestCase):
    def tearDown(self):probe.prepare_bundle.clear()

    def test_two_local_days_half_hour_columns_and_dst(self):
        for day in (date(2026,3,28),date(2026,10,24)):
            data=probe.prepare_bundle((),day,_forecasts=())
            self.assertEqual(len(data['columns']),24)
            for rows in (data['columns'][:12],data['columns'][12:]):
                self.assertEqual((rows[0]['label'],rows[-1]['label']),('07:30','18:30'))
                self.assertEqual(len({r['day'] for r in rows}),1)
            self.assertNotEqual(data['columns'][0]['zone'],data['columns'][12]['zone'])
            self.assertNotEqual(data['columns'][0]['iso'][-6:],data['columns'][12]['iso'][-6:])

    def test_thermal_physics_units_and_high_levels(self):
        source=forecast(changes={'pressure_msl':1011.,'wind_gusts_10m':20.,
            'geopotential_height_250hPa':10400.,'temperature_250hPa':-50.,
            'relative_humidity_250hPa':87.,'wind_speed_250hPa':30.,'wind_direction_250hPa':270.,
            'geopotential_height_200hPa':12000.,'temperature_200hPa':-56.})
        row=probe.hour_sample(source,NOW);values=dict(zip(probe.FIELDS,row['s']))
        thermal=evaluate_thermals(Profile.from_forecast(source,NOW))
        self.assertAlmostEqual(values['climb'],thermal.net_climb_knots,places=3)
        self.assertAlmostEqual(values['gust'],20*1.943844,places=3)
        self.assertEqual(values['mslp'],1011.)
        level=row['p'][probe.LEVELS.index(250)]
        self.assertAlmostEqual(level[0],10400*3.280839895,places=2)
        self.assertEqual(level[1],87.)
        self.assertAlmostEqual(level[2],30.)
        self.assertGreater(row['p'][-1][0],38000)
        self.assertIn('relative_humidity_250hPa',HOURLY_VARIABLES)
        self.assertIn('geopotential_height_200hPa',HOURLY_VARIABLES)

    def test_missing_hours_and_levels_are_null_not_false_clear_sky(self):
        source=forecast(hours=1,changes={'wind_gusts_10m':None,'pressure_msl':None})
        missing=probe.hour_sample(source,NOW+timedelta(hours=1))
        self.assertTrue(all(x is None for x in missing['s']))
        self.assertTrue(all(x is None for p in missing['p'] for x in p))
        row=probe.hour_sample(source,NOW)
        self.assertEqual(row['p'][probe.LEVELS.index(250)],[None]*5)
        self.assertIsNone(row['s'][probe.FIELDS.index('gust')])
        json.dumps(row,allow_nan=False)

    def test_preparation_reuses_bundle_and_rejects_model_blending(self):
        source=forecast(start=NOW.replace(hour=0));forecasts=(source,)
        with patch.object(probe,'hour_sample',wraps=probe.hour_sample) as compute:
            first=probe.prepare_bundle(forecast_run_id(forecasts),NOW.date(),_forecasts=forecasts)
            for _ in range(4):self.assertIs(first,probe.prepare_bundle(forecast_run_id(forecasts),NOW.date(),_forecasts=forecasts))
            self.assertEqual(compute.call_count,26)
            probe.prepare_bundle(forecast_run_id(forecasts),NOW.date()+timedelta(days=1),_forecasts=forecasts)
            self.assertEqual(compute.call_count,52)
        other=replace(source,model=FORECAST_MODELS['icon_eu'].api_name)
        with self.assertRaisesRegex(ValueError,'single named'):
            probe.prepare_bundle(('different',),NOW.date(),_forecasts=(source,other))

    def test_cached_grid_from_before_upper_air_upgrade_stays_usable(self):
        from concurrent.futures import Future
        from uk_map_grid import assemble,grid_variables
        source=forecast()
        source=replace(source,hourly={k:v for k,v in source.hourly.items() if not k.endswith(('250hPa','200hPa'))})
        pending=Future();pending.set_result((source,))
        data=assemble((pending,),((source.location,),),FORECAST_MODELS['ifs025'],NOW)
        self.assertEqual(data.values.shape[2],len(grid_variables()))
        import numpy as np
        self.assertTrue(np.isnan(data.hour(NOW)[0,grid_variables().index('relative_humidity_250hPa')]))

    def test_profile_contract_works_with_cached_older_pressure_class(self):
        source=forecast(changes={'geopotential_height_250hPa':10400.,'temperature_250hPa':-50.,
            'relative_humidity_250hPa':87.,'wind_speed_250hPa':30.,'wind_direction_250hPa':270.})
        profile=Profile.from_forecast(source,NOW)
        old=replace(profile,levels=tuple(p for p in profile.levels if p.pressure_hpa>250))
        self.assertEqual(probe.pressure_sample(old,250),probe.pressure_sample(profile,250))

    def test_observations_keep_provenance_and_missing_data_is_not_a_forecast(self):
        self.assertEqual(probe.observation_payload(None),[])
        report=SimpleNamespace(station='EGOS',latitude=52.8,longitude=-2.7,
            temperature_c=12.3,observed_at=NOW,stale=True,raw={'name':'Shawbury'})
        missing=SimpleNamespace(**{**vars(report),'temperature_c':None})
        invalid=SimpleNamespace(**{**vars(report),'latitude':float('nan')})
        aviation=SimpleNamespace(metars={'EGOS':report,'NULL':missing,'BAD':invalid})
        values=probe.observation_payload(aviation)
        self.assertEqual(len(values),1)
        self.assertEqual(values[0],{'station':'EGOS','name':'Shawbury','lat':52.8,
            'lon':-2.7,'time':NOW.isoformat(),'temperature':12.3,'stale':True})
        with patch('client_map.script') as attach:
            probe.add_probe(object(),(),NOW.date(),aviation=aviation)
            self.assertEqual(attach.call_args.args[-1]['observations'],values)
            probe.add_probe(object(),(),NOW.date())
            self.assertEqual(attach.call_args.args[-1]['observations'],[])
        self.assertNotIn('observations',probe.prepare_bundle((),NOW.date(),_forecasts=()))


class PointIsolationTests(unittest.TestCase):
    def test_enabled_probe_refreshes_its_old_optional_api_after_deployment(self):
        from streamlit.testing.v1 import AppTest
        from alerts import ForecastBundle,save_bundle
        import app
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'weather.json'
            save_bundle(ForecastBundle(tuple(forecast(p) for p in forecast_locations()),(),.5,(),NOW),path)
            with patch.dict('os.environ',{'DUO_DASHBOARD_SNAPSHOT':str(path),'DUO_TASK_STORE':tmp+'/tasks'}), \
                 patch.object(probe,'PROBE_API_REVISION',1):
                at=AppTest.from_file(str(Path(app.__file__)),default_timeout=90).run()
                self.assertEqual(probe.PROBE_API_REVISION,1)  # Off remains dormant.
                at.toggle(key='point_probe_enabled').set_value(True).run()
                self.assertFalse(at.exception)
                self.assertEqual(probe.PROBE_API_REVISION,4)
                self.assertFalse(any('temporarily unavailable' in w.value for w in at.warning))

    def test_off_never_imports_probe_other_pages_reset_and_import_failure_is_local(self):
        from streamlit.testing.v1 import AppTest
        from alerts import ForecastBundle,save_bundle
        import app
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'weather.json'
            save_bundle(ForecastBundle(tuple(forecast(p) for p in forecast_locations()),(),.5,(),NOW),path)
            with patch.dict('os.environ',{'DUO_DASHBOARD_SNAPSHOT':str(path),'DUO_TASK_STORE':tmp+'/tasks'}), \
                 patch.dict('sys.modules',{'point_forecast_meteogram':None}):
                at=AppTest.from_file(str(Path(app.__file__)),default_timeout=90).run()
                self.assertFalse(at.exception)
                self.assertFalse(at.toggle(key='point_probe_enabled').value)
                self.assertFalse(any('temporarily unavailable' in w.value for w in at.warning))
                at.toggle(key='point_probe_enabled').set_value(True).run()
                self.assertFalse(at.exception)
                self.assertTrue(any('Point Forecast Probe is temporarily unavailable' in w.value for w in at.warning))
                for page in ('Help Guide','Help & Briefing Bot','Data quality','Task planner','Wave watch','Live OGN Glider Radar'):
                    at.radio(key='active_page').set_value(page).run()
                    self.assertFalse(at.exception)
                    self.assertFalse(any(t.key=='point_probe_enabled' for t in at.toggle))
                    self.assertFalse(at.session_state['point_probe_enabled'])
                at.radio(key='active_page').set_value('Weather map').run()
                self.assertFalse(at.toggle(key='point_probe_enabled').value)
                self.assertFalse(at.exception)

    def test_module_has_no_transport_dependencies(self):
        import ast
        tree=ast.parse(Path(probe.__file__).read_text())
        modules={n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)}
        self.assertFalse(modules & {'requests','urllib.request','uk_map_grid','data_ingestion'})
