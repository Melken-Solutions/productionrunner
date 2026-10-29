"""Offline, all-or-nothing SQLite import into an empty migrated PostgreSQL schema."""
import hashlib
import json
import sqlite3
from pathlib import Path
import click
from .database import WRITE_LOCK,is_postgres


def fingerprint(rows):
    def normal(value):
        if isinstance(value,(bytes,memoryview)):return {'bytes':bytes(value).hex()}
        return value
    values=[json.dumps([normal(v) for v in row],ensure_ascii=False,separators=(',',':')) for row in rows]
    return hashlib.sha256('\n'.join(sorted(values)).encode()).hexdigest()


def import_sqlite(source,url):
    import psycopg
    from psycopg import sql
    if not is_postgres(url):raise ValueError('Destination must be PostgreSQL.')
    source=Path(source).resolve()
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as local, psycopg.connect(url) as remote:
        local.execute('BEGIN')
        if local.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or local.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Source database failed integrity checks.')
        tables=[r[0] for r in local.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid")]
        remote.execute('SELECT pg_advisory_xact_lock(%s)',(WRITE_LOCK,))
        remote.execute('SET LOCAL search_path TO productionrunner,pg_catalog')
        target={r[0] for r in remote.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='productionrunner' AND table_type='BASE TABLE' AND table_name!='schema_migrations'")}
        if set(tables)!=target:raise ValueError('Source/target schemas differ. Upgrade the SQLite snapshot first.')
        for table in tables:
            if remote.execute(sql.SQL('SELECT 1 FROM {} LIMIT 1').format(sql.Identifier(table))).fetchone():
                raise ValueError('Destination is not empty. Import refused.')
        # Trusted offline migration role only. Keep PostgreSQL foreign keys active.
        # Snapshot triggers are disabled transactionally to restore historical closed requests.
        for table in tables:remote.execute(sql.SQL('ALTER TABLE {} DISABLE TRIGGER USER').format(sql.Identifier(table)))
        counts={}
        for table in tables:
            columns=[r[1] for r in local.execute('PRAGMA table_info("'+table+'")')]
            records=local.execute('SELECT * FROM "'+table+'"').fetchall()
            query=sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,columns)),sql.SQL(',').join(sql.Placeholder() for _ in columns))
            with remote.cursor() as cursor:
                if records:cursor.executemany(query,records)
            copied=remote.execute(sql.SQL('SELECT {} FROM {}').format(sql.SQL(',').join(map(sql.Identifier,columns)),sql.Identifier(table))).fetchall()
            if fingerprint(records)!=fingerprint(copied):raise ValueError('Verification failed for '+table)
            counts[table]=len(records)
        for table in tables:remote.execute(sql.SQL('ALTER TABLE {} ENABLE TRIGGER USER').format(sql.Identifier(table)))
        for table,column in remote.execute("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='productionrunner' AND is_identity='YES'").fetchall():
            maximum=remote.execute(sql.SQL('SELECT MAX({}) FROM {}').format(sql.Identifier(column),sql.Identifier(table))).fetchone()[0]
            remote.execute('SELECT setval(pg_get_serial_sequence(%s,%s),%s,%s)',('productionrunner.'+table,column,maximum or 1,maximum is not None))
        return counts


def register(app):
    @app.cli.command('import-sqlite')
    @click.argument('source',type=click.Path(exists=True,path_type=Path))
    @click.option('--confirm-empty',is_flag=True,required=True,help='Acknowledge an offline import to an empty PostgreSQL destination.')
    def command(source,confirm_empty):
        try:counts=import_sqlite(source,app.config['DATABASE'])
        except (ValueError,sqlite3.Error) as exc:raise click.ClickException(str(exc)) from exc
        click.echo(f'Imported and verified {sum(counts.values())} rows across {len(counts)} tables.')
