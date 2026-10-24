from dataclasses import replace
from datetime import UTC,datetime,timedelta
import base64,json,tempfile,unittest,zlib
from pathlib import Path
from unittest.mock import patch,Mock
import numpy as np
from day_rating import calibrated_stars,rate_day,DayRating
from tests.fixtures import forecast,NOW
from config import MONITORING_ZONES
from ogn_traffic import parse_spot_xml,parse_devices,TrafficWorker
from terrain_wave import solve_field,load_terrain
from soaring_physics import Profile
from tests.test_ingestion import Response


class DayRatingTests(unittest.TestCase):
    def test_requested_distance_bands_and_quality_caps(self):
        for km,stars in ((0,1),(49,1),(50,2),(179,2),(180,3),(300,3),(350,4),(499,4),(500,5),(750,5)):
            self.assertEqual(calibrated_stars(km,4.5,5500,8)[0],stars)
        for values,expected in (((600,1.4,5500,8),2),((600,4,1700,8),2),((600,4,5500,2),2),
                                ((600,4,5500,5),4),((600,4,5500,8,.3),3),((600,4,5500,8,.5),2)):
            self.assertEqual(calibrated_stars(*values)[0],expected)
        self.assertIsNone(calibrated_stars(None,4,5000,8)[0])
        self.assertIsNone(calibrated_stars(float('nan'),4,5000,8)[0])

    def test_missing_hours_rain_and_wave_only_do_not_become_star_days(self):
        origin=MONITORING_ZONES['shropshire'];day=NOW.date()
        normal=forecast(origin,start=NOW.replace(hour=0),hours=24)
        self.assertIsNotNone(rate_day((normal,),origin,day).stars)
        rain=forecast(origin,start=NOW.replace(hour=0),hours=24,changes={'precipitation':2})
        rating=rate_day((rain,),origin,day,wave=True)
        self.assertEqual(rating.stars,1);self.assertEqual(rating.distance_km,0);self.assertTrue(rating.wave)
        for f in (forecast(origin,start=NOW.replace(hour=0),hours=8),forecast(origin,start=NOW.replace(hour=0),hours=24,changes={'precipitation':None})):
            self.assertIsNone(rate_day((f,),origin,day).stars)

    def test_high_priority_email_and_push_contain_rating(self):
        from alerts import Briefing,DeliverySettings,send_ntfy,send_email,render_html,render_whatsapp
        rating=DayRating(NOW.date().isoformat(),'Test region',4,440,3.2,4200,7,False,())
        b=Briefing(2,NOW,NOW,(),(),(),None,('4-Star day',),(),(rating,))
        self.assertIn('★★★★☆',render_html(b));self.assertIn('★★★★☆',render_whatsapp(b))
        opener=Mock();opener.open.return_value=Response(b'{"id":"abc123","event":"message","topic":"g-ckml-soaring-alerts"}')
        settings=DeliverySettings(gmail_app_password='test')
        with patch('alerts.build_opener',return_value=opener):send_ntfy(settings,b,'test')
        self.assertEqual(opener.open.call_args.args[0].get_header('Priority'),'high')
        with patch('alerts.smtplib.SMTP_SSL') as smtp:
            smtp.return_value.__enter__.return_value.send_message.return_value={}
            send_email(settings,b,'test')
            message=smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
            self.assertEqual(message['X-Priority'],'1');self.assertIn('4-Star',message['Subject'])


class OgnTests(unittest.TestCase):
    NOW=datetime(2026,10,24,12,tzinfo=UTC)
    DDB=b'{"devices":[{"device_id":"ABC123","tracked":"Y","identified":"Y","registration":"G-CKML","cn":"KA"}]}'
    def xml(self,**kw):
        values={'lat':'52.5','lon':'-2.8','cn':'KA','reg':'G-CKML','alt':'1000','clock':'11:59:50','age':'10','heading':'90','speed':'111.12','vario':'2.0','type':'1','receiver':'TEST','id':'ABC123','crc':'ABC'}|kw
        return ('<markers><m a="'+','.join(values.values())+'"/></markers>').encode()
    def parse(self,raw=None,devices=None):return parse_spot_xml(raw or self.xml(),now=self.NOW,devices=devices or parse_devices(self.DDB))

    def test_live_units_identity_age_and_uk_filter(self):
        row=self.parse()['aircraft'][0]
        self.assertEqual(row['groundspeed_kt'],60);self.assertEqual(row['altitude_ft'],3281);self.assertEqual(row['vario_kt'],3.9)
        self.assertEqual(row['heading'],90);self.assertTrue(row['highlight']);self.assertEqual(row['competition_id'],'KA')
        for args in ({'lat':'48'},{'age':'121'},{'clock':'10:59:50'},{'type':'9'},{'speed':'4'},{'vario':'99'}):
            self.assertEqual(self.parse(self.xml(**args))['aircraft'],[])
        self.assertEqual(self.parse(b'<markers/>')['aircraft'],[])

    def test_privacy_and_unidentified_devices_are_respected(self):
        hidden=parse_devices(self.DDB.replace(b'"tracked":"Y"',b'"tracked":"N"'))
        self.assertFalse(self.parse(devices=hidden)['aircraft'])
        anon=parse_devices(self.DDB.replace(b'"identified":"Y"',b'"identified":"N"'))
        row=self.parse(devices=anon)['aircraft'][0];self.assertFalse(row['highlight']);self.assertNotIn('G-CKML',row['registration'])

    def test_malformed_xml_new_layout_and_midnight(self):
        for raw in (b'<!DOCTYPE x><markers/>',b'<html/>',b'<markers><m a="bad"/></markers>'):
            with self.assertRaises(ValueError):self.parse(raw)
        midnight=self.NOW.replace(hour=0,second=5)
        row=parse_spot_xml(self.xml(clock='23:59:58',age='7'),now=midnight,devices=parse_devices(self.DDB))['aircraft'][0]
        self.assertEqual(datetime.fromisoformat(row['observed_at']).date(),(midnight-timedelta(days=1)).date())

    def test_worker_publishes_one_atomic_snapshot_and_never_invents_live_aircraft(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'uk.json';worker=TrafficWorker(path)
            worker.write(self.parse(b'<markers/>'))
            self.assertEqual(json.loads(path.read_text())['aircraft'],[])
            self.assertFalse(path.with_suffix('.tmp').exists())


class TerrainWaveTests(unittest.TestCase):
    def setUp(self):
        x=np.linspace(-12000,12000,64);xx,yy=np.meshgrid(x,x)
        self.hill=450*np.exp(-((xx/1800)**2+(yy/7000)**2));self.spacing=x[1]-x[0]
        self.profile=Profile.from_forecast(forecast(),NOW)

    def test_flat_terrain_cannot_create_wave_and_real_hill_has_both_phases(self):
        flat,_=solve_field(np.zeros((64,64)),self.spacing,self.profile,(3000,7000))
        self.assertTrue(all(np.nanmax(np.abs(a))<1e-9 for a in flat))
        waves,diag=solve_field(self.hill,self.spacing,self.profile,(3000,7000,10000))
        self.assertTrue(all(np.nanmin(a)<0 and np.nanmax(a)>0 for a in waves))
        self.assertGreater(diag['cross_ridge_mps'],0);self.assertGreater(diag['n_s1'],0)
        self.assertGreater(diag['wavelength_km'],0);self.assertTrue(all(a>b for a,b in zip(diag['bounce_decay'],diag['bounce_decay'][1:])))

    def test_reversing_flow_blocks_upper_wave_and_outside_profile_is_unknown(self):
        f=forecast(changes={'wind_direction_850hPa':90,'wind_direction_800hPa':90,'wind_direction_700hPa':90,
                            'wind_direction_600hPa':90,'wind_direction_500hPa':90})
        waves,_=solve_field(self.hill,self.spacing,Profile.from_forecast(f,NOW),(10000,50000))
        self.assertTrue(np.all(waves[0]==0));self.assertIsNone(waves[1])

    def test_wave_train_is_on_the_lee_side_of_an_isolated_hill(self):
        x=(np.arange(256)-127.5)*500;xx,yy=np.meshgrid(x,x)
        hill=450*np.exp(-((xx/1800)**2+(yy/7000)**2))
        low=next(l for l in self.profile.levels if l.height_m>self.profile.forecast.terrain_elevation_m+250 and l.u_mps is not None)
        fields,diagnostics=solve_field(hill,500,self.profile,(low.height_m/.3048,))
        self.assertAlmostEqual(diagnostics['wind_to'],90)
        # A westerly must leave its sustained bounces east of the terrain.
        self.assertGreater(np.linalg.norm(fields[0][:,x>6000]),5*np.linalg.norm(fields[0][:,x< -6000]))
        easterly=replace(self.profile,levels=tuple(replace(l,u_mps=-l.u_mps if l.u_mps is not None else None,
            v_mps=-l.v_mps if l.v_mps is not None else None) for l in self.profile.levels))
        fields,diagnostics=solve_field(hill,500,easterly,(low.height_m/.3048,))
        self.assertAlmostEqual(diagnostics['wind_to'],270)
        self.assertGreater(np.linalg.norm(fields[0][:,x< -6000]),5*np.linalg.norm(fields[0][:,x>6000]))

    def test_checked_dem_has_real_wales_shropshire_and_scotland_relief(self):
        domains=load_terrain();self.assertGreaterEqual(len(domains),16)
        for key in ('snowdonia','clwyd','cadair','mynd','clee','brecon','highlands','grampians'):
            d=next(d for d in domains if d['key']==key)
            self.assertEqual(d['heights'].shape,(256,256));self.assertEqual(d['spacing_m'],500)
            self.assertGreater(d['heights'].max()-d['heights'].min(),300)

    def test_display_simplification_does_not_modify_obstacle_geometry(self):
        from airspace import load_airspace,display_airspace
        body=load_airspace(NOW.date());before=json.dumps(body,sort_keys=True);shown=display_airspace(body)
        self.assertEqual(before,json.dumps(body,sort_keys=True));self.assertEqual(len(shown['features']),len(body['features']))
        self.assertLess(len(json.dumps(shown)),len(before))


class ClientCommitTests(unittest.TestCase):
    def test_one_commit_saves_order_and_completion_and_rejects_another_day(self):
        import planning_ui as ui
        import bga_turnpoints as b
        from route_tools import is_complete
        from tests.test_task_planning import A,B,DAY
        catalog=b.Catalog((A,B),'test','',b.content_hash((A,B)))
        def event(day,nonce):return {'last_object_clicked':{'duo':{'kind':'task_sync','nonce':nonce,'value':json.dumps({'date':day.isoformat(),'points':[{'code':p.code,'lat':p.latitude,'lon':p.longitude} for p in (A,B,A)],'complete':True,'action':'finish'})}}}
        with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',{'DUO_TASK_STORE':folder}),patch.object(ui.st,'session_state',{}):
            ui.process_map_event(event(DAY,'1'),catalog,DAY,'Add to task',visual_override='route')
            self.assertEqual(ui.active_task(DAY),(A,B,A));self.assertTrue(is_complete(DAY,(A,B,A)))
            ui.process_map_event(event(DAY+timedelta(days=1),'2'),catalog,DAY,'Add to task',visual_override='route')
            self.assertIn('another forecast day',ui.st.session_state['task_error'])
            self.assertEqual(ui.active_task(DAY),(A,B,A))


if __name__=='__main__':unittest.main()
