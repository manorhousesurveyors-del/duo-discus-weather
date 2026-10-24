"""Regression contracts for hidden maps, full days and natural pilot briefings."""
from datetime import timedelta
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
import numpy as np

from bga_turnpoints import load_catalog
from briefing_bot import (parse_intent,resolve_turnpoints,briefing_date,pilot_briefing,
                          gemini_key,gemini_briefing)
from config import FORECAST_MODELS, MONITORING_ZONES
from data_ingestion import OpenMeteoClient,JsonResponse,parse_forecast
from json_payload import browser_json
from tests.fixtures import NOW,forecast
from tests.test_ingestion import forecast_fixture

ROUTE='I want to fly from the Mynd to the Mynd North and Mynd South back to the Mynd'
QUESTION='Could you give me a briefing for tomorrow at the Mynd with strong West winds and ridge lift?'


class PilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.catalog=load_catalog()

    def test_requested_routes_and_stopwords(self):
        for text,codes in [(ROUTE,['MYN','MNDN','MNDS','MYN']),('mynd to mndN to Mynd',['MYN','MNDN','MYN']),
                           ('MGC to MYN N to MYN S back to Long Mynd',['MYN','MNDN','MNDS','MYN'])]:
            result=parse_intent(text,self.catalog)
            self.assertEqual(result.kind,'route');self.assertEqual([p.code for p in result.route],codes)
        self.assertEqual(resolve_turnpoints('the and can you fly for the day to the west',self.catalog),())
        self.assertNotEqual(parse_intent(QUESTION,self.catalog).kind,'turnpoint')
        self.assertEqual(resolve_turnpoints('Where is THE?',self.catalog)[0].code,'THE')
        self.assertFalse(parse_intent(ROUTE,self.catalog).route[1].official)

    def test_tomorrow_uses_today_not_selected_day(self):
        today=NOW.date()
        self.assertEqual(briefing_date(QUESTION,today+timedelta(days=3),today),today+timedelta(days=1))

    def test_no_task_hourly_briefing_and_honest_missing_data(self):
        day=NOW.date()+timedelta(days=1)
        answer,evidence=pilot_briefing(QUESTION,(forecast(MONITORING_ZONES['shropshire']),),day)
        self.assertGreaterEqual(len(evidence['hours']),8)
        for term in ('Wind and ridge','West launch','Cloud base','Thermal kt','Wave /100','duty instructor'):
            self.assertIn(term,answer)
        self.assertNotIn('|\n\n|',answer)
        self.assertNotIn('THE — Thetford',answer)
        answer,_=pilot_briefing(QUESTION,(),day)
        self.assertIn('Hourly forecast data is missing',answer)
        self.assertIn('west-facing ridge',answer)
        self.assertNotIn('Build or load a task first',answer)

    def test_rest_facade_preserves_local_briefing_on_provider_failure(self):
        from gemini_rest import Connection,GeminiError
        factory=MagicMock();client=factory.return_value
        client.connect.return_value=Connection(('gemini-3-flash-preview',),'key')
        client.generate.return_value=({'answer':'Review the wind.'},'gemini-3-flash-preview')
        text,source=gemini_briefing(QUESTION,{},'Checked hourly briefing',self.catalog,api_key='test-key',client_factory=factory)
        self.assertIn('Connected to Gemini',source);self.assertIn('Checked hourly briefing',text)
        client.generate.side_effect=GeminiError(429,'Quota exhausted')
        text,source=gemini_briefing(QUESTION,{},'Local briefing',self.catalog,api_key='test-key',client_factory=factory)
        self.assertEqual(text,'Local briefing');self.assertIn('HTTP 429',source)

    def test_key_precedence(self):
        with patch.dict('os.environ',{'GEMINI_API_KEY':'environment'}):
            self.assertEqual(gemini_key('runtime',{'GEMINI_API_KEY':'secret'}),'runtime')
            self.assertEqual(gemini_key('',{'GEMINI_API_KEY':'secret'}),'secret')
            self.assertEqual(gemini_key(),'environment')

    def test_route_loads_without_weather_and_survives_client_save(self):
        from streamlit.testing.v1 import AppTest
        from planning_ui import process_map_event,active_task
        import planning_ui
        script='''
from datetime import date
from bga_turnpoints import load_catalog
from planning_ui import briefing_panel
briefing_panel(load_catalog(),date(2026,10,24),'dry','ifs025',1,(),None,None,(),recorded=True,today=date(2026,10,24))
'''
        with tempfile.TemporaryDirectory() as directory,patch.dict('os.environ',{'DUO_TASK_STORE':directory,'GEMINI_API_KEY':''}):
            at=AppTest.from_string(script).run()
            at.chat_input[0].set_value(ROUTE).run()
            self.assertFalse(at.exception)
            saved=at.session_state['tasks_by_day']['2026-10-24']
            self.assertEqual([p.code for p in saved],['MYN','MNDN','MNDS','MYN'])
            self.assertIn('no complete hourly forecast',at.session_state['briefing_messages'][-1]['content'])
            day=NOW.date()
            payload={'date':day.isoformat(),'points':[{'code':p.code,'lat':p.latitude,'lon':p.longitude} for p in saved], 'complete':True}
            event={'last_object_clicked':{'duo':{'kind':'task_sync','value':json.dumps(payload),'nonce':'regression'}}}
            with patch.object(planning_ui.st,'session_state',{}):
                process_map_event(event,self.catalog,day,'Add to task',visual_override='route')
                self.assertEqual([p.code for p in active_task(day)],['MYN','MNDN','MNDS','MYN'])


class MapAndDayTests(unittest.TestCase):
    def test_nonfinite_nested_arrays_are_strict_json(self):
        value={'a':np.array([np.nan,np.inf,-np.inf,1]),'b':(np.float32('nan'),np.int64(2)), 'x':'</script>'}
        encoded=browser_json(value)
        self.assertEqual(json.loads(encoded)['a'],[None,None,None,1])
        self.assertEqual(json.loads(encoded)['b'],[None,2])
        self.assertNotIn('</script>',encoded)
        json.loads(encoded,parse_constant=lambda v:self.fail(v))

    def test_full_day_request_for_single_and_batch_and_parser(self):
        model=FORECAST_MODELS['ifs025'];point=MONITORING_ZONES['shropshire']
        body=forecast_fixture();start=NOW.replace(hour=0)-timedelta(hours=11)
        n=model.horizon_hours+25
        body['hourly']={'time':[int((start+timedelta(hours=h)).timestamp()) for h in range(n)],'temperature_2m':[12.]*n}
        http=Mock();http.get_json.return_value=JsonResponse(body,NOW,False)
        client=OpenMeteoClient(http=http)
        single=client.fetch(point,model)
        self.assertEqual(http.get_json.call_args.args[1]['past_hours'],24)
        batch=client.fetch_batched((point,),(model,))
        self.assertFalse(batch.failures)
        self.assertEqual(http.get_json.call_args.args[1]['past_hours'],24)
        self.assertEqual(len(single.times),n)
        self.assertEqual(single.times[-1],start+timedelta(hours=n-1))

    def test_empty_maps_mount_and_document_is_stable(self):
        from app import build_map
        from map_component import map_document
        from wave_map import add_wave_map
        a,_=build_map((),NOW,layers=[],visual_mode='route',map_identity='planner',turnpoints=load_catalog().turnpoints,live_airfields=False)
        b,_=build_map((),NOW,layers=[],visual_mode='route',map_identity='planner',turnpoints=load_catalog().turnpoints,live_airfields=False)
        self.assertEqual(map_document(a),map_document(b))
        self.assertIn('installDuoTask',map_document(a)['script'])
        add_wave_map(b,(),NOW)
        self.assertIn('installDuoWave',map_document(b)['script'])
        self.assertIn('ResizeObserver',map_document(b)['script'])
        self.assertIn('IntersectionObserver',map_document(b)['script'])
