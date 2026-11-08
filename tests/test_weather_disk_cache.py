"""Durable real-weather fallback preserves identity and rejects demonstrations."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import importlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest

from data_ingestion import JsonResponse
from forecast_quality import DEMO_SOURCE, DEMO_NOTE, is_surface_estimate
from met_norway import MET_URL, parse_met_forecast
from tests.fixtures import forecast
from tests.test_met_norway import START, POINT, met_body
import weather_disk_cache as disk


class WeatherDiskTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'forecast.json'
        self.real=forecast(start=START,hours=120,model='icon_eu')

    def test_roundtrip_survives_module_reload_and_preserves_other_requested_models(self):
        self.assertTrue(disk.save_live((self.real,),'icon_eu',self.path))
        other=replace(self.real,model='ecmwf_ifs025')
        self.assertTrue(disk.save_live((other,),'ifs025',self.path))
        importlib.reload(disk)
        for key,actual in (('icon_eu','icon_eu'),('ifs025','ecmwf_ifs025')):
            result=disk.load_live(key,START,self.path)
            self.assertEqual(result[0].model,actual)
            self.assertEqual(result[0].fetched_at,self.real.fetched_at)
            self.assertEqual(result[0].hourly['temperature_2m'],self.real.hourly['temperature_2m'])
        self.assertEqual(set(json.loads(self.path.read_text())['bundles']),{'icon_eu','ifs025'})

    def test_met_provider_identity_surface_only_warning_and_estimates_survive(self):
        met=parse_met_forecast(JsonResponse(met_body(),START),POINT,START)
        self.assertTrue(disk.save_live((met,),'ukmo',self.path))
        result=disk.load_live('ukmo',START,self.path)[0]
        self.assertEqual(result.model,'met_norway');self.assertEqual(result.source,MET_URL)
        self.assertTrue(is_surface_estimate(result))
        self.assertEqual(result.field_sources,met.field_sources)
        self.assertEqual(result.source_runs,met.source_runs)
        self.assertTrue(set(met.warnings)<=set(result.warnings))

    def test_demonstration_is_never_written_or_loaded(self):
        demo=replace(self.real,source=DEMO_SOURCE,warnings=(DEMO_NOTE,))
        self.assertFalse(disk.save_live((demo,),'icon_eu',self.path))
        self.assertFalse(self.path.exists())
        self.assertTrue(disk.save_live((self.real,demo),'icon_eu',self.path))
        self.assertEqual(len(disk.load_live('icon_eu',START,self.path)),1)
        payload=json.loads(self.path.read_text())
        payload['bundles']['icon_eu']['forecasts'][0]['source']=DEMO_SOURCE
        self.path.write_text(json.dumps(payload))
        self.assertEqual(disk.load_live('icon_eu',START,self.path),())

    def test_corruption_unknown_schema_oversize_and_expired_are_ignored(self):
        for raw in ('{broken',json.dumps({'schema_version':2,'bundles':{}}),'x'*(disk.MAX_BYTES+1)):
            self.path.write_text(raw)
            self.assertEqual(disk.load_live('icon_eu',START,self.path),())
        self.assertTrue(disk.save_live((self.real,),'icon_eu',self.path))
        self.assertEqual(disk.load_live('icon_eu',START+timedelta(days=8),self.path),())
        future=replace(self.real,fetched_at=datetime.now(UTC)+timedelta(hours=1))
        self.assertTrue(disk.save_live((future,),'icon_eu',self.path))
        self.assertEqual(disk.load_live('icon_eu',START,self.path),())

    def test_environment_path_readonly_failure_and_atomic_replace(self):
        with patch.dict('os.environ',{'DUO_WEATHER_CACHE_PATH':str(self.path)}):
            self.assertTrue(disk.save_live((self.real,),'icon_eu'))
            self.assertEqual(len(disk.load_live('icon_eu',START)),1)
        original=self.path.read_bytes()
        with patch.object(disk.os,'replace',side_effect=PermissionError('read only')):
            self.assertFalse(disk.save_live((self.real,),'ukmo',self.path))
        self.assertEqual(self.path.read_bytes(),original)
        self.assertFalse(list(self.path.parent.glob('.duo-weather-*')))

    def test_missing_requested_model_may_reuse_other_real_provider_without_relabelling(self):
        self.assertTrue(disk.save_live((self.real,),'icon_eu',self.path))
        restored=disk.load_live('ukmo',START,self.path)
        self.assertEqual(restored[0].model,'icon_eu')

    def test_nine_met_anchors_fit_size_cap_without_losing_provenance(self):
        met=parse_met_forecast(JsonResponse(met_body(),START),POINT,START)
        records=tuple(replace(met,location=replace(POINT,key=str(i))) for i in range(9))
        self.assertTrue(disk.save_live(records,'icon_eu',self.path))
        self.assertLess(self.path.stat().st_size,disk.MAX_BYTES)
        result=disk.load_live('icon_eu',START,self.path)
        self.assertEqual(len(result),9)
        self.assertEqual(result[0].field_sources,met.field_sources)


if __name__=='__main__':unittest.main()
