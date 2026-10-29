"""Watchdog runtime, partial providers, exit policy and deployment contract."""
from dataclasses import replace
from datetime import datetime, UTC, timedelta
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from threading import Event, Lock
from time import monotonic
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import yaml
import alerts
from config import FORECAST_MODELS, HOURLY_VARIABLES, MONITORING_ZONES, Settings, forecast_locations
from data_ingestion import ForecastBatch, ServiceError
from database import DatabaseManager
from day_rating import rate_day
from tests.fixtures import NOW, forecast
from tests.test_forecast_coverage import missing_model_fixture, START
from watchdog_weather import WatchdogHttpClient, fetch_weather

ROOT = Path(__file__).resolve().parents[1]


class WatchdogBudgetTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def group(self, models=None, points=None):
        return [('regional', points or (MONITORING_ZONES['shropshire'],),
                 models or tuple(FORECAST_MODELS.values()), HOURLY_VARIABLES, 1)]

    def test_single_attempt_short_timeout_and_no_retry_or_pacing_sleep(self):
        for exception in (TimeoutError('slow'), HTTPError('https://api.open-meteo.com',429,'limited',{'Retry-After':'60'},None)):
            opener, sleep = Mock(side_effect=exception), Mock(side_effect=AssertionError('No sleeps'))
            http = WatchdogHttpClient(opener=opener, sleep=sleep)
            with self.assertRaises(ServiceError):
                http.get_json('https://api.open-meteo.com/v1/forecast', {'latitude':'52,53','hourly':'temperature_2m'}, ttl_seconds=0)
            opener.assert_called_once();sleep.assert_not_called()
            self.assertEqual(opener.call_args.kwargs['timeout'],3.5)

    def test_deadline_keeps_fast_provider_without_waiting_for_stalled_dns(self):
        release = Event(); self.addCleanup(release.set)
        f = forecast(model='ecmwf_ifs025')
        def request(points, model, fields):
            if model.api_name != 'ecmwf_ifs025': release.wait(5)
            return ([f],[]) if model.api_name == 'ecmwf_ifs025' else ([],[])
        client = Mock(settings=Settings());client._fetch_chunk.side_effect = request
        start = monotonic()
        batch = fetch_weather(self.group(),client=client,budget=.15)['regional']
        self.assertLess(monotonic()-start,.4)
        self.assertEqual(batch.forecasts,(f,));self.assertEqual(len(batch.failures),2)
        self.assertTrue(all('deadline' in failure.message for failure in batch.failures))

    def test_at_most_twelve_concurrent_jobs_and_every_point_uses_one_request(self):
        lock, barrier = Lock(), Event()
        active = peak = calls = 0
        def request(*args):
            nonlocal active, peak, calls
            with lock:
                active+=1;calls+=1;peak=max(peak,active)
                if peak==12:barrier.set()
            barrier.wait(.5)
            with lock:active-=1
            return [],[]
        client=Mock(settings=Settings());client._fetch_chunk.side_effect=request
        batch=fetch_weather(self.group(points=forecast_locations()),client=client,budget=2)
        self.assertEqual(calls,69);self.assertEqual(peak,12);self.assertFalse(batch['regional'].failures)

    def test_daemon_fetch_workers_do_not_delay_cli_exit_on_a_hung_socket(self):
        code='''
from watchdog_weather import fetch_weather
from config import Settings,FORECAST_MODELS,MONITORING_ZONES,HOURLY_VARIABLES
from threading import Event
class Client:
    settings=Settings()
    def _fetch_chunk(self,*args): Event().wait(100)
result=fetch_weather([('test',(MONITORING_ZONES['shropshire'],),tuple(FORECAST_MODELS.values()),HOURLY_VARIABLES,1)],client=Client(),budget=.1)
assert len(result['test'].failures)==3
'''
        start=monotonic()
        subprocess.run([str(ROOT/'.venv/bin/python') if (ROOT/'.venv/bin/python').exists() else __import__('sys').executable,'-c',code],cwd=ROOT,check=True,timeout=3)
        self.assertLess(monotonic()-start,3)

    def test_programming_errors_propagate_instead_of_becoming_weather_warnings(self):
        client=Mock(settings=Settings());client._fetch_chunk.side_effect=RuntimeError('broken parser')
        with self.assertRaisesRegex(RuntimeError,'broken parser'):
            fetch_weather(self.group(),client=client,budget=1)

    def test_watchdog_uses_same_hourly_completion_for_all_models_and_manual_outlook(self):
        batch=ForecastBatch(missing_model_fixture(),())
        with patch('alerts.fetch_weather',return_value={'regional':batch}) as fetch:
            bundle=alerts.collect_forecasts(include_grid=False)
        self.assertEqual(len(fetch.call_args.args[0]),1)
        for model in FORECAST_MODELS.values():
            for day in range(5):
                rating=rate_day(bundle.forecasts,MONITORING_ZONES['shropshire'],START.date()+timedelta(days=day),preference=(model.api_name,))
                self.assertEqual(rating.covered_hours,8);self.assertIn(rating.stars,range(1,6))
        with patch('alerts.fetch_weather',return_value={'local':batch}):
            manual=alerts.build_test_briefing(alerts.collect_test_forecasts(),START)
        self.assertTrue(all(r.covered_hours==8 for r in manual.ratings))

    def test_total_weather_outage_exits_zero_and_reports_unavailable_not_good_weather(self):
        bundle=alerts.ForecastBundle((),(),.5,('Open-Meteo HTTP 429 / timeout',),NOW)
        with patch('alerts.collect_forecasts',return_value=bundle),patch('sys.stdout',new=io.StringIO()):
            self.assertEqual(alerts.main(['--dry-run','--output-dir',str(self.root)]),0)
        report=json.loads((self.root/'watchdog-summary.json').read_text())
        self.assertTrue(all(b['evaluation']=='unavailable' and not b['triggers'] for b in report['briefings']))
        self.assertIn('Open-Meteo HTTP 429', (self.root/'briefing-2day.html').read_text())

    def test_weather_warning_does_not_block_triggered_email_and_scheduled_delivery_errors_are_reported(self):
        now=datetime.now(UTC)
        bundle=alerts.ForecastBundle((),(),.5,('Weather timeout',),now)
        briefing=alerts.Briefing(2,now,now,(),(),(),None,('Synthetic trigger',),bundle.errors)
        db=DatabaseManager(self.root/'ledger.sqlite3')
        with patch.dict(os.environ,{'GMAIL_APP_PASSWORD':'abcd efgh ijkl mnop','DUO_ALERT_CHANNELS':'email'},clear=True), \
             patch('alerts.collect_forecasts',return_value=bundle),patch('alerts.build_briefing',return_value=briefing), \
             patch('alerts.open_delivery_database',return_value=db),patch('sys.stdout',new=io.StringIO()), \
             patch('alerts.send_email',side_effect=alerts.DeliveryError('SMTP unavailable')) as sender:
            self.assertEqual(alerts.main(['--send','--scheduled','--output-dir',str(self.root)]),0)
        sender.assert_called_once()
        report=json.loads((self.root/'watchdog-summary.json').read_text())
        self.assertTrue(report['delivery_warning'])
        self.assertEqual(db.list_alerts()[0]['status'],'uncertain')

    @unittest.skipUnless(hasattr(signal,'setitimer'),'Linux whole-exchange deadline')
    def test_notification_budget_holds_uncertain_claim_and_defers_unstarted_channels(self):
        settings=alerts.DeliverySettings(channels=('email','ntfy'),gmail_app_password='test')
        b=alerts.Briefing(2,NOW,NOW,(),(),(),None,('trigger',),())
        db=DatabaseManager(self.root/'ledger.sqlite3');push=Mock()
        start=monotonic()
        outcomes=alerts.dispatch(b,db,settings,deadline=start+.05,
                                senders={'email':lambda *args:Event().wait(5),'ntfy':push})
        self.assertLess(monotonic()-start,.3)
        self.assertEqual(outcomes['email'],'uncertain');self.assertIn('deferred:',outcomes['ntfy'])
        self.assertEqual(len(db.list_alerts()),1);push.assert_not_called()

    def test_sender_programming_crash_marks_claim_uncertain_and_is_not_swallowed(self):
        settings=alerts.DeliverySettings(channels=('email',),gmail_app_password='test')
        b=alerts.Briefing(2,NOW,NOW,(),(),(),None,('trigger',),())
        db=DatabaseManager(self.root/'ledger.sqlite3')
        with self.assertRaisesRegex(TypeError,'broken sender'):
            alerts.dispatch(b,db,settings,senders={'email':Mock(side_effect=TypeError('broken sender'))})
        self.assertEqual(db.list_alerts()[0]['status'],'uncertain')

    def test_workflow_uses_small_runtime_and_retains_state_thirty_days_with_tests_on_code_changes(self):
        workflow=yaml.safe_load((ROOT/'.github/workflows/soaring_watchdog.yml').read_text())
        steps={s.get('name'):s for s in workflow['jobs']['watchdog']['steps']}
        self.assertNotIn('Run offline tests',steps)
        self.assertIn('requirements-watchdog.txt',steps['Install dependencies']['run'])
        state=steps['Persist delivery ledger even after send errors']['with']
        self.assertEqual(state['name'],'soaring-watchdog-state');self.assertEqual(state['retention-days'],30)
        self.assertIn('watchdog-summary.json',steps['Save evaluation reports']['with']['path'])
        for pin in (ROOT/'requirements-watchdog.txt').read_text().splitlines():
            if pin and not pin.startswith('#'):self.assertIn(pin,(ROOT/'requirements.txt').read_text().splitlines())
        checks=yaml.safe_load((ROOT/'.github/workflows/tests.yml').read_text())
        self.assertTrue(any('unittest discover' in s.get('run','') for s in checks['jobs']['tests']['steps']))


if __name__=='__main__':unittest.main()
