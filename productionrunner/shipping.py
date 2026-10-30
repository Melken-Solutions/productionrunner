"""Destination-controlled boxes, parcel/pallet manifests, and remote label handoff."""
import hashlib
import io
import json
from decimal import Decimal, InvalidOperation
from flask import abort, g, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename
from .intake import actor, SOURCES
from .fulfillment import progress
from . import object_storage


def register_shipping(app, db, roles_required, now, event):
    def log(action, detail, load=None):
        db().execute('INSERT INTO shipping_events(tenant_id,load_id,actor_id,action,detail,created_at) VALUES(?,?,?,?,?,?)',
                     (g.tenant['id'],load,g.user['id'],action,json.dumps(detail),now()))

    def text(name, limit=500):
        value=request.form.get(name,'').strip()
        if not 1<=len(value)<=limit:abort(400,'Provide '+name.replace('_',' ')+'.')
        return value

    def lock(allowed=('planner','admin','operator')):
        db().execute('BEGIN IMMEDIATE')
        actor(db(),g.tenant['id'],g.user['id'],allowed)

    def load_for(lid):
        row=db().execute('SELECT * FROM shipping_loads WHERE tenant_id=? AND id=?',(g.tenant['id'],lid)).fetchone()
        if not row:abort(404)
        return row

    def locked_load(lid):
        lock(); row=load_for(lid)
        if request.form.get('version')!=str(row['version']):abort(409,'Shipment changed. Refresh before continuing.')
        if row['state']=='departed':abort(409,'This shipment has already departed.')
        return row

    def boxes(lid):
        return db().execute('SELECT b.*,f.quantity,f.serials,t.pn,t.revision,p.purpose,p.source,p.source_reference FROM shipping_boxes b JOIN fulfillment_records f ON f.id=b.pack_record_id AND f.tenant_id=b.tenant_id JOIN tickets t ON t.id=b.ticket_id AND t.tenant_id=b.tenant_id JOIN shipping_plans p ON p.ticket_id=b.ticket_id AND p.tenant_id=b.tenant_id WHERE b.tenant_id=? AND b.load_id=? AND b.voided=0 ORDER BY b.id',(g.tenant['id'],lid)).fetchall()

    def current_label(load):
        return db().execute('SELECT * FROM shipping_labels WHERE tenant_id=? AND load_id=? AND manifest_version=? ORDER BY id DESC LIMIT 1',(g.tenant['id'],load['id'],load['manifest_version'])).fetchone()

    def label_permission(load):
        allowed={'admin','planner'} if load['label_owner']=='planner' else ({'admin','operator'} if load['label_owner']=='packing' else {'admin','planner','operator'})
        actor(db(),g.tenant['id'],g.user['id'],allowed)

    @app.get('/admin/shipping')
    @roles_required('admin')
    def shipping_admin():
        return render_template('shipping_admin.html',sources=SOURCES,
            destinations=db().execute('SELECT * FROM shipping_destinations WHERE tenant_id=? ORDER BY name',(g.tenant['id'],)).fetchall(),
            rules=db().execute('SELECT * FROM shipping_rules WHERE tenant_id=? ORDER BY source',(g.tenant['id'],)).fetchall())

    @app.post('/admin/shipping')
    @roles_required('admin')
    def shipping_settings():
        why=text('reason')
        with db():
            lock(('admin',))
            if request.form.get('action')=='destination':
                name,address=text('name',120),text('address',1000)
                if db().execute('SELECT 1 FROM shipping_destinations WHERE tenant_id=? AND name=?',(g.tenant['id'],name)).fetchone():abort(409,'Destination name already exists.')
                db().execute('INSERT INTO shipping_destinations(tenant_id,name,address) VALUES(?,?,?)',(g.tenant['id'],name,address))
                log('destination_created',dict(name=name,address=address,reason=why))
            elif request.form.get('action')=='rule':
                source,purpose,method,owner=[request.form.get(k) for k in ('source','purpose','method','label_owner')]
                if source not in SOURCES or purpose not in {'stock','order'} or method not in {'fedex','ltl'} or owner not in {'planner','packing','either'}:abort(400,'Choose valid shipping defaults.')
                old=db().execute('SELECT * FROM shipping_rules WHERE tenant_id=? AND source=?',(g.tenant['id'],source)).fetchone()
                db().execute('INSERT INTO shipping_rules VALUES(?,?,?,?,?) ON CONFLICT(tenant_id,source) DO UPDATE SET purpose=excluded.purpose,method=excluded.method,label_owner=excluded.label_owner',(g.tenant['id'],source,purpose,method,owner))
                log('rule_saved',dict(source=source,purpose=purpose,method=method,label_owner=owner,previous=dict(old) if old else None,reason=why))
            else:abort(400)
        return redirect(url_for('shipping_admin'))

    @app.route('/tickets/<ticket_id>/shipping',methods=['GET','POST'])
    @roles_required('planner','admin','operator')
    def ticket_shipping(ticket_id):
        ticket=db().execute('SELECT * FROM tickets WHERE tenant_id=? AND id=?',(g.tenant['id'],ticket_id)).fetchone()
        if not ticket:abort(404)
        source=db().execute('SELECT source,source_reference,destination FROM intake_requests WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],ticket_id)).fetchone()
        source=dict(source) if source else dict(source='manual',source_reference='',destination='')
        rule=db().execute('SELECT * FROM shipping_rules WHERE tenant_id=? AND source=?',(g.tenant['id'],source['source'])).fetchone()
        plan=db().execute('SELECT * FROM shipping_plans WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],ticket_id)).fetchone()
        if request.method=='POST':
            with db():
                lock(('planner','admin'))
                if db().execute('SELECT 1 FROM shipping_boxes WHERE tenant_id=? AND ticket_id=? AND voided=0',(g.tenant['id'],ticket_id)).fetchone():abort(409,'Shipping plan is frozen while boxes exist.')
                dest=db().execute('SELECT id FROM shipping_destinations WHERE tenant_id=? AND id=? AND active=1',(g.tenant['id'],request.form.get('destination_id'))).fetchone()
                purpose,method,owner=[request.form.get(k) for k in ('purpose','method','label_owner')]
                if not dest or purpose not in {'stock','order'} or method not in {'fedex','ltl'} or owner not in {'planner','packing','either'}:abort(400,'Select destination and shipping options.')
                why=text('reason')
                db().execute('INSERT INTO shipping_plans VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(tenant_id,ticket_id) DO UPDATE SET destination_id=excluded.destination_id,purpose=excluded.purpose,method=excluded.method,label_owner=excluded.label_owner',(g.tenant['id'],ticket_id,dest[0],purpose,method,owner,source['source'],source['source_reference']))
                event(ticket_id,'shipping_plan_saved',json.dumps(dict(destination_id=dest[0],purpose=purpose,method=method,label_owner=owner,reason=why)))
            return redirect(url_for('ticket_shipping',ticket_id=ticket_id))
        packs=db().execute("SELECT f.* FROM fulfillment_active_records f WHERE f.tenant_id=? AND f.ticket_id=? AND f.kind='pack' AND NOT EXISTS(SELECT 1 FROM shipping_boxes b WHERE b.tenant_id=f.tenant_id AND b.pack_record_id=f.id AND b.voided=0)",(g.tenant['id'],ticket_id)).fetchall()
        return render_template('ticket_shipping.html',ticket=ticket,source=source,plan=plan,defaults=plan or rule,
            destinations=db().execute('SELECT * FROM shipping_destinations WHERE tenant_id=? AND active=1 ORDER BY name',(g.tenant['id'],)).fetchall(),packs=packs,
            boxes=db().execute('SELECT * FROM shipping_boxes WHERE tenant_id=? AND ticket_id=? AND voided=0 ORDER BY id',(g.tenant['id'],ticket_id)).fetchall())

    @app.post('/tickets/<ticket_id>/shipping/boxes')
    @roles_required('planner','admin','operator')
    def shipping_box(ticket_id):
        weight=text('weight_kg',20); dimensions=text('dimensions_cm',100)
        try:
            if not Decimal(weight).is_finite() or not 0<Decimal(weight)<=10000:raise ValueError()
        except (InvalidOperation,ValueError):abort(400,'Enter a positive weight in kg.')
        with db():
            lock()
            from .production_controls import require_active
            require_active(db(),g.tenant['id'],ticket_id)
            plan=db().execute('SELECT * FROM shipping_plans WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],ticket_id)).fetchone()
            if not plan:abort(409,'Planner must save the shipping plan first.')
            pack=db().execute("SELECT * FROM fulfillment_active_records WHERE tenant_id=? AND ticket_id=? AND id=? AND kind='pack'",(g.tenant['id'],ticket_id,request.form.get('pack_record_id'))).fetchone()
            if not pack:abort(404)
            if db().execute('SELECT 1 FROM shipping_boxes WHERE tenant_id=? AND pack_record_id=? AND voided=0',(g.tenant['id'],pack['id'])).fetchone():abort(409,'This packing record already has a box.')
            ticket=db().execute('SELECT * FROM tickets WHERE tenant_id=? AND id=?',(g.tenant['id'],ticket_id)).fetchone()
            state=progress(db(),g.tenant['id'],ticket)
            reserved=db().execute("SELECT COALESCE(SUM(f.quantity),0) FROM shipping_boxes b JOIN fulfillment_records f ON f.id=b.pack_record_id AND f.tenant_id=b.tenant_id LEFT JOIN shipping_loads s ON s.tenant_id=b.tenant_id AND s.id=b.load_id WHERE b.tenant_id=? AND b.ticket_id=? AND b.voided=0 AND (s.id IS NULL OR s.state!='departed')",(g.tenant['id'],ticket_id)).fetchone()[0]
            if pack['quantity']>state['waiting']-reserved:abort(409,'This quantity is already allocated or departed.')
            if db().execute("SELECT 1 FROM fulfillment_active_units p JOIN fulfillment_active_units d ON d.tenant_id=p.tenant_id AND d.unit_id=p.unit_id AND d.kind='depart' WHERE p.tenant_id=? AND p.record_id=?",(g.tenant['id'],pack['id'])).fetchone():abort(409,'A serial in this box has already departed.')
            result=db().execute('INSERT INTO shipping_boxes(tenant_id,ticket_id,pack_record_id,weight_kg,dimensions_cm,created_at) VALUES(?,?,?,?,?,?)',(g.tenant['id'],ticket_id,pack['id'],weight,dimensions,now()))
            event(ticket_id,'shipping_box_created',json.dumps(dict(box_id=result.lastrowid,pack_record_id=pack['id'],quantity=pack['quantity'],weight_kg=weight,dimensions_cm=dimensions)))
        return redirect(url_for('ticket_shipping',ticket_id=ticket_id))

    @app.post('/shipping/boxes/<int:bid>/void')
    @roles_required('planner','admin')
    def void_shipping_box(bid):
        why=text('reason')
        with db():
            lock(('planner','admin'))
            box=db().execute('SELECT * FROM shipping_boxes WHERE tenant_id=? AND id=? AND voided=0',(g.tenant['id'],bid)).fetchone()
            if not box:abort(404)
            if box['load_id'] is not None:abort(409,'Reopen the manifest and remove this box before correcting it.')
            db().execute('UPDATE shipping_boxes SET voided=1 WHERE tenant_id=? AND id=?',(g.tenant['id'],bid))
            event(box['ticket_id'],'shipping_box_voided',json.dumps(dict(box_id=bid,reason=why)))
        return redirect(url_for('ticket_shipping',ticket_id=box['ticket_id']))

    @app.context_processor
    def shipping_notifications():
        if not getattr(g,'tenant',None) or not set(getattr(g,'roles',[])) & {'planner','admin','operator'}:
            return {'shipping_waiting':0}
        counts=db().execute("SELECT state,COUNT(*) AS total FROM shipping_loads WHERE tenant_id=? AND state IN ('requested','ready') GROUP BY state",(g.tenant['id'],)).fetchall()
        return {'shipping_waiting':sum(r['total'] for r in counts)}

    @app.get('/shipping')
    @roles_required('planner','admin','operator')
    def shipping_queue():
        loads=db().execute("SELECT s.*, (SELECT COUNT(*) FROM shipping_boxes b WHERE b.tenant_id=s.tenant_id AND b.load_id=s.id AND b.voided=0) AS box_count FROM shipping_loads s WHERE s.tenant_id=? AND s.state!='departed' ORDER BY CASE s.state WHEN 'requested' THEN 0 WHEN 'ready' THEN 1 ELSE 2 END,s.id",(g.tenant['id'],)).fetchall()
        available=db().execute('SELECT b.*,t.pn,p.method,d.name AS destination FROM shipping_boxes b JOIN shipping_plans p ON p.tenant_id=b.tenant_id AND p.ticket_id=b.ticket_id JOIN shipping_destinations d ON d.tenant_id=p.tenant_id AND d.id=p.destination_id JOIN tickets t ON t.tenant_id=b.tenant_id AND t.id=b.ticket_id WHERE b.tenant_id=? AND b.load_id IS NULL AND b.voided=0 ORDER BY b.id',(g.tenant['id'],)).fetchall()
        return render_template('shipping_queue.html',loads=loads,available=available)

    @app.post('/shipping')
    @roles_required('planner','admin','operator')
    def create_load():
        with db():
            lock()
            box=db().execute('SELECT b.*,p.destination_id,p.method,p.label_owner,d.name,d.address,d.active FROM shipping_boxes b JOIN shipping_plans p ON p.tenant_id=b.tenant_id AND p.ticket_id=b.ticket_id JOIN shipping_destinations d ON d.tenant_id=p.tenant_id AND d.id=p.destination_id WHERE b.tenant_id=? AND b.id=? AND b.voided=0 AND b.load_id IS NULL',(g.tenant['id'],request.form.get('box_id'))).fetchone()
            if not box:abort(409,'Box unavailable.')
            if not box['active']:abort(409,'Destination inactive.')
            result=db().execute('INSERT INTO shipping_loads(tenant_id,destination_id,destination_name,destination_address,method,label_owner,created_at) VALUES(?,?,?,?,?,?,?)',(g.tenant['id'],box['destination_id'],box['name'],box['address'],box['method'],box['label_owner'],now()))
            lid=result.lastrowid
            db().execute('UPDATE shipping_boxes SET load_id=? WHERE tenant_id=? AND id=?',(lid,g.tenant['id'],box['id']))
            log('shipment_created',dict(box_id=box['id']),lid)
        return redirect(url_for('shipping_detail',lid=lid))

    @app.get('/shipping/<int:lid>')
    @roles_required('planner','admin','operator')
    def shipping_detail(lid):
        load=load_for(lid)
        available=db().execute('SELECT b.*,t.pn FROM shipping_boxes b JOIN shipping_plans p ON p.tenant_id=b.tenant_id AND p.ticket_id=b.ticket_id JOIN tickets t ON t.tenant_id=b.tenant_id AND t.id=b.ticket_id WHERE b.tenant_id=? AND b.load_id IS NULL AND b.voided=0 AND p.destination_id=? AND p.method=? AND p.label_owner=? ORDER BY b.id',(g.tenant['id'],load['destination_id'],load['method'],load['label_owner'])).fetchall()
        history=db().execute('SELECT e.*,u.username FROM shipping_events e JOIN users u ON u.id=e.actor_id WHERE e.tenant_id=? AND e.load_id=? ORDER BY e.id DESC',(g.tenant['id'],lid)).fetchall()
        can_upload=bool(set(g.roles)&({'planner','admin'} if load['label_owner']=='planner' else {'operator','admin'} if load['label_owner']=='packing' else {'planner','operator','admin'}))
        measurements=next((json.loads(h['detail']) for h in history if h['action']=='labels_requested'),{})
        return render_template('shipping_detail.html',load=load,boxes=boxes(lid),available=available,label=current_label(load),history=history,can_upload=can_upload,measurements=measurements)

    @app.post('/shipping/<int:lid>/action')
    @roles_required('planner','admin','operator')
    def shipping_action(lid):
        action=request.form.get('action')
        with db():
            load=locked_load(lid)
            if action in {'add','remove'}:
                if load['state']!='building':abort(409,'Reopen the manifest before changing boxes.')
                box=db().execute('SELECT b.*,p.destination_id,p.method,p.label_owner FROM shipping_boxes b JOIN shipping_plans p ON p.tenant_id=b.tenant_id AND p.ticket_id=b.ticket_id WHERE b.tenant_id=? AND b.id=? AND b.voided=0',(g.tenant['id'],request.form.get('box_id'))).fetchone()
                if not box:abort(404)
                if action=='add':
                    if box['load_id'] is not None or any(box[k]!=load[k] for k in ('destination_id','method','label_owner')):abort(409,'Boxes must share the destination, shipping method and label responsibility.')
                elif box['load_id']!=lid:abort(409,'Box is not in this shipment.')
                db().execute('UPDATE shipping_boxes SET load_id=? WHERE tenant_id=? AND id=?',(lid if action=='add' else None,g.tenant['id'],box['id']))
                log('box_'+action,dict(box_id=box['id']),lid)
            elif action=='request':
                if load['state']!='building' or not boxes(lid):abort(409,'Build a nonempty manifest first.')
                db().execute("UPDATE shipping_loads SET state='requested' WHERE tenant_id=? AND id=?",(g.tenant['id'],lid))
                measurements={}
                if load['method']=='ltl':
                    gross=text('gross_weight_kg',20); dimensions=text('pallet_dimensions_cm',100)
                    try:
                        weight=Decimal(gross)
                        if not weight.is_finite() or not 0<weight<=100000:raise ValueError()
                        if weight<sum(Decimal(b['weight_kg']) for b in boxes(lid)):raise ValueError()
                    except (InvalidOperation,ValueError):abort(400,'Pallet gross weight must include all boxes and pallet packaging.')
                    measurements=dict(gross_weight_kg=gross,pallet_dimensions_cm=dimensions)
                log('labels_requested',dict(boxes=[b['id'] for b in boxes(lid)],**measurements),lid)
            elif action=='reopen':
                if load['state'] not in {'requested','ready'}:abort(409,'Only a requested or labeled manifest can be reopened.')
                why=text('reason')
                db().execute("UPDATE shipping_loads SET state='building',manifest_version=manifest_version+1,carrier='',reference='' WHERE tenant_id=? AND id=?",(g.tenant['id'],lid))
                log('manifest_reopened',dict(reason=why,old_labels_invalid=True),lid)
            else:abort(400)
            db().execute('UPDATE shipping_loads SET version=version+1 WHERE tenant_id=? AND id=?',(g.tenant['id'],lid))
        return redirect(url_for('shipping_detail',lid=lid))

    @app.post('/shipping/<int:lid>/labels')
    @roles_required('planner','admin','operator')
    def upload_shipping_labels(lid):
        load=load_for(lid);label_permission(load)
        if load['state'] not in {'requested','ready'}:abort(409,'Request labels first.')
        upload=request.files.get('labels')
        data=upload.read(10*1024*1024+1) if upload else b''
        if not data.startswith(b'%PDF-') or len(data)>10*1024*1024:abort(400,'Upload a PDF label packet up to 10 MB.')
        filename=secure_filename(upload.filename or '')
        if not filename.lower().endswith('.pdf'):abort(400,'Choose a PDF file.')
        carrier,reference=text('carrier',100),text('reference',200)
        content,key,size=object_storage.prepare(g.tenant['id'],'pdf',data)
        with db():
            load=locked_load(lid);label_permission(load)
            if load['state'] not in {'requested','ready'}:abort(409,'Manifest changed. Request labels again.')
            result=db().execute('INSERT INTO shipping_labels(tenant_id,load_id,manifest_version,filename,content,object_key,object_size,sha256,actor_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(g.tenant['id'],lid,load['manifest_version'],filename,content,key,size,hashlib.sha256(data).hexdigest(),g.user['id'],now()))
            db().execute("UPDATE shipping_loads SET state='ready',carrier=?,reference=?,version=version+1 WHERE tenant_id=? AND id=?",(carrier,reference,g.tenant['id'],lid))
            log('labels_ready',dict(label_id=result.lastrowid,carrier=carrier,reference=reference,replaces_previous=True),lid)
        return redirect(url_for('shipping_detail',lid=lid))

    @app.get('/shipping/<int:lid>/labels/<int:label_id>')
    @roles_required('planner','admin','operator')
    def download_shipping_labels(lid,label_id):
        load=load_for(lid);label=current_label(load)
        if load['state'] not in {'ready','departed'} or not label or label['id']!=label_id:abort(409,'This label has been superseded or its manifest reopened. Open the current shipment.')
        data=object_storage.read(g.tenant['id'],'pdf',label,'content')
        with db():
            lock()
            fresh=load_for(lid); active=current_label(fresh)
            if fresh['state'] not in {'ready','departed'} or not active or active['id']!=label_id:abort(409,'Labels changed while downloading. Open the current shipment.')
            log('labels_downloaded',dict(label_id=label_id),lid)
        return send_file(io.BytesIO(data),mimetype='application/pdf',as_attachment=True,download_name=label['filename'])

    @app.get('/shipping/boxes/<int:bid>/print')
    @roles_required('planner','admin','operator')
    def print_shipping_box(bid):
        box=db().execute('SELECT b.*,t.pn,t.revision,f.quantity,f.serials,p.source,p.source_reference,p.purpose,d.name AS destination,d.address FROM shipping_boxes b JOIN tickets t ON t.tenant_id=b.tenant_id AND t.id=b.ticket_id JOIN fulfillment_records f ON f.tenant_id=b.tenant_id AND f.id=b.pack_record_id JOIN shipping_plans p ON p.tenant_id=b.tenant_id AND p.ticket_id=b.ticket_id JOIN shipping_destinations d ON d.tenant_id=p.tenant_id AND d.id=p.destination_id WHERE b.tenant_id=? AND b.id=? AND b.voided=0',(g.tenant['id'],bid)).fetchone()
        if not box:abort(404)
        return render_template('shipping_box_print.html',box=box)

    @app.post('/shipping/<int:lid>/depart')
    @roles_required('planner','admin','operator')
    def depart_shipping(lid):
        if request.form.get('confirm')!='yes':abort(400,'Confirm physical handoff or pickup.')
        notes=text('notes',1000)
        with db():
            load=locked_load(lid)
            if load['state']!='ready' or not current_label(load):abort(409,'Current shipping labels are required.')
            entries=boxes(lid)
            if not entries:abort(409,'Empty shipment.')
            groups={}
            for box in entries:groups.setdefault(box['ticket_id'],[]).append(box)
            for tid,group in groups.items():
                from .production_controls import require_active
                from .job_plan import evidence
                require_active(db(),g.tenant['id'],tid)
                ticket=db().execute('SELECT * FROM tickets WHERE tenant_id=? AND id=?',(g.tenant['id'],tid)).fetchone()
                state=progress(db(),g.tenant['id'],ticket)
                quantity=sum(b['quantity'] for b in group)
                if quantity>state['waiting']:abort(409,'Packing quantities changed. Review shipment.')
                if db().execute('SELECT 1 FROM fulfillment_checks c WHERE tenant_id=? AND ticket_id=? AND NOT EXISTS(SELECT 1 FROM fulfillment_check_results r WHERE r.tenant_id=c.tenant_id AND r.check_id=c.id)',(g.tenant['id'],tid)).fetchone():abort(409,'Complete all business-system checkpoints before dispatch.')
                evidence(db(),g.tenant['id'],tid,'shipping',event)
                units=[]
                for box in group:
                    if not db().execute("SELECT 1 FROM fulfillment_active_records WHERE tenant_id=? AND id=? AND kind='pack'",(g.tenant['id'],box['pack_record_id'])).fetchone():abort(409,'Packing record was reversed.')
                    units.extend(db().execute("SELECT s.id,s.serial,s.state FROM fulfillment_active_units f JOIN serial_units s ON s.tenant_id=f.tenant_id AND s.id=f.unit_id WHERE f.tenant_id=? AND f.record_id=? AND f.kind='pack'",(g.tenant['id'],box['pack_record_id'])).fetchall())
                if state['serial']:
                    if len(units)!=quantity or len({u['id'] for u in units})!=quantity:abort(409,'Serial manifest mismatch.')
                    for u in units:
                        if u['state']!='accepted' or db().execute("SELECT 1 FROM fulfillment_active_units WHERE tenant_id=? AND unit_id=? AND kind='depart'",(g.tenant['id'],u['id'])).fetchone():abort(409,'Serial is unaccepted or already departed.')
                elif ticket['state']!='accepted':abort(409,'Production must be accepted.')
                serials=[u['serial'] for u in units]
                rec=db().execute('INSERT INTO fulfillment_records(tenant_id,ticket_id,kind,quantity,destination,reference,method,notes,serials,actor_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(g.tenant['id'],tid,'depart',quantity,load['destination_name']+' — '+load['destination_address'],load['reference'],'external',notes,json.dumps(serials),g.user['id'],now()))
                db().execute('INSERT INTO shipping_departures(tenant_id,load_id,ticket_id,record_id) VALUES(?,?,?,?)',(g.tenant['id'],lid,tid,rec.lastrowid))
                detail=json.dumps(dict(shipment_id=lid,record_id=rec.lastrowid,quantity=quantity,serials=serials,reference=load['reference']))
                event(tid,'units_departed',detail)
                for u in units:
                    db().execute('INSERT INTO fulfillment_unit_entries VALUES(?,?,?,?,?)',(g.tenant['id'],tid,rec.lastrowid,u['id'],'depart'))
                    eid=event(tid,'unit_departed',detail)
                    db().execute('INSERT INTO unit_event_links VALUES(?,?,?,?)',(g.tenant['id'],tid,u['id'],eid))
                if state['departed']+quantity==ticket['quantity']:event(tid,'delivery_closed','All requested units physically departed via shipping manifest.')
                db().execute('UPDATE tickets SET version=version+1 WHERE tenant_id=? AND id=?',(g.tenant['id'],tid))
            db().execute("UPDATE shipping_loads SET state='departed',version=version+1 WHERE tenant_id=? AND id=?",(g.tenant['id'],lid))
            log('shipment_departed',dict(notes=notes,reference=load['reference'],box_ids=[b['id'] for b in entries]),lid)
        return redirect(url_for('shipping_detail',lid=lid))
