"""Browser-bound, single-use pairing for revocable display-only devices."""
import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from flask import abort, g, jsonify, render_template, request, session, redirect, url_for
from werkzeug.security import generate_password_hash


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def validate_session(conn):
    g.tv_device = None
    if not session.get('tv_device_id'):
        return
    if g.tenant and set(g.roles) == {'display'}:
        g.tv_device = conn.execute('SELECT * FROM tv_devices WHERE id=? AND tenant_id=? AND user_id=? AND revoked_at IS NULL',
            (session['tv_device_id'], g.tenant['id'], g.user['id'])).fetchone()
    if not g.tv_device:
        session.clear()
        g.user = g.tenant = None
        g.roles = []


def heartbeat(conn, now):
    if not g.tv_device:
        return None
    try: seen=int(request.args.get('seen_version', '0'))
    except ValueError: abort(400,'Invalid screen version.')
    if not 0<=seen<=2147483647: abort(400,'Invalid screen version.')
    with conn:
        conn.execute('UPDATE tv_devices SET last_seen=?,applied_version=CASE WHEN version=? THEN version ELSE applied_version END WHERE id=? AND revoked_at IS NULL',
            (now(), seen, g.tv_device['id']))
    return dict(version=g.tv_device['version'])


def register(app, db, roles_required, now):
    def clock():
        return app.config.get('AUTH_CLOCK', time.time)()

    def limited(key, maximum):
        # Persist attempts even when the caller rejects the request.
        with db():
            db().execute('BEGIN IMMEDIATE')
            row=db().execute('SELECT * FROM login_limits WHERE key=?',(key,)).fetchone()
            count=row['failures']+1 if row and row['expires_at']>clock() else 1
            expiry=row['expires_at'] if row and row['expires_at']>clock() else clock()+900
            db().execute('INSERT INTO login_limits(key,failures,expires_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET failures=excluded.failures,expires_at=excluded.expires_at',(key,count,expiry))
        if count>maximum:
            abort(429,'Too many pairing attempts. Try again in 15 minutes.')

    def pending():
        row=db().execute('SELECT * FROM tv_pairings WHERE id=?',(session.get('pair_id'),)).fetchone()
        if row and row['expires_at']>clock() and secrets.compare_digest(row['browser_hash'],digest(session.get('pair_secret',''))):
            return row
        return None

    def station_id():
        value=request.form.get('station','')
        if not value: return None
        try: value=int(value)
        except ValueError: abort(400,'Choose a valid station.')
        if not 1<=value<=2147483647: abort(400,'Choose a valid station.')
        row=db().execute('SELECT id FROM stations WHERE id=? AND tenant_id=? AND active=1',(value,g.tenant['id'])).fetchone()
        if not row: abort(400,'Choose an active station in your organization.')
        return row['id']

    def record(device, action):
        after=db().execute('SELECT name,station_id,version,revoked_at FROM tv_devices WHERE id=?',(device['id'],)).fetchone()
        detail=json.dumps(dict(device_id=device['id'],after=dict(after),before=dict(device)))
        db().execute('INSERT INTO auth_events(tenant_id,user_id,action,actor,detail,created_at) VALUES(?,?,?,?,?,?)',
            (g.tenant['id'],device['user_id'],action,'user:'+str(g.user['id']),detail,now()))

    @app.get('/tv/setup')
    def tv_setup():
        if g.tv_device: return redirect(url_for('tv_board'))
        if g.user: return 'Open this setup URL in the dedicated TV browser, signed out of personal accounts.',409
        return render_template('tv_setup.html')

    @app.post('/tv/pair/start')
    def tv_pair_start():
        if g.tv_device: return jsonify(url=url_for('tv_board'))
        if g.user: abort(409,'Use a signed-out TV browser.')
        row=pending()
        if row and not row['claimed_at']:
            return jsonify(code=session['pair_code'],expires_in=max(0,int(row['expires_at']-clock())))
        limited('tv-issue:'+digest(request.remote_addr or 'unknown'),120)
        code=''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(8))
        pid=secrets.token_hex(24); secret=secrets.token_urlsafe(32)
        with db():
            db().execute('DELETE FROM tv_pairings WHERE expires_at<=?',(clock(),))
            db().execute('INSERT INTO tv_pairings(id,code_hash,browser_hash,expires_at) VALUES(?,?,?,?)',(pid,digest(code),digest(secret),clock()+600))
        session.update(pair_id=pid,pair_secret=secret,pair_code=code[:4]+'-'+code[4:])
        return jsonify(code=session['pair_code'],expires_in=600)

    @app.post('/tv/pair/status')
    def tv_pair_status():
        if g.tv_device: return jsonify(url=url_for('tv_board'))
        if g.user: abort(409)
        with db():
            db().execute('BEGIN IMMEDIATE')
            row=pending()
            if not row or row['claimed_at']: return jsonify(state='expired')
            if not row['device_id']: return jsonify(state='waiting')
            device=db().execute('SELECT * FROM tv_devices WHERE id=? AND revoked_at IS NULL',(row['device_id'],)).fetchone()
            if not device: return jsonify(state='expired')
            from .admin import epoch,deleted_member
            member=db().execute('SELECT roles FROM memberships WHERE tenant_id=? AND user_id=?',(device['tenant_id'],device['user_id'])).fetchone()
            if not member or member['roles']!='display' or deleted_member(db(),device['tenant_id'],device['user_id']): return jsonify(state='expired')
            db().execute('UPDATE tv_pairings SET claimed_at=? WHERE id=?',(now(),row['id']))
            member_epoch=epoch(db(),device['tenant_id'],device['user_id'])
        session.clear()
        session.update(user_id=device['user_id'],tenant_id=device['tenant_id'],membership_epoch=member_epoch,
            auth_method='device',display_device=True,tv_device_id=device['id'],csrf=secrets.token_hex(32))
        session.permanent=True
        return jsonify(url=url_for('tv_board'))

    @app.route('/admin/tv-devices',methods=['GET','POST'])
    @roles_required('admin')
    def tv_devices_admin():
        if request.method=='POST':
            limited('tv-approve:'+str(g.tenant['id'])+':'+str(g.user['id']),20)
            code=request.form.get('code','').upper().replace('-','').replace(' ','')
            name=request.form.get('name','').strip()
            if not 1<=len(name)<=80: abort(400,'Enter a screen name of 1–80 characters.')
            with db():
                db().execute('BEGIN IMMEDIATE')
                from .intake import actor
                actor(db(),g.tenant['id'],g.user['id'],('admin',))
                row=db().execute('SELECT * FROM tv_pairings WHERE code_hash=?',(digest(code),)).fetchone()
                if not row or row['expires_at']<=clock() or row['device_id'] or row['claimed_at']: abort(400,'Code unavailable or expired. Use the current code shown on the TV.')
                station=station_id(); did=secrets.token_hex(24)
                uid=db().execute('INSERT INTO users(username,password_hash) VALUES(?,?)',('tv-'+did,generate_password_hash(secrets.token_urlsafe(48)))).lastrowid
                db().execute('INSERT INTO memberships(tenant_id,user_id,roles) VALUES(?,?,?)',(g.tenant['id'],uid,'display'))
                db().execute('INSERT INTO tv_devices(id,tenant_id,user_id,name,station_id,created_at) VALUES(?,?,?,?,?,?)',(did,g.tenant['id'],uid,name,station,now()))
                db().execute('UPDATE tv_pairings SET device_id=? WHERE id=?',(did,row['id']))
                record(dict(id=did,user_id=uid),'tv_device_paired')
            return redirect(url_for('tv_devices_admin'))
        devices=[dict(r) for r in db().execute('SELECT d.*,s.name AS station_name FROM tv_devices d LEFT JOIN stations s ON s.tenant_id=d.tenant_id AND s.id=d.station_id WHERE d.tenant_id=? ORDER BY d.created_at DESC',(g.tenant['id'],))]
        for d in devices:
            d['recent']=bool(d['last_seen'] and (datetime.now(timezone.utc)-datetime.fromisoformat(d['last_seen'])).total_seconds()<45)
        stations=db().execute('SELECT id,name FROM stations WHERE tenant_id=? AND active=1 ORDER BY name',(g.tenant['id'],)).fetchall()
        return render_template('tv_devices.html',devices=devices,stations=stations)

    @app.post('/admin/tv-devices/<device_id>')
    @roles_required('admin')
    def tv_device_manage(device_id):
        action=request.form.get('action')
        if action not in {'save','reload','revoke'}: abort(400)
        with db():
            db().execute('BEGIN IMMEDIATE')
            from .intake import actor
            actor(db(),g.tenant['id'],g.user['id'],('admin',))
            d=db().execute('SELECT * FROM tv_devices WHERE id=? AND tenant_id=? AND revoked_at IS NULL',(device_id,g.tenant['id'])).fetchone()
            if not d: abort(404)
            if request.form.get('version')!=str(d['version']): abort(409,'Screen changed. Refresh before trying again.')
            if action=='save':
                name=request.form.get('name','').strip()
                if not 1<=len(name)<=80: abort(400,'Enter a screen name of 1–80 characters.')
                db().execute('UPDATE tv_devices SET name=?,station_id=?,version=version+1 WHERE id=?',(name,station_id(),device_id))
            elif action=='reload': db().execute('UPDATE tv_devices SET version=version+1 WHERE id=?',(device_id,))
            else: db().execute('UPDATE tv_devices SET revoked_at=?,version=version+1 WHERE id=?',(now(),device_id))
            record(d,'tv_device_'+action)
        return redirect(url_for('tv_devices_admin'))
