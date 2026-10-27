"""Physical packing/departure ledger, separate from manufacturing acceptance."""
import json
from flask import abort, g, redirect, render_template, request, session, url_for
from .admin import epoch


def progress(conn, tenant, ticket):
    totals = {r['kind']: r['qty'] for r in conn.execute('SELECT kind,SUM(quantity) AS qty FROM fulfillment_records WHERE tenant_id=? AND ticket_id=? GROUP BY kind', (tenant, ticket['id']))}
    packed, departed = totals.get('pack', 0), totals.get('depart', 0)
    units = conn.execute('SELECT COUNT(*) AS total,SUM(state=\'accepted\') AS accepted FROM serial_units WHERE tenant_id=? AND ticket_id=?', (tenant, ticket['id'])).fetchone()
    accepted = (units['accepted'] or 0) if units['total'] else (ticket['quantity'] if ticket['state'] == 'accepted' else 0)
    pending = conn.execute('SELECT COUNT(*) FROM fulfillment_checks c WHERE tenant_id=? AND ticket_id=? AND NOT EXISTS(SELECT 1 FROM fulfillment_check_results r WHERE r.tenant_id=c.tenant_id AND r.check_id=c.id)', (tenant, ticket['id'])).fetchone()[0]
    closed = departed == ticket['quantity']
    status = 'Departed / closed' if closed else ('Partially departed' if departed else ('Ready for dispatch' if packed == ticket['quantity'] and not pending else ('Packed / checkpoints pending' if packed == ticket['quantity'] else ('Packing' if packed else ('Awaiting packing' if accepted else 'Production pending')))))
    return dict(packed=packed, departed=departed, remaining=ticket['quantity']-departed, accepted=accepted, available=accepted-packed, waiting=packed-departed, serial=bool(units['total']), pending=pending, closed=closed, status=status)


def register_fulfillment(app, db, roles_required, now, lock_ticket, event):
    @app.context_processor
    def context():
        return {'fulfillment_for': lambda ticket: progress(db(), g.tenant['id'], ticket)}

    def actor(allowed):
        row = db().execute('SELECT roles FROM memberships WHERE tenant_id=? AND user_id=?', (g.tenant['id'], g.user['id'])).fetchone()
        if not row or not set(row['roles'].split(',')) & set(allowed) or epoch(db(), g.tenant['id'], g.user['id']) != session.get('membership_epoch', 0):
            abort(403, 'Your access changed. Sign in again.')

    def text(name, limit=500, required=True):
        value = request.form.get(name, '').strip()
        if (required and not value) or len(value) > limit:
            abort(400, f'Provide {name} (up to {limit} characters).')
        return value

    def touch(ticket_id):
        db().execute('UPDATE tickets SET version=version+1 WHERE tenant_id=? AND id=?', (g.tenant['id'], ticket_id))

    @app.get('/fulfillment')
    @roles_required()
    def fulfillment_queue():
        tickets = db().execute('SELECT t.*,p.priority AS hot_priority FROM tickets t LEFT JOIN ticket_priorities p ON p.tenant_id=t.tenant_id AND p.ticket_id=t.id WHERE t.tenant_id=? AND NOT EXISTS(SELECT 1 FROM ticket_cancellations c WHERE c.tenant_id=t.tenant_id AND c.ticket_id=t.id) ORDER BY p.priority IS NULL,p.priority,p.hot_since,t.created_at,t.id', (g.tenant['id'],)).fetchall()
        items = [dict(ticket=t, delivery=progress(db(), g.tenant['id'], t)) for t in tickets]
        view = request.args.get('view', 'open')
        if view not in {'open', 'closed'}:
            abort(400)
        items = [i for i in items if i['delivery']['closed'] == (view == 'closed')]
        return render_template('fulfillment_queue.html', items=items, view=view)

    @app.get('/tickets/<ticket_id>/delivery')
    @roles_required()
    def delivery_detail(ticket_id):
        ticket = db().execute('SELECT * FROM tickets WHERE tenant_id=? AND id=?', (g.tenant['id'], ticket_id)).fetchone()
        if not ticket:
            abort(404)
        delivery = progress(db(), g.tenant['id'], ticket)
        checks = db().execute('SELECT c.*,r.reference,r.created_at AS completed_at,u.username FROM fulfillment_checks c LEFT JOIN fulfillment_check_results r ON r.check_id=c.id AND r.tenant_id=c.tenant_id LEFT JOIN users u ON u.id=r.actor_id WHERE c.tenant_id=? AND c.ticket_id=? ORDER BY c.id', (g.tenant['id'], ticket_id)).fetchall()
        history = db().execute('SELECT f.*,u.username FROM fulfillment_records f JOIN users u ON u.id=f.actor_id WHERE f.tenant_id=? AND f.ticket_id=? ORDER BY f.id', (g.tenant['id'], ticket_id)).fetchall()
        units = db().execute("SELECT s.*,EXISTS(SELECT 1 FROM fulfillment_units f WHERE f.tenant_id=s.tenant_id AND f.unit_id=s.id AND f.kind='pack') AS packed,EXISTS(SELECT 1 FROM fulfillment_units f WHERE f.tenant_id=s.tenant_id AND f.unit_id=s.id AND f.kind='depart') AS departed FROM serial_units s WHERE s.tenant_id=? AND s.ticket_id=? ORDER BY s.serial", (g.tenant['id'], ticket_id)).fetchall()
        return render_template('delivery.html', ticket=ticket, delivery=delivery, checks=checks, history=history, units=units)

    @app.post('/tickets/<ticket_id>/delivery/checks')
    @roles_required('planner', 'admin')
    def delivery_check(ticket_id):
        with db():
            ticket = lock_ticket(ticket_id)
            actor(('planner', 'admin'))
            state = progress(db(), g.tenant['id'], ticket)
            action = request.form.get('action')
            if state['closed']:
                abort(409, 'Closed tickets cannot change checkpoints.')
            if action == 'add':
                name = text('name', 120)
                stage = request.form.get('stage')
                if stage not in {'pack', 'depart'}:
                    abort(400, 'Choose a checkpoint stage.')
                if (stage == 'pack' and state['packed']) or (stage == 'depart' and state['departed']):
                    abort(409, 'That stage has already started. Requirements cannot be added retroactively.')
                if db().execute('SELECT COUNT(*) FROM fulfillment_checks WHERE tenant_id=? AND ticket_id=?', (g.tenant['id'], ticket_id)).fetchone()[0] >= 20:
                    abort(400, 'At most 20 checkpoints per ticket.')
                db().execute('INSERT INTO fulfillment_checks(tenant_id,ticket_id,name,stage,actor_id,created_at) VALUES(?,?,?,?,?,?)', (g.tenant['id'], ticket_id, name, stage, g.user['id'], now()))
                event(ticket_id, 'delivery_checkpoint_added', json.dumps(dict(name=name, stage=stage)))
            elif action == 'complete':
                check = db().execute('SELECT * FROM fulfillment_checks WHERE tenant_id=? AND ticket_id=? AND id=?', (g.tenant['id'], ticket_id, request.form.get('check_id'))).fetchone()
                if not check:
                    abort(404)
                if db().execute('SELECT 1 FROM fulfillment_check_results WHERE check_id=?', (check['id'],)).fetchone():
                    abort(409, 'Checkpoint already recorded.')
                if ticket['state'] != 'accepted':
                    abort(409, 'Whole-job checkpoints require production acceptance of the entire ticket.')
                reference = text('reference')
                db().execute('INSERT INTO fulfillment_check_results VALUES(?,?,?,?,?)', (g.tenant['id'], check['id'], g.user['id'], reference, now()))
                event(ticket_id, 'delivery_checkpoint_completed', json.dumps(dict(check_id=check['id'], name=check['name'], reference=reference)))
            else:
                abort(400)
            touch(ticket_id)
        return redirect(url_for('delivery_detail', ticket_id=ticket_id))

    @app.post('/tickets/<ticket_id>/delivery/<kind>')
    @roles_required('operator', 'planner', 'admin')
    def record_delivery(ticket_id, kind):
        if kind not in {'pack', 'depart'}:
            abort(404)
        if request.form.get('confirm') != 'yes':
            abort(400, 'Confirm the physical action before recording it.')
        notes = text('notes', 1000)
        destination = text('destination') if kind == 'depart' else ''
        reference = text('reference') if kind == 'depart' else ''
        method = request.form.get('method', '') if kind == 'depart' else ''
        if kind == 'depart' and method not in {'external', 'internal', 'pickup'}:
            abort(400, 'Choose external shipment, internal transfer or pickup.')
        with db():
            ticket = lock_ticket(ticket_id)
            actor(('operator', 'planner', 'admin'))
            state = progress(db(), g.tenant['id'], ticket)
            if state['closed']:
                abort(409, 'All units have already departed.')
            pending = db().execute('SELECT stage FROM fulfillment_checks c WHERE tenant_id=? AND ticket_id=? AND NOT EXISTS(SELECT 1 FROM fulfillment_check_results r WHERE r.tenant_id=c.tenant_id AND r.check_id=c.id)', (g.tenant['id'], ticket_id)).fetchall()
            if any(c['stage'] == 'pack' or kind == 'depart' for c in pending):
                abort(409, 'Complete the required checkpoints for this stage first.')
            selected = request.form.getlist('unit_id')
            serial_names = []
            if state['serial']:
                if not selected or len(set(selected)) != len(selected):
                    abort(400, 'Select distinct serial units.')
                for uid in selected:
                    unit = db().execute('SELECT * FROM serial_units WHERE tenant_id=? AND ticket_id=? AND id=?', (g.tenant['id'], ticket_id, uid)).fetchone()
                    if not unit:
                        abort(404)
                    if unit['state'] != 'accepted':
                        abort(409, 'Only accepted serial units can be packed or depart.')
                    done = {r[0] for r in db().execute('SELECT kind FROM fulfillment_units WHERE tenant_id=? AND unit_id=?', (g.tenant['id'], uid))}
                    if kind in done or (kind == 'depart' and 'pack' not in done):
                        abort(409, 'Unit already recorded or not yet packed.')
                    serial_names.append(unit['serial'])
                quantity = len(selected)
            else:
                if selected:
                    abort(400, 'Batch tickets use quantities, not serial travelers.')
                try:
                    quantity = int(request.form.get('quantity', ''))
                except ValueError:
                    abort(400, 'Enter a whole quantity.')
                if ticket['state'] != 'accepted':
                    abort(409, 'Batch inspection must pass before packing or departure.')
            limit = state['available'] if kind == 'pack' else state['waiting']
            if not 1 <= quantity <= limit:
                abort(409, 'Quantity exceeds accepted/unpacked or packed/undeparted units.')
            stamp = now()
            record = db().execute('INSERT INTO fulfillment_records(tenant_id,ticket_id,kind,quantity,destination,reference,method,notes,serials,actor_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)', (g.tenant['id'], ticket_id, kind, quantity, destination, reference, method, notes, json.dumps(serial_names), g.user['id'], stamp))
            for uid in selected:
                db().execute('INSERT INTO fulfillment_units VALUES(?,?,?,?,?)', (g.tenant['id'], ticket_id, record.lastrowid, uid, kind))
            event(ticket_id, 'units_packed' if kind == 'pack' else 'units_departed', json.dumps(dict(record_id=record.lastrowid, quantity=quantity, serials=serial_names, destination=destination, reference=reference, method=method, notes=notes)))
            if selected:
                # The existing link table permits one unit per event; keep each unit's history explicit.
                for uid, serial in zip(selected, serial_names):
                    event(ticket_id, 'unit_packed' if kind == 'pack' else 'unit_departed', json.dumps(dict(record_id=record.lastrowid, unit_id=uid, serial=serial, destination=destination, reference=reference)))
                    unit_event_id = db().execute('SELECT last_insert_rowid()').fetchone()[0]
                    db().execute('INSERT INTO unit_event_links VALUES(?,?,?,?)', (g.tenant['id'], ticket_id, uid, unit_event_id))
            if kind == 'depart' and state['departed'] + quantity == ticket['quantity']:
                event(ticket_id, 'delivery_closed', 'All requested units physically departed; production acceptance remains preserved.')
            touch(ticket_id)
        return redirect(url_for('delivery_detail', ticket_id=ticket_id))
