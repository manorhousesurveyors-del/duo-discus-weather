"""Independent regression cases for the requested Duo thermal-core calculation."""
from dataclasses import replace
from datetime import UTC,datetime
from types import SimpleNamespace
import math
import unittest
from unittest.mock import Mock

from config import FORECAST_MODELS,Settings,duo_circling_sink_knots,MPS_TO_KNOTS
from data_ingestion import OpenMeteoClient,JsonResponse,forecast_from_snapshot
from soaring_physics import Profile,evaluate_thermals,G,RD,CP,thermal_usable_depth
from tests.fixtures import forecast,NOW
import point_forecast_meteogram as probe


def moderate_forecast(**changes):
    return forecast(start=datetime(2026,10,25,tzinfo=UTC),changes={
        'temperature_2m':8.,'dew_point_2m':4.,'surface_pressure':1000.,
        'shortwave_radiation':325.,'direct_radiation':0.,
        'boundary_layer_height':550.,'soil_moisture_0_to_7cm':None,
        'cloud_cover_low':70.,'cloud_cover_mid':50.,'cloud_cover_high':60.,
        'relative_humidity_950hPa':88.,'cloud_cover_950hPa':75.,
        'relative_humidity_850hPa':90.,'cloud_cover_850hPa':85.,**changes})


class ThermalRevisionTests(unittest.TestCase):
    def test_moderate_total_sun_produces_net_climb_and_height(self):
        for radiation,depth in ((300,500),(325,550),(350,600)):
            f=moderate_forecast(shortwave_radiation=radiation,boundary_layer_height=depth)
            t=evaluate_thermals(Profile.from_forecast(f,f.times[12]))
            self.assertGreaterEqual(t.net_climb_knots,1.5)
            self.assertLessEqual(t.net_climb_knots,2.3)
            self.assertGreater(t.usable_top_ft_amsl-f.terrain_elevation_m/.3048,1000)
            self.assertLessEqual(t.usable_top_ft_amsl,t.cloud_base_ft_amsl)
            self.assertLess(t.usable_top_ft_amsl,(depth+f.terrain_elevation_m)/.3048)
            # Independent energy/velocity calculation catches duplicate sink deductions.
            heat=radiation*.8*.7*(.55+.025*4)
            density=100000/(RD*281.15)
            core=1.35*(G/281.15*heat/(density*CP)*depth)**(1/3)*MPS_TO_KNOTS
            self.assertAlmostEqual(t.core_updraught_knots,core)
            self.assertEqual(t.net_climb_knots,max(0,round(core-duo_circling_sink_knots(),1)))

    def test_night_is_zero_missing_is_unknown_and_diffuse_sun_counts(self):
        for radiation in (0,1,5):
            f=moderate_forecast(shortwave_radiation=radiation)
            t=evaluate_thermals(Profile.from_forecast(f,f.times[12]))
            self.assertEqual(t.net_climb_knots,0)
            self.assertEqual(t.usable_top_ft_amsl,f.terrain_elevation_m/.3048)
        f=moderate_forecast(shortwave_radiation=None)
        t=evaluate_thermals(Profile.from_forecast(f,f.times[12]))
        self.assertIsNone(t.net_climb_knots);self.assertIsNone(t.usable_top_ft_amsl)
        f=moderate_forecast()
        self.assertGreater(evaluate_thermals(Profile.from_forecast(f,f.times[12])).net_climb_knots,0)

    def test_loading_changes_sink_once_and_zero_sink_top(self):
        f=moderate_forecast();p=Profile.from_forecast(f,f.times[12])
        dry,wet=(evaluate_thermals(p,loading) for loading in ('dry','ballasted'))
        self.assertAlmostEqual(dry.core_updraught_knots,wet.core_updraught_knots)
        self.assertTrue(1.35<=dry.circling_sink_knots<=1.45)
        self.assertGreater(wet.circling_sink_knots,dry.circling_sink_knots)
        self.assertLess(wet.net_climb_knots,dry.net_climb_knots)
        self.assertLessEqual(wet.usable_top_ft_amsl,dry.usable_top_ft_amsl)
        depth=thermal_usable_depth(600,3,1.4,10000)
        self.assertAlmostEqual(3*((1-depth/600)/.6)**(1/3),1.4)
        self.assertEqual(thermal_usable_depth(600,1.3,1.4,10000),0)

    def test_probe_preserves_core_cloud_cover_and_loading(self):
        f=moderate_forecast();when=f.times[12]
        row=probe.hour_sample(f,when);v=dict(zip(probe.FIELDS,row['s']))
        self.assertEqual(v['climb'],round(v['core']-v['sink'],1))
        self.assertEqual(row['p'][probe.LEVELS.index(850)][4],85)
        wet=dict(zip(probe.FIELDS,probe.hour_sample(f,when,'ballasted')['s']))
        self.assertGreater(wet['sink'],v['sink']);self.assertLess(wet['climb'],v['climb'])
        missing=replace(f,hourly={k:v for k,v in f.hourly.items() if not k.startswith('cloud_cover_') or k in ('cloud_cover_low','cloud_cover_mid','cloud_cover_high')})
        self.assertIsNone(probe.hour_sample(missing,when)['p'][probe.LEVELS.index(850)][4])

    def test_paired_request_keeps_model_values_separate(self):
        f=moderate_forecast();names=[FORECAST_MODELS[k].api_name for k in ('ukmo','ifs025')]
        body={'latitude':f.model_latitude,'longitude':f.model_longitude,'elevation':f.terrain_elevation_m,
              'utc_offset_seconds':0,'hourly':{'time':[int(t.timestamp()) for t in f.times]},'hourly_units':{'time':'unixtime'}}
        # Stay inside the five-day paired request window.
        body['hourly']['time']=body['hourly']['time'][:120]
        for name in names:
            body['hourly'].update({k+'_'+name:list(v[:120]) for k,v in f.hourly.items()})
            body['hourly_units'].update({k+'_'+name:v for k,v in f.units.items()})
        body['hourly']['temperature_2m_'+names[0]]=[6.]*120
        http=SimpleNamespace(get_json=Mock(return_value=JsonResponse(body,NOW)))
        client=OpenMeteoClient(Settings(),http=http)
        batch=client.fetch_paired((f.location,))
        self.assertFalse(batch.failures);self.assertEqual(len(batch.forecasts),2)
        params=http.get_json.call_args.args[1]
        self.assertEqual(params['models'],'ukmo_seamless,ecmwf_ifs025')
        self.assertEqual(params['cell_selection'],'land');self.assertEqual(params['forecast_hours'],120)
        values={p.model:p.hourly['temperature_2m'][0] for p in batch.forecasts}
        self.assertEqual(values,{names[0]:6.,names[1]:8.})
        data=probe.prepare_bundle(('paired-test',),f.times[0].date(),_forecasts=batch.forecasts)
        self.assertEqual([series['model'] for series in data['stations'][0]['series']],names)
        self.assertEqual(data['stations'][0]['series'][0]['hours'][0]['s'][0],6.)
        # A missing primary model still leaves an explicitly named fallback.
        body['hourly']['temperature_2m_'+names[0]]=[None]*120
        batch=client.fetch_paired((f.location,))
        self.assertEqual([f.model for f in batch.forecasts],[names[1]])
        self.assertEqual(len(batch.failures),1)

    def test_archived_ukmo_forecasts_still_load(self):
        from dataclasses import asdict
        f=forecast(model=FORECAST_MODELS['ukmo'].api_name,hours=48)
        record=asdict(f);record['model']='ukmo_uk_deterministic_2km'
        record['times']=[t.isoformat() for t in f.times];record['fetched_at']=f.fetched_at.isoformat()
        restored=forecast_from_snapshot(record)
        self.assertEqual(restored.model,record['model'])
        from soaring_physics import select_forecast
        self.assertIs(select_forecast((restored,),restored.location.key,restored.times[0],('ukmo_seamless',)),restored)
