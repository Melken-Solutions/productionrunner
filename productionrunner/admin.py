"""Audited, organization-scoped account administration."""
import json
import re
import sqlite3

from flask import abort, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from .pin_auth import audit
from .tenancy import ROLES


def epoch(conn, tenant_id, user_id):
    row = conn.execute('SELECT session_epoch FROM membership_controls WHERE tenant_id=? AND user_id=?',
                       (tenant_id, user_id)).fetchone()
    return row[0] if row else 0


def bump(conn, tenant_id, user_id, invalidate=False):
    conn.execute('INSERT INTO membership_controls(tenant_id,user_id,version,session_epoch) VALUES(?,?,1,?) '
                 'ON CONFLICT(tenant_id,user_id) DO UPDATE SET version=version+1,session_epoch=session_epoch+?',
                 (tenant_id, user_id, int(invalidate), int(invalidate)))


def protects_last_admin(conn, tenant_id, user_id, assigned):
    members = conn.execute('SELECT user_id,roles FROM memberships WHERE tenant_id=?', (tenant_id,)).fetchall()
    was_admin = any(row[0] == user_id and 'admin' in row[1].split(',') for row in members)
    other_admin = any(row[0] != user_id and 'admin' in row[1].split(',') for row in members)
    return was_admin and 'admin' not in assigned.split(',') and not other_admin


def register_admin(app, db, roles_required):
    def selected_roles():
        values = set(request.form.getlist('roles'))
        if not values or not values <= ROLES:
            abort(400, 'Select at least one valid role.')
        return ','.join(sorted(values))

    def reason():
        value = request.form.get('reason', '').strip()
        if not value or len(value) > 500:
            abort(400, 'Record a reason of 1–500 characters; never include credentials.')
        return value

    def record(user_id, action, detail):
        audit(db(), g.tenant['id'], user_id, action, f"user:{g.user['id']}", app.config['AUTH_CLOCK'](), detail)

    def member(user_id):
        row = db().execute('SELECT u.id,u.username,m.roles,COALESCE(c.version,0) AS version, '
                           'p.pin_hash IS NOT NULL AS has_pin FROM memberships m JOIN users u ON u.id=m.user_id '
                           'LEFT JOIN membership_controls c ON c.tenant_id=m.tenant_id AND c.user_id=m.user_id '
                           'LEFT JOIN pin_credentials p ON p.tenant_id=m.tenant_id AND p.user_id=m.user_id '
                           'WHERE m.tenant_id=? AND m.user_id=?', (g.tenant['id'], user_id)).fetchone()
        if not row:
            abort(404, 'Unknown organization member.')
        return row

    def lock_member(user_id):
        db().execute('BEGIN IMMEDIATE')
        # Recheck the acting admin after acquiring the write lock.
        from flask import session
        acting = member(g.user['id'])
        if 'admin' not in acting['roles'].split(',') or epoch(db(), g.tenant['id'], g.user['id']) != session.get('membership_epoch', 0):
            abort(403, 'Your access changed. Sign in again.')
        target = member(user_id)
        if request.form.get('version') != str(target['version']):
            abort(409, 'This user changed. Reload before submitting again.')
        return target

    @app.get('/admin')
    @roles_required('admin')
    def admin_dashboard():
        return render_template('admin_dashboard.html')

    def organization():
        return db().execute("SELECT t.id,t.slug,t.name,COALESCE(p.contact_name,'') AS contact_name, "
                            "COALESCE(p.contact_email,'') AS contact_email,COALESCE(p.phone,'') AS phone, "
                            "COALESCE(p.address,'') AS address,COALESCE(p.version,0) AS version "
                            'FROM tenants t LEFT JOIN organization_profiles p ON p.tenant_id=t.id WHERE t.id=?',
                            (g.tenant['id'],)).fetchone()

    @app.get('/admin/organization')
    @roles_required('admin')
    def admin_organization():
        return render_template('admin_organization.html', organization=organization(), saved=request.args.get('saved') == '1')

    @app.post('/admin/organization')
    @roles_required('admin')
    def update_organization():
        limits = dict(name=160, contact_name=160, contact_email=254, phone=60, address=1000)
        values = {key: request.form.get(key, '').strip() for key in limits}
        detail = reason()
        if not values['name'] or any(len(values[key]) > limit for key, limit in limits.items()):
            abort(400, 'Provide an organization name and keep each field within its displayed limit.')
        if values['contact_email'] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', values['contact_email']):
            abort(400, 'Enter a valid contact email address or leave it blank.')
        if any(any(ord(c) < 32 and not (key == 'address' and c in '\r\n\t') for c in value) for key, value in values.items()):
            abort(400, 'Remove control characters from organization details.')
        with db():
            db().execute('BEGIN IMMEDIATE')
            from flask import session
            acting = member(g.user['id'])
            if 'admin' not in acting['roles'].split(',') or epoch(db(), g.tenant['id'], g.user['id']) != session.get('membership_epoch', 0):
                abort(403, 'Your access changed. Sign in again.')
            current = organization()
            if request.form.get('version') != str(current['version']):
                abort(409, 'Organization details changed. Reload before saving again.')
            changes = {key: {'before': current[key], 'after': value} for key, value in values.items() if current[key] != value}
            if changes:
                db().execute('UPDATE tenants SET name=? WHERE id=?', (values['name'], g.tenant['id']))
                db().execute('INSERT INTO organization_profiles(tenant_id,contact_name,contact_email,phone,address,version) '
                             'VALUES(?,?,?,?,?,1) ON CONFLICT(tenant_id) DO UPDATE SET '
                             'contact_name=excluded.contact_name,contact_email=excluded.contact_email,'
                             'phone=excluded.phone,address=excluded.address,version=version+1',
                             (g.tenant['id'], values['contact_name'], values['contact_email'], values['phone'], values['address']))
                record(g.user['id'], 'organization_updated', json.dumps({'reason': detail, 'changes': changes}, ensure_ascii=False))
        return redirect(url_for('admin_organization', saved='1'))

    @app.get('/admin/users')
    @roles_required('admin')
    def admin_users():
        members = db().execute('SELECT u.id,u.username,m.roles FROM memberships m JOIN users u ON u.id=m.user_id '
                               'WHERE m.tenant_id=? ORDER BY u.username', (g.tenant['id'],)).fetchall()
        return render_template('admin_users.html', members=members, roles=sorted(ROLES))

    @app.post('/admin/users')
    @roles_required('admin')
    def admin_create_user():
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        assigned, detail = selected_roles(), reason()
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._@-]{0,79}', username):
            abort(400, 'Use a username of 1–80 letters, digits, dots, underscores, @ or hyphens.')
        if not 12 <= len(password) <= 256 or password != request.form.get('password_confirm'):
            abort(400, 'Passwords must match and contain 12–256 characters.')
        hashed = generate_password_hash(password)
        with db():
            db().execute('BEGIN IMMEDIATE')
            from flask import session
            acting = member(g.user['id'])
            if 'admin' not in acting['roles'].split(',') or epoch(db(), g.tenant['id'], g.user['id']) != session.get('membership_epoch', 0):
                abort(403, 'Your access changed. Sign in again.')
            # Never claim or reset an existing global identity from another tenant.
            try:
                created = db().execute('INSERT INTO users(username,password_hash) VALUES(?,?)', (username, hashed))
            except sqlite3.IntegrityError:
                abort(409, 'That username is unavailable. Choose another username.')
            user_id = created.lastrowid
            db().execute('INSERT INTO memberships VALUES(?,?,?)', (g.tenant['id'], user_id, assigned))
            record(user_id, 'user_created', f'Roles: {assigned}. Reason: {detail}')
        return redirect(url_for('admin_user', user_id=user_id))

    @app.get('/admin/users/<int:user_id>')
    @roles_required('admin')
    def admin_user(user_id):
        return render_template('admin_user.html', member=member(user_id), roles=sorted(ROLES))

    @app.post('/admin/users/<int:user_id>/access')
    @roles_required('admin')
    def admin_access(user_id):
        action, detail = request.form.get('action'), reason()
        if action not in {'roles', 'revoke'}:
            abort(400, 'Choose a valid access action.')
        assigned = '' if action == 'revoke' else selected_roles()
        with db():
            target = lock_member(user_id)
            if protects_last_admin(db(), g.tenant['id'], user_id, assigned):
                abort(409, 'Keep at least one active admin. Assign another admin first.')
            if assigned != target['roles']:
                db().execute('UPDATE memberships SET roles=? WHERE tenant_id=? AND user_id=?', (assigned, g.tenant['id'], user_id))
                bump(db(), g.tenant['id'], user_id, invalidate=True)
                record(user_id, 'access_revoked' if not assigned else 'roles_changed',
                       f"{target['roles'] or 'inactive'} → {assigned or 'inactive'}. Reason: {detail}")
        return redirect(url_for('admin_users'))

    @app.post('/admin/users/<int:user_id>/pin')
    @roles_required('admin')
    def admin_pin(user_id):
        action, detail = request.form.get('action'), reason()
        if action not in {'reset', 'disable'}:
            abort(400, 'Choose a valid PIN action.')
        pin_hash = None
        if action == 'reset':
            pin = request.form.get('pin', '')
            if not re.fullmatch(r'[0-9]{6,12}', pin) or pin != request.form.get('pin_confirm'):
                abort(400, 'PINs must match and contain 6–12 digits.')
            pin_hash = generate_password_hash(pin)
        with db():
            target = lock_member(user_id)
            if not target['roles']:
                abort(409, 'Restore this membership before changing its PIN.')
            db().execute('INSERT INTO pin_credentials(tenant_id,user_id,pin_hash,version) VALUES(?,?,?,1) '
                         'ON CONFLICT(tenant_id,user_id) DO UPDATE SET pin_hash=excluded.pin_hash,version=version+1',
                         (g.tenant['id'], user_id, pin_hash))
            bump(db(), g.tenant['id'], user_id)
            record(user_id, 'pin_disabled' if action == 'disable' else ('pin_reset' if target['has_pin'] else 'pin_enrolled'), detail)
        return redirect(url_for('admin_user', user_id=user_id))
