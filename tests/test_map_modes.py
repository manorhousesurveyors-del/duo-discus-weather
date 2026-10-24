from datetime import datetime, UTC, timedelta
import json
import math
import unittest
from unittest.mock import patch

from config import GeoPoint
from tests.fixtures import forecast, NOW
from soaring_physics import Profile
from map_weather import (wind_vector,wind_payload,soaring_samples,contour_features,point_values,national_grid)
from map_modes import add_map_modes
from radar_rates import validate_grid,rate_at,rate_overlay
from data_ingestion import IngestionError
import folium


class WindMapTests(unittest.TestCase):
    def test_surface_and_pressure_winds_preserve_units_and_direction(self):
        p=Profile.from_forecast(forecast(),NOW)
        surface=wind_vector(p,0)
        self.assertAlmostEqual(surface[0],10);self.assertAlmostEqual(surface[1],0)
        v=wind_vector(p,800)
        self.assertTrue(v[2]);self.assertAlmostEqual(v[0],10)
        self.assertIsNone(wind_vector(p,400))

    def test_missing_and_below_terrain_pressure_levels_stay_missing(self):
        p=Profile.from_forecast(forecast(changes={'wind_speed_10m':None,'geopotential_height_950hPa':50}),NOW)
        self.assertIsNone(wind_vector(p,0))
        # Interpolation is permitted only when neighbouring atmospheric levels bracket it.
        self.assertTrue(wind_vector(p,950)[2])
        p=Profile.from_forecast(forecast(changes={'geopotential_height_500hPa':50}),NOW)
        self.assertIsNone(wind_vector(p,500))

    def test_five_day_hourly_axis_has_no_dst_duplicates_and_short_horizon_is_null(self):
        anchor=datetime(2026,10,25,12,tzinfo=UTC)
        f=forecast(start=anchor,hours=24)
        payload=wind_payload((f,),anchor)
        times=[datetime.fromisoformat(t) for t in payload['times']]
        self.assertEqual(len(times),120)
        self.assertTrue(all(b.astimezone(UTC)-a.astimezone(UTC)==timedelta(hours=1) for a,b in zip(times,times[1:])))
        self.assertTrue(all(v is None for v in payload['stations'][0]['values'][-1]))
        self.assertEqual(len(payload['levels']),6)
        json.dumps(payload,allow_nan=False)

    def test_national_land_samples_cover_scotland_wales_and_northern_ireland(self):
        points=national_grid()
        self.assertLess(len(points),100);self.assertGreater(max(p.latitude for p in points),58)
        self.assertTrue(any(54<p.latitude<56 and p.longitude<-5.5 for p in points))

    def test_layer_control_is_collapsed_and_js_payload_escapes_html(self):
        m=folium.Map();add_map_modes(m,(),NOW,NOW,'dry',mode='wind')
        html=m.get_root().render()
        self.assertIn('aria-expanded="false"',html)
        self.assertIn('requestAnimationFrame',html)
        self.assertIn('prefers-reduced-motion',html)
        self.assertIn('Five-day hourly wind timeline',html)
        self.assertIn('Windy-style Wind Stream',html)


class ContourTests(unittest.TestCase):
    def setUp(self):
        self.samples=[{'lat':lat,'lon':lon,'pfd':value,'thermal':value/80,'xc':value/4,'stf':value/5}
                      for lat,lon,value in ((52,-3,100),(52,-1,300),(54,-3,400),(54,-1,500))]

    def test_isobands_have_closed_rings_with_black_outline_properties(self):
        features=contour_features(self.samples,'pfd')['features']
        self.assertTrue(features)
        for feature in features:
            self.assertEqual(feature['geometry']['type'],'Polygon')
            for ring in feature['geometry']['coordinates']:
                self.assertEqual(ring[0],ring[-1]);self.assertGreaterEqual(len(ring),4)
            self.assertTrue(feature['properties']['colour'].startswith('#'))
            self.assertLess(feature['properties']['low'],feature['properties']['high'])

    def test_zero_unknown_and_collinear_fields_have_no_filled_polygons(self):
        for value in (0,None):
            samples=[{**sample,'pfd':value} for sample in self.samples]
            self.assertFalse(contour_features(samples,'pfd')['features'])
        self.assertFalse(contour_features([self.samples[0]],'pfd')['features'])
        self.assertIsNone(point_values(self.samples,60,-8))
        self.assertFalse(contour_features([{**s,'xc':0} for s in self.samples],'thermal')['features'])
        with self.assertRaises(ValueError):contour_features(self.samples,'fake')

    def test_loading_changes_stf_and_rain_blocks_speed_and_distance(self):
        f=forecast(start=NOW.replace(hour=8),hours=12)
        dry=soaring_samples((f,),NOW,'dry')[0];wet=soaring_samples((f,),NOW,'ballasted')[0]
        self.assertGreater(dry['xc'],0);self.assertGreater(wet['stf'],dry['stf'])
        rain=forecast(start=NOW.replace(hour=8),hours=12,changes={'precipitation':2})
        sample=soaring_samples((rain,),NOW,'dry')[0]
        self.assertEqual(sample['pfd'],0);self.assertEqual(sample['xc'],0);self.assertEqual(sample['stf'],0)


class RadarRateTests(unittest.TestCase):
    def setUp(self):
        self.grid={'schema_version':1,'units':'mm/h','observed_at':NOW.isoformat(),'source':'Test calibrated observations',
                   'bounds':[[50,-5],[54,1]],'values':[[0,.6,None],[3,12,200]]}

    def test_numeric_rate_and_missing_coverage_are_distinct(self):
        grid=validate_grid(self.grid,now=NOW)
        self.assertEqual(rate_at(grid,50,-5),0)
        self.assertEqual(rate_at(grid,54,1),200)
        self.assertIsNone(rate_at(grid,51,.9));self.assertIsNone(rate_at(grid,60,0))
        self.assertTrue(rate_overlay(grid)['image'].startswith('data:image/png;base64,'))

    def test_rejects_unverified_units_future_time_invalid_values_and_geometry(self):
        invalid=[{**self.grid,'units':'dBZ'},{**self.grid,'observed_at':(NOW+timedelta(hours=1)).isoformat()},
                 {**self.grid,'values':[[0,-1],[2,3]]},{**self.grid,'values':[[0,float('nan')],[2,3]]},
                 {**self.grid,'values':[[0],[1,2]]},{**self.grid,'bounds':[[54,-5],[50,1]]}]
        for grid in invalid:
            with self.subTest(grid=grid),self.assertRaises(IngestionError):validate_grid(grid,now=NOW)
