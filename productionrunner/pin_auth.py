"""Tenant PIN credentials and durable login attempt limits for the local MVP."""
import getpass
import hashlib
import re
import secrets
import time
from datetime import datetime, timezone

import click
from flask import g, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from .tenancy import connect

WINDOW = 15 * 60
ACCOUNT_LIMIT = 5
SOURCE_LIMIT = 50
# Equal-cost verification for unknown accounts or unenrolled PINs. Never a usable credential.
DUMMY_HASH = generate_password_hash(secrets.token_hex(32))


def audit(conn, tenant_id, user_id, action, actor, timestamp, detail=''):
    conn.execute('INSERT INTO auth_events(tenant_id,user_id,action,actor,detail,created_at) VALUES(?,?,?,?,?,?)',
                 (tenant_id, user_id, action, actor, detail, datetime.fromtimestamp(timestamp, timezone.utc).isoformat()))


def authenticate(conn, clock):
    """Return (identity, status); all failure counters and audit writes are atomic."""
    timestamp = clock()
    tenant = request.form.get('tenant', '').strip()
    username = request.form.get('username', '')
    method = request.form.get('method', 'password')
    keys = [('account:' + hashlib.sha256((tenant + '\0' + username).encode()).hexdigest(), ACCOUNT_LIMIT),
            ('source:' + hashlib.sha256((request.remote_addr or 'unknown').encode()).hexdigest(), SOURCE_LIMIT)]
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('DELETE FROM login_limits WHERE expires_at<=?', (timestamp,))
        for key, limit in keys:
            row = conn.execute('SELECT failures FROM login_limits WHERE key=?', (key,)).fetchone()
            if row and row['failures'] >= limit:
                return None, 429
        identity = conn.execute(
            'SELECT u.id, u.password_hash, m.tenant_id, p.pin_hash, p.version FROM users u '
            'JOIN memberships m ON m.user_id=u.id JOIN tenants t ON t.id=m.tenant_id '
            'LEFT JOIN pin_credentials p ON p.tenant_id=m.tenant_id AND p.user_id=m.user_id '
            'WHERE u.username=? AND t.slug=? AND m.roles != ?', (username, tenant, '')).fetchone()
        credential = request.form.get('pin' if method == 'pin' else 'password', '')
        stored = identity['pin_hash' if method == 'pin' else 'password_hash'] if identity else None
        verified = check_password_hash(stored or DUMMY_HASH, credential)
        valid = method in {'pin', 'password'} and bool(stored) and verified
        if valid:
            conn.execute('DELETE FROM login_limits WHERE key=?', (keys[0][0],))
            audit(conn, identity['tenant_id'], identity['id'], 'login_success', f"user:{identity['id']}", timestamp, method)
            return dict(identity), 302
        for key, _ in keys:
            conn.execute('INSERT INTO login_limits(key,failures,expires_at) VALUES(?,1,?) '
                         'ON CONFLICT(key) DO UPDATE SET failures=failures+1', (key, timestamp + WINDOW))
        if identity:
            audit(conn, identity['tenant_id'], identity['id'], 'login_failed', 'unauthenticated', timestamp)
        return None, 200


def register_pin_auth(app, db, roles_required):
    app.config.setdefault('AUTH_CLOCK', time.time)

    @app.before_request
    def validate_pin_session():
        if session.get('auth_method') == 'pin':
            record = db().execute('SELECT pin_hash,version FROM pin_credentials WHERE tenant_id=? AND user_id=?',
                                  (session.get('tenant_id'), session.get('user_id'))).fetchone()
            if not record or not record['pin_hash'] or record['version'] != session.get('pin_version'):
                session.clear()
                session['csrf'] = secrets.token_hex(32)
                g.user, g.tenant, g.roles = None, None, []

    @app.cli.command('set-pin')
    @click.argument('username')
    @click.option('--tenant', required=True)
    @click.option('--reason', required=True, help='Administrative reason; never include the PIN.')
    @click.option('--disable', is_flag=True)
    def set_pin(username, tenant, reason, disable):
        """Trusted local administrator: enroll/reset a PIN, or disable it."""
        if not reason.strip() or len(reason) > 500:
            raise click.ClickException('Provide a reason of 1–500 characters.')
        pin_hash = None
        if not disable:
            pin = click.prompt('New PIN (6–12 digits)', hide_input=True, confirmation_prompt=True)
            if not re.fullmatch(r'[0-9]{6,12}', pin):
                raise click.ClickException('PIN must contain 6–12 ASCII digits.')
            pin_hash = generate_password_hash(pin)
        with connect(app) as conn:
            conn.execute('BEGIN IMMEDIATE')
            member = conn.execute('SELECT m.tenant_id,m.user_id FROM memberships m JOIN users u ON u.id=m.user_id '
                                  'JOIN tenants t ON t.id=m.tenant_id WHERE u.username=? AND t.slug=? AND m.roles != ?',
                                  (username, tenant, '')).fetchone()
            if not member:
                raise click.ClickException('Unknown or inactive organization membership.')
            old = conn.execute('SELECT version FROM pin_credentials WHERE tenant_id=? AND user_id=?', member).fetchone()
            conn.execute('INSERT INTO pin_credentials(tenant_id,user_id,pin_hash,version) VALUES(?,?,?,1) '
                         'ON CONFLICT(tenant_id,user_id) DO UPDATE SET pin_hash=excluded.pin_hash,version=version+1',
                         (*member, pin_hash))
            action = 'pin_disabled' if disable else ('pin_reset' if old else 'pin_enrolled')
            audit(conn, *member, action, 'local-os:' + getpass.getuser(), app.config['AUTH_CLOCK'](), reason.strip())
        click.echo('PIN updated. Existing PIN sessions for this membership are invalidated.')

    @app.get('/security-history')
    @roles_required('admin')
    def security_history():
        from flask import render_template
        entries = db().execute('SELECT a.*,u.username FROM auth_events a JOIN users u ON u.id=a.user_id '
                               'WHERE a.tenant_id=? ORDER BY a.id DESC LIMIT 200', (g.tenant['id'],)).fetchall()
        return render_template('security_history.html', entries=entries)
