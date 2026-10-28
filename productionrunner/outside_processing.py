"""Planner-defined vendor operations with immutable custody and inspection evidence."""
import json
from datetime import date, datetime, timezone
from flask import abort, g, request, render_template, redirect, url_for
from .intake import actor
from .dispatch import released


def step(conn, tenant, operation):
    return conn.execute('SELECT * FROM outside_steps WHERE tenant_id=? AND operation_id=?', (tenant, operation)).fetchone()


def require_internal(conn, tenant, operation):
    if step(conn, tenant, operation):
        abort(409, 'Use outside processing send, receive and independent inspection for this operation.')


def ledger(conn, tenant, operation):
    moves = [dict(r) for r in conn.execute('SELECT m.*,u.username FROM outside_moves m JOIN users u ON u.id=m.actor_id WHERE m.tenant_id=? AND m.operation_id=? ORDER BY m.id', (tenant, operation))]
    for m in moves:
        m['units'] = json.loads(m['unit_ids'])
        m['inspection'] = conn.execute('SELECT i.*,u.username FROM outside_inspections i JOIN users u ON u.id=i.actor_id WHERE i.tenant_id=? AND i.receipt_id=? ORDER BY i.id DESC LIMIT 1', (tenant, m['id'])).fetchone()
        m['received'] = sum(r['quantity'] for r in moves if r['parent_id'] == m['id'])
        if m['kind'] == 'send':
            receipts = [r for r in moves if r['parent_id'] == m['id']]
            end = datetime.fromisoformat(receipts[-1]['created_at']) if m['received'] == m['quantity'] else datetime.now(timezone.utc)
            m['elapsed_days'] = round((end-datetime.fromisoformat(m['created_at'])).total_seconds()/86400, 1)
    return moves


def status(conn, tenant, operation, unit=None):
    plan = step(conn, tenant, operation)
    if not plan:
        return ''
    moves = ledger(conn, tenant, operation)
    def amount(m): return int(unit in m['units']) if unit else m['quantity']
    sent = sum(amount(m) for m in moves if m['kind']=='send')
    received = sum(amount(m) for m in moves if m['kind']=='receive')
    failed = sum(amount(m) for m in moves if m['kind']=='receive' and m['inspection'] and m['inspection']['result']=='fail')
    pending = sum(amount(m) for m in moves if m['kind']=='receive' and not m['inspection'])
    accepted = sum(amount(m) for m in moves if m['kind']=='receive' and m['inspection'] and m['inspection']['result']=='pass')
    parts = []
    if sent > received:
        overdue = ' · OVERDUE' if plan['due_date'] < datetime.now(timezone.utc).date().isoformat() else ''
        parts.append(f"At outside vendor: {sent-received} · {plan['vendor']} · due {plan['due_date']}{overdue}")
    if failed: parts.append(f'Outside inspection HOLD: {failed}')
    if accepted: parts.append(f'Outside processing accepted: {accepted}')
    if pending: parts.append(f'Outside inspection pending: {pending}')
    return ' / '.join(parts) or f"Outside processing · {plan['vendor']} · {plan['service']}"


def register_outside(app, db, roles_required, now, lock_ticket, event):
    def ticket(tid):
        row = db().execute('SELECT * FROM tickets WHERE tenant_id=? AND id=?',(g.tenant['id'],tid)).fetchone()
        if not row: abort(404)
        return row

    def operations(tid):
        return db().execute('SELECT * FROM operations WHERE tenant_id=? AND ticket_id=? ORDER BY sequence',(g.tenant['id'],tid)).fetchall()

    def text(name, maximum=1000):
        value = request.form.get(name,'').strip()
        if not value or len(value)>maximum: abort(400, f'Provide {name.replace("_"," ")} (up to {maximum} characters).')
        return value

    def log(tid, action, detail, units=()):
        for uid in units or [None]:
            event(tid, action, json.dumps(dict(detail, unit_id=uid)))
            if uid:
                eid = db().execute('SELECT last_insert_rowid()').fetchone()[0]
                db().execute('INSERT INTO unit_event_links VALUES(?,?,?,?)',(g.tenant['id'],tid,uid,eid))

    @app.context_processor
    def context():
        return dict(outside_step=lambda op:step(db(),g.tenant['id'],op['id']), outside_status=lambda op,uid=None:status(db(),g.tenant['id'],op['id'],uid), outside_plans=lambda t:db().execute('SELECT * FROM outside_steps WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],t['id'])).fetchall())

    @app.get('/tickets/<ticket_id>/outside')
    @roles_required()
    def outside_detail(ticket_id):
        t=ticket(ticket_id)
        return render_template('outside.html',ticket=t,operations=operations(ticket_id),ledger=lambda op:ledger(db(),g.tenant['id'],op['id']),units=db().execute('SELECT * FROM serial_units WHERE tenant_id=? AND ticket_id=? ORDER BY serial',(g.tenant['id'],ticket_id)).fetchall())

    @app.post('/tickets/<ticket_id>/outside/<int:operation_id>/plan')
    @roles_required('planner','admin')
    def outside_plan(ticket_id,operation_id):
        with db():
            t=lock_ticket(ticket_id);actor(db(),g.tenant['id'],g.user['id'])
            if released(db(),g.tenant['id'],ticket_id) or t['state']!='ready': abort(409,'Configure outside processing before releasing the ticket.')
            op=next((o for o in operations(ticket_id) if o['id']==operation_id),None)
            if not op:abort(404)
            if request.form.get('mode')=='internal':
                db().execute('DELETE FROM outside_steps WHERE tenant_id=? AND operation_id=?',(g.tenant['id'],operation_id))
                db().execute('UPDATE tickets SET version=version+1 WHERE tenant_id=? AND id=?',(g.tenant['id'],ticket_id))
                log(ticket_id,'outside_plan_removed',dict(operation_id=operation_id))
                return redirect(url_for('outside_detail',ticket_id=ticket_id))
            vendor=text('vendor',200);service=text('service',200);instructions=text('instructions',4000);criteria=text('inspection_requirements',4000)
            due=request.form.get('due_date','')
            try: due=date.fromisoformat(due).isoformat()
            except ValueError:abort(400,'Choose a valid expected return date.')
            reference=request.form.get('reference','').strip()
            if len(reference)>200:abort(400,'Reference is too long.')
            db().execute('INSERT INTO outside_steps VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(operation_id) DO UPDATE SET vendor=excluded.vendor,service=excluded.service,instructions=excluded.instructions,inspection_requirements=excluded.inspection_requirements,due_date=excluded.due_date,reference=excluded.reference',(g.tenant['id'],ticket_id,operation_id,vendor,service,instructions,criteria,due,reference))
            db().execute('DELETE FROM operation_assignments WHERE tenant_id=? AND operation_id=?',(g.tenant['id'],operation_id))
            db().execute('UPDATE tickets SET version=version+1 WHERE tenant_id=? AND id=?',(g.tenant['id'],ticket_id))
            log(ticket_id,'outside_plan_saved',dict(operation_id=operation_id,vendor=vendor,service=service,instructions=instructions,inspection_requirements=criteria,due_date=due,reference=reference))
        return redirect(url_for('outside_detail',ticket_id=ticket_id))

    @app.post('/tickets/<ticket_id>/outside/<int:operation_id>/<action>')
    @roles_required('planner','admin','operator','inspector')
    def outside_action(ticket_id,operation_id,action):
        with db():
            t=lock_ticket(ticket_id)
            actor(db(),g.tenant['id'],g.user['id'],('inspector',) if action=='inspect' else ('planner','admin','operator'))
            plan=step(db(),g.tenant['id'],operation_id)
            if not plan or plan['ticket_id']!=ticket_id:abort(404)
            if not released(db(),g.tenant['id'],ticket_id):abort(409,'Planner must release the ticket first.')
            op=next(o for o in operations(ticket_id) if o['id']==operation_id)
            moves=ledger(db(),g.tenant['id'],operation_id)
            units=db().execute('SELECT * FROM serial_units WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],ticket_id)).fetchall()
            notes=text('notes',4000);stamp=now();ids=[]
            if action in {'send','receive'}:
                parent=None
                if action=='receive':
                    parent=next((m for m in moves if m['kind']=='send' and str(m['id'])==request.form.get('shipment_id')),None)
                    if not parent:abort(400,'Choose a shipment from this operation.')
                if units:
                    serials=request.form.get('serials','').splitlines()
                    serials=[s.strip() for s in serials if s.strip()]
                    selected=[u for u in units if u['serial'] in serials]
                    if not serials or len(set(serials))!=len(serials) or len(selected)!=len(serials):abort(400,'Enter exact unique serial numbers belonging to this ticket.')
                    ids=[u['id'] for u in selected];quantity=len(ids)
                else:
                    try:quantity=int(request.form.get('quantity',''))
                    except ValueError:abort(400,'Enter a whole quantity.')
                    if not 1<=quantity<=t['quantity']:abort(400,'Quantity is out of range.')
                if action=='send':
                    if units:
                        sent={uid for m in moves if m['kind']=='send' for uid in m['units']}
                        if sent.intersection(ids):abort(409,'A selected serial has already been sent for this operation.')
                        for u in selected:
                            prior=db().execute('SELECT 1 FROM operations o WHERE o.tenant_id=? AND o.ticket_id=? AND o.sequence<? AND NOT EXISTS(SELECT 1 FROM unit_completions c WHERE c.tenant_id=o.tenant_id AND c.operation_id=o.id AND c.unit_id=?)',(g.tenant['id'],ticket_id,op['sequence'],u['id'])).fetchone()
                            if prior or u['state'] not in {'ready','in_progress'}:abort(409,'Complete prior operations for every selected unit first.')
                    elif t['state'] not in {'ready','in_progress'} or any(o['sequence']<op['sequence'] and not o['completed_by'] for o in operations(ticket_id)):
                        abort(409,'Complete prior operations before sending this batch.')
                    if sum(m['quantity'] for m in moves if m['kind']=='send')+quantity>t['quantity']:abort(409,'Cannot send more than the ticket quantity.')
                else:
                    receipts=[m for m in moves if m['parent_id']==parent['id']]
                    received={uid for m in receipts for uid in m['units']}
                    if units and (not set(ids)<=set(parent['units']) or received.intersection(ids)):abort(409,'Receive only outstanding serials from this shipment.')
                    if sum(m['quantity'] for m in receipts)+quantity>parent['quantity']:abort(409,'Receipt exceeds the outstanding shipment quantity.')
                row=db().execute('INSERT INTO outside_moves(tenant_id,ticket_id,operation_id,kind,parent_id,quantity,unit_ids,actor_id,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(g.tenant['id'],ticket_id,operation_id,action,parent['id'] if parent else None,quantity,json.dumps(ids),g.user['id'],notes,stamp))
                log(ticket_id,'outside_'+action,dict(operation_id=operation_id,record_id=row.lastrowid,quantity=quantity,serials=serials if units else [],notes=notes),ids)
                if action=='send':
                    if units:
                        from .serials import update_unit_state
                        for u in selected:update_unit_state(db(),u,'in_progress')
                    else:db().execute("UPDATE tickets SET state='in_progress' WHERE tenant_id=? AND id=?",(g.tenant['id'],ticket_id))
            elif action=='inspect':
                receipt=next((m for m in moves if m['kind']=='receive' and str(m['id'])==request.form.get('receipt_id')),None)
                if not receipt:abort(400,'Choose a receipt from this operation.')
                if receipt['inspection'] and receipt['inspection']['result']=='pass':abort(409,'This receipt was already accepted.')
                result=request.form.get('result')
                if result not in {'pass','fail'}:abort(400,'Choose pass or fail.')
                shipment=next(m for m in moves if m['id']==receipt['parent_id'])
                user=g.user['id'];ids=receipt['units']
                if user in {receipt['actor_id'],shipment['actor_id']}:abort(403,'Receiving inspection must be independent of sending and receiving.')
                from .work_tracking import contributed
                for uid in ids or [None]:
                    if contributed(db(),g.tenant['id'],ticket_id,uid,user):abort(403,'Production contributors cannot inspect this receipt.')
                    query='SELECT 1 FROM unit_completions WHERE tenant_id=? AND unit_id=? AND actor_id=?' if uid else 'SELECT 1 FROM operations WHERE tenant_id=? AND ticket_id=? AND completed_by=?'
                    if db().execute(query,(g.tenant['id'],uid or ticket_id,user)).fetchone():abort(403,'Production contributors cannot inspect this receipt.')
                row=db().execute('INSERT INTO outside_inspections(tenant_id,ticket_id,receipt_id,result,actor_id,notes,created_at) VALUES(?,?,?,?,?,?,?)',(g.tenant['id'],ticket_id,receipt['id'],result,user,notes,stamp))
                log(ticket_id,'outside_inspection_'+result,dict(operation_id=operation_id,receipt_id=receipt['id'],inspection_id=row.lastrowid,notes=notes),ids)
                if result=='pass':
                    from .serials import update_unit_state
                    if units:
                        for uid in ids:
                            db().execute('INSERT INTO unit_completions VALUES(?,?,?,?,?,?)',(g.tenant['id'],ticket_id,uid,operation_id,shipment['actor_id'],stamp))
                            u=next(u for u in units if u['id']==uid)
                            remaining=db().execute('SELECT 1 FROM operations o WHERE o.tenant_id=? AND o.ticket_id=? AND NOT EXISTS(SELECT 1 FROM unit_completions c WHERE c.tenant_id=o.tenant_id AND c.operation_id=o.id AND c.unit_id=?)',(g.tenant['id'],ticket_id,uid)).fetchone()
                            update_unit_state(db(),u,'in_progress' if remaining else 'awaiting_inspection')
                    else:
                        accepted=sum(m['quantity'] for m in ledger(db(),g.tenant['id'],operation_id) if m['kind']=='receive' and m['inspection'] and m['inspection']['result']=='pass')
                        if accepted==t['quantity']:
                            db().execute('UPDATE operations SET completed_by=?,completed_at=? WHERE tenant_id=? AND id=?',(shipment['actor_id'],stamp,g.tenant['id'],operation_id))
                            remaining=db().execute('SELECT 1 FROM operations WHERE tenant_id=? AND ticket_id=? AND completed_by IS NULL',(g.tenant['id'],ticket_id)).fetchone()
                            db().execute('UPDATE tickets SET state=? WHERE tenant_id=? AND id=?',('in_progress' if remaining else 'awaiting_inspection',g.tenant['id'],ticket_id))
            else:abort(400,'Unknown outside processing action.')
            db().execute('UPDATE tickets SET version=version+1 WHERE tenant_id=? AND id=?',(g.tenant['id'],ticket_id))
        return redirect(url_for('outside_detail',ticket_id=ticket_id))
