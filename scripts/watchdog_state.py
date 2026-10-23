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

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database import DatabaseManager,SCHEMA_VERSION


def verify(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError('Alert state is missing or empty; refusing to reset deduplication')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Alert state failed integrity verification')
        if db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
            raise ValueError('Unsupported alert state schema; migration/review required')
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'alerts','alert_events','gold_standard_profiles'} <= names:
            raise ValueError('Alert state is missing required tables')


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
    parser.add_argument('operation',choices=('initialize','verify','backup'))
    parser.add_argument('--database',type=Path,default=Path('var/duo_discus.sqlite3'))
    parser.add_argument('--output',type=Path,default=Path('var/watchdog-state'))
    args=parser.parse_args(argv)
    if args.operation=='initialize':
        if args.database.exists():
            verify(args.database)
        else:
            DatabaseManager(args.database)
    elif args.operation=='backup':
        backup(args.database,args.output)
    verify(args.database)
    print(f'Alert state {args.operation}: verified')


if __name__=='__main__':
    main()
