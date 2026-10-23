from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest

from config import MONITORING_ZONES
from database import AlertKey, ClaimConflict, DatabaseManager


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'state.sqlite3'
        self.db = DatabaseManager(self.path)
        self.now = datetime(2026, 6, 10, 12, tzinfo=UTC)
        self.key = AlertKey(date(2026, 6, 12), 2, 'thermal', 'route:A', 'email', 'test@example.test')

    def claim(self, **kwargs):
        return self.db.claim_alert(self.key, now=self.now, **kwargs)

    def test_claim_send_and_deduplicate_after_reopen(self):
        claim = self.claim(payload={'climb_knots': 4.1})
        self.assertIsNone(self.claim(payload={'climb_knots': 4.2}))
        self.db.start_delivery(claim, now=self.now)
        self.db.mark_sent(claim, provider_message_id='example-123', now=self.now)
        self.db.mark_sent(claim, now=self.now)  # Idempotent completion.
        reopened = DatabaseManager(self.path)
        self.assertTrue(reopened.has_sent_alert(self.key))
        self.assertIsNone(reopened.claim_alert(self.key))
        self.assertEqual(reopened.list_alerts()[0]['payload'], {'climb_knots': 4.1})
        self.assertEqual([e['status'] for e in reopened.alert_history(claim.alert_id)], ['reserved', 'sending', 'sent'])

    def test_two_and_five_day_and_channels_are_distinct(self):
        self.assertIsNotNone(self.claim())
        for horizon, channel in ((5, 'email'), (2, 'whatsapp')):
            key = AlertKey(self.key.forecast_date, horizon, 'thermal', 'route:A', channel, 'test@example.test')
            self.assertIsNotNone(self.db.claim_alert(key, now=self.now))

    def test_concurrent_jobs_have_one_winner(self):
        with ThreadPoolExecutor(max_workers=12) as pool:
            claims = list(pool.map(lambda _: self.db.claim_alert(self.key, now=self.now), range(30)))
        self.assertEqual(sum(claim is not None for claim in claims), 1)
        self.assertEqual(len(self.db.list_alerts()), 1)

    def test_expired_reservation_can_be_reclaimed_and_old_owner_rejected(self):
        old = self.claim(lease_seconds=10)
        later = self.now + timedelta(seconds=11)
        new = self.db.claim_alert(self.key, now=later)
        self.assertEqual(old.alert_id, new.alert_id)
        self.assertNotEqual(old.token, new.token)
        with self.assertRaises(ClaimConflict):
            self.db.start_delivery(old, now=later)
        self.db.start_delivery(new, now=later)
        with self.assertRaises(ClaimConflict):
            self.db.mark_sent(old, now=later)

    def test_expired_reservation_cannot_start(self):
        claim = self.claim(lease_seconds=1)
        with self.assertRaises(ClaimConflict):
            self.db.start_delivery(claim, now=self.now + timedelta(seconds=2))

    def test_started_or_uncertain_send_never_reclaimed_on_timeout(self):
        claim = self.claim(lease_seconds=1)
        self.db.start_delivery(claim, now=self.now)
        later = self.now + timedelta(days=5)
        self.assertIsNone(self.db.claim_alert(self.key, now=later))
        self.db.mark_failed(claim, error='provider timeout', now=self.now)
        self.assertIsNone(self.db.claim_alert(self.key, now=later))
        self.db.reconcile_delivery(claim.alert_id, delivered=True, evidence='Provider confirms acceptance', now=later)
        self.assertTrue(self.db.has_sent_alert(self.key))

    def test_confirmed_failure_can_retry(self):
        claim = self.claim()
        self.db.start_delivery(claim, now=self.now)
        self.db.mark_failed(claim, error='Rejected before acceptance', uncertain=False, now=self.now)
        second = self.claim()
        self.assertNotEqual(claim.token, second.token)
        self.assertEqual(self.db.list_alerts()[0]['attempts'], 2)

    def test_invalid_transition_rolls_back_without_audit_event(self):
        claim = self.claim()
        with self.assertRaises(ClaimConflict):
            self.db.mark_sent(claim, now=self.now)
        self.assertEqual(len(self.db.alert_history(claim.alert_id)), 1)
        self.assertEqual(self.db.list_alerts()[0]['status'], 'reserved')

    def test_profile_roundtrip_duplicate_and_revision(self):
        arguments = dict(location=MONITORING_ZONES['shropshire'], valid_at=self.now,
                         source='synthetic test', label='Gold reference',
                         profile={'units': {'temperature_2m': '°C'}, 'temperature_2m': 22.5, 'cape': None})
        first = self.db.save_gold_standard_profile(**arguments)
        self.assertEqual(first, self.db.save_gold_standard_profile(**arguments))
        arguments['profile']['temperature_2m'] = 23
        second = self.db.save_gold_standard_profile(**arguments)
        self.assertNotEqual(first, second)
        self.assertEqual(self.db.get_gold_standard_profile(first)['profile']['temperature_2m'], 22.5)
        self.assertEqual(len(self.db.find_gold_standard_profiles(location_key='shropshire', start_date=date(2026, 6, 10))), 2)
        self.assertIsNone(self.db.get_gold_standard_profile(9999))

    def test_profile_local_date_is_london_date(self):
        profile_id = self.db.save_gold_standard_profile(
            location=MONITORING_ZONES['shropshire'], valid_at=datetime(2026, 6, 10, 23, 30, tzinfo=UTC),
            source='test', label='Night', profile={'value': 1})
        self.assertEqual(self.db.get_gold_standard_profile(profile_id)['local_date'], '2026-06-11')

    def test_json_validation_and_sql_parameterization(self):
        with self.assertRaises(ValueError):
            self.claim(payload={'climb': float('nan')})
        key = AlertKey(self.key.forecast_date, 2, 'thermal', "x'); DROP TABLE alerts; --", 'email', 'test')
        self.db.claim_alert(key, now=self.now)
        self.assertEqual(len(self.db.list_alerts()), 1)
        with self.assertRaises(ValueError):
            self.db.claim_alert(self.key, now=datetime(2026, 1, 1))

    def test_database_integrity_and_newer_schema_rejected(self):
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(connection.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            connection.execute('PRAGMA user_version=999')
        with self.assertRaises(RuntimeError):
            DatabaseManager(self.path)
