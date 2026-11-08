from dataclasses import replace
from unittest import TestCase
import json
import math
import numpy as np
from map_weather import WIND_LEVELS, national_grid, wind_payload
from tests.fixtures import forecast, NOW

NOW=NOW.replace(month=11,day=8)
PROBES=((50.5,-3.5),(52.5,-2.9),(54.5,-2.),(57.2,-3.8),(53.3,-7.5))


class FullDomainTests(TestCase):
    def test_missing_north_ireland_and_individual_nodes_filled_all_hours_and_levels(self):
        points=national_grid()
        # Reproduce truncated north/west chunks and an internal missing node.
        sources=[forecast(p,start=NOW.replace(hour=0),hours=120) for i,p in enumerate(points)
                 if p.latitude<56 and p.longitude>-7 and i%7]
        payload=wind_payload(sources,NOW)
        grid=payload['grid'];stations=payload['stations']
        self.assertEqual((grid['ny'],grid['nx']),(12,14))
        self.assertEqual((grid['latMin'],grid['latMax'],grid['lonMin'],grid['lonMax']),(49.5,61,-11,2.5))
        for row in grid['indices']:
            for index in row:
                values=np.asarray(stations[index]['values'])
                self.assertEqual(values.shape,(120,6,3))
                self.assertTrue(np.isfinite(values).all())
        for lat,lon in PROBES:
            row=min(10,int((lat-49.5)/11.5*11));col=min(12,int((lon+11)/13.5*13))
            for y,x in ((row,col),(row+1,col),(row,col+1),(row+1,col+1)):
                self.assertIsNotNone(stations[grid['indices'][y][x]]['values'][12][0])
        self.assertGreater(payload['coverage']['spatial_estimates'],0)
        self.assertGreater(payload['coverage']['max_nearest_source_km'],100)
        # Original native values survive; filled values are explicitly marked.
        self.assertEqual(stations[0]['values'][12][0][2]&4,0)
        self.assertTrue(stations[grid['indices'][-1][0]]['values'][12][0][2]&4)
        json.dumps(payload,allow_nan=False)

    def test_total_outage_and_missing_hour_never_become_invented_calm(self):
        for forecasts in ((),(forecast(national_grid()[0],start=NOW.replace(hour=0),hours=24),)):
            payload=wind_payload(forecasts,NOW)
            self.assertTrue(all(all(v is None for v in s['values'][-1]) for s in payload['stations']))
            self.assertGreater(payload['coverage']['unavailable_slices'],0)
            json.dumps(payload,allow_nan=False)

    def test_vector_fill_avoids_north_direction_wrap_and_ignores_nonfinite(self):
        from map_weather import fill_wind_grid
        nodes=[{'lat':52.,'lon':-3.,'values':[[[u,-10.,0]]*6]} for u in (-.1,.1)]
        nodes[1]['lat']=54.
        nodes.append({'lat':53.,'lon':-3.,'values':[[[float('nan'),1.,0]]*6]})
        fill_wind_grid(nodes)
        u,v,flag=nodes[2]['values'][0][0]
        self.assertAlmostEqual(u,0);self.assertAlmostEqual(v,-10);self.assertEqual(flag,4)
