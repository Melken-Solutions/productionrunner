"""Tenant-owned approved vendors and their distinct supported services."""
import json
import sqlite3
from flask import abort,g,request,render_template,redirect,url_for
from .intake import actor
from .pin_auth import audit


def catalog(conn,tenant,active=True):
    rows=conn.execute('SELECT * FROM vendors WHERE tenant_id=?'+(' AND active=1' if active else '')+' ORDER BY name',(tenant,)).fetchall()
    return [dict(r,services=json.loads(r['services'])) for r in rows]


def validate(conn,tenant,vendor,service):
    entries=catalog(conn,tenant,False)
    if not entries:return  # Organizations without a catalog retain their existing workflow.
    if not any(v['active'] and v['name']==vendor and service in v['services'] for v in entries):
        abort(409,'Choose an active approved vendor and one of its supported services. Ask Admin to update the vendor catalog if needed.')


def validate_request(conn,tenant,rid):
    from .request_outside import get_plan
    plan=get_plan(conn,tenant,rid)
    if plan:validate(conn,tenant,plan['vendor'],plan['service'])


def validate_ticket(conn,tenant,tid):
    for plan in conn.execute('SELECT vendor,service FROM outside_steps WHERE tenant_id=? AND ticket_id=?',(tenant,tid)):
        validate(conn,tenant,plan['vendor'],plan['service'])


def register_vendors(app,db,roles_required):
    @app.context_processor
    def context():
        return dict(vendor_catalog=lambda:catalog(db(),g.tenant['id']),vendor_catalog_configured=lambda:bool(catalog(db(),g.tenant['id'],False)))

    @app.route('/admin/vendors',methods=['GET','POST'])
    @roles_required('admin')
    def vendors_admin():
        if request.method=='POST':
            name=request.form.get('name','').strip();reason=request.form.get('reason','').strip()
            services=[s.strip() for s in request.form.get('services','').splitlines() if s.strip()]
            active=request.form.get('active','1')
            if not 1<=len(name)<=200 or any(ord(c)<32 for c in name) or not 1<=len(reason)<=500 or active not in {'0','1'}:
                abort(400,'Provide a vendor name, status and change reason.')
            if not 1<=len(services)<=50 or any(len(s)>200 or any(ord(c)<32 for c in s) for s in services) or len({s.casefold() for s in services})!=len(services):
                abort(400,'Enter 1–50 distinct services, one per line, up to 200 characters each.')
            with db():
                db().execute('BEGIN IMMEDIATE');actor(db(),g.tenant['id'],g.user['id'],('admin',))
                vid=request.form.get('vendor_id');old=None
                if vid:
                    old=db().execute('SELECT * FROM vendors WHERE tenant_id=? AND id=?',(g.tenant['id'],vid)).fetchone()
                    if not old:abort(404)
                    if request.form.get('version')!=str(old['version']):abort(409,'Vendor changed. Reload before saving.')
                try:
                    if old:db().execute('UPDATE vendors SET name=?,services=?,active=?,version=version+1 WHERE tenant_id=? AND id=?',(name,json.dumps(services),int(active),g.tenant['id'],vid))
                    else:vid=db().execute('INSERT INTO vendors(tenant_id,name,services,active) VALUES(?,?,?,?)',(g.tenant['id'],name,json.dumps(services),int(active))).lastrowid
                except sqlite3.IntegrityError:abort(409,'A vendor with that name already exists.')
                audit(db(),g.tenant['id'],g.user['id'],'vendor_updated' if old else 'vendor_created',f"user:{g.user['id']}",app.config['AUTH_CLOCK'](),json.dumps(dict(vendor_id=vid,before=dict(old) if old else None,after=dict(name=name,services=services,active=int(active)),reason=reason)))
            return redirect(url_for('vendors_admin'))
        return render_template('vendors.html',vendors=catalog(db(),g.tenant['id'],False))
