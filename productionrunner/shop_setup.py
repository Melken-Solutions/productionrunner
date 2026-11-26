"""Reviewed, additive starter configuration for a sheet-metal shop."""
import json
from flask import abort,g,request,render_template,redirect,url_for
from .intake import actor
from .pin_auth import audit
from .materials import FIELDS

VERSION='sheet-metal-v1'
STATIONS=('Laser 1','Laser 2','Drill press','Timesaver','Bending','Inspection','Packing')
MATERIALS=[dict(name=f'{gauge} {metal} {finish}',metal=metal,gauge=gauge,finish=finish,alloy='',thickness_mm='',stock_size='',handling='',required_service='Clear coating' if metal=='Brass' else '') for metal,finish in [('Stainless steel','Satin'),('Stainless steel','Mirror'),('Brass','Satin')] for gauge in ['14 ga','20 ga']]


def register(app,db,roles_required):
    @app.route('/admin/shop-setup',methods=['GET','POST'])
    @roles_required('admin')
    def shop_setup():
        if request.method=='POST':
            groups=set(request.form.getlist('groups'));reason=request.form.get('reason','').strip()
            if request.form.get('starter_version')!=VERSION:abort(409,'Starter setup changed. Review the current preview.')
            if not groups or not groups<={'stations','materials'} or request.form.get('confirm')!='yes' or not 1<=len(reason)<=500:abort(400,'Select setup sections, review the preview and record a reason.')
            created=[];skipped=[]
            with db():
                db().execute('BEGIN IMMEDIATE');actor(db(),g.tenant['id'],g.user['id'],('admin',))
                def insert(table,columns,values,label):
                    cursor=db().execute('INSERT OR IGNORE INTO '+table+'('+','.join(columns)+') VALUES('+','.join('?' for _ in values)+')',values)
                    (created if cursor.rowcount else skipped).append(label)
                if 'stations' in groups:
                    for name in STATIONS:insert('stations',['tenant_id','name'],[g.tenant['id'],name],'Station: '+name)
                if 'materials' in groups:
                    from .material_choices import LABELS,SCOPED
                    for m in MATERIALS:
                        for field in LABELS:
                            if m[field]:insert('material_choices',['tenant_id','field','value','metal'],[g.tenant['id'],field,m[field],m['metal'] if field in SCOPED else ''],'Choice: '+field+' / '+m[field])
                        # Never create a new material using a previously retired option.
                        blocked=False
                        for field in LABELS:
                            if m[field]:
                                row=db().execute('SELECT active FROM material_choices WHERE tenant_id=? AND field=? AND value=? AND metal=?',(g.tenant['id'],field,m[field],m['metal'] if field in SCOPED else '')).fetchone()
                                blocked=blocked or not row['active']
                        if blocked:skipped.append('Material (retired choice): '+m['name']);continue
                        insert('materials',['tenant_id']+list(FIELDS),[g.tenant['id']]+[m[k] for k in FIELDS],'Material: '+m['name'])
                audit(db(),g.tenant['id'],g.user['id'],'shop_starter_applied',f"user:{g.user['id']}",app.config['AUTH_CLOCK'](),json.dumps(dict(starter_version=VERSION,groups=sorted(groups),created=created,skipped=skipped,reason=reason)))
            return redirect(url_for('shop_setup',saved='1'))
        stations={r['name']:dict(r) for r in db().execute('SELECT * FROM stations WHERE tenant_id=?',(g.tenant['id'],))}
        materials={r['name']:dict(r) for r in db().execute('SELECT * FROM materials WHERE tenant_id=?',(g.tenant['id'],))}
        return render_template('shop_setup.html',starter_version=VERSION,starter_stations=STATIONS,starter_materials=MATERIALS,existing_stations=stations,existing_materials=materials,saved=request.args.get('saved')=='1')
