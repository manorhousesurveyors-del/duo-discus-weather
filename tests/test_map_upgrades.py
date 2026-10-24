import ast
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import shutil
import subprocess
import unittest
from unittest.mock import patch,Mock

import numpy as np
from config import WAVE_RIDGES,FORECAST_MODELS,GeoPoint
from tests.fixtures import NOW,forecast,alter
from tests.test_task_planning import A,B,TASK,DAY
from map_weather import national_grid,wind_vector,wind_payload,WIND_LATITUDES,WIND_LONGITUDES
from soaring_physics import Profile,wind_direction,angular_difference
from task_planning import export_cup,export_tsk,assess_task
from task_import import import_task
from uk_airfields import load_airfields,reporting_stations,airfield_by_id,nearest_station
from airspace import parse_openair,validate_geojson,corridor_intersections,import_airspace
from wave_map import at_height,wave_payload
from route_analysis import energy_sample_rows,energy_line,hazard_report,terrain_samples,hourly_details
from data_ingestion import JsonResponse,IngestionError


class WindCoverageTests(unittest.TestCase):
    def test_regular_rectangle_has_all_coastal_nodes_and_exact_requested_bounds(self):
        points=national_grid();self.assertEqual(len(points),len(WIND_LATITUDES)*len(WIND_LONGITUDES))
        self.assertEqual((min(p.latitude for p in points),max(p.latitude for p in points)),(49.8,59.))
        self.assertEqual((min(p.longitude for p in points),max(p.longitude for p in points)),(-8.5,2.))
        self.assertEqual(len({(p.latitude,p.longitude) for p in points}),len(points))
        np.testing.assert_allclose(np.diff(WIND_LATITUDES),np.diff(WIND_LATITUDES).mean(),atol=1e-6)
        np.testing.assert_allclose(np.diff(WIND_LONGITUDES),.75)

    def test_missing_950_and_800_interpolate_vectors_without_temperature_dependency(self):
        f=forecast(changes={'wind_speed_950hPa':None,'wind_direction_950hPa':None,
            'wind_speed_800hPa':None,'wind_direction_800hPa':None,
            'wind_direction_10m':350,'wind_direction_1000hPa':350,'wind_direction_925hPa':10,
            'temperature_925hPa':None,'temperature_850hPa':None})
        p=Profile.from_forecast(f,NOW)
        for level in (950,800):self.assertIsNotNone(wind_vector(p,level));self.assertEqual(wind_vector(p,level)[2],1)
        u,v,_=wind_vector(p,950);self.assertLess(angular_difference(wind_direction(u,v),0),10)

    def test_mountain_950_uses_labelled_surface_not_underground_pressure_values(self):
        f=replace(forecast(changes={'surface_pressure':880,'wind_speed_10m':7,'wind_direction_10m':180}),terrain_elevation_m=1400)
        v=wind_vector(Profile.from_forecast(f,NOW),950)
        self.assertEqual(v[2],2);self.assertAlmostEqual(v[1],7)
        empty=alter(f,wind_speed_10m=None,wind_speed_80m=None,wind_speed_100m=None)
        self.assertIsNone(wind_vector(Profile.from_forecast(empty,NOW),950))

    def test_pressure_between_ground_and_first_surface_sample_has_labelled_fallback(self):
        f=replace(forecast(changes={'surface_pressure':950.3,'wind_speed_950hPa':None,
            'wind_direction_950hPa':None,'geopotential_height_950hPa':None}),terrain_elevation_m=326)
        self.assertEqual(wind_vector(Profile.from_forecast(f,NOW),950)[2],2)

    def test_grid_payload_has_six_valid_levels_at_every_node_and_no_cutoff(self):
        rows=tuple(forecast(p,start=NOW.replace(hour=0),hours=24) for p in national_grid())
        body=wind_payload(rows,NOW,days=1)
        self.assertTrue(all(i is not None for row in body['grid']['indices'] for i in row))
        self.assertTrue(all(all(v is not None for v in s['values'][12]) for s in body['stations']))
        self.assertNotIn('coverage_km',body)

    def test_all_components_return_only_click_objects(self):
        count=0
        for path in ('app.py','route_analysis.py'):
            for node in ast.walk(ast.parse(Path(path).read_text())):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='st_folium':
                    kwargs={k.arg:k.value for k in node.keywords};self.assertEqual(ast.literal_eval(kwargs['returned_objects']),['last_object_clicked','last_clicked']);count+=1
        self.assertEqual(count,4)

    @unittest.skipUnless(shutil.which('node'),'Node is needed to parse generated map JavaScript')
    def test_generated_map_scripts_parse_with_route_markers_and_airfields(self):
        from app import build_map
        from wave_map import add_wave_map
        for mode in ('route','wind','clean','wave'):
            view,_=build_map((forecast(),),NOW,layers=[],task=TASK,turnpoints=TASK,
                visual_mode='clean' if mode=='wave' else mode,show_airfields=True,
                include_modes=mode!='wave',map_identity=mode)
            if mode=='wave':add_wave_map(view,(),NOW)
            view.get_root().render()
            parsed=subprocess.run(['node','--check'],input=view.get_root().script.render(),capture_output=True,text=True)
            self.assertEqual(parsed.returncode,0,parsed.stderr)


class AirfieldTests(unittest.TestCase):
    def test_catalog_has_all_named_clubs_and_reporting_stations(self):
        fields=load_airfields();self.assertGreater(len(fields),1000);self.assertGreaterEqual(len(set(reporting_stations())),125)
        for name in ('Long Mynd','Denbigh','Shobdon','Sleap','Shawbury','Bidford','Shenington','Aston Down','Nympsfield','Talgarth','Usk','Swansea','Camphill','Sutton Bank','Rufforth','Pocklington','Burn','Kirton in Lindsey','Saltby','Husbands Bosworth','Dunstable','Gransden Lodge','Lasham','Booker','Parham','Portmoak','Aboyne','Feshiebridge'):
            self.assertTrue(any(name.casefold() in a.label.casefold() for a in fields),name)
        self.assertIn('closed',airfield_by_id('GB-0497').status.lower())
        self.assertEqual(airfield_by_id('GB-0196').turnpoint.code,'GB-0196')
        self.assertFalse(airfield_by_id('GB-0196').turnpoint.official)

    def test_nearest_station_keeps_coordinate_distance_and_own_reports(self):
        field=airfield_by_id('EGLL');nearest,distance=nearest_station(field)
        self.assertEqual(nearest.icao,'EGLL');self.assertAlmostEqual(distance,0)
        station,distance=nearest_station(airfield_by_id('GB-0196'))
        self.assertTrue(station.metar);self.assertGreater(distance,0)

    def test_click_bridge_preserves_point_identity_and_map_independence(self):
        import planning_ui as ui
        import bga_turnpoints as b
        catalog=b.Catalog((A,B),'test','',b.content_hash((A,B)))
        event={'last_object_clicked':{'lat':0,'lng':0,'duo':{'kind':'point','value':A.code,'nonce':'1'}}}
        with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',{'DUO_TASK_STORE':folder}),patch.object(ui.st,'session_state',{'visual_mode':'wind'}):
            ui.process_map_event(event,catalog,DAY,'Add to task',event_key='planner_event',visual_override='route')
            ui.process_map_event(event,catalog,DAY,'Add to task',event_key='planner_event',visual_override='route')
            self.assertEqual(ui.active_task(DAY),(A,))
            field=airfield_by_id('GB-0196')
            click={'last_object_clicked':{'lat':field.latitude,'lng':field.longitude,'duo':{'kind':'airfield','value':field.ident,'nonce':'2'}}}
            ui.process_map_event(click,catalog,DAY,'Add to task',event_key='planner_event',visual_override='route')
            self.assertEqual(ui.active_task(DAY)[-1].code,field.ident)
            self.assertEqual(ui.st.session_state['visual_mode'],'wind')


class TaskImportTests(unittest.TestCase):
    def test_complete_cup_and_tsk_preserve_start_turn_finish_order(self):
        for format,content in [('cup',export_cup(TASK)),('tsk',export_tsk(TASK))]:
            result=import_task(content.encode(),format,catalog=TASK)
            self.assertEqual([p.code for p in result],[p.code for p in TASK])
            self.assertAlmostEqual(result[1].latitude,TASK[1].latitude)
        with self.assertRaisesRegex(ValueError,'waypoints only'):import_task(export_cup(TASK).split('-----Related Tasks-----')[0].encode(),'cup')

    def test_unknown_references_unsafe_xml_and_invalid_route_are_rejected(self):
        invalid=[(b'<!DOCTYPE Task><Task/>','tsk'),(b'<Task/>','tsk'),(b'x'*2_000_001,'cup'),
                 (export_cup(TASK).replace('-----Related Tasks-----','-----Related Tasks-----\n"Bad task","missing","missing","missing","missing"').encode(),'cup')]
        for data,format in invalid:
            with self.assertRaises(ValueError):import_task(data,format)


class AirspaceTests(unittest.TestCase):
    TEXT='AC D\nAN Test danger zone\nAL SFC\nAH FL100\nDP 52:00:00 N 003:00:00 W\nDP 53:00:00 N 003:00:00 W\nDP 53:00:00 N 002:00:00 W\nDP 52:00:00 N 002:00:00 W\n'
    def test_crossing_leg_detected_even_when_endpoints_outside(self):
        body=parse_openair(self.TEXT)
        a,b=GeoPoint('a','A',52.5,-4),GeoPoint('b','B',52.5,-1)
        hits=corridor_intersections(a,b,body,0)
        self.assertEqual(hits[0]['name'],'Test danger zone');self.assertEqual(hits[0]['floor'],'SFC')
        self.assertFalse(corridor_intersections(GeoPoint('c','C',54,-4),GeoPoint('d','D',54,-1),body,5))
        self.assertEqual(corridor_intersections(a,b,None),())

    def test_circles_and_arcs_and_bad_geometry(self):
        body=parse_openair('AC R\nAN Circle\nAL SFC\nAH 5000 FT\nV X=52:30:00 N 002:30:00 W\nDC 3\n')
        self.assertEqual(len(body['features'][0]['geometry']['coordinates'][0]),121)
        arc=parse_openair('AC D\nAN Arc\nV X=52:30:00 N 002:30:00 W\nDA 3,0,180\nDP 52:30:00 N 002:30:00 W\n')
        self.assertTrue(arc['features'])
        with self.assertRaises(ValueError):parse_openair('AC D\nAN Bad\nDC 10')
        with self.assertRaises(ValueError):validate_geojson({'type':'FeatureCollection','features':[]})

    def test_geojson_missing_labels_are_explicitly_unknown(self):
        body=parse_openair(self.TEXT);body['features'][0]['properties']={}
        validate_geojson(body)
        self.assertEqual(body['features'][0]['properties'],dict.fromkeys(('name','class','floor','ceiling'),'Unknown'))


class RouteAndWaveTests(unittest.TestCase):
    def test_hourly_agl_and_absent_airspace_remain_explicit(self):
        task=(A,replace(B,longitude=-1.5),A)
        samples=tuple({p.key:p for leg in energy_sample_rows(task) for row in leg for p in row}.values())
        weather=tuple(forecast(p) for p in samples);assessment=assess_task(task,weather,DAY)
        details=hourly_details(task,weather,assessment)
        self.assertTrue(details)
        self.assertGreater(details[0,NOW.isoformat()]['cloud_agl'],0)
        self.assertTrue(all(d['cloud_agl'] is None for d in hourly_details(task,(),assessment).values()))
        hazards=hazard_report(task,weather,assessment,None,None,NOW)
        self.assertTrue(all('UNASSESSED' in row['Airspace / danger areas'] for row in hazards))
        self.assertTrue(all('Unassessed' in row['Terrain warning'] for row in hazards))

    def test_energy_line_uses_available_weather_and_rejects_rain_blocked_corridor(self):
        task=(A,replace(B,longitude=-1.5),A)
        sections=energy_sample_rows(task);points=tuple({p.key:p for leg in sections for row in leg for p in row}.values())
        weather=tuple(forecast(p) for p in points)
        lines=energy_line(task,weather,NOW)
        self.assertTrue(all(line['points'] for line in lines))
        self.assertEqual(lines[0]['points'][0],(A.latitude,A.longitude))
        rain=tuple(alter(f,precipitation=2) for f in weather)
        self.assertTrue(all(not line['points'] for line in energy_line(task,rain,NOW)))
        self.assertTrue(all(not line['points'] for line in energy_line(task,(),NOW)))

    def test_terrain_uses_dem_and_keeps_missing_samples(self):
        terrain_samples.clear()
        def response(endpoint,params,**kw):
            self.assertEqual(endpoint,'https://api.open-meteo.com/v1/elevation')
            count=len(params['latitude'].split(','))
            return JsonResponse({'elevation':[500]*(count-1)+[None]},NOW)
        with patch('route_analysis.JsonHttpClient') as client:
            client.return_value.get_json.side_effect=response
            result=terrain_samples(TASK)
        self.assertTrue(any(r['missing'] for r in result));self.assertEqual(result[0]['peak_m'],500)
        terrain_samples.clear()

    def test_wave_altitudes_keep_missing_and_underground_levels_unavailable(self):
        p=Profile.from_forecast(forecast(),NOW)
        self.assertIsNone(at_height(p,90));self.assertIsNone(at_height(p,12000))
        self.assertIsNotNone(at_height(p,25000*.3048))
        forecasts=tuple(forecast(GeoPoint('ridge:'+key,r.point.name,r.point.latitude,r.point.longitude)) for key,r in WAVE_RIDGES.items())
        body=wave_payload(forecasts,NOW)
        self.assertEqual([f['altitude'] for f in body['frames']],[3000,7000,10000,12000,13000,16000,20000,23000,25000])
        self.assertTrue(all(tile['image'].startswith('data:image/png;base64,') for f in body['frames'] for tile in f['tiles']))
        self.assertTrue(any(f['negative_cells'] for f in body['frames']))
        self.assertTrue(any(f['positive_cells'] for f in body['frames']))
        self.assertTrue(any('Denbigh' in s['name'] for s in body['frames'][0]['sites']))
        empty=wave_payload((),NOW);self.assertTrue(all(f['positive_cells']==0 and not f['bars'] for f in empty['frames']))
