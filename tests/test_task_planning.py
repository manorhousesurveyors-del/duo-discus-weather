"""Task geometry, hourly flight integration and portable-export contracts."""
from dataclasses import replace
from datetime import date, timedelta
import csv
import io
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from bga_turnpoints import Turnpoint, load_catalog, parse_turnpoints
from config import LOCAL_TIMEZONE
from soaring_physics import distance_bearing
import task_planning as t
from tests.fixtures import NOW, forecast

DAY=NOW.date()
START=NOW.replace(hour=9)
A=Turnpoint('AAA','Test start',52.5,-2.8,200,official=True)
B=Turnpoint('BBB','Test west',52.5,-4.6,100,official=True)
TASK=(A,B,A)


def weather(task=TASK,**changes):
    return tuple(forecast(p,start=START,hours=10,changes=changes) for p in t.corridor_points(task))


class TaskTests(unittest.TestCase):
    def test_geodesic_distance_and_corridor_spacing(self):
        distance=t.task_distance(TASK)
        self.assertAlmostEqual(distance,2*distance_bearing(A.point,B.point)[0])
        self.assertGreater(distance,240);self.assertLess(distance,250)
        for leg in t.sampled_legs(TASK):
            self.assertLessEqual(max(distance_bearing(a,b)[0] for a,b in zip(leg,leg[1:])),35.001)
        with self.assertRaises(ValueError): t.sampled_legs((A,A))
        with self.assertRaises(ValueError): t.sampled_legs(TASK,0)
        with self.assertRaises(ValueError): t.coordinate_turnpoint(0,0)

    def test_roles_reordering_and_task_limit(self):
        c=replace(B,code='CCC',longitude=-3.5)
        self.assertEqual(t.add_task_point((A,A),B,'Turnpoint'),TASK)
        self.assertEqual(t.add_task_point(TASK,c,'Start'),(c,B,A))
        self.assertEqual(t.add_task_point(TASK,c,'Finish'),(A,B,c))
        self.assertEqual(t.reorder_task((A,B,c),1,-1),(B,A,c))
        with self.assertRaises(ValueError):t.reorder_task(TASK,0,-1)
        with self.assertRaises(ValueError):t.add_task_point((A,)*20,B)

    def test_complete_corridor_produces_every_leg_hour_and_launch_window(self):
        result=t.assess_task(TASK,weather(),DAY)
        self.assertEqual(result.verdict,'FULLY ACHIEVABLE')
        self.assertEqual(len(result.hourly_legs),18)
        self.assertTrue(all(not row.missing for row in result.hourly_legs))
        self.assertEqual(result.launch_windows[0].earliest.astimezone(LOCAL_TIMEZONE).hour,10)
        self.assertLess(result.best.elapsed_hours,4)
        self.assertAlmostEqual(result.best.average_groundspeed_kmh,result.distance_km/result.best.elapsed_hours)
        self.assertGreater(result.best.average_maccready_kmh,result.best.average_groundspeed_kmh)
        self.assertEqual(result.best.weakest_leg,'AAA → BBB')
        ballasted=t.assess_task(TASK,weather(),DAY,'ballasted')
        self.assertNotEqual(result.best.average_maccready_kmh,ballasted.best.average_maccready_kmh)

    def test_missing_required_midpoint_never_becomes_achievable(self):
        forecasts=weather()
        middle=next(f.location.key for f in forecasts if f.location.key.startswith('corridor:'))
        result=t.assess_task(TASK,[f for f in forecasts if f.location.key!=middle],DAY)
        self.assertEqual(result.verdict,'INSUFFICIENT DATA');self.assertIsNone(result.best)
        self.assertTrue(any(row.missing for row in result.hourly_legs))

    def test_rain_and_low_usable_height_block_task(self):
        for changes in [dict(precipitation=2),dict(boundary_layer_height=300),dict(direct_radiation=0,shortwave_radiation=0)]:
            with self.subTest(changes=changes):
                result=t.assess_task(TASK,weather(**changes),DAY)
                self.assertEqual(result.verdict,'NOT ACHIEVABLE');self.assertIsNone(result.best)

    def test_no_interpolation_over_rain_gap_and_narrow_window_is_marginal(self):
        # A 244 km flight cannot cross the blocked fourth hour. Good hours after
        # it permit completion, but only a narrow late launch window remains.
        rain=[0,0,0,2,0,0,0,0,0,0]
        # Keep the gap-integration check independent of thermal recalibration.
        with patch('task_planning.cross_country_speed',return_value=(90.,40.,False)):
            result=t.assess_task(TASK,weather(precipitation=rain),DAY)
        self.assertIsNotNone(result.best)
        self.assertTrue(all(window.earliest>=START+timedelta(hours=4) or
                            window.latest<=START+timedelta(minutes=15) for window in result.launch_windows))
        self.assertTrue(all(row.groundspeed_kmh==0 for row in result.hourly_legs if row.valid_at==START+timedelta(hours=3)))
        late=[2]*5+[0]*5
        with patch('task_planning.cross_country_speed',return_value=(90.,40.,False)):
            result=t.assess_task(TASK,weather(precipitation=late),DAY)
        self.assertIn(result.verdict,('MARGINAL / NARROW WINDOW','NOT ACHIEVABLE'))

    def test_segment_integration_has_expected_elapsed_under_constant_speed(self):
        with patch('task_planning.cross_country_speed',return_value=(100.,40.,False)):
            result=t.assess_task(TASK,weather(),DAY)
        self.assertAlmostEqual(result.best.elapsed_hours,result.distance_km/100,places=6)
        self.assertAlmostEqual(result.best.average_maccready_kmh,144)
        self.assertTrue(all(row.groundspeed_kmh==100 for row in result.hourly_legs))

    def test_nearby_screening_is_labelled_and_cannot_be_full_verdict(self):
        approximate=t.nearby_forecasts(t.corridor_points(TASK),weather(),maximum_km=1)
        result=t.assess_task(TASK,approximate,DAY)
        self.assertEqual(result.verdict,'MARGINAL / NARROW WINDOW')
        self.assertIn('stored samples',' '.join(result.warnings))
        self.assertFalse(t.nearby_forecasts((B.point,),(forecast(A.point),),maximum_km=10))

    def test_day_classes_require_complete_local_weather_and_reserve(self):
        f=forecast(A.point,start=START,hours=10)
        for speed,label in [(80,'500+ km Day'),(45,'300 km Day'),(36,'250 km Day'),(20,'100 km Local Task Day'),(0,'Non-Cross-Country Day')]:
            with self.subTest(label=label),patch('task_planning.cross_country_speed',return_value=(speed,40,False)):
                result=t.classify_day((f,),A.point,DAY)
                self.assertEqual(result.label,label);self.assertAlmostEqual(result.distance_km,speed*8*.9)
        self.assertIsNone(t.classify_day((),A.point,DAY).distance_km)
        f=forecast(A.point,start=START,hours=10,changes={'precipitation':2})
        self.assertIn('Wave Only',t.classify_day((f,),A.point,DAY,wave_available=True).label)

    def test_real_catalog_candidates_have_requested_distance_and_closure(self):
        catalog=load_catalog();origin=catalog.by_code['MYN']
        for target in (250,300):
            candidates=t.candidate_tasks(catalog.turnpoints,origin,target)
            self.assertTrue(any(len(task)==3 for task in candidates))
            self.assertTrue(any(len(task)==4 for task in candidates))
            for task in candidates:
                self.assertEqual(task[0],task[-1]);self.assertTrue(all(p.official for p in task))
                self.assertTrue(target<=t.task_distance(task)<=1.08*target)

    def test_cup_and_xcsoar_export_order_escaping_and_rounding(self):
        a=replace(A,name='Start, <A> & "B"',latitude=52+59.9999/60)
        points=(a,B,a)
        cup=t.export_cup(points);parsed=parse_turnpoints(cup)
        self.assertEqual({p.code for p in parsed},{'AAA','BBB'})
        self.assertEqual(next(p for p in parsed if p.code=='AAA').latitude,53)
        rows=list(csv.reader(io.StringIO(cup)))
        self.assertEqual(rows[-1][1:],[a.name,a.name,B.name,a.name,a.name])
        tree=ET.fromstring(t.export_tsk(points))
        self.assertEqual([p.attrib['type'] for p in tree.findall('Point')],['Start','Turn','Finish'])
        self.assertEqual(tree.find('Point/Waypoint').attrib['name'],'AAA '+a.name)
        self.assertEqual(tree.findall('Point')[1].find('ObservationZone').attrib,{'type':'Cylinder','radius':'500'})

    def test_daily_task_persistence_and_clear_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict('os.environ',{'DUO_TASK_STORE':directory}):
            self.assertEqual(t.load_daily_task(DAY),())
            t.save_daily_task(DAY,TASK);t.save_daily_task(DAY+timedelta(days=1),(B,A))
            self.assertEqual(t.load_daily_task(DAY),TASK)
            t.save_daily_task(DAY,())
            self.assertEqual(t.load_daily_task(DAY),())
            self.assertEqual(t.load_daily_task(DAY+timedelta(days=1)),(B,A))

    def test_cup_disambiguates_imported_waypoints_with_identical_names(self):
        points=(A,replace(B,name=A.name),A)
        cup=t.export_cup(points)
        names=[p.name for p in parse_turnpoints(cup)]
        self.assertEqual(len(set(names)),2)
        self.assertTrue(all(name.endswith(A.name) for name in names))

    def test_risky_launch_hours_do_not_widen_a_short_clean_window(self):
        short=(A,replace(B,longitude=-3.05),A)
        rain=[.2]*5+[0]+[.2]*4
        result=t.assess_task(short,weather(short,precipitation=rain),DAY)
        self.assertIsNotNone(result.best)
        self.assertFalse(any(flag=='precipitation forecast' for flag in result.best.risk_flags))
        self.assertEqual(result.verdict,'MARGINAL / NARROW WINDOW')
