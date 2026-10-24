from dataclasses import replace
from datetime import UTC,datetime,timedelta
import io
import json
from pathlib import Path
import smtplib
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from alerts import (Briefing, DeliveryError, DeliverySettings, ForecastBundle, build_briefing,
    dispatch, load_bundle, main, render_html, render_whatsapp, save_bundle, send_email,
    send_whatsapp, send_ntfy, strong_convergence_regions)
from config import FORECAST_MODELS, forecast_locations
from database import DatabaseManager
from soaring_physics import ConvergenceCell, ConvergenceResult
from tests.fixtures import NOW, forecast
from tests.test_ingestion import Response
from scripts.watchdog_state import backup, verify


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)
        self.db=DatabaseManager(self.path/'ledger.sqlite3')
        self.settings=DeliverySettings(channels=('email','whatsapp'),gmail_app_password='synthetic-secret',callmebot_api_key='synthetic-api-key')
        self.briefing=Briefing(2,NOW+timedelta(days=2),NOW,(),(),(),None,('Synthetic trigger',),())

    def test_horizons_use_enclosing_hour_and_preserve_model_comparisons(self):
        bundle=ForecastBundle(tuple(forecast(p,model=m.api_name) for p in forecast_locations()
                                   for m in FORECAST_MODELS.values()),(),.5,(),NOW)
        b=build_briefing(bundle,5,now=NOW+timedelta(minutes=17))
        self.assertEqual(b.target_time,NOW+timedelta(days=5))
        self.assertEqual(len(b.regions),9)
        self.assertEqual(len(b.waves),14)
        self.assertEqual(len(b.routes),2)
        self.assertTrue(all(r.comparisons for r in b.regions))
        self.assertTrue(any('Shropshire' in t for t in b.triggers))
        with self.assertRaises(ValueError): build_briefing(bundle,3,now=NOW)

    def test_missing_forecasts_cannot_trigger_all_clear(self):
        b=build_briefing(ForecastBundle((),(),.5,('API unavailable',),NOW),2,now=NOW)
        self.assertFalse(b.triggers)
        self.assertTrue(any('cannot be evaluated' in w for w in b.warnings))
        self.assertTrue(all(v=='insufficient_data' for r in b.routes for v in r.viability.values()))

    def test_html_escapes_content_and_whatsapp_is_bounded(self):
        b=replace(self.briefing,triggers=('<script>alert(1)</script>',)*40)
        body=render_html(b)
        self.assertNotIn('<script>',body)
        self.assertIn('&lt;script&gt;',body)
        self.assertLessEqual(len(render_whatsapp(b)),1200)

    def test_convergence_requires_coverage_and_three_adjacent_strong_cells(self):
        cells=tuple(ConvergenceCell(2,c,52,-3+c*.5,4e-5,2e-5,2,'deep / synoptic candidate','Welsh Marches') for c in (2,3,4))
        result=ConvergenceResult(cells,25,25,'synthetic',NOW,55,())
        self.assertEqual(strong_convergence_regions(result),('Welsh Marches',))
        self.assertFalse(strong_convergence_regions(replace(result,valid_surface_cells=19)))
        self.assertFalse(strong_convergence_regions(replace(result,cells=cells[:2])))

    def test_send_deduplication_survives_backup_restore_and_horizons_are_distinct(self):
        sender=Mock(return_value='accepted-123')
        senders={'email':sender,'whatsapp':sender}
        self.assertEqual(set(dispatch(self.briefing,self.db,self.settings,senders=senders).values()),{'accepted'})
        backup(self.path/'ledger.sqlite3',self.path/'saved')
        restored=DatabaseManager(self.path/'saved'/'alerts.sqlite3')
        dispatch(self.briefing,restored,self.settings,senders=senders)
        self.assertEqual(sender.call_count,2)
        dispatch(replace(self.briefing,horizon_days=5),restored,self.settings,senders=senders)
        self.assertEqual(sender.call_count,4)
        self.assertEqual(len(restored.list_alerts()),4)

    def test_uncertain_outcome_never_automatically_retries(self):
        sender=Mock(side_effect=DeliveryError('Provider timeout'))
        setting=replace(self.settings,channels=('email',))
        self.assertEqual(dispatch(self.briefing,self.db,setting,senders={'email':sender}),{'email':'uncertain'})
        dispatch(self.briefing,self.db,setting,senders={'email':sender})
        self.assertEqual(sender.call_count,1)
        self.assertEqual(self.db.list_alerts()[0]['status'],'uncertain')

    def test_confirmed_rejection_can_retry_and_no_trigger_never_sends(self):
        sender=Mock(side_effect=[DeliveryError('Rejected',uncertain=False),'accepted'])
        setting=replace(self.settings,channels=('email',))
        for outcome in ('failed','accepted'):
            self.assertEqual(dispatch(self.briefing,self.db,setting,senders={'email':sender}),{'email':outcome})
        dispatch(replace(self.briefing,triggers=()),self.db,setting,senders={'email':sender})
        self.assertEqual(sender.call_count,2)

    def test_settings_require_credentials_and_approved_twilio_template(self):
        with self.assertRaises(ValueError): DeliverySettings.from_env({}).validate()
        settings=DeliverySettings.from_env({'DUO_ALERT_CHANNELS':'whatsapp','WHATSAPP_PROVIDER':'twilio'})
        with self.assertRaises(ValueError): settings.validate()
        self.assertNotIn('synthetic-secret',repr(self.settings))
        self.assertNotIn('synthetic-api-key',repr(self.settings))

    def test_blank_callmebot_is_skipped_without_a_claim_and_gmail_spaces_are_removed(self):
        settings=DeliverySettings.from_env({'GMAIL_APP_PASSWORD':'  abcd efgh\tijkl\nmnop  ','CALLMEBOT_API_KEY':'  '})
        settings.validate();self.assertEqual(settings.gmail_app_password,'abcdefghijklmnop')
        email,push,whatsapp=Mock(return_value='gmail-ok'),Mock(return_value='ntfy-ok'),Mock()
        result=dispatch(self.briefing,self.db,settings,senders={'email':email,'ntfy':push,'whatsapp':whatsapp})
        self.assertEqual(result,{'email':'accepted','ntfy':'accepted','whatsapp':'skipped: CALLMEBOT_API_KEY is unset'})
        whatsapp.assert_not_called();self.assertEqual(len(self.db.list_alerts()),2)
        self.assertEqual({r['channel'] for r in self.db.list_alerts()},{'email','ntfy'})
        dispatch(self.briefing,self.db,settings,senders={'email':email,'ntfy':push,'whatsapp':whatsapp})
        self.assertEqual(email.call_count,1);self.assertEqual(push.call_count,1)

    def test_ntfy_posts_utf8_without_key_and_validates_acceptance(self):
        settings=DeliverySettings(channels=('ntfy',));settings.validate()
        opener=Mock();opener.open.return_value=Response(json.dumps({'id':'abc123xyz78','event':'message','topic':settings.ntfy_topic}).encode())
        with patch('alerts.build_opener',return_value=opener):
            self.assertEqual(send_ntfy(settings,self.briefing,'ignored'),'ntfy:abc123xyz78')
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,'https://ntfy.sh/g-ckml-soaring-alerts')
        self.assertEqual(request.method,'POST');self.assertIsNone(request.get_header('Authorization'))
        self.assertIn('Synthetic trigger',request.data.decode('utf-8'))
        self.assertTrue(opener.open.call_args.kwargs['timeout']>0)
        opener.open.return_value=Response(b'{"id":"bad","event":"message","topic":"other-topic"}')
        with patch('alerts.build_opener',return_value=opener),self.assertRaises(DeliveryError):send_ntfy(settings,self.briefing,'x')
        for invalid_id in (None,123,[],{},''):
            opener.open.return_value=Response(json.dumps({'id':invalid_id,'event':'message','topic':settings.ntfy_topic}).encode())
            with patch('alerts.build_opener',return_value=opener),self.assertRaises(DeliveryError):send_ntfy(settings,self.briefing,'x')
        for bad in ('../admin','topic?x=y','topic/name','', 'a'*65):
            with self.assertRaises(ValueError):replace(settings,ntfy_topic=bad).validate()

    def test_send_cli_returns_zero_with_gmail_and_no_whatsapp_key_even_if_push_fails(self):
        now=datetime.now(UTC)
        bundle=ForecastBundle((replace(forecast(),fetched_at=now),),(),.5,(),now)
        briefing=replace(self.briefing,regions=(Mock(thermal=Mock()),))
        # Real dispatch and SQLite, with only external transports mocked.
        with patch.dict('os.environ',{'GMAIL_APP_PASSWORD':'abcd efgh ijkl mnop'},clear=True), \
             patch('alerts.collect_forecasts',return_value=bundle),patch('alerts.build_briefing',return_value=briefing), \
             patch('alerts.render_html',return_value='<p>briefing</p>'),patch('alerts._write_json'), \
             patch('alerts.DatabaseManager',return_value=self.db),patch('alerts.send_email',return_value='gmail-ok') as email, \
             patch('alerts.send_ntfy',side_effect=DeliveryError('ntfy unavailable')),patch('alerts.send_whatsapp') as whatsapp, \
             patch('sys.stdout',new=io.StringIO()) as output:
            self.assertEqual(main(['--send','--output-dir',str(self.path/'optional-push')]),0)
        email.assert_called_once();whatsapp.assert_not_called()
        self.assertEqual(email.call_args.args[0].email_to,'manorhousesurveyors@gmail.com')
        self.assertIn('skipped: CALLMEBOT_API_KEY is unset',output.getvalue())

    def test_ntfy_timeout_is_not_retried_and_no_trigger_sends_nothing(self):
        settings=DeliverySettings(channels=('ntfy',));opener=Mock()
        opener.open.side_effect=URLError('timeout')
        with patch('alerts.build_opener',return_value=opener):
            self.assertEqual(dispatch(self.briefing,self.db,settings),{'ntfy':'uncertain'})
            dispatch(self.briefing,self.db,settings)
            dispatch(replace(self.briefing,triggers=()),self.db,settings)
        self.assertEqual(opener.open.call_count,1)

    def test_email_tls_multipart_and_safe_error(self):
        smtp=Mock()
        smtp.send_message.return_value={}
        with patch('alerts.smtplib.SMTP_SSL') as factory:
            factory.return_value.__enter__.return_value=smtp
            self.assertEqual(send_email(self.settings,self.briefing,'abc'),'<abc@duo-discus-weather.local>')
        self.assertEqual(factory.call_args.args,('smtp.gmail.com',465))
        self.assertTrue(factory.call_args.kwargs['context'].check_hostname)
        message=smtp.send_message.call_args.args[0]
        self.assertEqual(message.get_content_type(),'multipart/alternative')
        with patch('alerts.smtplib.SMTP_SSL',side_effect=smtplib.SMTPAuthenticationError(535,b'synthetic-secret')):
            with self.assertRaises(DeliveryError) as caught: send_email(self.settings,self.briefing,'abc')
        self.assertFalse(caught.exception.uncertain)
        self.assertNotIn('synthetic-secret',str(caught.exception))

    def test_callmebot_encodes_recipient_and_requires_explicit_acceptance(self):
        opener=Mock()
        opener.open.return_value=Response(b'Message queued')
        with patch('alerts.build_opener',return_value=opener):
            self.assertEqual(send_whatsapp(self.settings,self.briefing,'123'),'callmebot:123')
        query=parse_qs(urlsplit(opener.open.call_args.args[0].full_url).query)
        self.assertEqual(query['phone'],[self.settings.whatsapp_to])
        opener.open.return_value=Response(b'<html>Error: bad key</html>')
        with patch('alerts.build_opener',return_value=opener), self.assertRaises(DeliveryError):
            send_whatsapp(self.settings,self.briefing,'123')

    def test_twilio_uses_approved_content_template_and_checks_sid(self):
        setting=replace(self.settings,whatsapp_provider='twilio',twilio_account_sid='AC'+'0'*32,
                        twilio_auth_token='test-token',twilio_content_sid='HX'+'1'*32,
                        twilio_whatsapp_from='whatsapp:+441234567890')
        setting.validate()
        opener=Mock()
        opener.open.return_value=Response(json.dumps({'sid':'SM'+'2'*32}).encode(),status=201)
        with patch('alerts.build_opener',return_value=opener):
            self.assertEqual(send_whatsapp(setting,self.briefing,'123'),'SM'+'2'*32)
        request=opener.open.call_args.args[0]
        self.assertEqual(request.method,'POST')
        body=parse_qs(request.data.decode())
        self.assertEqual(body['ContentSid'],['HX'+'1'*32])
        variable=json.loads(body['ContentVariables'][0])['1']
        self.assertNotIn('\n',variable)
        self.assertLessEqual(len(variable),900)

    def test_notification_error_does_not_leak_url_or_retry(self):
        opener=Mock()
        opener.open.side_effect=URLError('https://example.test?secret=hidden')
        with patch('alerts.build_opener',return_value=opener), self.assertRaises(DeliveryError) as caught:
            send_whatsapp(self.settings,self.briefing,'123')
        self.assertNotIn('hidden',str(caught.exception))
        self.assertEqual(opener.open.call_count,1)

    def test_snapshot_roundtrip_and_dryrun_does_not_open_delivery_database(self):
        bundle=ForecastBundle((forecast(),),(),.5,(),NOW)
        path=self.path/'forecast.json'
        save_bundle(bundle,path)
        self.assertEqual(load_bundle(path).forecasts,bundle.forecasts)
        with patch('alerts.DatabaseManager') as database, patch('sys.stdout',new=io.StringIO()):
            result=main(['--snapshot',str(path),'--at',NOW.isoformat(),'--output-dir',str(self.path/'out')])
        database.assert_not_called()
        self.assertEqual(result,0)
        self.assertTrue((self.path/'out'/'briefing-5day.html').is_file())

    def test_stale_snapshot_is_rejected_before_dispatch(self):
        path=self.path/'old.json'
        save_bundle(ForecastBundle((forecast(),),(),.5,(),NOW),path)
        with patch('alerts.DeliverySettings.from_env',return_value=self.settings), patch('alerts.dispatch') as sender:
            with self.assertRaisesRegex(ValueError,'stale'):
                main(['--send','--snapshot',str(path),'--output-dir',str(self.path/'out')])
        sender.assert_not_called()

    def test_state_validation_refuses_missing_empty_corrupt_or_wrong_schema(self):
        for name,body in (('empty',b''),('corrupt',b'not sqlite')):
            path=self.path/name
            path.write_bytes(body)
            with self.assertRaises(Exception): verify(path)
        with self.assertRaises(ValueError): verify(self.path/'absent')
        self.assertFalse((self.path/'absent').exists())
        verify(self.path/'ledger.sqlite3')
