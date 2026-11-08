"""MET Norway provider boundary, provenance, and deadline regressions."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from threading import Event, Lock
from time import perf_counter, sleep
from unittest.mock import Mock
import unittest

from config import GeoPoint
from data_ingestion import JsonResponse, SchemaError
from map_weather import wind_payload
from met_norway import (FIELDS, MET_URL, USER_AGENT, MetHttpClient,
                       fetch_met_forecasts, parse_met_forecast)
from tests.test_ingestion import Response

START = datetime(2026,11,8,tzinfo=UTC)
POINT = GeoPoint('mynd','Long Mynd',52.518,-2.88)


def met_body():
    units = {key:unit for key,unit,_ in FIELDS.values()}
    units['precipitation_amount']='mm'
    rows=[]
    for hour in (*range(48),*range(48,145,6)):
        details={'air_temperature':18.,'relative_humidity':65.,'wind_speed':5.,
                 'wind_from_direction':270.,'wind_speed_of_gust':8.,'cloud_area_fraction':40.,
                 'cloud_area_fraction_low':30.,'cloud_area_fraction_medium':20.,
                 'cloud_area_fraction_high':10.,'air_pressure_at_sea_level':1014.}
        data={'instant':{'details':details}}
        data['next_1_hours' if hour<48 else 'next_6_hours']={'details':{'precipitation_amount':.2 if hour<48 else 18.}}
        rows.append({'time':(START+timedelta(hours=hour)).isoformat().replace('+00:00','Z'),'data':data})
    return {'type':'Feature','geometry':{'type':'Point','coordinates':[-2.88,52.518,430.]},
            'properties':{'meta':{'updated_at':START.isoformat(),'units':units},'timeseries':rows}}


class MetNorwayTests(unittest.TestCase):
    def parse(self,body=None):
        return parse_met_forecast(JsonResponse(met_body() if body is None else body,START),POINT,START)

    def test_surface_mapping_five_days_and_explicit_estimated_profiles(self):
        forecast=self.parse()
        self.assertEqual(forecast.source,MET_URL)
        self.assertEqual(forecast.model,'met_norway')
        self.assertEqual(len(forecast.times),120)
        self.assertEqual(forecast.times[-1],START+timedelta(hours=119))
        self.assertEqual(forecast.hourly['wind_speed_10m'][12],5.)
        self.assertEqual(forecast.hourly['cloud_cover_mid'][12],20.)
        self.assertGreater(forecast.hourly['dew_point_2m'][12],0.)
        self.assertLess(forecast.hourly['surface_pressure'][12],1014.)
        self.assertGreater(forecast.hourly['shortwave_radiation'][12],0.)
        self.assertEqual(forecast.hourly['shortwave_radiation'][0],0.)
        self.assertEqual(forecast.hourly['boundary_layer_height'][0],0.)
        self.assertIn('not atmospheric boundary layer',forecast.field_sources['boundary_layer_height'][0])
        self.assertGreater(forecast.hourly['boundary_layer_height'][12],0.)
        self.assertTrue(any('surface-only' in note for note in forecast.warnings))
        for key in ('wind_speed_850hPa','temperature_700hPa','relative_humidity_500hPa','shortwave_radiation'):
            self.assertIn('estimated',forecast.field_sources[key][12])
        self.assertIn('no measured upper-air',forecast.field_sources['wind_speed_850hPa'][12])
        wind=wind_payload((forecast,),START)
        self.assertTrue(all(wind['stations'][0]['values'][12]))

    def test_six_hour_tail_interpolation_wraps_direction_and_preserves_rain_amount(self):
        body=met_body()
        body['properties']['timeseries'][48]['data']['instant']['details']['wind_from_direction']=359.
        body['properties']['timeseries'][49]['data']['instant']['details']['wind_from_direction']=1.
        forecast=self.parse(body)
        self.assertGreater(forecast.hourly['wind_direction_10m'][50],350.)
        self.assertIn('6-hour',forecast.field_sources['wind_direction_10m'][50])
        self.assertAlmostEqual(sum(forecast.hourly['precipitation'][48:54]),18.)
        self.assertIn('6-hour precipitation period mean',forecast.field_sources['precipitation'][50])

    def test_missing_native_fields_and_coverage_are_never_backfilled_with_invented_weather(self):
        body=met_body();body['properties']['timeseries']=body['properties']['timeseries'][6:20]
        for row in body['properties']['timeseries']:
            row['data']['instant']['details'].pop('wind_speed')
            row['data'].pop('next_1_hours')
        forecast=self.parse(body)
        self.assertTrue(all(v is None for v in forecast.hourly['temperature_2m'][:6]))
        self.assertTrue(all(v is None for v in forecast.hourly['temperature_2m'][20:]))
        self.assertTrue(all(v is None for v in forecast.hourly['wind_speed_10m']))
        self.assertTrue(all(v is None for v in forecast.hourly['wind_speed_850hPa']))
        self.assertTrue(all(v is None for v in forecast.hourly['precipitation']))

    def test_empty_malformed_and_wrong_units_are_rejected(self):
        bodies=[{}, {'properties':{'timeseries':[]}}, met_body(),met_body()]
        bodies[2]['properties']['meta']['units']['air_temperature']='kelvin'
        bodies[3]['properties']['timeseries'][0]['data']['instant']['details']['air_temperature']='warm'
        for body in bodies:
            with self.subTest(body=str(body)[:70]),self.assertRaises(SchemaError):self.parse(body)

    def test_identifying_user_agent_and_short_timeout(self):
        seen=[]
        def opener(request,**kwargs):
            seen.append((request.get_header('User-agent'),kwargs['timeout']))
            return Response(json.dumps(met_body()).encode())
        client=MetHttpClient(opener=opener,clock=lambda:START,sleep=lambda _:self.fail('No retry sleeps'))
        result=fetch_met_forecasts((POINT,),http=client,anchor=START)
        self.assertFalse(result.failures)
        self.assertEqual(seen,[(USER_AGENT,1.25)])

    def test_nine_anchors_fetch_concurrently_and_timeout_does_not_block_caller(self):
        points=tuple(GeoPoint(str(i),str(i),52+i*.1,-3.) for i in range(9))
        lock=Lock();active=[0,0]
        def get(*args,**kwargs):
            with lock:active[0]+=1;active[1]=max(active)
            sleep(.04)
            with lock:active[0]-=1
            return JsonResponse(met_body(),START)
        started=perf_counter()
        result=fetch_met_forecasts(points,http=Mock(get_json=get),anchor=START)
        self.assertEqual(len(result.forecasts),9)
        self.assertEqual(active[1],9)
        self.assertLess(perf_counter()-started,1.0)
        release=Event()
        def blocked(*args,**kwargs):
            release.wait(3)
            return JsonResponse(met_body(),START)
        try:
            started=perf_counter()
            result=fetch_met_forecasts(points,http=Mock(get_json=blocked),anchor=START)
            self.assertLess(perf_counter()-started,1.8)
            self.assertEqual(len(result.failures),9)
            self.assertFalse(result.forecasts)
        finally:release.set()


if __name__=='__main__':unittest.main()
