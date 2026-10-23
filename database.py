"""SQLite persistence for alert delivery claims and historical atmospheric profiles.

Connections are short-lived and never shared between threads. WAL mode and an
atomic claim prevent overlapping watchdog jobs from claiming the same alert.
An external message and a SQLite commit cannot form one transaction: after a
send has started, an uncertain outcome is held for reconciliation, not retried
automatically. Module 4 must call start_delivery BEFORE contacting a provider.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Mapping
import uuid

from config import ALERT_HORIZONS_DAYS, GeoPoint, LOCAL_TIMEZONE, Settings

SCHEMA_VERSION = 1


def _utc(value: datetime | None = None) -> datetime:
    value = datetime.now(UTC) if value is None else value
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware datetime is required")
    return value.astimezone(UTC)


def _stamp(value: datetime | None = None) -> str:
    return _utc(value).isoformat(timespec="microseconds")


def _json(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping) or any(not isinstance(k, str) for k in value):
        raise ValueError("Payload must be a mapping with string keys")
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


@dataclass(frozen=True, slots=True)
class AlertKey:
    forecast_date: date  # Target local date, never the watchdog execution date.
    horizon_days: int
    kind: str
    scope: str
    channel: str
    recipient: str

    def __post_init__(self) -> None:
        if not isinstance(self.forecast_date, date) or isinstance(self.forecast_date, datetime):
            raise ValueError("forecast_date must be a date")
        if isinstance(self.horizon_days, bool) or not isinstance(self.horizon_days, int) or self.horizon_days not in ALERT_HORIZONS_DAYS:
            raise ValueError("Alert horizon must be 2 or 5 days")
        if self.kind not in {"thermal", "wave", "convergence", "briefing"}:
            raise ValueError("Unknown alert kind")
        if self.channel not in {"email", "whatsapp"}:
            raise ValueError("Channel must be email or whatsapp")
        if not self.scope.strip() or not self.recipient.strip():
            raise ValueError("Scope and recipient must be nonempty")

    @property
    def values(self) -> tuple[str | int, ...]:
        return (self.forecast_date.isoformat(), self.horizon_days, self.kind,
                self.scope, self.channel, self.recipient)


@dataclass(frozen=True, slots=True)
class AlertClaim:
    alert_id: int
    token: str
    key: AlertKey
    lease_expires_at: datetime


class ClaimConflict(RuntimeError):
    """The caller no longer owns the alert or requested an invalid transition."""


class DatabaseManager:
    def __init__(self, path: str | Path | None = None, *, busy_timeout_seconds: float = 10) -> None:
        self.path = Path(path if path is not None else Settings.from_env().database_path)
        if str(self.path) == ":memory:":
            raise ValueError("Use a file-backed database; this manager opens a connection per operation")
        if not 0 < busy_timeout_seconds <= 120:
            raise ValueError("busy_timeout_seconds must be in (0, 120]")
        self.busy_timeout_seconds = busy_timeout_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=self.busy_timeout_seconds,
                                     isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        connection = sqlite3.connect(self.path, timeout=self.busy_timeout_seconds)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
        finally:
            connection.close()
        with self._connection(write=True) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"Database schema {version} is newer than supported schema {SCHEMA_VERSION}")
            if version == SCHEMA_VERSION:
                return
            statements = (
                """CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY,
                    forecast_date TEXT NOT NULL,
                    horizon_days INTEGER NOT NULL CHECK(horizon_days IN (2,5)),
                    kind TEXT NOT NULL CHECK(kind IN ('thermal','wave','convergence','briefing')),
                    scope TEXT NOT NULL,
                    channel TEXT NOT NULL CHECK(channel IN ('email','whatsapp')),
                    recipient TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('reserved','sending','sent','failed','uncertain')),
                    claim_token TEXT NOT NULL,
                    lease_expires_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    sent_at TEXT,
                    provider_message_id TEXT,
                    payload_json TEXT NOT NULL,
                    last_error TEXT,
                    UNIQUE(forecast_date,horizon_days,kind,scope,channel,recipient)
                )""",
                """CREATE TABLE alert_events (
                    id INTEGER PRIMARY KEY,
                    alert_id INTEGER NOT NULL REFERENCES alerts(id),
                    occurred_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT
                )""",
                "CREATE INDEX alert_events_alert_id ON alert_events(alert_id,id)",
                "CREATE INDEX alerts_status_date ON alerts(status,forecast_date)",
                """CREATE TABLE gold_standard_profiles (
                    id INTEGER PRIMARY KEY,
                    location_key TEXT NOT NULL,
                    location_name TEXT NOT NULL,
                    latitude REAL NOT NULL CHECK(latitude BETWEEN -90 AND 90),
                    longitude REAL NOT NULL CHECK(longitude BETWEEN -180 AND 180),
                    valid_at TEXT NOT NULL,
                    local_date TEXT NOT NULL,
                    source TEXT NOT NULL,
                    label TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    profile_schema_version INTEGER NOT NULL CHECK(profile_schema_version > 0),
                    profile_json TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                )""",
                "CREATE INDEX profiles_location_date ON gold_standard_profiles(location_key,local_date)",
            )
            for statement in statements:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @staticmethod
    def _event(connection: sqlite3.Connection, alert_id: int, when: str,
               status: str, detail: str | None = None) -> None:
        connection.execute("INSERT INTO alert_events(alert_id,occurred_at,status,detail) VALUES(?,?,?,?)",
                           (alert_id, when, status, detail))

    def claim_alert(self, key: AlertKey, *, payload: Mapping[str, Any] | None = None,
                    lease_seconds: int = 600, now: datetime | None = None) -> AlertClaim | None:
        """Reserve one delivery, or return None when sent/in-flight/uncertain.

        Only definitively failed deliveries and expired *unstarted* reservations
        can be reclaimed. Changing the payload never bypasses deduplication.
        """
        if not 1 <= lease_seconds <= 86400:
            raise ValueError("lease_seconds must be in 1..86400")
        current = _utc(now)
        stamp = _stamp(current)
        expires = current + timedelta(seconds=lease_seconds)
        token = uuid.uuid4().hex
        serialized = _json({} if payload is None else payload)
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM alerts WHERE forecast_date=? AND horizon_days=? AND kind=? "
                "AND scope=? AND channel=? AND recipient=?", key.values).fetchone()
            if row is not None:
                reclaimable = row["status"] == "failed" or (
                    row["status"] == "reserved" and row["lease_expires_at"] <= stamp)
                if not reclaimable:
                    return None
                alert_id = row["id"]
                connection.execute(
                    "UPDATE alerts SET status='reserved',claim_token=?,lease_expires_at=?,"
                    "attempts=attempts+1,updated_at=?,payload_json=?,last_error=NULL WHERE id=?",
                    (token, _stamp(expires), stamp, serialized, alert_id))
            else:
                cursor = connection.execute(
                    "INSERT INTO alerts(forecast_date,horizon_days,kind,scope,channel,recipient,"
                    "status,claim_token,lease_expires_at,created_at,updated_at,payload_json) "
                    "VALUES(?,?,?,?,?,?,'reserved',?,?,?,?,?)",
                    (*key.values, token, _stamp(expires), stamp, stamp, serialized))
                alert_id = cursor.lastrowid
            self._event(connection, alert_id, stamp, "reserved")
            return AlertClaim(alert_id, token, key, expires)

    def start_delivery(self, claim: AlertClaim, *, now: datetime | None = None) -> None:
        """Commit intent immediately before the external send; call exactly once."""
        stamp = _stamp(now)
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                "UPDATE alerts SET status='sending',updated_at=? WHERE id=? AND claim_token=? "
                "AND status='reserved' AND lease_expires_at>?",
                (stamp, claim.alert_id, claim.token, stamp))
            if cursor.rowcount != 1:
                raise ClaimConflict("Delivery reservation expired, was reclaimed, or already started")
            self._event(connection, claim.alert_id, stamp, "sending")

    def mark_sent(self, claim: AlertClaim, *, provider_message_id: str | None = None,
                  now: datetime | None = None) -> None:
        stamp = _stamp(now)
        with self._connection(write=True) as connection:
            row = connection.execute("SELECT status,claim_token FROM alerts WHERE id=?", (claim.alert_id,)).fetchone()
            if row is not None and row["claim_token"] == claim.token and row["status"] == "sent":
                return
            cursor = connection.execute(
                "UPDATE alerts SET status='sent',sent_at=?,updated_at=?,provider_message_id=?,last_error=NULL "
                "WHERE id=? AND claim_token=? AND status='sending'",
                (stamp, stamp, provider_message_id, claim.alert_id, claim.token))
            if cursor.rowcount != 1:
                raise ClaimConflict("Only the owner of a started delivery can mark it sent")
            self._event(connection, claim.alert_id, stamp, "sent", provider_message_id)

    def mark_failed(self, claim: AlertClaim, *, error: str, uncertain: bool = True,
                    now: datetime | None = None) -> None:
        """Use uncertain=False only when the provider definitely did not accept.

        Timeouts after sending are uncertain; the default prevents duplicate
        notifications. Pass a sanitized error code/message, never credentials.
        """
        stamp = _stamp(now)
        status = "uncertain" if uncertain else "failed"
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                "UPDATE alerts SET status=?,updated_at=?,last_error=? WHERE id=? AND claim_token=? "
                "AND status IN ('reserved','sending')", (status, stamp, error, claim.alert_id, claim.token))
            if cursor.rowcount != 1:
                raise ClaimConflict("Claim is not an active delivery")
            self._event(connection, claim.alert_id, stamp, status, error)

    def reconcile_delivery(self, alert_id: int, *, delivered: bool, evidence: str,
                           now: datetime | None = None) -> None:
        """Operator reconciliation after provider checks; never call on a live send.

        A crashed 'sending' record is also held indefinitely until reconciled.
        A confirmed nondelivery makes the key available to the next watchdog.
        """
        if not evidence.strip() or not isinstance(delivered, bool):
            raise ValueError("Reconciliation needs a boolean decision and evidence")
        stamp = _stamp(now)
        status = "sent" if delivered else "failed"
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                "UPDATE alerts SET status=?,updated_at=?,sent_at=?,last_error=? "
                "WHERE id=? AND status IN ('sending','uncertain')",
                (status, stamp, stamp if delivered else None, None if delivered else evidence, alert_id))
            if cursor.rowcount != 1:
                raise ClaimConflict("Only an unresolved delivery can be reconciled")
            self._event(connection, alert_id, stamp, status, evidence)

    def has_sent_alert(self, key: AlertKey) -> bool:
        with self._connection() as connection:
            return connection.execute(
                "SELECT 1 FROM alerts WHERE forecast_date=? AND horizon_days=? AND kind=? "
                "AND scope=? AND channel=? AND recipient=? AND status='sent'", key.values).fetchone() is not None

    def list_alerts(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 10000:
            raise ValueError("limit must be in 1..10000")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM alerts WHERE (? IS NULL OR status=?) ORDER BY id DESC LIMIT ?",
                (status, status, limit)).fetchall()
        return [self._decode(row, "payload_json", "payload") for row in rows]

    def alert_history(self, alert_id: int) -> list[dict[str, Any]]:
        with self._connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM alert_events WHERE alert_id=? ORDER BY id", (alert_id,))]

    def save_gold_standard_profile(self, *, location: GeoPoint, valid_at: datetime,
                                    source: str, profile: Mapping[str, Any], label: str,
                                    notes: str = "", profile_schema_version: int = 1) -> int:
        """Store a JSON atmospheric snapshot, including its units and provenance.

        Identical source/time/location/content is idempotent. Revised profiles
        create a new row, preserving history. Labels and notes are annotations.
        """
        valid_at = _utc(valid_at)
        if not source.strip() or not label.strip() or not profile:
            raise ValueError("Source, label and atmospheric profile are required")
        if isinstance(profile_schema_version, bool) or not isinstance(profile_schema_version, int) or profile_schema_version < 1:
            raise ValueError("profile_schema_version must be a positive integer")
        serialized = _json(profile)
        identity = _json({"location": location.key, "latitude": location.latitude,
                          "longitude": location.longitude, "valid_at": _stamp(valid_at),
                          "source": source, "schema": profile_schema_version, "profile": json.loads(serialized)})
        fingerprint = hashlib.sha256(identity.encode()).hexdigest()
        with self._connection(write=True) as connection:
            connection.execute(
                "INSERT INTO gold_standard_profiles(location_key,location_name,latitude,longitude,valid_at,"
                "local_date,source,label,notes,profile_schema_version,profile_json,fingerprint,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(fingerprint) DO NOTHING",
                (location.key, location.name, location.latitude, location.longitude, _stamp(valid_at),
                 valid_at.astimezone(LOCAL_TIMEZONE).date().isoformat(), source, label, notes,
                 profile_schema_version, serialized, fingerprint, _stamp()))
            return connection.execute("SELECT id FROM gold_standard_profiles WHERE fingerprint=?", (fingerprint,)).fetchone()[0]

    @staticmethod
    def _decode(row: sqlite3.Row, column: str, name: str) -> dict[str, Any]:
        result = dict(row)
        result[name] = json.loads(result.pop(column))
        return result

    def get_gold_standard_profile(self, profile_id: int) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM gold_standard_profiles WHERE id=?", (profile_id,)).fetchone()
        return None if row is None else self._decode(row, "profile_json", "profile")

    def find_gold_standard_profiles(self, *, location_key: str | None = None,
                                    start_date: date | None = None, end_date: date | None = None,
                                    limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 10000 or (start_date and end_date and start_date > end_date):
            raise ValueError("Invalid date range or result limit")
        start = start_date.isoformat() if start_date else None
        end = end_date.isoformat() if end_date else None
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM gold_standard_profiles WHERE (? IS NULL OR location_key=?) "
                "AND (? IS NULL OR local_date>=?) AND (? IS NULL OR local_date<=?) "
                "ORDER BY valid_at DESC,id DESC LIMIT ?", (location_key, location_key, start, start, end, end, limit)).fetchall()
        return [self._decode(row, "profile_json", "profile") for row in rows]
