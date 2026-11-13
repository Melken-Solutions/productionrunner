"""Organization-owned controlled vocabulary for material specifications."""
import json
import sqlite3
from decimal import Decimal, InvalidOperation
from flask import abort, g, request, render_template, redirect, url_for
from .intake import actor
from .pin_auth import audit

LABELS = {'metal':'Metal','alloy':'Grade / alloy','gauge':'Gauge','thickness_mm':'Thickness (mm)','finish':'Stock finish','stock_size':'Sheet size','required_service':'Required outside service'}
SCOPED = {'alloy','gauge','thickness_mm','finish'}


def choices(conn, tenant, active=False):
    return conn.execute('SELECT * FROM material_choices WHERE tenant_id=?' + (' AND active=1' if active else '') + ' ORDER BY field,metal,value', (tenant,)).fetchall()


def validate(conn, tenant, data, old=None):
    for field in LABELS:
        value=data[field]
        if not value:
            continue
        scope=data['metal'] if field in SCOPED else ''
        row=conn.execute('SELECT active FROM material_choices WHERE tenant_id=? AND field=? AND value=? AND metal=?',(tenant,field,value,scope)).fetchone()
        if row and row['active']:
            continue
        # Historical specifications remain editable without reactivating a retired choice.
        if old and old[field]==value and (field not in SCOPED or old['metal']==data['metal']):
            continue
        abort(400,'Choose an active approved '+LABELS[field].lower()+'. Add missing options in Manage choices first.')


def register(app, db, roles_required):
    @app.route('/admin/materials/choices', methods=['GET','POST'])
    @roles_required('admin')
    def material_choices_admin():
        if request.method=='POST':
            reason=request.form.get('reason','').strip()
            if not 1<=len(reason)<=500:abort(400,'Record a reason for the choice change.')
            with db():
                db().execute('BEGIN IMMEDIATE');actor(db(),g.tenant['id'],g.user['id'],('admin',))
                cid=request.form.get('choice_id')
                if cid:
                    old=db().execute('SELECT * FROM material_choices WHERE tenant_id=? AND id=?',(g.tenant['id'],cid)).fetchone()
                    if not old:abort(404)
                    active=request.form.get('active')
                    if str(old['version'])!=request.form.get('version'):abort(409,'Choice changed. Reload before saving.')
                    if active not in {'0','1'}:abort(400,'Choose active or inactive.')
                    db().execute('UPDATE material_choices SET active=?,version=version+1 WHERE tenant_id=? AND id=?',(int(active),g.tenant['id'],cid))
                    detail=dict(choice_id=old['id'],before=dict(old),active=int(active),reason=reason)
                else:
                    field=request.form.get('field');value=request.form.get('value','').strip();metal=request.form.get('metal','').strip() if field in SCOPED else ''
                    if field not in LABELS or not 1<=len(value)<=200:abort(400,'Choose a property and enter a value of 1–200 characters.')
                    if field in SCOPED and not db().execute("SELECT 1 FROM material_choices WHERE tenant_id=? AND field='metal' AND value=? AND active=1",(g.tenant['id'],metal)).fetchone():abort(400,'Choose an active metal for this property.')
                    if field=='thickness_mm':
                        try:
                            number=Decimal(value)
                            if not number.is_finite() or not 0<number<=100:raise ValueError
                        except (InvalidOperation,ValueError):abort(400,'Thickness must be a positive millimeter measurement up to 100.')
                    try:
                        cid=db().execute('INSERT INTO material_choices(tenant_id,field,value,metal) VALUES(?,?,?,?)',(g.tenant['id'],field,value,metal)).lastrowid
                    except sqlite3.IntegrityError:abort(409,'This choice already exists. Reactivate it if needed.')
                    detail=dict(choice_id=cid,field=field,value=value,metal=metal,reason=reason)
                audit(db(),g.tenant['id'],g.user['id'],'material_choice_changed',f"user:{g.user['id']}",app.config['AUTH_CLOCK'](),json.dumps(detail))
            return redirect(url_for('material_choices_admin'))
        return render_template('material_choices.html',choices=choices(db(),g.tenant['id']),labels=LABELS,scoped=SCOPED)
