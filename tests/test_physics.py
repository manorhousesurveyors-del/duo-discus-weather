from dataclasses import replace
from datetime import timedelta
import math
import unittest
from unittest.mock import patch

import numpy as np

from config import DUO_DISCUS_POLARS, LOCAL_TIMEZONE, MONITORING_ZONES, ROUTES, WAVE_RIDGES
from soaring_physics import (EARTH_RADIUS_M, G, RD, Profile, angular_difference,
    boundary_layer_height, convergence_fields, cross_country_speed, evaluate_task_day,
    evaluate_thermals, evaluate_wave, maccready_speed, vertical_velocity_mps, wind_components, wind_direction)
from tests.fixtures import NOW, alter, forecast


class PhysicsTests(unittest.TestCase):
    def setUp(self):
        self.forecast=forecast()

    def profile(self, **changes):
        return Profile.from_forecast(alter(self.forecast,**changes),NOW)

    def test_vertical_velocity_units_density_and_sign(self):
        density=85000/(RD*283.15)
        self.assertAlmostEqual(vertical_velocity_mps(-1,'Pa/s',850,10),1/(density*G))
        self.assertAlmostEqual(vertical_velocity_mps(-.01,'hPa/s',850,10),1/(density*G))
        self.assertEqual(vertical_velocity_mps(.23,'m/s',850,10),.23)
        with self.assertRaises(ValueError): vertical_velocity_mps(1,'knots',850,10)

    def test_vector_interpolation_handles_north_wrap(self):
        p=self.profile(wind_direction_925hPa=350.,wind_direction_850hPa=10.)
        level=p.level(900)
        self.assertTrue(level.interpolated)
        self.assertLess(angular_difference(wind_direction(level.u_mps,level.v_mps),0),10)
        self.assertIsNone(p.level(250))
        self.assertEqual(wind_components(10,270)[0],10)

    def test_subterrain_and_inverted_pressure_order_excluded_or_rejected(self):
        self.assertNotIn(1000,[l.pressure_hpa for l in self.profile(geopotential_height_1000hPa=50).levels])
        with self.assertRaises(ValueError): self.profile(geopotential_height_925hPa=2000)

    def test_parcel_mixing_top_must_be_bracketed(self):
        depth,source=boundary_layer_height(self.profile(boundary_layer_height=None))
        self.assertGreater(depth,0)
        self.assertLess(depth,5500)
        self.assertIn('estimated',source)
        depth,source=boundary_layer_height(self.profile(boundary_layer_height=None,temperature_2m=70))
        self.assertIsNone(depth)
        self.assertIn('no extrapolation',source)

    def test_hennig_height_and_crosscheck_use_agl_and_amsl(self):
        result=evaluate_thermals(self.profile())
        self.assertAlmostEqual(result.cloud_base_ft_amsl,16*400+100/.3048)
        self.assertTrue(result.alert_eligible)
        shallow=evaluate_thermals(self.profile(boundary_layer_height=500))
        self.assertAlmostEqual(shallow.usable_top_ft_amsl,600/.3048)
        self.assertFalse(shallow.cumulus_supported)
        self.assertFalse(shallow.alert_eligible)

    def test_soil_damping_and_conservative_missing_moisture(self):
        dry=evaluate_thermals(self.profile())
        wet=evaluate_thermals(self.profile(soil_moisture_0_to_7cm=.4))
        missing=evaluate_thermals(self.profile(soil_moisture_0_to_7cm=None))
        self.assertGreater(dry.net_climb_knots,wet.net_climb_knots)
        self.assertGreater(missing.net_climb_knots,missing.conservative_climb_knots)
        self.assertAlmostEqual(missing.conservative_climb_knots,wet.net_climb_knots)
        self.assertIsNone(evaluate_thermals(self.profile(direct_radiation=None)).net_climb_knots)
        self.assertEqual(evaluate_thermals(self.profile(direct_radiation=0)).net_climb_knots,-1.6)

    def test_overdevelopment_and_cloud_street_thresholds(self):
        t=evaluate_thermals(self.profile(relative_humidity_800hPa=86,cloud_cover_low=61))
        self.assertTrue(t.overdevelopment)
        self.assertTrue(t.cloud_streets)
        self.assertFalse(evaluate_thermals(self.profile(wind_direction_10m=240)).cloud_streets)
        self.assertFalse(evaluate_thermals(self.profile(relative_humidity_800hPa=85,cloud_cover_low=61)).overdevelopment)

    def test_wave_missing_motion_and_failed_core_cannot_alert(self):
        ridge=WAVE_RIDGES['malvern']
        full=evaluate_wave(self.profile(),ridge)
        self.assertTrue(full.complete)
        self.assertGreater(full.brunt_vaisala_n2,0)
        incomplete=evaluate_wave(self.profile(vertical_velocity_700hPa=None),ridge)
        self.assertFalse(incomplete.complete)
        failed=evaluate_wave(self.profile(wind_direction_500hPa=180),ridge)
        self.assertEqual(failed.components['directional_shear'],0)
        self.assertLessEqual(failed.score,70)

    def test_maccready_stationary_optimum_and_fit_bounds(self):
        polar=DUO_DISCUS_POLARS['dry']
        speed,limited=maccready_speed(1,polar)
        self.assertFalse(limited)
        self.assertAlmostEqual(speed*(2*polar.a*speed+polar.b)-polar.sink_mps(speed),1)
        speed,limited=maccready_speed(30,polar)
        self.assertTrue(limited)
        self.assertEqual(speed,polar.max_fit_speed_mps)
        with self.assertRaises(ValueError): maccready_speed(-1,polar)

    def test_groundspeed_headwind_crosswind_and_no_lift(self):
        p=DUO_DISCUS_POLARS['dry']
        still=cross_country_speed(2,90,0,0,p)[0]
        self.assertAlmostEqual(cross_country_speed(2,90,5,0,p)[0],still+18)
        self.assertLess(cross_country_speed(2,90,0,5,p)[0],still)
        self.assertEqual(cross_country_speed(2,90,0,100,p)[0],0)
        self.assertEqual(cross_country_speed(0,90,30,0,p)[0],0)

    def task_forecasts(self):
        return [forecast(point,start=NOW.replace(hour=0)) for point in MONITORING_ZONES.values()]

    def test_task_uses_eight_intervals_and_local_summer_time(self):
        with patch('soaring_physics.cross_country_speed',return_value=(90,30,False)):
            result=evaluate_task_day(ROUTES['A'],self.task_forecasts(),NOW.date())
        self.assertEqual(len(result.hourly_legs),27)
        self.assertEqual(result.hourly_legs[0].valid_at.hour,9)
        self.assertEqual(result.viability[500],'supported')
        self.assertEqual(result.viability[750],'not_supported')  # 8*90, not 9*90.

    def test_task_does_not_sum_across_missing_midday_weather(self):
        values=[forecast(p,start=NOW.replace(hour=0),changes={'direct_radiation':
                 [None if i%24==13 else 1000 for i in range(145)]}) for p in MONITORING_ZONES.values()]
        with patch('soaring_physics.cross_country_speed',return_value=(90,30,False)):
            result=evaluate_task_day(ROUTES['A'],values,NOW.date())
        self.assertEqual(result.viability[500],'insufficient_data')
        self.assertEqual(result.viability[250],'supported')

    def test_spherical_derivatives_use_metric_distances(self):
        lat=np.array([51.,52.,53.,54.,55.]); lon=np.array([-4.,-3.,-2.,-1.,0.])
        x=EARTH_RADIUS_M*np.cos(np.radians(lat))[:,None]*np.radians(lon)[None,:]
        u=-4e-5*x; zero=np.zeros_like(u); dew=x*2/100000
        convergence,gradient=convergence_fields(lat,lon,u,zero,dew)
        np.testing.assert_allclose(convergence,4e-5,atol=1e-12)
        self.assertTrue(np.all(gradient>=2-1e-12))
        u[2,2]=np.nan
        missing,_=convergence_fields(lat,lon,u,zero,dew)
        self.assertTrue(np.isnan(missing[2,1]))
        with self.assertRaises(ValueError): convergence_fields(lat[::-1],lon,u,zero,dew)
