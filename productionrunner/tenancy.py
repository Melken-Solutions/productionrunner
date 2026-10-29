"""Local account provisioning and explicit, backed-up migration of the original MVP."""
import re
from contextlib import contextmanager
import sqlite3
from pathlib import Path

import click
from werkzeug.security import generate_password_hash


ROLES = {'planner', 'operator', 'inspector', 'admin', 'viewer', 'display'}


def validate_roles(value):
    assigned = set(value.split(','))
    if not assigned or not assigned <= ROLES or ('display' in assigned and len(assigned)>1):
        raise click.ClickException('Unknown role.')
    return ','.join(sorted(assigned))


def validate_tenant(slug, name):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', slug) or not name.strip():
        raise click.ClickException('Use a lowercase organization slug and a nonempty name.')


@contextmanager
def connect(app):
    from .database import connect as open_database
    connection = open_database(app.config['DATABASE'])
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def register_commands(app):
    schema = Path(app.root_path, 'schema.sql').read_text()

    @app.cli.command('init-db')
    def init_db():
        """Initialize a fresh database; never implicitly assign legacy data."""
        from .database import is_postgres, migrate
        if is_postgres(app.config['DATABASE']):
            migrate(app.config['DATABASE'])
            click.echo('PostgreSQL migrations applied.')
            return
        with connect(app) as conn:
            columns = {r[1] for r in conn.execute('PRAGMA table_info(tickets)')}
            if columns and 'tenant_id' not in columns:
                raise click.ClickException('Legacy database: stop the app and run migrate-tenancy with an explicit organization and backup path.')
            conn.executescript(schema)
            # Existing SQLite installs retain embedded bytes. Remote storage is PostgreSQL-only.
            for table in ('part_revisions', 'part_dxf'):
                names = {r[1] for r in conn.execute('PRAGMA table_info('+table+')')}
                for name, kind in [('object_key','TEXT'), ('object_size','INTEGER')]:
                    if name not in names:
                        conn.execute('ALTER TABLE '+table+' ADD COLUMN '+name+' '+kind)
        click.echo('Database initialized (existing data preserved).')

    @app.cli.command('add-tenant')
    @click.argument('slug')
    @click.option('--name', required=True)
    def add_tenant(slug, name):
        validate_tenant(slug, name)
        try:
            with connect(app) as conn:
                conn.execute('INSERT INTO tenants(slug,name) VALUES(?,?)', (slug, name.strip()))
        except sqlite3.IntegrityError:
            raise click.ClickException('Organization already exists.') from None
        click.echo(f'Created organization {slug}.')

    @app.cli.command('add-user')
    @click.argument('username')
    @click.option('--tenant', required=True)
    @click.option('--roles', required=True)
    @click.password_option()
    def add_user(username, tenant, roles, password):
        """Create an individual identity with one organization membership."""
        roles = validate_roles(roles)
        if not username.strip() or len(password) < 12:
            raise click.ClickException('Use a nonempty username and at least 12 password characters.')
        try:
            with connect(app) as conn:
                organization = conn.execute('SELECT id FROM tenants WHERE slug=?', (tenant,)).fetchone()
                if not organization:
                    raise click.ClickException('Unknown organization.')
                user = conn.execute('INSERT INTO users(username,password_hash) VALUES(?,?)',
                                    (username.strip(), generate_password_hash(password)))
                conn.execute('INSERT INTO memberships VALUES(?,?,?)', (organization[0], user.lastrowid, roles))
        except sqlite3.IntegrityError:
            raise click.ClickException('Username already exists; use set-membership for an existing identity.') from None
        click.echo(f'Created {username} in {tenant}.')

    @app.cli.command('set-membership')
    @click.argument('username')
    @click.option('--tenant', required=True)
    @click.option('--roles', help='Comma-separated roles; replaces this organization membership only.')
    @click.option('--revoke', is_flag=True, help='Disable access while preserving historical attribution.')
    def set_membership(username, tenant, roles, revoke):
        if revoke == (roles is not None):
            raise click.ClickException('Choose exactly one of --roles or --revoke.')
        assigned = '' if revoke else validate_roles(roles)
        with connect(app) as conn:
            conn.execute('BEGIN IMMEDIATE')
            user = conn.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
            organization = conn.execute('SELECT id FROM tenants WHERE slug=?', (tenant,)).fetchone()
            if not user or not organization:
                raise click.ClickException('Unknown user or organization.')
            from .admin import bump, protects_last_admin
            if protects_last_admin(conn, organization[0], user[0], assigned):
                raise click.ClickException('Keep at least one active admin. Assign another admin first.')
            conn.execute('INSERT INTO memberships VALUES(?,?,?) ON CONFLICT(tenant_id,user_id) DO UPDATE SET roles=excluded.roles',
                         (organization[0], user[0], assigned))
            bump(conn, organization[0], user[0], invalidate=True)
            from .pin_auth import audit
            import getpass
            import time
            audit(conn, organization[0], user[0], 'membership_changed', 'local-os:' + getpass.getuser(), time.time(),
                  'CLI membership set to: ' + (assigned or 'inactive'))
        click.echo(f'Updated membership for {username} in {tenant}.')

    @app.cli.command('migrate-tenancy')
    @click.option('--tenant', required=True)
    @click.option('--name', required=True)
    @click.option('--backup', required=True, type=click.Path(path_type=Path))
    def migrate_tenancy(tenant, name, backup):
        """OFFLINE ONLY: assign every legacy record to one named organization."""
        validate_tenant(tenant, name)
        with connect(app) as conn:
            columns = {r[1] for r in conn.execute('PRAGMA table_info(tickets)')}
            if not columns or 'tenant_id' in columns:
                raise click.ClickException('Migration requires the original single-tenant schema; no changes made.')
            # Exclusive creation prevents overwriting either the source or an earlier backup.
            try:
                with backup.open('xb'):
                    pass
            except FileExistsError:
                raise click.ClickException('Backup path already exists; choose a new path.') from None
            backup.chmod(0o600)
            with sqlite3.connect(backup) as destination:
                conn.backup(destination)
            conn.execute('PRAGMA foreign_keys=OFF')
            tables = ('users', 'tickets', 'operations', 'inspections', 'events')
            prefix = 'BEGIN EXCLUSIVE;\nDROP TRIGGER events_no_update;\nDROP TRIGGER events_no_delete;\n'
            prefix += '\n'.join(f'ALTER TABLE {table} RENAME TO legacy_{table};' for table in tables)
            try:
                # executescript starts the transaction; all DDL and copies roll back together.
                conn.executescript(prefix + '\n' + schema)
                conn.execute('INSERT INTO tenants(id,slug,name) VALUES(1,?,?)', (tenant, name.strip()))
                conn.execute('INSERT INTO users SELECT id,username,password_hash FROM legacy_users')
                conn.execute('INSERT INTO memberships SELECT 1,id,roles FROM legacy_users')
                for table in tables[1:]:
                    fields = [row[1] for row in conn.execute(f'PRAGMA table_info(legacy_{table})')]
                    names = ','.join(fields)
                    conn.execute(f'INSERT INTO {table}({names},tenant_id) SELECT {names},1 FROM legacy_{table}')
                for table in reversed(tables):
                    conn.execute(f'DROP TABLE legacy_{table}')
                if conn.execute('PRAGMA foreign_key_check').fetchall():
                    raise click.ClickException('Ownership validation failed; migration rolled back. Backup retained.')
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.execute('PRAGMA foreign_keys=ON')
        click.echo(f'Migrated all legacy records to {tenant}. Backup: {backup}')
