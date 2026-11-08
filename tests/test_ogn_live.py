from datetime import UTC, datetime, timedelta
from unittest import TestCase
from unittest.mock import Mock, MagicMock, patch
from pathlib import Path
import json
import tempfile
import time

from ogn_live import (APRS_LOGIN, AprsReceiver, TrafficCache, fetch,
                      parse_aprs, parse_devices, parse_spot_xml)
from ogn_traffic import TrafficWorker

NOW = datetime(2026,11,8,12,tzinfo=UTC)
DDB = parse_devices(b'{"devices":[{"device_id":"ABC123","tracked":"Y","identified":"Y","registration":"G-CKML","cn":"KA","aircraft_model":"Duo Discus"}]}')
XML = b'<markers><m a="52.5,-2.8,OLD,G-OLD,1000,11:59:50,10,90,111.12,-0.6,1,TEST,ABC123,token"/></markers>'
APRS = 'FLRABC123>APRS,qAS,TEST:/115950h5230.00N/00248.00W\'090/060/A=003281 id06ABC123 -118fpm'


class OgnLiveTests(TestCase):
    def test_aprs_units_registry_and_cross_transport_deduplication(self):
        row=parse_aprs(APRS,now=NOW,devices=DDB)
        self.assertEqual(row['competition_id'],'KA');self.assertEqual(row['aircraft_model'],'Duo Discus')
        self.assertEqual((row['lat'],row['lon']),(52.5,-2.8))
        self.assertEqual(row['altitude_ft'],3281);self.assertEqual(row['groundspeed_kt'],60)
        self.assertEqual(row['vario_kt'],-1.2)
        cache=TrafficCache();cache.merge([row],now=NOW)
        other=parse_spot_xml(XML,now=NOW,devices=DDB)['aircraft']
        self.assertEqual(len(cache.merge(other,now=NOW)),1)
        self.assertEqual(len(cache.merge([],now=NOW+timedelta(minutes=14))),1)
        self.assertEqual(cache.merge([],now=NOW+timedelta(minutes=16)),[])

    def test_ground_sink_tug_and_fifteen_minute_positions_are_kept(self):
        for kind in (1,2,8):
            xml=XML.replace(b'111.12',b'0').replace(b',1,TEST',f',{kind},TEST'.encode()).replace(b'11:59:50,10',b'11:46:00,840')
            row=parse_spot_xml(xml,now=NOW,devices=DDB)['aircraft'][0]
            self.assertFalse(row['airborne']);self.assertEqual(row['age_seconds'],840)
            self.assertEqual(row['vario_kt'],-1.2)
        self.assertEqual(parse_spot_xml(XML.replace(b'11:59:50,10',b'11:40:00,1200'),now=NOW)['aircraft'],[])

    def test_legacy_packed_clock_only_layout_and_unknown_flarm(self):
        legacy=XML.replace(b'11:59:50,10,',b'11:59:50,')
        self.assertEqual(parse_spot_xml(legacy,now=NOW,devices=DDB)['aircraft'][0]['competition_id'],'KA')
        unknown=XML.replace(b',1,TEST',b',0,TEST')
        self.assertEqual(parse_spot_xml(unknown,now=NOW,devices=DDB)['aircraft'],[])
        registry={'ABC123':[dict(DDB['ABC123'][0],device_type='F')]}
        self.assertEqual(len(parse_spot_xml(unknown,now=NOW,devices=registry)['aircraft']),1)

    def test_key_value_layout_unknown_vario_and_anonymous_label(self):
        xml=b'<markers><m lat="53.3" lon="-7.5" id="ABC188" alt="150" time="2026-11-08T11:59:00Z" speed="5" type="1"/></markers>'
        row=parse_spot_xml(xml,now=NOW)['aircraft'][0]
        self.assertEqual(row['registration'],'?88');self.assertIsNone(row['vario_kt'])
        self.assertEqual(row['age_seconds'],60)
        with self.assertRaises(ValueError):parse_spot_xml(xml.replace(b'2026-11-08T11:59:00Z',b'bad'),now=NOW)

    def test_aprs_comment_unidentified_and_privacy_flags(self):
        for line in ('# welcome',APRS.replace('id06','id86'),APRS.replace('115950','105950'),APRS.replace('id06','id26')):
            self.assertIsNone(parse_aprs(line,now=NOW,devices=DDB))
        row=parse_aprs(APRS.replace('id06','id46'),now=NOW,devices=DDB)
        self.assertFalse(row['highlight']);self.assertEqual(row['registration'],'?23')
        hidden={'ABC123':[dict(DDB['ABC123'][0],tracked='N')]}
        self.assertIsNone(parse_aprs(APRS,now=NOW,devices=hidden))
        anonymous=parse_aprs(APRS,now=NOW)
        self.assertEqual(anonymous['registration'],'?23')

    def test_http_headers_and_bounded_read(self):
        response=MagicMock();response.__enter__.return_value=response;response.read.return_value=b'<markers/>'
        with patch('ogn_live.urlopen',return_value=response) as opener:
            self.assertEqual(fetch('https://live.glidernet.org/lxml.php',100),b'<markers/>')
        request=opener.call_args.args[0]
        self.assertIn('Mozilla/5.0',request.get_header('User-agent'))
        self.assertEqual(request.get_header('Referer'),'https://live.glidernet.org/')
        self.assertEqual(opener.call_args.kwargs['timeout'],6)
        response.read.assert_called_once_with(101)

    def test_receiver_login_reconnect_and_cache_with_mock_socket(self):
        cache=TrafficCache();receiver=None
        class Connection:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def sendall(self,login):self.login=login
            def settimeout(self,value):self.timeout=value
            def recv(self,size):
                receiver.stop.set()
                return (APRS+'\r\n').encode()
        connection=Connection();connector=Mock(side_effect=[OSError('Primary down'),connection])
        receiver=AprsReceiver(cache,lambda:DDB,lambda:True,connector=connector)
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return NOW
        with patch('ogn_live.datetime',Clock):
            receiver.stop.wait=Mock(return_value=False)
            receiver.run()
        self.assertEqual(connection.login,APRS_LOGIN)
        self.assertEqual(connector.call_args_list[0].args[0],('aprs.glidernet.org',14580))
        self.assertEqual(connector.call_args_list[1].args[0],('glidern1.glidernet.org',14580))
        self.assertEqual(len(cache.merge([],now=NOW)),1)

    def test_mirror_failure_preserves_recent_cache_and_never_publishes_demo(self):
        worker=TrafficWorker();worker.devices=DDB;worker.registry_at=time.time()
        now=datetime.now(UTC);clock=(now-timedelta(seconds=10)).strftime('%H:%M:%S').encode()
        xml=XML.replace(b'11:59:50',clock)
        with patch('ogn_traffic.fetch',side_effect=[OSError('primary down'),xml]):
            first=worker.once()
        self.assertEqual(first['status'],'live');self.assertEqual(len(first['aircraft']),1)
        with patch('ogn_traffic.fetch',side_effect=OSError('both down')):
            second=worker.once()
        self.assertEqual(second['status'],'degraded');self.assertEqual(len(second['aircraft']),1)
        self.assertEqual(second['aircraft'][0]['competition_id'],'KA')
        worker.devices={'ABC123':[dict(DDB['ABC123'][0],identified='N')]}
        with patch('ogn_traffic.fetch',side_effect=OSError('both down')):
            self.assertEqual(worker.once()['aircraft'][0]['registration'],'?23')
