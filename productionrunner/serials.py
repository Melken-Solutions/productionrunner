"""Independent serial execution using the ticket's immutable route definition."""
import secrets
from barcode import Code128
from flask import abort, g, redirect, render_template, request, url_for, Response


def units_for(conn, tenant_id, ticket_id):
    return conn.execute('SELECT s.*, (SELECT COUNT(*) FROM unit_completions c WHERE c.tenant_id=s.tenant_id AND c.unit_id=s.id) AS completed_count FROM serial_units s WHERE s.tenant_id=? AND s.ticket_id=? ORDER BY s.serial',
                        (tenant_id, ticket_id)).fetchall()


def create_units(conn, tenant_id, ticket_id, serials):
    for serial in serials:
        conn.execute('INSERT INTO serial_units(id,tenant_id,ticket_id,serial) VALUES(?,?,?,?)',
                     ('JR-U-' + secrets.token_hex(6).upper(), tenant_id, ticket_id, serial))


def register_serials(app, db, roles_required, now):
    def get_unit(unit_id):
        unit = db().execute('SELECT * FROM serial_units WHERE tenant_id=? AND id=?', (g.tenant['id'], unit_id)).fetchone()
        if not unit:
            abort(404, 'Unknown unit traveler.')
        return unit

    def route(unit):
        return db().execute(
            'SELECT o.*, c.actor_id AS completed_by_unit, c.completed_at AS unit_completed_at, u.username '
            'FROM operations o LEFT JOIN unit_completions c ON c.operation_id=o.id AND c.unit_id=? '
            'AND c.tenant_id=o.tenant_id LEFT JOIN users u ON u.id=c.actor_id '
            'WHERE o.tenant_id=? AND o.ticket_id=? ORDER BY o.sequence',
            (unit['id'], unit['tenant_id'], unit['ticket_id'])).fetchall()

    def unit_event(unit, action, detail):
        entry = db().execute('INSERT INTO events(tenant_id,ticket_id,actor_id,action,detail,created_at) VALUES(?,?,?,?,?,?)',
                             (unit['tenant_id'], unit['ticket_id'], g.user['id'], action,
                              f"{unit['id']} · Serial {unit['serial']}: {detail}", now()))
        db().execute('INSERT INTO unit_event_links VALUES(?,?,?,?)',
                     (unit['tenant_id'], unit['ticket_id'], unit['id'], entry.lastrowid))

    def lock_unit(unit_id):
        db().execute('BEGIN IMMEDIATE')
        unit = get_unit(unit_id)
        if request.form.get('version') != str(unit['version']):
            abort(409, 'This unit changed. Reload before submitting again.')
        return unit

    def update_state(unit, state):
        db().execute('UPDATE serial_units SET state=?,version=version+1 WHERE tenant_id=? AND id=?',
                     (state, unit['tenant_id'], unit['id']))
        states = {u['state'] for u in units_for(db(), unit['tenant_id'], unit['ticket_id'])}
        if 'held' in states:
            summary = 'held'
        elif states == {'accepted'}:
            summary = 'accepted'
        elif states == {'ready'}:
            summary = 'ready'
        elif states <= {'awaiting_inspection', 'accepted'}:
            summary = 'awaiting_inspection'
        else:
            summary = 'in_progress'
        db().execute('UPDATE tickets SET state=?,version=version+1 WHERE tenant_id=? AND id=?',
                     (summary, unit['tenant_id'], unit['ticket_id']))

    def context(unit):
        ticket = db().execute('SELECT * FROM tickets WHERE tenant_id=? AND id=?',
                              (unit['tenant_id'], unit['ticket_id'])).fetchone()
        operations = route(unit)
        return dict(unit=unit, ticket=ticket, operations=operations,
                    next_operation=next((o for o in operations if o['completed_by_unit'] is None), None),
                    independent=all(o['completed_by_unit'] != g.user['id'] for o in operations))

    @app.get('/units/<unit_id>')
    @roles_required()
    def unit_detail(unit_id):
        unit = get_unit(unit_id)
        events = db().execute('SELECT e.*,u.username FROM unit_event_links l JOIN events e ON e.id=l.event_id '
                              'JOIN users u ON u.id=e.actor_id WHERE l.tenant_id=? AND l.unit_id=? ORDER BY e.id',
                              (unit['tenant_id'], unit_id)).fetchall()
        return render_template('unit.html', events=events, **context(unit))

    @app.post('/units/<unit_id>/complete')
    @roles_required('operator')
    def complete_unit(unit_id):
        with db():
            unit = lock_unit(unit_id)
            if unit['state'] not in {'ready', 'in_progress'}:
                abort(409, 'This unit is not available for production.')
            operations = route(unit)
            remaining = [o for o in operations if o['completed_by_unit'] is None]
            if not remaining or request.form.get('operation_id') != str(remaining[0]['id']):
                abort(409, 'Only the next operation for this unit can be completed.')
            if request.form.get('confirm') != 'yes':
                abort(400, 'Confirm completion for this individual unit.')
            operation = remaining[0]
            db().execute('INSERT INTO unit_completions VALUES(?,?,?,?,?,?)',
                         (unit['tenant_id'], unit['ticket_id'], unit_id, operation['id'], g.user['id'], now()))
            update_state(unit, 'in_progress' if len(remaining) > 1 else 'awaiting_inspection')
            unit_event(unit, 'unit_operation_completed', f"{operation['sequence']}: {operation['name']}")
        return redirect(url_for('unit_detail', unit_id=unit_id))

    @app.post('/units/<unit_id>/inspect')
    @roles_required('inspector')
    def inspect_unit(unit_id):
        with db():
            unit = lock_unit(unit_id)
            if unit['state'] != 'awaiting_inspection':
                abort(409, 'Complete this unit’s route before inspection.')
            if any(o['completed_by_unit'] == g.user['id'] for o in route(unit)):
                abort(403, 'A production contributor cannot inspect this unit.')
            result, notes = request.form.get('result'), request.form.get('notes', '').strip()
            if result not in {'pass', 'fail'} or not notes or len(notes) > 4000:
                abort(400, 'Choose pass or fail and record evidence (up to 4000 characters).')
            db().execute('INSERT INTO unit_inspections VALUES(?,?,?,?,?,?,?)',
                         (unit['tenant_id'], unit['ticket_id'], unit_id, g.user['id'], result, notes, now()))
            update_state(unit, 'accepted' if result == 'pass' else 'held')
            unit_event(unit, 'unit_inspection_' + result, notes)
        return redirect(url_for('unit_detail', unit_id=unit_id))

    @app.post('/units/<unit_id>/issue-traveler')
    @roles_required('planner', 'operator')
    def issue_unit_traveler(unit_id):
        with db():
            unit = get_unit(unit_id)
            unit_event(unit, 'unit_traveler_requested', 'Print/reprint requested; physical printing is not verified.')
        return redirect(url_for('unit_traveler', unit_id=unit_id))

    @app.get('/units/<unit_id>/traveler')
    @roles_required()
    def unit_traveler(unit_id):
        return render_template('unit_traveler.html', **context(get_unit(unit_id)))

    @app.get('/units/<unit_id>/barcode.svg')
    @roles_required()
    def unit_barcode(unit_id):
        get_unit(unit_id)
        return Response(Code128(unit_id).render({'module_width': 0.3, 'module_height': 18, 'quiet_zone': 6.5}), mimetype='image/svg+xml')
