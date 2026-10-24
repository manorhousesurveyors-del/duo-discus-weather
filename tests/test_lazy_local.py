"""Behavioural contracts for lazy pages, shared tasks and local forecast scope."""
from datetime import timedelta
from pathlib import Path
from dataclasses import replace
import json,tempfile,unittest
from unittest.mock import patch,Mock
import numpy as np
from tests.fixtures import NOW,forecast
from config import GeoPoint,forecast_locations
from bga_turnpoints import load_catalog
from flight_briefing import build_context,local_response
from place_resolver import resolve_place,Place,PlaceError,geocode,local_forecasts


class LocalForecastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.catalog=load_catalog()

    def test_named_catalog_clubs_and_no_english_trigraph(self):
        for name in ('Midlands Gliding Club','Long Mynd','Shrewsbury','Husbands Bosworth','Lasham','Denbigh','Talgarth','Cotswold','EGBB','MYN'):
            place=resolve_place('soaring conditions at '+name,self.catalog)
            self.assertTrue(49<place.point.latitude<61)
            self.assertNotEqual(place.source,'Default Long Mynd forecast point')
        place=resolve_place('Can I potter for the day?',self.catalog)
        self.assertIn('Mynd',place.point.name)
        self.assertNotIn('Potton',place.point.name)

    def test_uk_geocoding_and_ambiguous_names(self):
        geocode.clear()
        rows={'results':[{'id':1,'name':'Birmingham','country_code':'US','latitude':33,'longitude':-86},
                         {'id':2,'name':'Birmingham','country_code':'GB','latitude':52.4814,'longitude':-1.8998,'admin1':'England'}]}
        with patch('place_resolver.JsonHttpClient') as http:
            http.return_value.get_json.return_value.body=rows
            place=resolve_place('What are the soaring conditions at Birmingham?',self.catalog,allow_network=True)
            self.assertEqual(place.point.latitude,52.4814)
            again=geocode('Birmingham');self.assertEqual(again,place)
            self.assertEqual(http.return_value.get_json.call_count,1)
        geocode.clear()
        with patch('place_resolver.geocode',return_value=Place(GeoPoint('town','Church Stretton',52.5378,-2.8015),'Test geocoder')) as lookup:
            place=resolve_place('Conditions at Church Stretton?',self.catalog,allow_network=True)
            self.assertEqual(place.point.name,'Church Stretton')
            lookup.assert_called_once_with('Church Stretton')
        rows['results']=[{**rows['results'][1],'id':i,'name':'Newport','admin2':county} for i,county in enumerate(('Shropshire','Wales'))]
        with patch('place_resolver.JsonHttpClient') as http:
            http.return_value.get_json.return_value.body=rows
            with self.assertRaisesRegex(PlaceError,'Several places'):geocode('Newport')
        geocode.clear()

    def test_local_scope_excludes_old_route_and_all_remote_context(self):
        origin=GeoPoint('town','Birmingham',52.4814,-1.8998)
        values=(forecast(origin,start=NOW.replace(hour=0)),)
        old=tuple(self.catalog.by_code[c] for c in ('MYN','GOO','DID','MYN') if c in self.catalog.by_code)
        context,_=build_context('weather forecast at Birmingham',self.catalog,values,NOW.date(),NOW.date(),'dry',
            active=old,resolved=Place(origin,'Verified test coordinate'))
        text=local_response('weather forecast at Birmingham',context)
        for unwanted in ('Goole','Didcot','Where the better sectors lie','Five-day outlook','Your task','West launch'):
            self.assertNotIn(unwanted,text)
        self.assertEqual(context['active_task'],[]);self.assertEqual(context['catalog'],[])
        self.assertFalse(context['regions']);self.assertIsNone(context['task'])
        for expected in ('20 km','09:00','18:00','Base ft AGL','Spread-out risk','No surveyed ridge'):
            self.assertIn(expected,text)
        h=context['site']['hours'][0]
        self.assertAlmostEqual(h['cloud_base_ft']-h['cloud_base_agl_ft'],328.084,places=2)
        context,_=build_context('Local cross-country weather here without national tables',self.catalog,values,
            NOW.date(),NOW.date(),'dry',active=old,selected=origin)
        self.assertEqual(context['scope']['kind'],'local')
        self.assertFalse(context['active_task'])

    def test_remote_weather_is_not_substituted_into_20km_sector(self):
        point=GeoPoint('city','Birmingham',52.48,-1.90)
        context,_=build_context('weather forecast here',self.catalog,(forecast(),),NOW.date(),NOW.date(),'dry',selected=point)
        self.assertFalse(context['site']['hours'])
        self.assertIn('No forecast within 20 km',local_response('',context))

    def test_explicit_task_and_national_requests_keep_assessment(self):
        forecasts=tuple(forecast(p,start=NOW.replace(hour=0)) for p in forecast_locations())
        task=tuple(self.catalog.by_code[c] for c in ('MYN','BIE','MYN'))
        context,_=build_context('Explain my task and compare all UK regions',self.catalog,forecasts,NOW.date(),NOW.date(),'dry',active=task)
        self.assertIsNotNone(context['task']);self.assertGreater(len(context['regions']),9)
        self.assertFalse(context['circuits'])
        context,_=build_context('Assess the circuit MYN to Goole to Didcot to Church Stretton',self.catalog,forecasts,NOW.date(),NOW.date(),'dry')
        self.assertEqual(len(context['circuits']),1)

    def test_local_forecast_fetches_centre_and_four_bounded_sector_samples(self):
        from soaring_physics import distance_bearing
        point=GeoPoint('town','Shrewsbury',52.7,-2.75);local_forecasts.clear()
        with patch('data_ingestion.OpenMeteoClient') as client:
            client.return_value.fetch_batched.return_value='batch'
            self.assertEqual(local_forecasts(point,20,'ifs025',0),'batch')
            points=client.return_value.fetch_batched.call_args.args[0]
            self.assertEqual(len(points),5);self.assertEqual(points[0],point)
            for p in points[1:]:self.assertAlmostEqual(distance_bearing(point,p)[0],20,places=2)
        local_forecasts.clear()


class CanonicalTaskTests(unittest.TestCase):
    def test_clear_versions_and_day_separation_reject_late_browser_edits(self):
        import planning_ui as ui
        from tests.test_task_planning import A,B,DAY
        with tempfile.TemporaryDirectory() as tmp,patch.dict('os.environ',{'DUO_TASK_STORE':tmp}),patch.object(ui.st,'session_state',{}):
            ui.set_task(DAY,(A,B),rerun=False)
            before=ui.task_payload();self.assertEqual(ui.st.session_state['active_task_points'],(A,B))
            ui.set_task(DAY,(),rerun=False);cleared=ui.task_payload()
            self.assertGreater(cleared['version'],before['version']);self.assertEqual(cleared['points'],[])
            self.assertFalse(ui.set_task(DAY,(A,B),client_version=before['version'],rerun=False))
            self.assertEqual(ui.active_task(DAY),())
            ui.set_task(DAY+timedelta(days=1),(B,A),rerun=False)
            self.assertEqual(ui.active_task(DAY),());self.assertEqual(ui.active_task(DAY+timedelta(days=1)),(B,A))

    def test_point_sync_saves_without_requesting_full_dashboard_refresh(self):
        import planning_ui as ui
        catalog=load_catalog();day=NOW.date()
        with tempfile.TemporaryDirectory() as tmp,patch.dict('os.environ',{'DUO_TASK_STORE':tmp}),patch.object(ui.st,'session_state',{}):
            ui.active_task(day);payload=ui.task_payload();payload.update(version=payload['version']+1,points=[{'code':'MYN','lat':52.5,'lon':-2.9}],action='add')
            event={'last_object_clicked':{'duo':{'kind':'task_sync','nonce':'1','value':json.dumps(payload)}}}
            ui.process_map_event(event,catalog,day,'Add to task',visual_override='route')
            self.assertEqual(ui.active_task(day)[0],catalog.by_code['MYN'])
            self.assertNotIn('_task_full_refresh',ui.st.session_state)


class LazyCacheTests(unittest.TestCase):
    def test_map_compression_roundtrips_script_and_has_stable_identity(self):
        import base64,zlib,folium
        from map_component import compressed_document,map_document
        view=folium.Map(location=[52.5,-2.8],tiles=None)
        folium.Marker([52.5,-2.8],tooltip='BGA · test').add_to(view)
        packet=compressed_document(view)
        self.assertEqual(json.loads(zlib.decompress(base64.b64decode(packet['compressed_document']))),map_document(view))
        self.assertEqual(packet,compressed_document(view))
        self.assertNotIn('script',packet)

    def test_discovery_cache_is_separate_for_credentials_and_auth_modes(self):
        from app_cache import gemini_discovery
        from gemini_rest import Connection
        gemini_discovery.clear()
        with patch('gemini_rest.GeminiREST') as client:
            client.return_value.connect.return_value=Connection(('gemini-test',),'key')
            for fingerprint,auth,key in [('hash-a','key','a'),('hash-a','key','a'),('hash-b','key','b'),('hash-a-bearer','bearer','a')]:
                gemini_discovery(fingerprint,auth,_credential=key)
            self.assertEqual(client.call_count,3)
        gemini_discovery.clear()

    def test_wave_cache_reuses_same_forecast_hour_only(self):
        import wave_map
        wave_map.cached_wave_payload.clear()
        with patch.object(wave_map,'wave_payload',return_value={'frames':[]} ) as calculate:
            for _ in range(2):wave_map.cached_wave_payload(('run-a',),NOW,_forecasts=())
            self.assertEqual(calculate.call_count,1)
            wave_map.cached_wave_payload(('run-a',),NOW+timedelta(hours=1),_forecasts=())
            self.assertEqual(calculate.call_count,2)
        wave_map.cached_wave_payload.clear()

    def test_cached_slopes_are_readonly_and_do_not_change_solver(self):
        from terrain_wave import load_continuous_terrain,prepare_terrain,solve_field
        from soaring_physics import Profile
        dem=load_continuous_terrain();self.assertIn('gx',dem['prepared'])
        self.assertFalse(dem['prepared']['gx'].flags.writeable)
        h=np.zeros((32,32));h[10:20,10:20]=200
        expected,_=solve_field(h,1000,Profile.from_forecast(forecast(),NOW),(7000,))
        actual,_=solve_field(h,1000,Profile.from_forecast(forecast(),NOW),(7000,),prepared=prepare_terrain(h,1000))
        np.testing.assert_allclose(expected[0],actual[0],equal_nan=True)

    def test_only_active_page_constructs_a_map_and_text_pages_run_no_solver(self):
        from streamlit.testing.v1 import AppTest
        from alerts import ForecastBundle,save_bundle
        import app
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'snapshot.json';save_bundle(ForecastBundle((),(),.5,(),NOW),path)
            with patch.dict('os.environ',{'DUO_TASK_STORE':tmp+'/tasks','DUO_DASHBOARD_SNAPSHOT':str(path),'GEMINI_API_KEY':''}), \
                 patch('map_component.render_map',return_value=None) as render, \
                 patch('wave_map.wave_payload',side_effect=AssertionError('Wave calculation on another page')), \
                 patch('ogn_traffic.ensure_worker',side_effect=AssertionError('Background OGN poll')):
                at=AppTest.from_file(str(Path(app.__file__)),default_timeout=20).run()
                self.assertFalse(at.exception);self.assertEqual(render.call_count,1)
                self.assertEqual(render.call_args.kwargs['key'],'soaring_map')
                for page in ('Help Guide','Help & Briefing Bot','Data quality'):
                    render.reset_mock();at.radio(key='active_page').set_value(page).run()
                    self.assertFalse(at.exception);render.assert_not_called()
                    self.assertFalse(at.get('plotly_chart'))
                render.reset_mock();at.radio(key='active_page').set_value('Task planner').run()
                self.assertFalse(at.exception);self.assertEqual(render.call_count,1)
                self.assertEqual(render.call_args.kwargs['key'],'planner_map')

    def test_display_rounding_preserves_original_precision(self):
        from json_payload import browser_json
        raw={'lat':52.123456789,'coordinates':[[-2.987654321,53.87654321]],'thermal':3.123456}
        shown=json.loads(browser_json(raw))
        self.assertEqual(shown['lat'],52.1235);self.assertEqual(shown['coordinates'][0],[-2.9877,53.8765])
        self.assertEqual(shown['thermal'],raw['thermal']);self.assertEqual(raw['lat'],52.123456789)
