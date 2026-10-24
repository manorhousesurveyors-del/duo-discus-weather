from dataclasses import replace
from datetime import UTC,datetime,timedelta
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch

import yaml
import alerts
from database import AlertKey,DatabaseManager
from scripts.watchdog_state import backup,prepare,verify
from tests.fixtures import NOW,forecast

ROOT=Path(__file__).resolve().parents[1]


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.root=Path(folder.name)

    def test_missing_empty_json_corrupt_and_unsupported_artifacts_start_empty(self):
        for name,raw in (('missing',None),('empty',b''),('json',b'{}'),('corrupt',b'not sqlite')):
            with self.subTest(name=name):
                source=self.root/(name+'.sqlite3');target=self.root/name/'working.sqlite3'
                if raw is not None:source.write_bytes(raw)
                with patch('sys.stdout',new=io.StringIO()) as output:
                    self.assertEqual(prepare(target,source),'fresh')
                self.assertIn('empty alert state {}',output.getvalue());verify(target)
                self.assertEqual(DatabaseManager(target).list_alerts(),[])
                if raw is not None:self.assertEqual(source.read_bytes(),raw)
        source=self.root/'future.sqlite3';DatabaseManager(source)
        with sqlite3.connect(source) as db:db.execute('PRAGMA user_version=999')
        self.assertEqual(prepare(self.root/'new.sqlite3',source),'fresh')

    def test_valid_artifact_retains_sends_and_corrupt_working_copy_is_quarantined(self):
        source=self.root/'original.sqlite3';database=DatabaseManager(source)
        key=AlertKey(NOW.date(),5,'briefing','national','email','test@example.test')
        claim=database.claim_alert(key);database.start_delivery(claim);database.mark_sent(claim,provider_message_id='accepted')
        backup(source,self.root/'artifact')
        target=self.root/'working.sqlite3';target.write_bytes(b'broken')
        self.assertEqual(prepare(target,self.root/'artifact'/'alerts.sqlite3'),'restored')
        self.assertEqual(DatabaseManager(target).list_alerts(),database.list_alerts())
        self.assertIsNone(DatabaseManager(target).claim_alert(key))
        self.assertEqual(next((self.root/'rejected-state').glob('*/working.sqlite3')).read_bytes(),b'broken')
        self.assertEqual(prepare(target),'existing')

    def test_schema_one_artifact_migrates_and_wrong_table_contract_resets(self):
        source=self.root/'old.sqlite3';DatabaseManager(source)
        with sqlite3.connect(source) as db:db.execute('PRAGMA user_version=1')
        target=self.root/'working.sqlite3'
        self.assertEqual(prepare(target,source),'restored');verify(target)
        with sqlite3.connect(source) as db:
            db.execute('PRAGMA user_version=2');db.execute('ALTER TABLE alerts RENAME COLUMN recipient TO invalid_name')
        self.assertEqual(prepare(self.root/'other.sqlite3',source),'fresh')

    def test_alert_cli_database_open_recovers_corruption(self):
        path=self.root/'corrupt.sqlite3';path.write_text('{}')
        with patch.dict(os.environ,{'DUO_DATABASE_PATH':str(path)}):
            database=alerts.open_delivery_database()
        self.assertEqual(database.list_alerts(),[]);verify(path)

    @unittest.skipUnless(shutil.which('node'),'Node is required for GitHub script regression checks')
    def test_github_discovery_handles_run_three_and_api_failures(self):
        subprocess.run(['node','tests/watchdog_state_discovery.cjs'],cwd=ROOT,check=True,capture_output=True,text=True)

    def test_workflow_routes_manual_send_to_one_test_and_tolerates_restore_failures(self):
        workflow=yaml.safe_load((ROOT/'.github/workflows/soaring_watchdog.yml').read_text())
        steps=workflow['jobs']['watchdog']['steps'];lookup={s.get('name'):s for s in steps}
        for name in ('Find durable alert state','Restore previous ledger'):
            self.assertTrue(lookup[name]['continue-on-error'])
        self.assertIn('prepare --restored',lookup['Validate or initialize ledger']['run'])
        executable=self.root/'python';executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n');executable.chmod(0o755)
        command=lookup['Evaluate and dispatch']['run']
        for manual,send,expected in (('true','true','--test-email'),('false','true','--send'),('false','false','--dry-run')):
            result=subprocess.run(['bash','-eu','-c',command],capture_output=True,text=True,check=True,
                env={**os.environ,'PATH':str(self.root)+os.pathsep+os.environ['PATH'],'MANUAL_TEST_EMAIL':manual,'SEND_NOTIFICATIONS':send})
            self.assertEqual(result.stdout.splitlines(),['alerts.py',expected])


class ManualEmailTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.root=Path(folder.name)
        f=replace(forecast(start=NOW.replace(hour=0),hours=24*6,changes={'precipitation':3}),fetched_at=NOW)
        self.bundle=alerts.ForecastBundle((f,),(),.5,(),NOW)
        self.env={'GMAIL_APP_PASSWORD':'abcd efgh ijkl mnop','DUO_ALERT_CHANNELS':'ntfy,whatsapp',
                  'DUO_EMAIL_TO':'ignored@example.test','SMTP_USERNAME':'manorhousesurveyors@gmail.com'}

    def test_manual_send_twice_ignores_thresholds_channels_and_history_and_has_five_stars_days(self):
        smtp=Mock();smtp.send_message.return_value={}
        with patch.dict(os.environ,self.env,clear=True),patch('alerts.datetime',wraps=datetime) as clock, \
             patch('alerts.collect_test_forecasts',return_value=self.bundle),patch('alerts.collect_forecasts') as national, \
             patch('alerts.DatabaseManager') as ledger,patch('alerts.dispatch') as regular, \
             patch('alerts.smtplib.SMTP_SSL') as factory,patch('sys.stdout',new=io.StringIO()):
            clock.now.return_value=NOW;factory.return_value.__enter__.return_value=smtp
            for _ in range(2):self.assertEqual(alerts.main(['--test-email','--output-dir',str(self.root)]),0)
        self.assertEqual(smtp.send_message.call_count,2);ledger.assert_not_called();regular.assert_not_called();national.assert_not_called()
        messages=[call.args[0] for call in smtp.send_message.call_args_list]
        self.assertNotEqual(messages[0]['Message-ID'],messages[1]['Message-ID'])
        for message in messages:
            self.assertEqual(message['To'],'manorhousesurveyors@gmail.com');self.assertIn('TEST',message['Subject'])
            html=message.get_body(preferencelist=('html',)).get_content()
            self.assertIn('Five-day Star Rating',html);self.assertEqual(html.count('★☆☆☆☆'),5)
            for i in range(5):self.assertIn((NOW+timedelta(days=i)).strftime('%a %d %b'),html)
        smtp.login.assert_called_with('manorhousesurveyors@gmail.com','abcdefghijklmnop')
        report=json.loads((self.root/'briefing-test-email.json').read_text())
        self.assertEqual(report['delivery'],'accepted');self.assertEqual(report['briefing']['triggers'],[])

    def test_forecast_outage_still_sends_with_five_unavailable_days(self):
        with patch.dict(os.environ,self.env,clear=True),patch('alerts.collect_test_forecasts',side_effect=OSError('offline')), \
             patch('alerts.send_email',return_value='accepted') as sender,patch('sys.stdout',new=io.StringIO()):
            self.assertEqual(alerts.main(['--test-email','--output-dir',str(self.root)]),0)
        b=sender.call_args.args[1];self.assertEqual(len(b.ratings),5);self.assertTrue(all(r.stars is None for r in b.ratings))
        self.assertIn('Forecast unavailable',alerts.render_html(b))

    def test_stale_snapshot_is_excluded_but_does_not_block_explicit_delivery_test(self):
        path=self.root/'snapshot.json';alerts.save_bundle(self.bundle,path)
        with patch.dict(os.environ,self.env,clear=True),patch('alerts.datetime',wraps=datetime) as clock, \
             patch('alerts.send_email',return_value='accepted') as sender,patch('sys.stdout',new=io.StringIO()):
            clock.now.return_value=NOW+timedelta(days=1)
            self.assertEqual(alerts.main(['--test-email','--snapshot',str(path),'--output-dir',str(self.root)]),0)
        self.assertTrue(all(r.stars is None for r in sender.call_args.args[1].ratings))

    def test_rejected_email_remains_an_actual_delivery_failure(self):
        with patch.dict(os.environ,self.env,clear=True),patch('alerts.collect_test_forecasts',return_value=self.bundle), \
             patch('alerts.send_email',side_effect=alerts.DeliveryError('SMTP rejected test',uncertain=False)), \
             patch('sys.stdout',new=io.StringIO()):
            self.assertEqual(alerts.main(['--test-email','--output-dir',str(self.root)]),2)
        self.assertEqual(json.loads((self.root/'briefing-test-email.json').read_text())['delivery'],'failed')


if __name__=='__main__':unittest.main()
