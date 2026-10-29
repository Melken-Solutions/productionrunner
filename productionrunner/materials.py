"""Organization-controlled sheet stock and immutable job material requirements."""
import json
import sqlite3
from decimal import Decimal, InvalidOperation
from flask import abort,g,request,render_template,redirect,url_for
from .intake import actor
from .pin_auth import audit

FIELDS=('name','metal','gauge','finish','alloy','thickness_mm','stock_size','handling','required_service')


def catalog(conn,tenant,active=True):
    return conn.execute('SELECT * FROM materials WHERE tenant_id=?'+(' AND active=1' if active else '')+' ORDER BY name',(tenant,)).fetchall()


def request_material(conn,tenant,rid):
    row=conn.execute('SELECT * FROM request_materials WHERE tenant_id=? AND request_id=?',(tenant,rid)).fetchone()
    return dict(row,spec=json.loads(row['spec'])) if row else None


def ticket_material(conn,tenant,tid):
    row=conn.execute('SELECT * FROM ticket_materials WHERE tenant_id=? AND ticket_id=?',(tenant,tid)).fetchone()
    return dict(row,spec=json.loads(row['spec'])) if row else None


def select_material(conn,tenant,form):
    mid=form.get('material_id','')
    if not mid:
        if catalog(conn,tenant,False):abort(400,'Choose an active material from the organization catalog.')
        return None
    row=conn.execute('SELECT * FROM materials WHERE tenant_id=? AND id=? AND active=1',(tenant,mid)).fetchone()
    if not row:abort(400,'Choose an active material belonging to this organization.')
    count=form.get('planned_sheets','').strip();notes=form.get('material_notes','').strip()
    if count:
        try:
            number=Decimal(count)
            if not number.is_finite() or not Decimal('0')<number<=Decimal('100000') or len(count)>20:raise ValueError()
        except (InvalidOperation,ValueError):abort(400,'Planned sheets must be a positive number up to 100000.')
    if len(notes)>1000:abort(400,'Material notes must be at most 1000 characters.')
    return dict(material_id=row['id'],material_version=row['version'],spec={k:row[k] for k in FIELDS},planned_sheets=count,notes=notes)


def save_request(conn,tenant,rid,selected):
    conn.execute('DELETE FROM request_materials WHERE tenant_id=? AND request_id=?',(tenant,rid))
    if selected:conn.execute('INSERT INTO request_materials VALUES(?,?,?,?,?,?,?)',(tenant,rid,selected['material_id'],selected['material_version'],json.dumps(selected['spec']),selected['planned_sheets'],selected['notes']))


def validate_request(conn,tenant,rid):
    m=request_material(conn,tenant,rid)
    if not m:
        if catalog(conn,tenant,False):abort(409,'Edit this request and choose a material before approval or JobRunner creation.')
        return None
    current=conn.execute('SELECT version,active FROM materials WHERE tenant_id=? AND id=?',(tenant,m['material_id'])).fetchone()
    if not current or not current['active'] or current['version']!=m['material_version']:abort(409,'Material specification changed or was deactivated. Edit and reapprove this request.')
    service=m['spec']['required_service']
    if service:
        from .request_outside import get_plan
        plan=get_plan(conn,tenant,rid)
        if not plan or plan['service'].strip().casefold()!=service.casefold():abort(409,'This material requires outside '+service+'. Complete its vendor plan on the request.')
    return m


def snapshot(conn,tenant,rid,tid,event,original=None):
    m=validate_request(conn,tenant,rid)
    old=ticket_material(conn,tenant,original) if original else None
    if old and (not m or m['material_id']!=old['material_id']):abort(409,'The repair request must use the original job material.')
    if m:
        conn.execute('INSERT INTO ticket_materials VALUES(?,?,?,?,?,?,?)',(tenant,tid,m['material_id'],m['material_version'],json.dumps(m['spec']),m['planned_sheets'],m['notes']))
        require_service(conn,tenant,tid)
        event(tid,'material_specification_applied',json.dumps(m))


def require_service(conn,tenant,tid):
    m=ticket_material(conn,tenant,tid)
    if not m or not m['spec']['required_service']:return
    last=conn.execute('SELECT id FROM operations WHERE tenant_id=? AND ticket_id=? ORDER BY sequence DESC LIMIT 1',(tenant,tid)).fetchone()
    plan=conn.execute('SELECT service FROM outside_steps WHERE tenant_id=? AND ticket_id=? AND operation_id=?',(tenant,tid,last['id'] if last else None)).fetchone()
    if not plan or plan['service'].strip().casefold()!=m['spec']['required_service'].casefold():abort(409,'Material requires outside '+m['spec']['required_service']+' as the final production step, before independent inspection and packing.')


def register_materials(app,db,roles_required):
    from .material_choices import register, choices, LABELS, SCOPED, validate as validate_choices
    register(app,db,roles_required)
    @app.context_processor
    def context():
        return dict(material_catalog=lambda:catalog(db(),g.tenant['id']),request_material=lambda rid:request_material(db(),g.tenant['id'],rid),ticket_material=lambda t:ticket_material(db(),g.tenant['id'],t['id']))

    @app.route('/admin/materials',methods=['GET','POST'])
    @roles_required('admin')
    def materials_admin():
        if request.method=='POST':
            data={k:request.form.get(k,'').strip() for k in FIELDS}
            if not data['name']:
                data['name']=' · '.join(data[k] for k in ('gauge','metal','alloy','finish','thickness_mm','stock_size','required_service') if data[k])
            if any(len(v)>(1000 if k=='handling' else 200) for k,v in data.items()) or any(not data[k] for k in ('name','metal','gauge','finish')):abort(400,'Provide name, metal, gauge and finish within the field limits.')
            if data['thickness_mm']:
                try:
                    v=Decimal(data['thickness_mm'])
                    if not v.is_finite() or not 0<v<=100:raise ValueError()
                except (InvalidOperation,ValueError):abort(400,'Thickness must be a positive millimeter measurement up to 100. Do not infer it from gauge.')
            why=request.form.get('reason','').strip();active=request.form.get('active','1')
            if not 1<=len(why)<=500 or active not in {'0','1'}:abort(400,'Provide a reason and valid active status.')
            with db():
                db().execute('BEGIN IMMEDIATE');actor(db(),g.tenant['id'],g.user['id'],('admin',))
                mid=request.form.get('material_id');old=None
                if mid:
                    old=db().execute('SELECT * FROM materials WHERE tenant_id=? AND id=?',(g.tenant['id'],mid)).fetchone()
                    if not old:abort(404)
                    if request.form.get('version')!=str(old['version']):abort(409,'Material changed. Reload before saving.')
                validate_choices(db(),g.tenant['id'],data,old)
                try:
                    if old:db().execute('UPDATE materials SET '+','.join(k+'=?' for k in FIELDS)+',active=?,version=version+1 WHERE tenant_id=? AND id=?',(*(data[k] for k in FIELDS),int(active),g.tenant['id'],mid))
                    else:mid=db().execute('INSERT INTO materials(tenant_id,'+','.join(FIELDS)+',active) VALUES('+','.join('?' for _ in range(len(FIELDS)+2))+')',(g.tenant['id'],*(data[k] for k in FIELDS),int(active))).lastrowid
                except sqlite3.IntegrityError:abort(409,'A material with that name already exists.')
                audit(db(),g.tenant['id'],g.user['id'],'material_updated' if old else 'material_created',f"user:{g.user['id']}",app.config['AUTH_CLOCK'](),json.dumps(dict(material_id=mid,before=dict(old) if old else None,after=dict(data,active=int(active)),reason=why)))
            return redirect(url_for('materials_admin'))
        return render_template('materials.html',materials=catalog(db(),g.tenant['id'],False),fields=FIELDS,choice_rows=choices(db(),g.tenant['id']),choice_labels=LABELS,scoped=SCOPED)
