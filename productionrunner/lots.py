"""Quantity travelers sharing one order, route and independent-inspection controls."""
import json
import secrets
from flask import abort,g,request,redirect,url_for,render_template
from .intake import actor


def info(conn,tenant,uid):
    return conn.execute('SELECT * FROM production_lots WHERE tenant_id=? AND unit_id=?',(tenant,uid)).fetchone()


def quantity(conn,tenant,uid):
    lot=info(conn,tenant,uid)
    return lot['quantity'] if lot else 1


def active(conn,tenant,uid):
    lot=info(conn,tenant,uid)
    if lot and lot['scrapped']:abort(409,'This lot was scrapped. Use its replacement traveler.')
    return lot


def enabled(conn,tenant,tid):
    return conn.execute('SELECT 1 FROM lot_jobs WHERE tenant_id=? AND ticket_id=?',(tenant,tid)).fetchone() is not None


def ancestors(conn,tenant,uid):
    result=[]
    while uid:
        row=info(conn,tenant,uid)
        uid=row['parent_id'] if row else None
        if uid:result.append(uid)
    return result


def create(conn,tenant,tid,amount,parent=None):
    count=conn.execute('SELECT COUNT(*) FROM production_lots WHERE tenant_id=? AND ticket_id=?',(tenant,tid)).fetchone()[0]
    uid='JR-U-'+secrets.token_hex(6).upper()
    conn.execute('INSERT INTO serial_units(id,tenant_id,ticket_id,serial) VALUES(?,?,?,?)',(uid,tenant,tid,f'LOT-{count+1:03d}'))
    conn.execute('INSERT INTO production_lots(tenant_id,ticket_id,unit_id,quantity,parent_id) VALUES(?,?,?,?,?)',(tenant,tid,uid,amount,parent))
    return uid


def initialize(conn,tenant,tid,total,size):
    if not 1<=size<=total:abort(400,'Choose a lot size between 1 and the order quantity.')
    if (total+size-1)//size>200:abort(400,'Use a lot size that creates at most 200 travelers.')
    conn.execute('INSERT INTO lot_jobs VALUES(?,?)',(tenant,tid))
    while total:
        amount=min(size,total);create(conn,tenant,tid,amount);total-=amount


def pending_checks(conn,tenant,tid,ids,kind):
    checks=conn.execute('SELECT id,stage FROM fulfillment_checks c WHERE tenant_id=? AND ticket_id=? AND NOT EXISTS(SELECT 1 FROM fulfillment_check_results r WHERE r.tenant_id=c.tenant_id AND r.check_id=c.id)',(tenant,tid)).fetchall()
    checks=[c for c in checks if c['stage']=='pack' or kind=='depart']
    if not enabled(conn,tenant,tid):return bool(checks)
    return any(not ids or any(not conn.execute('SELECT 1 FROM lot_check_results WHERE tenant_id=? AND check_id=? AND unit_id=?',(tenant,c['id'],uid)).fetchone() for uid in ids) for c in checks)


def summary(conn,tenant,ticket):
    from .fulfillment import progress
    units=conn.execute('SELECT s.*,l.quantity,l.scrapped FROM serial_units s JOIN production_lots l ON l.tenant_id=s.tenant_id AND l.unit_id=s.id WHERE s.tenant_id=? AND s.ticket_id=? ORDER BY s.serial',(tenant,ticket['id'])).fetchall()
    stages=conn.execute('SELECT o.*,s.name AS station_name FROM operations o LEFT JOIN operation_assignments a ON a.tenant_id=o.tenant_id AND a.operation_id=o.id LEFT JOIN stations s ON s.tenant_id=a.tenant_id AND s.id=a.station_id WHERE o.tenant_id=? AND o.ticket_id=? ORDER BY o.sequence',(tenant,ticket['id'])).fetchall()
    completed={}
    for r in conn.execute('SELECT unit_id,operation_id FROM unit_completions WHERE tenant_id=? AND ticket_id=?',(tenant,ticket['id'])):completed.setdefault(r['unit_id'],set()).add(r['operation_id'])
    movements={}
    for r in conn.execute('SELECT unit_id,kind FROM fulfillment_active_units WHERE tenant_id=? AND ticket_id=?',(tenant,ticket['id'])):
        movements.setdefault(r['unit_id'],set()).add(r['kind'])
    units=[dict(u,next_step=next((op['station_name'] or op['name'] for op in stages if op['id'] not in completed.get(u['id'],set())),'Independent inspection' if u['state']=='awaiting_inspection' else 'Packing / shipping'),delivery_state='Departed' if 'depart' in movements.get(u['id'],set()) else 'Packed' if 'pack' in movements.get(u['id'],set()) else '') for u in units]
    stages=[dict(op,good=sum(u['quantity'] for u in units if not u['scrapped'] and op['id'] in completed.get(u['id'],set()))) for op in stages]
    req=conn.execute('SELECT required_date FROM intake_requests WHERE tenant_id=? AND ticket_id=?',(tenant,ticket['id'])).fetchone()
    return dict(units=units,stages=stages,delivery=progress(conn,tenant,ticket),due=req['required_date'] if req else '',
                held=sum(u['quantity'] for u in units if u['state']=='held' and not u['scrapped']),scrapped=sum(u['quantity'] for u in units if u['scrapped']))


def register(app,db,roles_required,now,lock_ticket,event):
    @app.context_processor
    def context():
        return dict(lot_info=lambda uid:info(db(),g.tenant['id'],uid),lot_quantity=lambda uid:quantity(db(),g.tenant['id'],uid),is_lot_job=lambda tid:enabled(db(),g.tenant['id'],tid),lot_check_done=lambda cid,uid:db().execute('SELECT 1 FROM lot_check_results WHERE tenant_id=? AND check_id=? AND unit_id=?',(g.tenant['id'],cid,uid)).fetchone())

    @app.get('/tickets/<ticket_id>/lots')
    @roles_required()
    def lot_dashboard(ticket_id):
        ticket=db().execute('SELECT t.*,p.priority AS hot_priority FROM tickets t LEFT JOIN ticket_priorities p ON p.tenant_id=t.tenant_id AND p.ticket_id=t.id WHERE t.tenant_id=? AND t.id=?',(g.tenant['id'],ticket_id)).fetchone()
        if not ticket:abort(404)
        return render_template('lots.html',ticket=ticket,lot_enabled=enabled(db(),g.tenant['id'],ticket_id),**summary(db(),g.tenant['id'],ticket))

    @app.post('/tickets/<ticket_id>/lots')
    @roles_required('planner','admin')
    def enable_lots(ticket_id):
        with db():
            ticket=lock_ticket(ticket_id);actor(db(),g.tenant['id'],g.user['id'])
            if ticket['tracking']!='batch' or ticket['state']!='ready' or db().execute('SELECT 1 FROM serial_units WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],ticket_id)).fetchone():
                abort(409,'Only an unstarted batch can switch to production lots.')
            for table in ('work_sessions','outside_moves','fulfillment_records','rework_cases'):
                if db().execute(f'SELECT 1 FROM {table} WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],ticket_id)).fetchone():abort(409,'This job has recorded work. Its history cannot be converted automatically.')
            if db().execute('SELECT 1 FROM operations WHERE tenant_id=? AND ticket_id=? AND completed_by IS NOT NULL',(g.tenant['id'],ticket_id)).fetchone():abort(409,'Production already started.')
            try:size=int(request.form.get('lot_size',''))
            except ValueError:abort(400,'Enter a whole lot size.')
            initialize(db(),g.tenant['id'],ticket_id,ticket['quantity'],size)
            db().execute('UPDATE tickets SET version=version+1 WHERE tenant_id=? AND id=?',(g.tenant['id'],ticket_id))
            event(ticket_id,'production_lots_enabled',json.dumps(dict(quantity=ticket['quantity'],lot_size=size)))
        return redirect(url_for('lot_dashboard',ticket_id=ticket_id))

    @app.post('/lots/<unit_id>/action')
    @roles_required('operator','planner','admin')
    def lot_action(unit_id):
        with db():
            db().execute('BEGIN IMMEDIATE');actor(db(),g.tenant['id'],g.user['id'],('operator','planner','admin'))
            unit=db().execute('SELECT * FROM serial_units WHERE tenant_id=? AND id=?',(g.tenant['id'],unit_id)).fetchone()
            if not unit:abort(404)
            lot=active(db(),g.tenant['id'],unit_id)
            if not lot:abort(404)
            from .production_controls import require_active
            require_active(db(),g.tenant['id'],unit['ticket_id'])
            if request.form.get('version')!=str(unit['version']):abort(409,'Lot changed. Reload before recording.')
            why=request.form.get('reason','').strip()
            if not 1<=len(why)<=1000:abort(400,'Record the reason for this lot change.')
            from .work_tracking import open_sessions
            if any(s['unit_id']==unit_id for s in open_sessions(db(),g.tenant['id'])):abort(409,'Stop the lot’s work session before splitting, holding or scrapping it.')
            if db().execute('SELECT 1 FROM fulfillment_unit_entries WHERE tenant_id=? AND unit_id=?',(g.tenant['id'],unit_id)).fetchone():abort(409,'Packed or dispatched lots cannot be changed here.')
            if db().execute('SELECT 1 FROM rework_cases WHERE tenant_id=? AND unit_id=?',(g.tenant['id'],unit_id)).fetchone():abort(409,'This lot has a rework case; complete its controlled disposition first.')
            action=request.form.get('action')
            detail=dict(lot=unit['serial'],quantity=lot['quantity'],reason=why)
            if action in ('split','scrap'):
                if any(unit_id in json.loads(r['unit_ids']) for r in db().execute('SELECT unit_ids FROM outside_moves WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],unit['ticket_id']))):abort(409,'A lot with vendor custody history cannot be split or scrapped here. Keep its receiving records intact.')
                if action=='split' and db().execute('SELECT 1 FROM unit_inspections WHERE tenant_id=? AND unit_id=?',(g.tenant['id'],unit_id)).fetchone():abort(409,'Inspected lots retain their identity. Use the controlled rework process.')
            if action=='split':
                try:amount=int(request.form.get('quantity',''))
                except ValueError:abort(400,'Enter a whole split quantity.')
                if not 1<=amount<lot['quantity']:abort(400,'Split quantity must be smaller than this lot.')
                child=create(db(),g.tenant['id'],unit['ticket_id'],amount,unit_id)
                db().execute('UPDATE production_lots SET quantity=quantity-? WHERE tenant_id=? AND unit_id=?',(amount,g.tenant['id'],unit_id))
                db().execute('UPDATE serial_units SET state=? WHERE tenant_id=? AND id=?',(unit['state'],g.tenant['id'],child))
                for c in db().execute('SELECT * FROM unit_completions WHERE tenant_id=? AND unit_id=?',(g.tenant['id'],unit_id)).fetchall():
                    db().execute('INSERT INTO unit_completions VALUES(?,?,?,?,?,?)',(g.tenant['id'],unit['ticket_id'],child,c['operation_id'],c['actor_id'],c['completed_at']))
                detail.update(child=child,split_quantity=amount,remaining=lot['quantity']-amount)
            elif action=='hold':
                if unit['state'] in ('accepted','held'):abort(409,'Only active production can be placed on this hold.')
                db().execute("UPDATE serial_units SET state='held' WHERE tenant_id=? AND id=?",(g.tenant['id'],unit_id))
            elif action=='scrap':
                actor(db(),g.tenant['id'],g.user['id'],('admin',))
                if unit['state']!='held':abort(409,'Segregate and hold the lot before authorizing scrap.')
                db().execute('UPDATE production_lots SET scrapped=1 WHERE tenant_id=? AND unit_id=?',(g.tenant['id'],unit_id))
                detail['replacement']=create(db(),g.tenant['id'],unit['ticket_id'],lot['quantity'])
            else:abort(400,'Choose split, hold or authorized scrap.')
            eid=event(unit['ticket_id'],'lot_'+action,json.dumps(detail))
            db().execute('INSERT INTO unit_event_links VALUES(?,?,?,?)',(g.tenant['id'],unit['ticket_id'],unit_id,eid))
            if action=='split':
                child_event=event(unit['ticket_id'],'lot_split_inherited',json.dumps(dict(parent=unit_id,child=child,quantity=amount,reason=why)))
                db().execute('INSERT INTO unit_event_links VALUES(?,?,?,?)',(g.tenant['id'],unit['ticket_id'],child,child_event))
            from .serials import update_unit_state
            state=db().execute('SELECT state FROM serial_units WHERE id=? AND tenant_id=?',(unit_id,g.tenant['id'])).fetchone()[0]
            update_unit_state(db(),unit,state)
        return redirect(url_for('lot_dashboard',ticket_id=unit['ticket_id']))
