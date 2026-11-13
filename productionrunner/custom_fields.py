"""Tenant-defined request fields with immutable definition/value snapshots on jobs."""
import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from flask import abort, g, request, render_template, redirect, url_for
from .pin_auth import audit

KINDS={'text':'Text','number':'Number','date':'Date','select':'Dropdown','boolean':'Yes / no'}


def definitions(conn, tenant, active=True):
    rows=conn.execute('SELECT * FROM custom_field_definitions WHERE tenant_id=?'+(' AND active=1' if active else '')+' ORDER BY id',(tenant,)).fetchall()
    return [dict(r,options=json.loads(r['options'])) for r in rows]


def revision(conn,tenant):
    return hashlib.sha256(json.dumps(definitions(conn,tenant),sort_keys=True).encode()).hexdigest()


def saved(conn,tenant,rid):
    row=conn.execute('SELECT data FROM request_custom_fields WHERE tenant_id=? AND request_id=?',(tenant,rid)).fetchone()
    return json.loads(row['data']) if row else []


def collect(conn,tenant,form,previous=None):
    fields=definitions(conn,tenant)
    if 'custom_fields_revision' not in form:
        if previous: return previous  # Existing integrations preserve already-saved evidence.
        if fields:abort(409,'Custom fields changed. Reopen the request form before saving.')
        return []
    if form['custom_fields_revision']!=revision(conn,tenant):abort(409,'Custom fields changed. Reopen the request form before saving.')
    result=[]
    for field in fields:
        value=form.get('custom_'+str(field['id']),'').strip()
        if len(value)>1000:abort(400,field['label']+' must be at most 1000 characters.')
        if value:
            try:
                if field['kind']=='number' and not Decimal(value).is_finite():raise ValueError
                if field['kind']=='date' and date.fromisoformat(value).isoformat()!=value:raise ValueError
                if field['kind']=='select' and value not in field['options']:raise ValueError
                if field['kind']=='boolean' and value not in {'yes','no'}:raise ValueError
            except (ValueError,InvalidOperation):abort(400,'Choose a valid value for '+field['label']+'.')
        result.append({k:field[k] for k in ('id','label','kind','options','required','version')}|dict(value=value))
    # Retired fields remain visible as historical evidence and cannot be silently erased.
    active_ids={f['id'] for f in fields}
    result.extend(dict(f,retired=True) for f in (previous or []) if f['id'] not in active_ids)
    return result


def save(conn,tenant,rid,data):
    conn.execute('INSERT INTO request_custom_fields VALUES(?,?,?) ON CONFLICT(request_id) DO UPDATE SET data=excluded.data',(tenant,rid,json.dumps(data)))


def validate(conn,tenant,rid):
    for field in saved(conn,tenant,rid):
        if field['required'] and not field.get('retired') and not field['value']:abort(409,'Complete required shop detail: '+field['label']+'. Edit the request before approval.')


def snapshot(conn,tenant,rid,tid):
    conn.execute('INSERT INTO ticket_custom_fields VALUES(?,?,?)',(tenant,tid,json.dumps(saved(conn,tenant,rid))))


def register(app,db,roles_required):
    from .intake import actor
    @app.context_processor
    def context():
        def ticket_fields(tid):
            row=db().execute('SELECT data FROM ticket_custom_fields WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],tid)).fetchone()
            return json.loads(row['data']) if row else []
        return dict(custom_definitions=lambda:definitions(db(),g.tenant['id']),custom_revision=lambda:revision(db(),g.tenant['id']),request_custom=lambda rid:saved(db(),g.tenant['id'],rid),ticket_custom=ticket_fields)

    @app.route('/admin/custom-fields',methods=['GET','POST'])
    @roles_required('admin')
    def custom_fields_admin():
        if request.method=='POST':
            reason=request.form.get('reason','').strip()
            if not 1<=len(reason)<=500:abort(400,'Record a reason for the configuration change.')
            with db():
                db().execute('BEGIN IMMEDIATE');actor(db(),g.tenant['id'],g.user['id'],('admin',))
                fid=request.form.get('field_id');old=None
                if fid:
                    old=db().execute('SELECT * FROM custom_field_definitions WHERE tenant_id=? AND id=?',(g.tenant['id'],fid)).fetchone()
                    if not old:abort(404)
                    if str(old['version'])!=request.form.get('version'):abort(409,'Field changed. Reload before saving.')
                label=request.form.get('label','').strip();kind=request.form.get('kind','')
                options=[x.strip() for x in request.form.get('options','').splitlines() if x.strip()] if kind=='select' else []
                if not 1<=len(label)<=100 or kind not in KINDS:abort(400,'Choose a field name and supported input type.')
                if kind=='select' and (not 1<=len(options)<=50 or len(set(options))!=len(options) or any(len(x)>100 for x in options)):abort(400,'Provide 1–50 unique dropdown choices, up to 100 characters each.')
                active=request.form.get('active','1');required=int(request.form.get('required')=='yes')
                if active not in {'0','1'}:abort(400,'Choose active or inactive.')
                # A definition's identity and type are stable; create a replacement for type changes.
                if old and kind!=old['kind']:abort(400,'Create a new field to change its input type.')
                if not old and len(definitions(db(),g.tenant['id'],False))>=50:abort(400,'This organization supports up to 50 custom fields.')
                if db().execute('SELECT 1 FROM custom_field_definitions WHERE tenant_id=? AND lower(label)=lower(?) AND id<>?',(g.tenant['id'],label,old['id'] if old else -1)).fetchone():abort(409,'A field with that name already exists.')
                data=dict(label=label,kind=kind,options=options,required=required,active=int(active))
                if old:db().execute('UPDATE custom_field_definitions SET label=?,options=?,required=?,active=?,version=version+1 WHERE tenant_id=? AND id=?',(label,json.dumps(options),required,int(active),g.tenant['id'],fid))
                else:fid=db().execute('INSERT INTO custom_field_definitions(tenant_id,label,kind,options,required,active) VALUES(?,?,?,?,?,?)',(g.tenant['id'],label,kind,json.dumps(options),required,int(active))).lastrowid
                audit(db(),g.tenant['id'],g.user['id'],'custom_field_configured',f"user:{g.user['id']}",app.config['AUTH_CLOCK'](),json.dumps(dict(field_id=fid,before=dict(old) if old else None,after=data,reason=reason)))
            return redirect(url_for('custom_fields_admin'))
        return render_template('custom_fields.html',fields=definitions(db(),g.tenant['id'],False),kinds=KINDS)
