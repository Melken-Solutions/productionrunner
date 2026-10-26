"""Touch-first read/confirmation views; execution uses the existing guarded endpoints."""
from .dispatch import released
from flask import abort, g, redirect, render_template, request, url_for


def register_shop_floor(app, db, roles_required):
    def page_number():
        try:
            page = int(request.args.get('page', '1'))
        except ValueError:
            abort(400, 'Invalid page.')
        if not 1 <= page <= 100000:
            abort(400, 'Invalid page.')
        return page

    def ticket_for(ticket_id):
        ticket = db().execute('SELECT t.*,p.priority AS hot_priority FROM tickets t LEFT JOIN ticket_priorities p ON p.tenant_id=t.tenant_id AND p.ticket_id=t.id WHERE t.tenant_id=? AND t.id=?', (g.tenant['id'],ticket_id)).fetchone()
        if not ticket:
            abort(404, 'Unknown traveler. Scan again or return to Shop Floor.')
        return ticket

    def context(code):
        unit = None
        if code.startswith('JR-U-'):
            unit = db().execute('SELECT * FROM serial_units WHERE tenant_id=? AND id=?',(g.tenant['id'],code)).fetchone()
            if not unit:
                abort(404,'Unknown unit traveler.')
            ticket = ticket_for(unit['ticket_id'])
            operations = db().execute('SELECT o.id,o.sequence,o.name,o.instructions,c.actor_id AS completed_by,c.completed_at FROM operations o LEFT JOIN unit_completions c ON c.tenant_id=o.tenant_id AND c.operation_id=o.id AND c.unit_id=? WHERE o.tenant_id=? AND o.ticket_id=? ORDER BY o.sequence',(code,g.tenant['id'],ticket['id'])).fetchall()
        else:
            ticket = ticket_for(code)
            operations = db().execute('SELECT * FROM operations WHERE tenant_id=? AND ticket_id=? ORDER BY sequence',(g.tenant['id'],ticket['id'])).fetchall()
        has_units = not unit and db().execute('SELECT 1 FROM serial_units WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],ticket['id'])).fetchone() is not None
        cancelled = db().execute('SELECT * FROM ticket_cancellations WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],ticket['id'])).fetchone()
        target = unit if unit else ticket
        next_op = next((o for o in operations if o['completed_by'] is None),None)
        return dict(ticket=ticket,unit=unit,target=target,operations=operations,next_operation=next_op,
                    has_units=has_units,cancelled=cancelled,code=code,
                    can_complete=released(db(),g.tenant['id'],ticket['id']) and not cancelled and not has_units and target['state'] in {'ready','in_progress'} and next_op is not None)

    @app.get('/shop-floor')
    @roles_required('operator')
    def shop_floor():
        page = page_number()
        tickets = db().execute("SELECT t.*,p.priority AS hot_priority FROM tickets t LEFT JOIN ticket_priorities p ON p.tenant_id=t.tenant_id AND p.ticket_id=t.id WHERE t.tenant_id=? AND t.state!='accepted' AND NOT EXISTS(SELECT 1 FROM ticket_cancellations x WHERE x.tenant_id=t.tenant_id AND x.ticket_id=t.id) ORDER BY (p.priority IS NULL),p.priority,p.hot_since,CASE WHEN p.priority IS NOT NULL THEN t.id END,t.created_at DESC,t.id LIMIT 51 OFFSET ?",(g.tenant['id'],(page-1)*50)).fetchall()
        return render_template('shop_queue.html',tickets=tickets[:50],has_more=len(tickets)>50,page=page)

    @app.get('/shop-floor/scan')
    @roles_required('operator')
    def shop_scan():
        code = request.args.get('code','').strip()
        context(code)
        return redirect(url_for('shop_work',code=code))

    @app.get('/shop-floor/work/<code>')
    @roles_required('operator')
    def shop_work(code):
        data = context(code)
        page = page_number()
        units=[]
        if data['has_units']:
            units=db().execute('SELECT * FROM serial_units WHERE tenant_id=? AND ticket_id=? ORDER BY serial LIMIT 51 OFFSET ?',(g.tenant['id'],data['ticket']['id'],(page-1)*50)).fetchall()
        return render_template('shop_work.html',**data,units=units[:50],page=page,has_more=len(units)>50)

    @app.get('/shop-floor/work/<code>/confirm')
    @roles_required('operator')
    def shop_confirm(code):
        data=context(code)
        if not data['can_complete']:
            abort(409,'This traveler is not available for production. Reopen its live status.')
        if request.args.get('version') != str(data['target']['version']) or request.args.get('operation_id') != str(data['next_operation']['id']):
            abort(409,'Work changed. Reopen the traveler before confirming.')
        return render_template('shop_confirm.html',**data)
