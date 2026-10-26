"""Separate platform identities: organization admins cannot grant developer access."""
import getpass
import hashlib
import json
import re
import secrets
import sqlite3
from functools import wraps

import click
from flask import abort, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .admin import bump, protects_last_admin
from .pin_auth import ACCOUNT_LIMIT, SOURCE_LIMIT, WINDOW, DUMMY_HASH, audit
from .tenancy import connect


def register_developer(app, db, now):
    def event(action, detail, tenant_id=None, actor=None):
        db().execute('INSERT INTO platform_events(actor,tenant_id,action,detail,created_at) VALUES(?,?,?,?,?)',
                     (actor or f"developer:{g.developer['id']}", tenant_id, action, detail, now()))

    @app.before_request
    def developer_identity():
        g.developer = None
        if session.get('developer_id'):
            row = db().execute('SELECT id,username,version FROM developer_accounts WHERE id=? AND active=1', (session['developer_id'],)).fetchone()
            if row and row['version'] == session.get('developer_version'):
                g.developer = row
            else:
                session.clear()
                session['csrf'] = secrets.token_hex(32)

    def required(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not g.developer:
                if g.user:
                    abort(403, 'Developer access requires a separate developer account.')
                return redirect(url_for('developer_login'))
            return fn(*args, **kwargs)
        return wrapped

    def lock():
        db().execute('BEGIN IMMEDIATE')
        current = db().execute('SELECT version FROM developer_accounts WHERE id=? AND active=1', (g.developer['id'],)).fetchone()
        if not current or current['version'] != session.get('developer_version'):
            abort(403, 'Developer access changed. Sign in again.')

    def field(name, limit, optional=False):
        value = request.form.get(name, '').strip()
        if (not optional and not value) or len(value) > limit or any(ord(c) < 32 and c not in '\r\n\t' for c in value):
            abort(400, f'Provide a valid {name} (maximum {limit} characters).')
        return value

    def new_admin_credentials():
        username = field('username', 80)
        password = request.form.get('password', '')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._@-]{0,79}', username):
            abort(400, 'Use letters, digits, dots, underscores, @ or hyphens for the username.')
        if not 12 <= len(password) <= 256 or password != request.form.get('password_confirm'):
            abort(400, 'Matching passwords of 12–256 characters are required.')
        return username, generate_password_hash(password)

    def create_admin(tenant_id, username, password_hash, reason):
        try:
            user = db().execute('INSERT INTO users(username,password_hash) VALUES(?,?)', (username, password_hash))
        except sqlite3.IntegrityError:
            abort(409, 'Username unavailable; choose a new individual username.')
        db().execute("INSERT INTO memberships VALUES(?,?,'admin')", (tenant_id, user.lastrowid))
        audit(db(), tenant_id, user.lastrowid, 'local_admin_created', f"developer:{g.developer['id']}", app.config['AUTH_CLOCK'](), reason)
        return user.lastrowid

    @app.route('/developer/login', methods=['GET', 'POST'])
    def developer_login():
        error, status = None, 200
        if request.method == 'POST':
            username = request.form.get('username', '')
            timestamp = app.config['AUTH_CLOCK']()
            keys = [('developer-account:' + hashlib.sha256(username.encode()).hexdigest(), ACCOUNT_LIMIT),
                    ('developer-source:' + hashlib.sha256((request.remote_addr or 'unknown').encode()).hexdigest(), SOURCE_LIMIT)]
            with db():
                db().execute('BEGIN IMMEDIATE')
                db().execute('DELETE FROM login_limits WHERE expires_at<=?', (timestamp,))
                limited = any((db().execute('SELECT failures FROM login_limits WHERE key=?', (key,)).fetchone() or [0])[0] >= limit for key, limit in keys)
                if limited:
                    status, error = 429, 'Too many attempts. Try again in 15 minutes.'
                else:
                    identity = db().execute('SELECT * FROM developer_accounts WHERE username=? AND active=1', (username,)).fetchone()
                    verified = check_password_hash(identity['password_hash'] if identity else DUMMY_HASH, request.form.get('password', ''))
                    if identity and verified:
                        db().execute('DELETE FROM login_limits WHERE key=?', (keys[0][0],))
                        event('developer_login', 'Password sign-in', actor=f"developer:{identity['id']}")
                        session.clear()
                        session.update(developer_id=identity['id'], developer_version=identity['version'], csrf=secrets.token_hex(32))
                        session.permanent = True
                        return redirect(url_for('developer_dashboard'))
                    for key, _ in keys:
                        db().execute('INSERT INTO login_limits VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET failures=failures+1', (key, timestamp+WINDOW))
                    if identity:
                        event('developer_login_failed', 'Invalid credential', actor=f"developer:{identity['id']}")
                    error = 'Username or password is incorrect.'
            response = app.make_response((render_template('developer_login.html', error=error), status))
            if status == 429:
                response.headers['Retry-After'] = str(WINDOW)
            return response
        return render_template('developer_login.html', error=error)

    @app.get('/developer')
    @required
    def developer_dashboard():
        organizations = db().execute("SELECT t.*, (SELECT COUNT(*) FROM memberships m WHERE m.tenant_id=t.id AND m.roles!='') AS members FROM tenants t ORDER BY t.name,t.id").fetchall()
        entries = db().execute('SELECT e.*,t.name AS organization_name FROM platform_events e LEFT JOIN tenants t ON t.id=e.tenant_id ORDER BY e.id DESC LIMIT 100').fetchall()
        return render_template('developer_dashboard.html', organizations=organizations, entries=entries)

    @app.post('/developer/organizations')
    @required
    def developer_create_organization():
        name, slug, reason = field('name',160), field('slug',64), field('reason',500)
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', slug):
            abort(400, 'Use a lowercase organization code containing letters, digits or hyphens.')
        username, hashed = new_admin_credentials()
        with db():
            lock()
            try:
                tenant = db().execute('INSERT INTO tenants(slug,name) VALUES(?,?)', (slug,name))
            except sqlite3.IntegrityError:
                abort(409, 'Organization sign-in code already exists.')
            user_id = create_admin(tenant.lastrowid, username, hashed, reason)
            event('organization_created', json.dumps(dict(name=name,slug=slug,admin_user_id=user_id,reason=reason)), tenant.lastrowid)
        return redirect(url_for('developer_organization', tenant_id=tenant.lastrowid))

    def organization(tenant_id):
        row = db().execute("SELECT t.*,COALESCE(p.version,0) AS version,COALESCE(p.contact_name,'') AS contact_name,COALESCE(p.contact_email,'') AS contact_email,COALESCE(p.phone,'') AS phone,COALESCE(p.address,'') AS address FROM tenants t LEFT JOIN organization_profiles p ON p.tenant_id=t.id WHERE t.id=?", (tenant_id,)).fetchone()
        if not row:
            abort(404, 'Unknown organization.')
        return row

    @app.get('/developer/organizations/<int:tenant_id>')
    @required
    def developer_organization(tenant_id):
        org = organization(tenant_id)
        members = db().execute('SELECT u.id,u.username,m.roles,COALESCE(c.version,0) AS version FROM memberships m JOIN users u ON u.id=m.user_id LEFT JOIN membership_controls c ON c.tenant_id=m.tenant_id AND c.user_id=m.user_id WHERE m.tenant_id=? ORDER BY u.username', (tenant_id,)).fetchall()
        return render_template('developer_organization.html', organization=org, members=members)

    @app.post('/developer/organizations/<int:tenant_id>/profile')
    @required
    def developer_update_profile(tenant_id):
        values = {key: field(key, limit, key != 'name') for key, limit in dict(name=160,contact_name=160,contact_email=254,phone=60,address=1000).items()}
        reason = field('reason',500)
        if values['contact_email'] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',values['contact_email']):
            abort(400, 'Enter a valid contact email or leave it blank.')
        with db():
            lock()
            org = organization(tenant_id)
            if request.form.get('version') != str(org['version']):
                abort(409, 'Organization changed. Reload before saving.')
            changes = {key: {'before':org[key],'after':value} for key,value in values.items() if org[key]!=value}
            if changes:
                db().execute('UPDATE tenants SET name=? WHERE id=?',(values['name'],tenant_id))
                db().execute('INSERT INTO organization_profiles VALUES(?,?,?,?,?,1) ON CONFLICT(tenant_id) DO UPDATE SET contact_name=excluded.contact_name,contact_email=excluded.contact_email,phone=excluded.phone,address=excluded.address,version=version+1', (tenant_id, values['contact_name'],values['contact_email'],values['phone'],values['address']))
                event('organization_updated',json.dumps(dict(reason=reason,changes=changes)),tenant_id)
        return redirect(url_for('developer_organization', tenant_id=tenant_id))

    @app.post('/developer/organizations/<int:tenant_id>/admins')
    @required
    def developer_add_admin(tenant_id):
        username, hashed = new_admin_credentials()
        reason = field('reason',500)
        with db():
            lock()
            organization(tenant_id)
            user_id = create_admin(tenant_id,username,hashed,reason)
            event('local_admin_created',json.dumps(dict(user_id=user_id,reason=reason)),tenant_id)
        return redirect(url_for('developer_organization',tenant_id=tenant_id))

    @app.post('/developer/organizations/<int:tenant_id>/members/<int:user_id>')
    @required
    def developer_admin_role(tenant_id,user_id):
        reason, action = field('reason',500), request.form.get('action')
        if action not in {'grant','remove'}:
            abort(400, 'Choose grant or remove local admin.')
        with db():
            lock()
            organization(tenant_id)
            target = db().execute('SELECT m.roles,COALESCE(c.version,0) AS version FROM memberships m LEFT JOIN membership_controls c ON c.tenant_id=m.tenant_id AND c.user_id=m.user_id WHERE m.tenant_id=? AND m.user_id=?', (tenant_id,user_id)).fetchone()
            if not target:
                abort(404, 'Unknown organization member.')
            if request.form.get('version') != str(target['version']):
                abort(409, 'Membership changed. Reload before saving.')
            roles = set(filter(None,target['roles'].split(',')))
            if action=='grant':
                roles.add('admin')
            else:
                roles.discard('admin')
            assigned=','.join(sorted(roles))
            if protects_last_admin(db(),tenant_id,user_id,assigned):
                abort(409,'Keep at least one active local admin. Assign another first.')
            if assigned != target['roles']:
                db().execute('UPDATE memberships SET roles=? WHERE tenant_id=? AND user_id=?',(assigned,tenant_id,user_id))
                bump(db(),tenant_id,user_id,invalidate=True)
                detail=json.dumps(dict(user_id=user_id,before=target['roles'],after=assigned,reason=reason))
                event('local_admin_changed',detail,tenant_id)
                audit(db(),tenant_id,user_id,'local_admin_changed',f"developer:{g.developer['id']}",app.config['AUTH_CLOCK'](),detail)
        return redirect(url_for('developer_organization',tenant_id=tenant_id))

    @app.cli.command('add-developer')
    @click.argument('username')
    @click.password_option()
    @click.option('--reason',required=True)
    def add_developer(username,password,reason):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._@-]{0,79}',username) or not 12<=len(password)<=256 or not 1<=len(reason.strip())<=500:
            raise click.ClickException('Valid username, 12–256 character password and reason required.')
        with connect(app) as conn:
            try:
                result=conn.execute('INSERT INTO developer_accounts(username,password_hash) VALUES(?,?)',(username,generate_password_hash(password)))
            except sqlite3.IntegrityError:
                raise click.ClickException('Developer username unavailable.') from None
            conn.execute('INSERT INTO platform_events(actor,action,detail,created_at) VALUES(?,?,?,?)',('local-os:'+getpass.getuser(),'developer_created',json.dumps(dict(developer_id=result.lastrowid,reason=reason)),now()))
        click.echo('Developer account created. Sign in at /developer/login.')

    @app.cli.command('set-developer-access')
    @click.argument('username')
    @click.option('--active/--inactive',required=True,default=None)
    @click.option('--reason',required=True)
    def set_developer_access(username,active,reason):
        if not 1<=len(reason.strip())<=500:
            raise click.ClickException('A reason of 1–500 characters is required.')
        with connect(app) as conn:
            conn.execute('BEGIN IMMEDIATE')
            target=conn.execute('SELECT id,active FROM developer_accounts WHERE username=?',(username,)).fetchone()
            if not target:
                raise click.ClickException('Unknown developer.')
            if not active and target[1] and conn.execute('SELECT COUNT(*) FROM developer_accounts WHERE active=1').fetchone()[0]<=1:
                raise click.ClickException('Keep at least one active developer.')
            conn.execute('UPDATE developer_accounts SET active=?,version=version+1 WHERE id=?',(int(active),target[0]))
            conn.execute('INSERT INTO platform_events(actor,action,detail,created_at) VALUES(?,?,?,?)',('local-os:'+getpass.getuser(),'developer_access_changed',json.dumps(dict(developer_id=target[0],active=active,reason=reason)),now()))
        click.echo('Developer access changed; old sessions invalidated.')
