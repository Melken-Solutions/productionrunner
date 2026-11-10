"""Audited next-shipment targets, independent of physical lots and departures."""
import json
from datetime import date
from flask import abort, g, request, redirect, url_for
from .fulfillment import progress
from .intake import actor


def target_for(conn, tenant, ticket):
    row = conn.execute("SELECT detail FROM events WHERE tenant_id=? AND ticket_id=? AND action='shipment_target_set' ORDER BY id DESC LIMIT 1", (tenant, ticket['id'])).fetchone()
    if not row:
        return None
    target = json.loads(row['detail'])
    priority = conn.execute('SELECT priority FROM ticket_priorities WHERE tenant_id=? AND ticket_id=?', (tenant, ticket['id'])).fetchone()
    target['hot_priority'] = priority['priority'] if priority else None
    state = progress(conn, tenant, ticket)
    baseline, amount = target['departed_at_start'], target['quantity']
    # Cumulative thresholds keep targets honest when a departure is reversed.
    goal = baseline + amount
    target.update(shipped=min(amount, max(0, state['departed']-baseline)),
                  remaining=max(0, goal-state['departed']),
                  accepted=min(amount, max(0, state['accepted']-baseline)),
                  packed=min(amount, max(0, state['packed']-baseline)),
                  order_remaining=state['remaining'],
                  overdue=target['ship_date'] < date.today().isoformat() and state['departed'] < goal)
    target['met'] = target['remaining'] == 0
    return target


def register(app, db, roles_required, lock_ticket, event):
    @app.context_processor
    def context():
        return {'shipment_target': lambda ticket: target_for(db(), g.tenant['id'], ticket)}

    @app.post('/tickets/<ticket_id>/shipment-target')
    @roles_required('planner', 'admin')
    def set_shipment_target(ticket_id):
        with db():
            ticket = lock_ticket(ticket_id)
            actor(db(), g.tenant['id'], g.user['id'], ('planner', 'admin'))
            state = progress(db(), g.tenant['id'], ticket)
            if state['closed']:
                abort(409, 'This order has already departed in full.')
            if not state['serial']:
                abort(409, 'Enable production lots before planning staged shipments.')
            try:
                amount = int(request.form.get('quantity', ''))
                ship_date = date.fromisoformat(request.form.get('ship_date', '')).isoformat()
            except ValueError:
                abort(400, 'Enter a whole shipment quantity and valid ship date.')
            if not 1 <= amount <= state['remaining']:
                abort(400, 'Shipment quantity must be between 1 and the quantity still owed.')
            reason = request.form.get('reason', '').strip()
            if not 1 <= len(reason) <= 1000:
                abort(400, 'Choose a reason for this shipment target.')
            event(ticket_id, 'shipment_target_set', json.dumps(dict(quantity=amount, ship_date=ship_date, departed_at_start=state['departed'], reason=reason)))
            db().execute('UPDATE tickets SET version=version+1 WHERE tenant_id=? AND id=?', (g.tenant['id'], ticket_id))
        return redirect(url_for('delivery_detail', ticket_id=ticket_id))
