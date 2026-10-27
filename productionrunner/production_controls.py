"""Cancellation and documented repair followed by independent reinspection."""
from flask import abort, g, redirect, render_template, request, url_for

from .serials import update_unit_state


def require_active(conn, tenant_id, ticket_id):
    if conn.execute('SELECT 1 FROM ticket_cancellations WHERE tenant_id=? AND ticket_id=?', (tenant_id, ticket_id)).fetchone():
        abort(409, 'This ticket is cancelled. No further production or inspection is allowed.')


def register_controls(app, db, roles_required, now, lock_ticket):
    def cancellation(ticket):
        return db().execute('SELECT * FROM ticket_cancellations WHERE tenant_id=? AND ticket_id=?',
                            (g.tenant['id'], ticket['id'])).fetchone()

    def cases(ticket, unit=None):
        return db().execute('SELECT * FROM rework_cases WHERE tenant_id=? AND ticket_id=? AND unit_id IS ? ORDER BY id',
                            (g.tenant['id'], ticket['id'], unit['id'] if unit else None)).fetchall()

    @app.context_processor
    def controls_context():
        return dict(cancellation_for=cancellation, rework_for=cases)

    def text_field(name, limit=4000):
        value = request.form.get(name, '').strip()
        if not value or len(value) > limit:
            abort(400, f'Record {name} (1–{limit} characters).')
        return value

    def event(ticket_id, unit_id, action, detail):
        entry = db().execute('INSERT INTO events(tenant_id,ticket_id,actor_id,action,detail,created_at) VALUES(?,?,?,?,?,?)',
                             (g.tenant['id'], ticket_id, g.user['id'], action, (f'{unit_id}: ' if unit_id else '') + detail, now()))
        if unit_id:
            db().execute('INSERT INTO unit_event_links VALUES(?,?,?,?)', (g.tenant['id'], ticket_id, unit_id, entry.lastrowid))

    def touch(ticket_id, unit_id):
        db().execute('UPDATE tickets SET version=version+1 WHERE id=? AND tenant_id=?', (ticket_id, g.tenant['id']))
        if unit_id:
            db().execute('UPDATE serial_units SET version=version+1 WHERE id=? AND tenant_id=?', (unit_id, g.tenant['id']))

    @app.post('/tickets/<ticket_id>/cancel')
    @roles_required('admin')
    def cancel_ticket(ticket_id):
        reason = text_field('reason', 500)
        if request.form.get('confirm') != 'yes':
            abort(400, 'Confirm cancellation of this ticket and its remaining work.')
        with db():
            ticket = lock_ticket(ticket_id)
            if db().execute('SELECT 1 FROM return_repairs WHERE tenant_id=? AND repair_ticket_id=?',(g.tenant['id'],ticket_id)).fetchone():
                abort(409,'Linked return repair jobs cannot be cancelled; keep the return traceable.')
            if db().execute('SELECT 1 FROM fulfillment_records WHERE tenant_id=? AND ticket_id=?', (g.tenant['id'], ticket_id)).fetchone():
                abort(409, 'Tickets with packing or departure evidence cannot be cancelled.')
            if ticket['state'] == 'accepted':
                abort(409, 'Accepted tickets cannot be cancelled.')
            db().execute('INSERT INTO ticket_cancellations VALUES(?,?,?,?,?)',
                         (g.tenant['id'], ticket_id, g.user['id'], reason, now()))
            touch(ticket_id, None)
            event(ticket_id, None, 'ticket_cancelled', reason)
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))

    @app.post('/tickets/<ticket_id>/rework')
    @roles_required('admin')
    def authorize_rework(ticket_id):
        reason, instructions, criteria = text_field('reason', 500), text_field('instructions'), text_field('criteria')
        unit_id = request.form.get('unit_id') or None
        with db():
            ticket = lock_ticket(ticket_id)
            has_units = db().execute('SELECT 1 FROM serial_units WHERE tenant_id=? AND ticket_id=?', (g.tenant['id'], ticket_id)).fetchone()
            target = ticket
            if has_units:
                target = db().execute('SELECT * FROM serial_units WHERE tenant_id=? AND ticket_id=? AND id=?', (g.tenant['id'], ticket_id, unit_id)).fetchone()
                if not target:
                    abort(404, 'Choose a unit belonging to this ticket.')
            elif unit_id:
                abort(404, 'This ticket has no individual units.')
            if target['state'] != 'held':
                abort(409, 'Only held work can be sent for rework.')
            if db().execute('SELECT 1 FROM rework_cases WHERE tenant_id=? AND ticket_id=? AND unit_id IS ? AND result IS NULL', (g.tenant['id'], ticket_id, unit_id)).fetchone():
                abort(409, 'An open rework case already exists.')
            case = db().execute('INSERT INTO rework_cases(tenant_id,ticket_id,unit_id,authorized_by,reason,instructions,criteria,created_at) VALUES(?,?,?,?,?,?,?,?)',
                                (g.tenant['id'], ticket_id, unit_id, g.user['id'], reason, instructions, criteria, now()))
            touch(ticket_id, unit_id)
            event(ticket_id, unit_id, 'rework_authorized', f'Rework {case.lastrowid}. Reason: {reason}. Instructions: {instructions}. Acceptance criteria: {criteria}')
        return redirect(url_for('rework_detail', case_id=case.lastrowid))

    def get_case(case_id):
        case = db().execute('SELECT * FROM rework_cases WHERE tenant_id=? AND id=?', (g.tenant['id'], case_id)).fetchone()
        if not case:
            abort(404, 'Unknown rework case.')
        return case

    def independent(case):
        if case['unit_id']:
            original = db().execute('SELECT 1 FROM unit_completions WHERE tenant_id=? AND unit_id=? AND actor_id=?', (g.tenant['id'], case['unit_id'], g.user['id'])).fetchone()
        else:
            original = db().execute('SELECT 1 FROM operations WHERE tenant_id=? AND ticket_id=? AND completed_by=?', (g.tenant['id'], case['ticket_id'], g.user['id'])).fetchone()
        repair = db().execute('SELECT 1 FROM rework_cases WHERE tenant_id=? AND ticket_id=? AND unit_id IS ? AND completed_by=?',
                              (g.tenant['id'], case['ticket_id'], case['unit_id'], g.user['id'])).fetchone()
        return not original and not repair

    def locked_case(case_id):
        db().execute('BEGIN IMMEDIATE')
        case = get_case(case_id)
        require_active(db(), g.tenant['id'], case['ticket_id'])
        if request.form.get('version') != str(case['version']) or case['result']:
            abort(409, 'This rework case changed or is closed. Reload before submitting.')
        return case

    @app.get('/rework/<int:case_id>')
    @roles_required()
    def rework_detail(case_id):
        case = get_case(case_id)
        ticket = db().execute('SELECT * FROM tickets WHERE tenant_id=? AND id=?', (g.tenant['id'], case['ticket_id'])).fetchone()
        return render_template('rework.html', case=case, ticket=ticket, independent=independent(case))

    @app.post('/rework/<int:case_id>/complete')
    @roles_required('operator')
    def complete_rework(case_id):
        notes = text_field('notes')
        if request.form.get('confirm') != 'yes':
            abort(400, 'Confirm that the documented repair is complete.')
        with db():
            case = locked_case(case_id)
            if case['completed_by']:
                abort(409, 'Repair is already complete; independent inspection is required.')
            db().execute('UPDATE rework_cases SET completed_by=?,completion_notes=?,completed_at=?,version=version+1 WHERE id=?', (g.user['id'], notes, now(), case_id))
            touch(case['ticket_id'], case['unit_id'])
            event(case['ticket_id'], case['unit_id'], 'rework_completed', f'Rework {case_id}: {notes}')
        return redirect(url_for('rework_detail', case_id=case_id))

    @app.post('/rework/<int:case_id>/inspect')
    @roles_required('inspector')
    def inspect_rework(case_id):
        notes, result = text_field('notes'), request.form.get('result')
        if result not in {'pass', 'fail'}:
            abort(400, 'Choose pass or fail.')
        with db():
            case = locked_case(case_id)
            if not case['completed_by']:
                abort(409, 'Complete the repair before reinspection.')
            if not independent(case):
                abort(403, 'An original production or rework contributor cannot inspect this work.')
            db().execute('UPDATE rework_cases SET inspector_id=?,result=?,inspection_notes=?,inspected_at=?,version=version+1 WHERE id=?', (g.user['id'], result, notes, now(), case_id))
            state = 'accepted' if result == 'pass' else 'held'
            if case['unit_id']:
                unit = db().execute('SELECT * FROM serial_units WHERE tenant_id=? AND id=?', (g.tenant['id'], case['unit_id'])).fetchone()
                update_unit_state(db(), unit, state)
            else:
                db().execute('UPDATE tickets SET state=?,version=version+1 WHERE tenant_id=? AND id=?', (state, g.tenant['id'], case['ticket_id']))
            event(case['ticket_id'], case['unit_id'], 'rework_inspection_' + result, f'Rework {case_id}: {notes}')
        return redirect(url_for('rework_detail', case_id=case_id))
