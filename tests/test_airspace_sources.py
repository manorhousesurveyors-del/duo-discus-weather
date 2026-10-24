from datetime import date
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from shapely.geometry import shape

import airspace
from config import GeoPoint
from scripts.update_airspace import boundary_ring,compact_coordinate,convert_yaixm,radius_km


class AirspaceSourceTests(unittest.TestCase):
    def test_compact_coordinates_circle_and_arc_preserve_endpoints(self):
        self.assertEqual(compact_coordinate('523000N 0023000W'),(52.5,-2.5))
        self.assertAlmostEqual(radius_km('2 nm'),3.704)
        ring=boundary_ring([{'circle':{'centre':'523000N 0023000W','radius':'2 nm'}}])
        self.assertEqual(len(ring),121);self.assertEqual(ring[0],ring[-1])
        arc=boundary_ring([{'line':['523000N 0022900W']},
            {'arc':{'centre':'523000N 0023000W','dir':'cw','radius':'1 nm','to':'522900N 0023000W'}},
            {'line':['523000N 0023000W']}])
        self.assertIn([-2.5,52.483333],arc)
        for bad in ('526000N 0023000W','52.5 -2.5','bad'):
            with self.assertRaises(ValueError):compact_coordinate(bad)
        with self.assertRaises(ValueError):boundary_ring([{'corridor':{}}])

    def test_conversion_preserves_limits_and_does_not_apply_gliding_concessions(self):
        shape={'lower':'SFC','upper':'FL100','boundary':[{'circle':{'centre':'523000N 0023000W','radius':'2 nm'}}]}
        body={'airspace':[{'name':'Danger area','type':'D','geometry':[shape]},
                          {'name':'Gliding concession','type':'D_OTHER','localtype':'GLIDER','geometry':[shape]}],
              'rat':[{'name':'Temporary restriction','type':'R','geometry':[shape]}]}
        result=convert_yaixm(body,effective_date='2026-10-01',source='test',source_commit='abc',source_hashes={})
        self.assertEqual(len(result['features']),2);self.assertEqual(result['excluded_nonrestriction_entries'],1)
        self.assertEqual(result['valid_until'],'2026-10-28')
        self.assertEqual(result['features'][0]['properties']['floor'],'SFC')
        self.assertIn('verify',result['features'][1]['properties']['activation'])

    def test_bundled_current_and_upcoming_releases_select_by_forecast_date(self):
        with patch.object(airspace,'LOCAL_PATH',Path('/tmp/nonexistent-duo-airspace-upload.geojson')):
            current=airspace.load_airspace(date(2026,10,24));upcoming=airspace.load_airspace(date(2026,10,29))
        self.assertEqual(current['effective_date'],'2026-10-01')
        self.assertEqual(upcoming['effective_date'],'2026-10-29')
        for body,day in ((current,date(2026,10,24)),(upcoming,date(2026,10,29))):
            self.assertGreater(len(body['features']),700)
            self.assertRegex(body['source_commit'],r'^[a-f0-9]{40}$')
            self.assertTrue(all(len(value)==64 for value in body['source_sha256'].values()))
            self.assertIsNone(airspace.airspace_date_warning(body,day))
            self.assertTrue(all(f['bbox'][0]<f['bbox'][2] and f['bbox'][1]<f['bbox'][3] for f in body['features']))
            self.assertTrue(all(shape(f['geometry']).is_valid for f in body['features']))
        self.assertIn('outside',airspace.airspace_date_warning(current,date(2026,10,29)))
        self.assertIn('unavailable',airspace.airspace_date_warning({},date(2026,10,24)))

    def test_verified_heathrow_track_hits_london_ctr_and_distant_track_does_not(self):
        body=airspace.validate_geojson(json.loads(airspace.DEFAULT_PATH.read_text()))
        hits=airspace.corridor_intersections(GeoPoint('a','A',51.45,-.6),GeoPoint('b','B',51.49,-.3),body,0)
        self.assertTrue(any('LONDON CTR' in h['name'] for h in hits))
        self.assertFalse(airspace.corridor_intersections(GeoPoint('c','C',40,-20),GeoPoint('d','D',41,-19),body))


if __name__=='__main__':unittest.main()
