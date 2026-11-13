"""Reauthenticated credential changes and trusted-host password recovery."""
import getpass
import json
import re
from datetime import datetime, timezone

import click
from flask import abort, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from .admin import bump, epoch
from .pin_auth import ACCOUNT_LIMIT, WINDOW, audit
from .tenancy import connect


def revoke_sessions(conn, user_id):
    for row in conn.execute('SELECT tenant_id FROM memberships WHERE user_id=?', (user_id,)).fetchall():
        bump(conn, row[0], user_id, invalidate=True)


def register_account_security(app, db):
    @app.route('/account/security', methods=['GET', 'POST'])
    def account_security():
        platform = bool(g.developer)
        identity = g.developer if platform else g.user
        if not identity:
            return redirect(url_for('login'))
        error, status = None, 200
        if request.method == 'POST':
            action = request.form.get('action')
            if action not in ({'password'} if platform else {'password', 'pin', 'disable_pin'}):
                abort(400, 'Choose a valid credential action.')
            value = request.form.get('new_credential', '')
            if action != 'disable_pin':
                valid = 12 <= len(value) <= 256 if action == 'password' else bool(re.fullmatch(r'[0-9]{6,12}', value))
                if not valid or value != request.form.get('confirm_credential'):
                    abort(400, 'Matching passwords of 12–256 characters or PINs of 6–12 digits are required.')
            timestamp = app.config['AUTH_CLOCK']()
            key = f"credential-change:{'platform' if platform else 'user'}:{identity['id']}"
            with db():
                db().execute('BEGIN IMMEDIATE')
                table = 'developer_accounts' if platform else 'users'
                current = db().execute(f'SELECT * FROM {table} WHERE id=?', (identity['id'],)).fetchone()
                if platform:
                    if not current['active'] or current['version'] != session.get('developer_version'):
                        abort(403, 'Your access changed. Sign in again.')
                elif epoch(db(), g.tenant['id'], identity['id']) != session.get('membership_epoch', 0):
                    abort(403, 'Your access changed. Sign in again.')
                db().execute('DELETE FROM login_limits WHERE expires_at<=?', (timestamp,))
                limit = db().execute('SELECT failures FROM login_limits WHERE key=?', (key,)).fetchone()
                if limit and limit[0] >= ACCOUNT_LIMIT:
                    error, status = 'Too many attempts. Try again in 15 minutes.', 429
                elif not check_password_hash(current['password_hash'], request.form.get('current_password', '')):
                    db().execute('INSERT INTO login_limits VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET failures=failures+1', (key, timestamp + WINDOW))
                    error, status = 'Current password is incorrect.', 400
                    record_change(db(), platform, identity['id'], None if platform else g.tenant['id'], 'credential_change_failed', timestamp)
                else:
                    if action == 'password':
                        if check_password_hash(current['password_hash'], value):
                            abort(400, 'Choose a different password.')
                        hashed = generate_password_hash(value)
                        if platform:
                            db().execute('UPDATE developer_accounts SET password_hash=?,version=version+1 WHERE id=?', (hashed, identity['id']))
                        else:
                            db().execute('UPDATE users SET password_hash=? WHERE id=?', (hashed, identity['id']))
                            revoke_sessions(db(), identity['id'])
                    else:
                        hashed = generate_password_hash(value) if action == 'pin' else None
                        db().execute('INSERT INTO pin_credentials(tenant_id,user_id,pin_hash,version) VALUES(?,?,?,1) ON CONFLICT(tenant_id,user_id) DO UPDATE SET pin_hash=excluded.pin_hash,version=version+1', (g.tenant['id'], identity['id'], hashed))
                        bump(db(), g.tenant['id'], identity['id'], invalidate=True)
                    db().execute('DELETE FROM login_limits WHERE key=?', (key,))
                    action_name = {'password': 'password_changed', 'pin': 'pin_changed', 'disable_pin': 'pin_disabled'}[action]
                    record_change(db(), platform, identity['id'], None if platform else g.tenant['id'], action_name, timestamp)
            if not error:
                session.clear()
                return redirect(url_for('developer_login' if platform else 'login', changed='1'))
        response = app.make_response((render_template('account_security.html', platform=platform, error=error), status))
        if status == 429:
            response.headers['Retry-After'] = str(WINDOW)
        return response

    @app.cli.command('reset-password')
    @click.argument('username')
    @click.option('--platform', is_flag=True, help='Reset a separate platform account.')
    @click.option('--reason', required=True)
    @click.password_option(confirmation_prompt=True)
    def reset_password(username, platform, reason, password):
        """Trusted host recovery. Organization passwords apply across memberships."""
        if not 12 <= len(password) <= 256 or not 1 <= len(reason.strip()) <= 500:
            raise click.ClickException('A password of 12–256 characters and a reason of 1–500 characters are required.')
        with connect(app) as conn:
            conn.execute('BEGIN IMMEDIATE')
            table = 'developer_accounts' if platform else 'users'
            target = conn.execute(f'SELECT id FROM {table} WHERE username=?', (username,)).fetchone()
            if not target:
                raise click.ClickException('Unknown account.')
            hashed = generate_password_hash(password)
            timestamp = app.config['AUTH_CLOCK']()
            actor = 'local-os:' + getpass.getuser()
            if platform:
                conn.execute('UPDATE developer_accounts SET password_hash=?,version=version+1 WHERE id=?', (hashed, target[0]))
                conn.execute('INSERT INTO platform_events(actor,action,detail,created_at) VALUES(?,?,?,?)',
                             (actor, 'password_reset', json.dumps(dict(developer_id=target[0], reason=reason)), datetime.fromtimestamp(timestamp, timezone.utc).isoformat()))
            else:
                conn.execute('UPDATE users SET password_hash=? WHERE id=?', (hashed, target[0]))
                revoke_sessions(conn, target[0])
                for member in conn.execute('SELECT tenant_id FROM memberships WHERE user_id=?', (target[0],)).fetchall():
                    audit(conn, member[0], target[0], 'password_reset', actor, timestamp, reason)
        click.echo('Password reset. Existing sessions revoked. Deliver the password privately.')


def record_change(conn, platform, user_id, tenant_id, action, timestamp):
    if platform:
        conn.execute('INSERT INTO platform_events(actor,action,detail,created_at) VALUES(?,?,?,?)',
                     (f'developer:{user_id}', action, 'Self-service account security', datetime.fromtimestamp(timestamp, timezone.utc).isoformat()))
    else:
        audit(conn, tenant_id, user_id, action, f'user:{user_id}', timestamp, 'Self-service account security')
