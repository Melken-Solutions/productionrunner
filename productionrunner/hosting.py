"""Provider-independent configuration and backup support for a single-instance pilot."""
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit,parse_qs

import click
from flask import jsonify
from .database import is_postgres, connect as open_database


REQUIRED_TABLES = ('job_plans', 'first_piece_results', 'vendors', 'materials', 'request_materials', 'ticket_materials', 'request_outside', 'outside_steps', 'outside_moves', 'outside_inspections', 'jobrunner_sequences', 'users', 'tenants', 'memberships', 'tickets', 'operations', 'events',
                   'serial_units', 'part_revisions', 'part_dxf', 'organization_branding',
                   'stations', 'operation_assignments', 'ticket_dispatch', 'developer_accounts',
                   'storage_policies', 'storage_file_controls', 'part_imports', 'fulfillment_records', 'fulfillment_units',
                   'work_settings', 'work_reasons', 'work_sessions', 'work_events', 'work_time_corrections', 'return_repairs', 'return_repair_units', 'return_closure_events', 'customer_returns', 'customer_return_units', 'customer_return_reviews', 'fulfillment_reversals', 'fulfillment_unit_entries', 'fulfillment_checks', 'fulfillment_check_results', 'intake_requests', 'intake_events', 'workflow_defaults', 'ticket_workflow')


def configure(app):
    app.config['PR_ENV'] = os.environ.get('PR_ENV', 'development')
    if app.config['PR_ENV'] not in {'development', 'production'}:
        raise ValueError('PR_ENV must be development or production.')
    hosts = [host.strip() for host in os.environ.get('PR_TRUSTED_HOSTS', '').split(',') if host.strip()]
    if any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', host) or '..' in host for host in hosts):
        raise ValueError('PR_TRUSTED_HOSTS must contain exact hostnames without schemes, ports or wildcards.')
    if hosts:
        app.config['TRUSTED_HOSTS'] = hosts
    origin=os.environ.get('PR_PUBLIC_ORIGIN','').rstrip('/')
    if origin:
        parsed=urlsplit(origin)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or parsed.hostname not in hosts:
            raise ValueError('PR_PUBLIC_ORIGIN must be an HTTPS origin matching PR_TRUSTED_HOSTS.')
        app.config['PR_PUBLIC_ORIGIN']=origin
    if app.config['PR_ENV'] == 'production':
        database_url=os.environ.get('PR_DATABASE_URL','')
        if is_postgres(database_url) and parse_qs(urlsplit(database_url).query).get('sslmode')!=['verify-full']:
            raise ValueError('Production PostgreSQL requires sslmode=verify-full and a trusted root certificate.')
        if not os.environ.get('PR_DATA_DIR') and not is_postgres(os.environ.get('PR_DATABASE_URL','')):
            raise ValueError('Production requires PR_DATA_DIR on persistent storage.')
        if len(os.environ.get('PR_SECRET_KEY', '')) < 32:
            raise ValueError('Production requires a PR_SECRET_KEY of at least 32 characters from a secret manager.')
        if os.environ.get('PR_HTTPS') != '1':
            raise ValueError('Production requires PR_HTTPS=1 and an HTTPS ingress.')
        if not hosts:
            raise ValueError('Production requires PR_TRUSTED_HOSTS.')
        if app.debug or app.testing:
            raise ValueError('Production cannot enable DEBUG or TESTING.')


def readiness(app):
    """Read-only probe; never creates a missing database or reveals tenant details."""
    try:
        if is_postgres(app.config['DATABASE']):
            import psycopg
            try:
                with closing(open_database(app.config['DATABASE'])) as conn:
                    if app.config.get('PR_ENV')=='production':
                        role=conn.raw.execute("SELECT rolsuper OR rolbypassrls OR has_schema_privilege(current_user,'productionrunner','CREATE') FROM pg_roles WHERE rolname=current_user").fetchone()
                        if not role or role[0]:return jsonify(status='not_ready'),503
                    names={r[0] for r in conn.raw.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='productionrunner'")}
                    if not set(REQUIRED_TABLES) <= names or 'schema_migrations' not in names:
                        return jsonify(status='not_ready'),503
                    conn.execute('SELECT id,tenant_id,version FROM tickets LIMIT 1').fetchall()
                    conn.execute('SELECT object_key,object_size FROM part_revisions LIMIT 1').fetchall()
                    conn.execute('SELECT object_key,object_size FROM part_dxf LIMIT 1').fetchall()
                return jsonify(status='ready')
            except (psycopg.Error,sqlite3.Error):
                return jsonify(status='not_ready'),503
        uri = Path(app.config['DATABASE']).resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as conn:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not set(REQUIRED_TABLES) <= names:
                return jsonify(status='not_ready'), 503
            conn.execute('SELECT id,tenant_id,version FROM tickets LIMIT 1').fetchall()
            conn.execute('SELECT tenant_id,version FROM organization_branding LIMIT 1').fetchall()
        return jsonify(status='ready')
    except sqlite3.Error:
        return jsonify(status='not_ready'), 503


def register_hosting(app):
    app.add_url_rule('/health/ready', 'hosting_readiness', lambda: readiness(app), methods=['GET'])

    @app.cli.command('backup-db')
    @click.argument('destination', type=click.Path(path_type=Path))
    def backup_db(destination):
        """Create a private consistent SQLite snapshot at a NEW destination path."""
        if is_postgres(app.config['DATABASE']):
            raise click.ClickException('Use pg_dump with the migration/backup connection; see the cloud recovery runbook.')
        created = False
        try:
            # Open source read-only before creating destination; never create an empty source.
            source = Path(app.config['DATABASE']).resolve().as_uri() + '?mode=ro'
            with closing(sqlite3.connect(source, uri=True, timeout=10)) as conn:
                fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
                created = True
                with closing(sqlite3.connect(destination)) as snapshot:
                    conn.backup(snapshot)
                    if snapshot.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                        raise ValueError('Snapshot failed integrity verification.')
                    if snapshot.execute('PRAGMA foreign_key_check').fetchone():
                        raise ValueError('Snapshot failed foreign-key verification.')
        except (OSError, sqlite3.Error, ValueError) as exc:
            if created:
                destination.unlink(missing_ok=True)
            raise click.ClickException('Backup failed; destination must be new and writable, and source valid.') from exc
        click.echo(f'Created verified database snapshot: {destination}')
        click.echo('Back up secret-manager values, deployment configuration and TLS recovery material separately.')
