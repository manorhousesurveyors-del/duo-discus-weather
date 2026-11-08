"""Protocol, continuous lifecycle and telemetry-only UI regression coverage."""
from datetime import timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch
import importlib
import math
import os
import subprocess
import sys

from ogn_live import APRS_HOSTS, FEEDS, MirrorSession, TrafficCache, aprs_login, parse_aprs, parse_spot_xml
from tests.test_ogn_live import NOW, DDB, XML, APRS


def encode91(value):
    encoded=''
    for _ in range(4):encoded=chr(value%91+33)+encoded;value//=91
    return encoded


class StreamTests(TestCase):
    def test_timestamp_variants_and_received_time_are_explicit(self):
        tail=APRS.split(':',1)[1][8:]
        for prefix,kind in (('!','received'),('=','received'),('/115950h','packet UTC'),
                            ('@081159z','packet UTC'),('/081159/','packet Europe/London')):
            row=parse_aprs('FLRABC123>APRS:'+prefix+tail,now=NOW,devices=DDB)
            self.assertEqual(row['badge'],'KA');self.assertEqual(row['category'],'glider')
            self.assertEqual(row['timestamp_kind'],kind)
        self.assertIsNone(parse_aprs(APRS.replace('115950','105950'),now=NOW))

    def test_compressed_position_course_speed_altitude_and_comment_telemetry(self):
        y=encode91(round((90-52.5)*380926));x=encode91(round((-2.8+180)*190463))
        cs=chr(33+22)+chr(33+round(math.log(61)/math.log(1.08)))+chr(33)
        line='FLRABC123>APRS:!/'+y+x+'g'+cs+'/A=003281 id06ABC123 +200fpm +0.3rot 12.5dB'
        row=parse_aprs(line,now=NOW,devices=DDB)
        self.assertAlmostEqual(row['lat'],52.5,places=4);self.assertAlmostEqual(row['lon'],-2.8,places=4)
        self.assertEqual(row['heading'],88);self.assertAlmostEqual(row['groundspeed_kt'],60,delta=3)
        self.assertEqual(row['altitude_ft'],3281);self.assertEqual(row['vario_kt'],2.)
        self.assertEqual(row['rotation'],.3);self.assertEqual(row['signal_db'],12.5)
        altitude=round(math.log(3281)/math.log(1.002));cs=chr(33+altitude//91)+chr(33+altitude%91)+chr(33+16)
        row=parse_aprs('FLRABC123>APRS:!/'+y+x+'g'+cs+' id06ABC123',now=NOW,devices=DDB)
        self.assertAlmostEqual(row['altitude_ft'],3281,delta=5)
        self.assertIsNone(row['groundspeed_kt']);self.assertIsNone(row['heading'])
        row=parse_aprs('FLRABC123>APRS:!a'+y+x+'g  ! id06ABC123',now=NOW,devices=DDB)
        self.assertIsNotNone(row);self.assertIsNone(row['groundspeed_kt'])

    def test_missing_altitude_comment_id_and_course_do_not_discard_a_position(self):
        line=APRS.replace('090/060/A=003281 id06ABC123','')
        row=parse_aprs(line,now=NOW,devices=DDB)
        self.assertEqual(row['id'],'ABC123');self.assertIsNone(row['altitude_ft'])
        self.assertIsNone(row['groundspeed_kt']);self.assertIsNone(row['heading'])

    def test_all_categories_ground_and_unregistered_targets_survive(self):
        for kind in range(16):
            raw=XML.replace(b',1,TEST',f',{kind},TEST'.encode()).replace(b'111.12',b'0')
            row=parse_spot_xml(raw,now=NOW,devices=DDB)['aircraft'][0]
            self.assertEqual(row['category'],'glider' if kind in (1,8) else 'other')
            self.assertFalse(row['airborne'])
        row=parse_spot_xml(XML.replace(b'OLD,G-OLD',b'_fb,opaque').replace(b'ABC123,token',b'CAFEFB,opaque'),now=NOW)['aircraft'][0]
        self.assertEqual((row['badge'],row['category']),('_fb','other'))
        without_cn={'ABC123':[dict(DDB['ABC123'][0],cn='',registration='G-TEST')]}
        self.assertEqual(parse_spot_xml(XML,now=NOW,devices=without_cn)['aircraft'][0]['badge'],'EST')

    def test_newest_registration_alias_is_merged_and_twenty_minutes_expires(self):
        row=parse_spot_xml(XML,now=NOW,devices=DDB)['aircraft'][0]
        cache=TrafficCache();cache.merge([row],now=NOW)
        other=dict(row,id='ABC456',observed_at=(NOW-timedelta(seconds=5)).isoformat())
        merged=cache.merge([other],now=NOW)
        self.assertEqual(len(merged),1);self.assertEqual(merged[0]['id'],'ABC456')
        self.assertEqual(len(cache.merge([],now=NOW+timedelta(minutes=19))),1)
        self.assertFalse(cache.merge([],now=NOW+timedelta(minutes=21)))

    def test_cookie_session_warms_before_xml_and_reuses_session(self):
        session=MagicMock();session.headers={}
        response=MagicMock();response.__enter__.return_value=response
        response.iter_content.return_value=[b'<markers/>'];session.get.return_value=response
        client=MirrorSession(session)
        self.assertEqual(client.fetch(FEEDS[0],100),b'<markers/>')
        client.fetch(FEEDS[0],100)
        self.assertEqual(session.get.call_count,3)
        self.assertEqual(session.get.call_args_list[0].args[0],'https://live.glidernet.org/')
        self.assertIn('a=1',session.get.call_args_list[1].args[0])
        self.assertEqual(session.headers['Origin'],'https://live.glidernet.org')
        self.assertNotEqual(session.verify,False)
        response.iter_content.return_value=[b'x'*101]
        with self.assertRaises(ValueError):client.fetch(FEEDS[0],100)

    def test_random_receive_only_login_and_all_fallback_hosts(self):
        self.assertRegex(aprs_login(),rb'^user DUO[0-9]{4} pass -1 vers duo-wx 1.0 filter r/54.2/-3.5/750\r\n$')
        self.assertEqual(len(APRS_HOSTS),5)
        from ogn_live import AprsReceiver
        receiver=None;calls=[]
        def connect(host,**kwargs):
            calls.append(host)
            if len(calls)==5:receiver.stop.set()
            raise OSError('offline socket test')
        receiver=AprsReceiver(TrafficCache(),lambda:{},lambda:True,connector=connect)
        receiver.stop.wait=Mock(return_value=False);receiver.run()
        self.assertEqual([host for host,port in calls],list(APRS_HOSTS))
        self.assertTrue(all(port==14580 for host,port in calls))

    def test_startup_resource_stays_active_and_snapshots_publish_aprs_without_http(self):
        import ogn_traffic as traffic
        worker=traffic.TrafficWorker();worker.cache.merge([parse_aprs(APRS,now=NOW,devices=DDB)],now=NOW)
        self.addCleanup(worker.close)
        class Clock(type(NOW)):
            @classmethod
            def now(cls,tz=None):return NOW
        worker.aprs.status='Connected to aprs.glidernet.org'
        with patch.object(traffic,'datetime',Clock):
            self.assertEqual(worker.snapshot()['target_count'],1)
            self.assertEqual(worker.snapshot()['source'],'OGN APRS-IS Live Stream')
        with patch.object(traffic,'_worker',worker):
            traffic.set_worker_active(False)
            self.assertTrue(worker.aprs.active())
        traffic.ensure_worker.clear()
        with patch.object(traffic,'TrafficWorker') as constructor,patch.dict(os.environ,{'DUO_OGN_ENABLED':'1'}):
            resource=constructor.return_value.start.return_value
            for _ in range(3):self.assertIs(traffic.start_background(),resource)
            constructor.assert_called_once();constructor.return_value.start.assert_called_once()
        traffic.ensure_worker.clear()

    def test_unwritable_snapshot_does_not_crash_weather_startup(self):
        import ogn_traffic
        with patch.dict(os.environ,{'DUO_OGN_ENABLED':'1'}), \
             patch.object(ogn_traffic,'ensure_worker',side_effect=OSError('readonly')), \
             self.assertLogs('ogn_traffic',level='ERROR'):
            self.assertIsNone(ogn_traffic.start_background())

    def test_boot_calls_receiver_even_on_weather_page_and_no_product_demo_path(self):
        import ast
        tree=ast.parse(Path('app.py').read_text())
        main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_main')
        self.assertTrue(any(isinstance(n,ast.Constant) and n.value=='start_background' for n in ast.walk(main)))
        code=Path('assets/ogn_traffic.js').read_text()
        for factory in ('demoTick','Math.sin','Math.cos','Array.from','data-ogn="demo"'):
            self.assertNotIn(factory,code)
        self.assertIn('next.schema!==2',code)

    def test_real_app_startup_starts_receiver_before_ogn_page_is_opened(self):
        from data_ingestion import ForecastBatch
        from tempfile import TemporaryDirectory
        from streamlit.testing.v1 import AppTest
        import app,ogn_traffic
        with TemporaryDirectory() as tmp, \
             patch.dict(os.environ,{'DUO_OGN_ENABLED':'1','DUO_DASHBOARD_SNAPSHOT':'','DUO_TASK_STORE':tmp}), \
             patch.object(app,'load_sites',return_value=ForecastBatch((),())), \
             patch.object(ogn_traffic,'ensure_worker') as receiver:
            at=AppTest.from_string('import app\napp._main()',default_timeout=30).run()
            self.assertFalse(at.exception)
            self.assertEqual(at.radio(key='active_page').value,'Weather map')
            receiver.assert_called()
            at.radio(key='active_page').set_value('Help Guide').run()
            self.assertFalse(at.exception)
            self.assertGreaterEqual(receiver.call_count,2)

    def test_hot_deploy_stops_old_receiver_before_loading_new_api(self):
        script='''
from threading import Event
from types import SimpleNamespace
import ogn_traffic,ogn_live,runtime_imports
old=SimpleNamespace(stop=Event(),aprs=SimpleNamespace(stop=Event()))
ogn_traffic._worker=old
ogn_traffic.TRAFFIC_API_REVISION=1
del ogn_live.MirrorSession
updated=runtime_imports.optional_module('ogn_traffic')
assert old.stop.is_set() and old.aprs.stop.is_set()
assert updated.TRAFFIC_API_REVISION==2
assert updated._worker is None
assert hasattr(ogn_live,'MirrorSession')
'''
        result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
