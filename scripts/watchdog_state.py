"""Validate/backup the alert ledger for ephemeral GitHub Actions runners."""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import UTC,datetime
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database import DatabaseManager,SCHEMA_VERSION


def verify(path: Path, *, allow_legacy: bool = False) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError('Alert state is missing or empty')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Alert state failed integrity verification')
        versions={1,SCHEMA_VERSION} if allow_legacy else {SCHEMA_VERSION}
        if db.execute('PRAGMA user_version').fetchone()[0] not in versions:
            raise ValueError('Unsupported alert state schema; migration/review required')
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'alerts','alert_events','gold_standard_profiles'} <= names:
            raise ValueError('Alert state is missing required tables')
        # Integrity alone accepts a SQLite file whose tables have the wrong
        # columns. Verify the delivery contract before calling it recoverable.
        db.execute('SELECT id,forecast_date,horizon_days,kind,scope,channel,recipient,status,claim_token,lease_expires_at,attempts,created_at,updated_at,sent_at,provider_message_id,payload_json,last_error FROM alerts LIMIT 0')
        db.execute('SELECT id,alert_id,occurred_at,status,detail FROM alert_events LIMIT 0')
        db.execute('SELECT id,location_key,location_name,latitude,longitude,valid_at,local_date,source,label,notes,profile_schema_version,profile_json,fingerprint,created_at FROM gold_standard_profiles LIMIT 0')
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Alert state has broken audit references')


def _quarantine(path: Path) -> None:
    """Keep unreadable local state for inspection before replacing the ledger."""
    files=[Path(str(path)+suffix) for suffix in ('','-wal','-shm')]
    if not any(p.exists() for p in files):return
    directory=path.parent/'rejected-state'/uuid.uuid4().hex
    directory.mkdir(parents=True,exist_ok=True)
    for item in files:
        if item.exists():item.replace(directory/item.name)


def prepare(database: Path, restored: Path | None = None) -> str:
    """Restore verified state, or initialize empty state without a history lock.

    Invalid artifacts remain untouched. A reset may repeat previously sent
    alerts, which is logged explicitly. Only failure to create/use the new local
    database remains fatal; missing/corrupt/incompatible history is recoverable.
    """
    database=Path(database);database.parent.mkdir(parents=True,exist_ok=True)
    sources=[Path(restored)] if restored is not None else []
    if database not in sources:sources.append(database)
    for source in sources:
        try:
            verify(source,allow_legacy=True)
            if source==database:
                DatabaseManager(database);verify(database)
                print('Alert state: existing verified ledger retained')
                return 'existing'
            with tempfile.TemporaryDirectory(prefix='ledger-restore-',dir=database.parent) as folder:
                candidate=Path(folder)/'alerts.sqlite3'
                with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as read:
                    with closing(sqlite3.connect(candidate)) as write:read.backup(write)
                DatabaseManager(candidate);verify(candidate)
                _quarantine(database)
                candidate.replace(database)
            print('Alert state: restored and verified previous ledger')
            return 'restored'
        except (ValueError,OSError,sqlite3.DatabaseError,RuntimeError) as exc:
            print(f'Alert state unavailable at {source} ({type(exc).__name__}); continuing recovery.')
    _quarantine(database)
    DatabaseManager(database);verify(database)
    print('No verified prior ledger. Initialized fresh empty alert state {} (SQLite); earlier alerts may repeat.')
    return 'fresh'


def backup(source: Path, directory: Path) -> None:
    verify(source)
    directory.mkdir(parents=True,exist_ok=True)
    temporary = directory/'alerts.sqlite3.tmp'
    temporary.unlink(missing_ok=True)
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as read:
        with closing(sqlite3.connect(temporary)) as write:
            read.backup(write)
    verify(temporary)
    temporary.replace(directory/'alerts.sqlite3')
    (directory/'manifest.json').write_text(json.dumps({'schema_version':SCHEMA_VERSION,
        'created_at':datetime.now(UTC).isoformat(),'run_id':os.environ.get('GITHUB_RUN_ID'),
        'run_attempt':os.environ.get('GITHUB_RUN_ATTEMPT')},indent=2)+'\n')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=('initialize','prepare','verify','migrate','backup'))
    parser.add_argument('--allow-legacy',action='store_true',help='Accept a schema-1 artifact for verification before migration')
    parser.add_argument('--database',type=Path,default=Path('var/duo_discus.sqlite3'))
    parser.add_argument('--output',type=Path,default=Path('var/watchdog-state'))
    parser.add_argument('--restored',type=Path,help='Optional downloaded ledger; invalid or missing state initializes an empty ledger')
    args=parser.parse_args(argv)
    if args.operation in ('initialize','prepare'):
        prepare(args.database,args.restored)
    elif args.operation=='backup':
        backup(args.database,args.output)
    elif args.operation=='migrate':
        verify(args.database,allow_legacy=True)
        DatabaseManager(args.database)
    verify(args.database,allow_legacy=args.allow_legacy and args.operation=='verify')
    print(f'Alert state {args.operation}: verified')


if __name__=='__main__':
    main()
