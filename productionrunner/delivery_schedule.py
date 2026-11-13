"""Versioned delivery schedules captured with requests and copied to orders."""
import json
from datetime import date
from flask import abort

EMPTY = {'mode': 'together', 'shipments': []}


def request_schedule(conn, tenant, rid):
    row = conn.execute("SELECT detail FROM intake_events WHERE tenant_id=? AND request_id=? AND action='delivery_schedule_saved' ORDER BY id DESC LIMIT 1", (tenant, rid)).fetchone()
    return json.loads(row['detail'])['schedule'] if row else dict(EMPTY)


def ticket_schedule(conn, tenant, tid):
    row = conn.execute("SELECT detail FROM events WHERE tenant_id=? AND ticket_id=? AND action='delivery_schedule_saved' ORDER BY id DESC LIMIT 1", (tenant, tid)).fetchone()
    return json.loads(row['detail']) if row else None


def parse(form, total, deadline='', previous=None, complete=False):
    if 'delivery_mode' not in form:
        result = previous if previous is not None else dict(EMPTY)
    else:
        mode = form.get('delivery_mode')
        if mode not in {'together', 'staged'}:
            abort(400, 'Choose a delivery plan.')
        result = dict(mode=mode, shipments=[])
        if mode == 'staged':
            quantities, dates = form.getlist('shipment_quantity'), form.getlist('shipment_date')
            if len(quantities) != len(dates) or len(quantities) > 100:
                abort(400, 'Provide up to 100 shipment quantities and dates.')
            for raw, day in zip(quantities, dates):
                if not raw.strip() and not day.strip():
                    continue
                try:
                    amount = int(raw)
                    day = date.fromisoformat(day).isoformat()
                except ValueError:
                    abort(400, 'Each shipment needs a whole quantity and a valid ship date.')
                if amount < 1:
                    abort(400, 'Shipment quantities must be positive.')
                result['shipments'].append(dict(quantity=amount, ship_date=day))
    validate(result, total, deadline, complete)
    return result


def validate(schedule, total, deadline='', complete=False):
    if schedule['mode'] != 'staged':
        return
    shipments = schedule['shipments']
    amount = sum(s['quantity'] for s in shipments)
    if amount > total:
        abort(400, 'Scheduled shipments exceed the order quantity. Reduce the schedule or increase the order quantity.')
    days = [s['ship_date'] for s in shipments]
    if days != sorted(days):
        abort(400, 'Put shipments in ship-date order, earliest first.')
    if deadline and any(day > deadline for day in days):
        abort(400, 'A shipment is later than the order required date. Update the date or schedule.')
    if complete and amount != total:
        abort(409, f'Schedule the remaining {total-amount} units before approval or release.')


def save_request(conn, tenant, user, rid, schedule, stamp, reason='Initial request'):
    from .intake import log
    log(conn, tenant, user, rid, 'delivery_schedule_saved', dict(schedule=schedule, reason=reason), stamp)


def validate_request(conn, tenant, row):
    schedule = request_schedule(conn, tenant, row['id'])
    validate(schedule, row['quantity'], row['required_date'], complete=True)
    return schedule


def snapshot(conn, tenant, user, req, tid, stamp):
    schedule = validate_request(conn, tenant, req)
    if schedule['mode'] == 'staged':
        data = dict(schedule, departed_at_start=0, reason='Approved request delivery schedule', request_id=req['id'])
        conn.execute('INSERT INTO events(tenant_id,ticket_id,actor_id,action,detail,created_at) VALUES(?,?,?,?,?,?)', (tenant, tid, user, 'delivery_schedule_saved', json.dumps(data), stamp))
