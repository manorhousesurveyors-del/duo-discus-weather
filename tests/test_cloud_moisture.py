from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import Mock

from atmospheric_moisture import dewpoint,relative_humidity,ice_relative_humidity
from config import FORECAST_MODELS,HOURLY_VARIABLES,PRESSURE_LEVELS_HPA,Settings
from data_ingestion import OpenMeteoClient,JsonResponse
from forecast_coverage import complete_forecasts
from point_forecast_meteogram import hour_sample,pressure_sample,PRESSURE_FIELDS,LEVELS,FIELDS
from soaring_physics import Profile
from tests.fixtures import forecast,NOW,alter


class CloudMoistureTests(unittest.TestCase):
    def test_magnus_conversions_ice_supersaturation_and_invalid_evidence(self):
        self.assertAlmostEqual(relative_humidity(20,10),52.54,delta=.1)
        self.assertAlmostEqual(dewpoint(20,relative_humidity(20,10)),10,places=6)
        td=dewpoint(-30,80)
        self.assertGreater(relative_humidity(-30,td,ice=True),100)
        self.assertAlmostEqual(ice_relative_humidity(-30,80),relative_humidity(-30,td,ice=True))
        self.assertEqual(ice_relative_humidity(-30,0),0)
        for t,d in ((None,4),(10,None),(-240,-240),(float('nan'),0)):
            self.assertIsNone(relative_humidity(t,d))
        self.assertIsNone(dewpoint(20,0));self.assertIsNone(dewpoint(20,110))

    def test_every_model_requests_the_full_moisture_schema(self):
        expected={'cloud_cover','cloud_cover_low','cloud_cover_mid','cloud_cover_high','cape','lifted_index','precipitation','showers'}
        for p in (950,925,900,850,800,700,600,500,400,300,250):
            expected.update(f'{name}_{p}hPa' for name in ('temperature','dew_point','relative_humidity','cloud_cover'))
        self.assertTrue(expected<=set(HOURLY_VARIABLES))
        for model in FORECAST_MODELS.values():
            f=forecast(model=model.api_name,hours=24)
            body={'latitude':f.model_latitude,'longitude':f.model_longitude,'elevation':100,'utc_offset_seconds':0,
                  'hourly_units':{'time':'unixtime',**f.units},'hourly':{'time':[int(t.timestamp()) for t in f.times],**{k:list(v) for k,v in f.hourly.items()}}}
            http=Mock();http.get_json.return_value=JsonResponse(body,NOW)
            client=OpenMeteoClient(Settings(),http=http);client.fetch(f.location,model)
            self.assertTrue(expected<=set(http.get_json.call_args.args[1]['hourly'].split(',')))
            client.fetch_batched((f.location,),(model,))
            self.assertTrue(expected<=set(http.get_json.call_args.args[1]['hourly'].split(',')))

    def test_native_dewpoint_supplies_missing_rh_before_model_backfill(self):
        uk=forecast(model='ukmo_seamless',changes={'temperature_850hPa':10,'dew_point_850hPa':9,'relative_humidity_850hPa':None})
        self.assertAlmostEqual(uk.hourly['relative_humidity_850hPa'][0],93.51,delta=.1)
        self.assertIn('Magnus RH',uk.field_sources['relative_humidity_850hPa'][0])
        ec=forecast(changes={'relative_humidity_850hPa':50})
        completed=complete_forecasts((uk,ec),FORECAST_MODELS['ukmo'])[0]
        self.assertEqual(completed.hourly['relative_humidity_850hPa'][0],uk.hourly['relative_humidity_850hPa'][0])
        # A genuine supplied zero must not be replaced with saturation.
        zero=forecast(changes={'relative_humidity_850hPa':0,'dew_point_850hPa':9})
        self.assertEqual(zero.hourly['relative_humidity_850hPa'][0],0)

    def test_hourly_backfill_restores_vertical_moisture_and_labels_source(self):
        primary=forecast(model='icon_eu',changes={'relative_humidity_700hPa':None,'dew_point_700hPa':None,'showers':None})
        donor=forecast(changes={'relative_humidity_700hPa':92,'showers':.7})
        f=complete_forecasts((primary,donor),FORECAST_MODELS['icon_eu'])[0]
        row=hour_sample(f,NOW);p=dict(zip(PRESSURE_FIELDS,row['p'][LEVELS.index(700)]))
        self.assertEqual(p['rh'],92);self.assertIsNotNone(p['dewpoint']);self.assertIsNotNone(p['temperature'])
        self.assertEqual(dict(zip(FIELDS,row['s']))['showers'],.7)
        self.assertEqual(f.field_sources['relative_humidity_700hPa'][0],'ecmwf_ifs025')
        missing=complete_forecasts((primary,),FORECAST_MODELS['icon_eu'])[0]
        self.assertIsNone(missing.hourly['relative_humidity_700hPa'][0])

    def test_900_hpa_moisture_is_interpolated_and_upper_ice_is_available(self):
        f=forecast(changes={'temperature_250hPa':-48,'geopotential_height_250hPa':10400,'relative_humidity_250hPa':85})
        p=Profile.from_forecast(f,NOW)
        sample=pressure_sample(p,900)
        self.assertEqual(sample[1],70);self.assertIsNotNone(sample[5]);self.assertIsNotNone(sample[6])
        self.assertGreater(pressure_sample(p,250)[7],100)
        self.assertTrue(set((950,925,900,850,800,700,600,500,400,300,250))<=set(LEVELS))

    @unittest.skipUnless(shutil.which('node'),'Node is needed for production JavaScript classifier')
    def test_production_cloud_classifier_and_threshold_boundaries(self):
        result=subprocess.run(['node','tests/cloud_classification.cjs'],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['cloud_families'],5)


if __name__=='__main__':unittest.main()
