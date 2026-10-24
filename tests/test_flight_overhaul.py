from datetime import timedelta
import io,json
import unittest
from unittest.mock import Mock,patch
from urllib.error import HTTPError
from urllib.parse import urlsplit,parse_qs
import numpy as np
from gemini_rest import GeminiREST,GeminiError,model_order
from flight_briefing import build_context,local_response,requested_site,validated_suggestions,SITES
from briefing_bot import resolve_turnpoints,briefing_date
from bga_turnpoints import load_catalog
from tests.fixtures import NOW,forecast
from config import forecast_locations,GeoPoint

class RESTTests(unittest.TestCase):
    def response(self,value):return io.BytesIO(json.dumps(value).encode())
    def test_models_pagination_api_key_and_only_account_models(self):
        calls=[]
        def open_(request,**kw):
            calls.append(request)
            return self.response({'models':[{'name':'models/gemini-3-flash-preview','supportedGenerationMethods':['generateContent']}],'nextPageToken':'two'}) if len(calls)==1 else self.response({'models':[{'name':'models/gemini-3.1-pro-preview','supportedGenerationMethods':['generateContent']},{'name':'models/gemini-3-image','supportedGenerationMethods':['generateContent']}]})
        result=GeminiREST('AIza-test',opener=open_).connect()
        self.assertEqual(result.models,('gemini-3.1-pro-preview','gemini-3-flash-preview'))
        self.assertNotIn('gemini-3.8-flash',result.models)
        self.assertEqual(parse_qs(urlsplit(calls[0].full_url).query)['key'],['AIza-test'])
        self.assertEqual(parse_qs(urlsplit(calls[1].full_url).query)['pageToken'],['two'])

    def test_aq_bearer_and_redacted_exact_errors(self):
        calls=[]
        def open_(r,**kw):
            calls.append(r)
            raise HTTPError(r.full_url,403,'Forbidden',{},self.response({'error':{'status':'PERMISSION_DENIED','message':'Disabled for AQ.private-token'}}))
        result=GeminiREST('AQ.private-token','bearer',opener=open_).connect()
        self.assertEqual(calls[0].get_header('Authorization'),'Bearer AQ.private-token')
        self.assertNotIn('key=',calls[0].full_url)
        self.assertIn('HTTP 403',result.error);self.assertIn('PERMISSION_DENIED',result.error)
        self.assertNotIn('AQ.private-token',result.error)

    def test_rest_model_fallback_and_free_form_prompt(self):
        calls=[]
        def open_(r,**kw):
            calls.append(r)
            if len(calls)==1:raise HTTPError(r.full_url,404,'Not Found',{},self.response({'error':{'message':'Model not enabled'}}))
            return self.response({'candidates':[{'content':{'parts':[{'text':json.dumps({'answer':'Denbigh has an afternoon window.','suggested_routes':[]})}]}}]})
        client=GeminiREST('test','key',opener=open_)
        answer,active=client.generate('Can we potter around Denbigh?',{'site':'Denbigh'},models=('gemini-3.8-flash','gemini-3-flash-preview'))
        self.assertEqual(active,'gemini-3-flash-preview');self.assertIn('Denbigh',answer['answer'])
        payload=json.loads(calls[1].data)
        self.assertIn('Can we potter',payload['contents'][-1]['parts'][0]['text'])
        self.assertNotIn('250',payload['contents'][-1]['parts'][0]['text'])
        self.assertNotIn('test',payload['systemInstruction']['parts'][0]['text'])

    def test_quota_error_does_not_spin_through_models(self):
        def open_(r,**kw):raise HTTPError(r.full_url,429,'Too Many Requests',{},self.response({'error':{'message':'Quota exhausted'}}))
        opener=Mock(side_effect=open_)
        with self.assertRaisesRegex(GeminiError,'HTTP 429'):
            GeminiREST('test','key',opener=opener).generate('weather',{},models=('gemini-3-flash-preview','gemini-2.5-flash'))
        self.assertEqual(opener.call_count,1)

class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog=load_catalog();cls.forecasts=tuple(forecast(p) for p in forecast_locations())
    def test_no_lowercase_english_becomes_potton_or_thetford(self):
        self.assertEqual(resolve_turnpoints('can you pot around the west and fly for the day',self.catalog),())
        self.assertEqual(resolve_turnpoints('POT',self.catalog)[0].code,'POT')
    def test_clicked_point_and_site_specific_ridge(self):
        point=GeoPoint('clicked','Exact click',53.16,-3.25)
        context,_=build_context('weather forecast here',self.catalog,self.forecasts,NOW.date(),NOW.date(),'dry',selected=point)
        self.assertEqual(context['site']['site'],'Exact click')
        self.assertEqual(context['site']['hours'][0]['ridge_facing_deg'],270)
        self.assertEqual(len(context['site']['hours'][0]['wave_bands']),9)
        self.assertTrue(context['special_forecast'])
        text=local_response('weather forecast here',context)
        for name in ('Goole','Didcot','Church Stretton','Snowdonia','South Wales','3.5–4.5','4,500–5,000','Five-day','STF'):
            self.assertIn(name,text)
        self.assertNotIn('No achievable 250',text)
        self.assertTrue(all(len(row['five_days'])==5 for row in context['regions']))
        self.assertTrue(all('hours' in day for row in context['regions'] for day in row['five_days']))
        denbigh=next(row for row in context['regions'] if row['key']=='denbigh')
        self.assertIsNotNone(denbigh['five_days'][1]['stars'])
    def test_model_routes_must_resolve_catalog_and_missing_data_stays_unknown(self):
        response={'suggested_routes':[{'codes':['MYN','MNDN','MYN']},{'codes':['FAKE','MYN']}]}
        self.assertEqual(len(validated_suggestions(response,self.catalog)),1)
        context,_=build_context('Where should we fly tomorrow?',self.catalog,(),NOW.date()+timedelta(days=1),NOW.date(),'dry')
        text=local_response('Where should we fly tomorrow?',context)
        self.assertIn('unknown',text);self.assertIn('Five-day outlook',text)
        self.assertFalse(any(r['midday'] for r in context['regions']))

class ContinuousTests(unittest.TestCase):
    def test_one_grid_covers_named_ranges_and_no_profile_means_no_wave(self):
        from terrain_wave import load_continuous_terrain,continuous_field,mercator_y
        grid=load_continuous_terrain();self.assertEqual(grid['bounds'],[[50,-8],[59,2]])
        h=grid['heights'];self.assertTrue(np.isfinite(h).all())
        for lat,lon in [(53.07,-4.08),(52.47,-3.78),(51.9,-3.4),(54.7,-2.48),(56.8,-5),(57.2,-3.5),(58,-5)]:
            x=int((lon+8)/10*h.shape[1]);y=int((mercator_y(lat)-mercator_y(50))/(mercator_y(59)-mercator_y(50))*h.shape[0])
            self.assertGreater(h[max(0,y-5):y+6,max(0,x-5):x+6].max(),300)
        fields,winds,profiles=continuous_field(grid,(),NOW,(3000,7000))
        self.assertFalse(profiles);self.assertTrue(all(np.isnan(f).all() for f in fields))
    def test_blend_has_no_site_ownership_seam_and_includes_background_ascent(self):
        from terrain_wave import continuous_field
        grid={'heights':np.ones((32,32))*100,'bounds':[[51,-4],[54,-1]],'spacing_m':2000}
        forecasts=(forecast(GeoPoint('ridge:a','West',52,-3.5)),forecast(GeoPoint('ridge:b','East',53,-1.5)))
        def field(h,s,p,altitudes,**kw):return [np.full(h.shape,1 if p.forecast.location.name=='West' else 3)],{}
        with patch('terrain_wave.solve_field',side_effect=field):fields,_,_=continuous_field(grid,forecasts,NOW,(7000,))
        self.assertTrue(np.isfinite(fields[0]).all())
        self.assertLess(np.max(np.abs(np.diff(fields[0],axis=1))),.4)
