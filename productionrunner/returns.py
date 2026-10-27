"""Return receipt and disposition decisions, separate from outbound delivery."""
import json
from flask import abort, g, redirect, render_template, request, url_for
from .intake import actor

DECISIONS = {'repair_required': 'Repair required', 'replacement_required': 'Replacement required',
             'scrap_authorized': 'Scrap authorized', 'further_review': 'Further review required'}


def register_returns(app, db, roles_required, now, lock_ticket, event):
    def history(ticket_id=None):
        clause = ' AND r.ticket_id=?' if ticket_id else ''
        args = (g.tenant['id'], ticket_id) if ticket_id else (g.tenant['id'],)
        return db().execute('SELECT r.*,u.username,t.pn,t.revision,v.decision,v.notes AS review_notes,v.created_at AS reviewed_at,vu.username AS reviewed_by,l.repair_ticket_id,rt.version AS repair_version,ce.action AS closure_action,ce.reason AS closure_reason,ce.created_at AS closure_at FROM customer_returns r JOIN tickets t ON t.tenant_id=r.tenant_id AND t.id=r.ticket_id JOIN users u ON u.id=r.actor_id LEFT JOIN customer_return_reviews v ON v.tenant_id=r.tenant_id AND v.return_id=r.id LEFT JOIN users vu ON vu.id=v.actor_id LEFT JOIN return_repairs l ON l.tenant_id=r.tenant_id AND l.return_id=r.id LEFT JOIN tickets rt ON rt.tenant_id=l.tenant_id AND rt.id=l.repair_ticket_id LEFT JOIN return_closure_events ce ON ce.id=(SELECT MAX(e.id) FROM return_closure_events e WHERE e.tenant_id=r.tenant_id AND e.return_id=r.id) WHERE r.tenant_id=?'+clause+' ORDER BY r.id DESC', args).fetchall()

    def text(name, limit=500):
        value = request.form.get(name, '').strip()
        if not 1 <= len(value) <= limit:
            abort(400, f'Provide {name} (up to {limit} characters).')
        return value

    def audit_return(ticket_id, return_id, action, detail, units):
        detail = json.dumps(dict(return_id=return_id, **detail))
        event(ticket_id, action, detail)
        for uid in units:
            event(ticket_id, 'unit_'+action, detail)
            eid = db().execute('SELECT last_insert_rowid()').fetchone()[0]
            db().execute('INSERT INTO unit_event_links VALUES(?,?,?,?)', (g.tenant['id'], ticket_id, uid, eid))
        db().execute('UPDATE tickets SET version=version+1 WHERE tenant_id=? AND id=?', (g.tenant['id'], ticket_id))

    @app.get('/returns')
    @roles_required()
    def returns_queue():
        view=request.args.get('view','open')
        if view not in {'open','closed'}:abort(400)
        entries=[r for r in history() if (r['closure_action']=='closed')==(view=='closed')]
        return render_template('returns_queue.html', entries=entries, decisions=DECISIONS, view=view)

    @app.route('/tickets/<ticket_id>/returns', methods=['GET', 'POST'])
    @roles_required()
    def ticket_returns(ticket_id):
        ticket = db().execute('SELECT * FROM tickets WHERE tenant_id=? AND id=?', (g.tenant['id'], ticket_id)).fetchone()
        if not ticket:
            abort(404)
        if request.method == 'POST':
            with db():
                ticket = lock_ticket(ticket_id)
                actor(db(),g.tenant['id'],g.user['id'],('operator','planner','admin'))
                if request.form.get('confirm') != 'yes':
                    abort(400, 'Confirm physical receipt and segregation of returned material.')
                departure = db().execute("SELECT * FROM fulfillment_active_records WHERE tenant_id=? AND ticket_id=? AND id=? AND kind='depart'", (g.tenant['id'], ticket_id, request.form.get('departure_id'))).fetchone()
                if not departure:
                    abort(404, 'Choose an unreversed departure for this ticket.')
                reference, reason, location = text('reference'), text('reason',1000), text('location')
                selected = request.form.getlist('unit_id')
                serials=[]
                serial = db().execute('SELECT 1 FROM serial_units WHERE tenant_id=? AND ticket_id=?', (g.tenant['id'],ticket_id)).fetchone()
                if serial:
                    if not selected or len(set(selected)) != len(selected):
                        abort(400,'Select distinct returned serials.')
                    for uid in selected:
                        unit = db().execute("SELECT s.serial FROM fulfillment_active_units f JOIN serial_units s ON s.tenant_id=f.tenant_id AND s.id=f.unit_id WHERE f.tenant_id=? AND f.ticket_id=? AND f.record_id=? AND f.unit_id=? AND f.kind='depart'", (g.tenant['id'],ticket_id,departure['id'],uid)).fetchone()
                        if not unit:
                            abort(409,'Every selected serial must belong to this departure.')
                        if db().execute('SELECT 1 FROM customer_return_units WHERE tenant_id=? AND unit_id=?',(g.tenant['id'],uid)).fetchone():
                            abort(409,'This serial has already been received as a return.')
                        serials.append(unit['serial'])
                    quantity=len(selected)
                else:
                    if selected:abort(400,'Batch returns use quantities.')
                    try:quantity=int(request.form.get('quantity',''))
                    except ValueError:abort(400,'Enter a whole quantity.')
                returned=db().execute('SELECT COALESCE(SUM(quantity),0) FROM customer_returns WHERE tenant_id=? AND departure_id=?',(g.tenant['id'],departure['id'])).fetchone()[0]
                if not 1<=quantity<=departure['quantity']-returned:
                    abort(409,'Return quantity exceeds the unreturned quantity in this departure.')
                row=db().execute('INSERT INTO customer_returns(tenant_id,ticket_id,departure_id,quantity,reference,reason,location,serials,actor_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(g.tenant['id'],ticket_id,departure['id'],quantity,reference,reason,location,json.dumps(serials),g.user['id'],now()))
                rid=row.lastrowid
                for uid in selected:
                    db().execute('INSERT INTO customer_return_units VALUES(?,?,?,?)',(g.tenant['id'],ticket_id,rid,uid))
                audit_return(ticket_id,rid,'return_received',dict(departure_id=departure['id'],quantity=quantity,serials=serials,reference=reference,reason=reason,location=location),selected)
            return redirect(url_for('ticket_returns',ticket_id=ticket_id))
        departures=db().execute("SELECT f.*,f.quantity-COALESCE((SELECT SUM(r.quantity) FROM customer_returns r WHERE r.tenant_id=f.tenant_id AND r.departure_id=f.id),0) AS available FROM fulfillment_active_records f WHERE f.tenant_id=? AND f.ticket_id=? AND f.kind='depart' ORDER BY f.id",(g.tenant['id'],ticket_id)).fetchall()
        units=db().execute("SELECT f.record_id,s.id,s.serial FROM fulfillment_active_units f JOIN serial_units s ON s.tenant_id=f.tenant_id AND s.id=f.unit_id WHERE f.tenant_id=? AND f.ticket_id=? AND f.kind='depart' AND NOT EXISTS(SELECT 1 FROM customer_return_units r WHERE r.tenant_id=f.tenant_id AND r.unit_id=f.unit_id) ORDER BY s.serial",(g.tenant['id'],ticket_id)).fetchall()
        serial=bool(db().execute('SELECT 1 FROM serial_units WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],ticket_id)).fetchone())
        return render_template('ticket_returns.html',serial=serial,ticket=ticket,departures=departures,units=units,entries=history(ticket_id),decisions=DECISIONS)

    @app.post('/tickets/<ticket_id>/returns/<int:return_id>/review')
    @roles_required('planner','admin')
    def review_return(ticket_id, return_id):
        with db():
            ticket=lock_ticket(ticket_id)
            actor(db(),g.tenant['id'],g.user['id'],('planner','admin'))
            entry=db().execute('SELECT * FROM customer_returns WHERE tenant_id=? AND ticket_id=? AND id=?',(g.tenant['id'],ticket_id,return_id)).fetchone()
            if not entry:abort(404)
            if db().execute('SELECT 1 FROM customer_return_reviews WHERE return_id=?',(return_id,)).fetchone():
                abort(409,'Disposition already recorded.')
            decision=request.form.get('decision')
            if decision not in DECISIONS:abort(400,'Choose a disposition.')
            notes=text('notes',1000)
            db().execute('INSERT INTO customer_return_reviews VALUES(?,?,?,?,?,?,?)',(g.tenant['id'],ticket_id,return_id,decision,notes,g.user['id'],now()))
            units=[r[0] for r in db().execute('SELECT unit_id FROM customer_return_units WHERE tenant_id=? AND return_id=?',(g.tenant['id'],return_id))]
            audit_return(ticket_id,return_id,'return_disposition_recorded',dict(decision=decision,notes=notes),units)
        return redirect(url_for('ticket_returns',ticket_id=ticket_id))
