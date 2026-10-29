"""Plan the ordered work at intake and snapshot it into the JobRunner."""
import json
from flask import abort

STAGES = ('inspection', 'packing', 'shipping')


def get(conn, tenant, rid):
    row = conn.execute('SELECT data FROM request_job_plans WHERE tenant_id=? AND request_id=?', (tenant, rid)).fetchone()
    return json.loads(row['data']) if row else None


def parse(form, previous=None):
    if form.get('request_plan_present') != 'yes':
        return previous
    try:
        plan = json.loads(form.get('request_plan_json', '{}'))
        if not isinstance(plan, dict) or not isinstance(plan.get('steps'), list) or len(plan['steps']) > 20:
            raise ValueError
        cleaned = {'steps': [], 'dxf_required': plan.get('dxf_required') is True}
        for step in plan['steps']:
            item = {k: str(step.get(k, '')).strip() for k in ('name', 'instructions', 'criteria')}
            if len(item['name']) > 120 or len(item['instructions']) > 350 or len(item['criteria']) > 2000 or '|' in item['name']:
                raise ValueError
            item.update(station_id=int(step.get('station_id') or 0), outside=step.get('outside') is True, first_piece=step.get('first_piece') is True)
            cleaned['steps'].append(item)
        for stage in STAGES:
            value = plan.get(stage, {})
            cleaned[stage] = dict(station_id=int(value.get('station_id') or 0), criteria=str(value.get('criteria', '')).strip())
            if len(cleaned[stage]['criteria']) > 2000:
                raise ValueError
        return cleaned
    except (ValueError, TypeError, AttributeError):
        abort(400, 'Invalid Job Plan. Use up to 20 steps and the displayed field limits.')


def save(conn, tenant, rid, plan):
    if plan is not None:
        # Even incomplete drafts must not refer to another organization's stations.
        for step in plan['steps'] + [plan[k] for k in STAGES]:
            sid = step['station_id']
            if sid and not conn.execute('SELECT 1 FROM stations WHERE tenant_id=? AND id=? AND active=1', (tenant, sid)).fetchone():
                abort(400, 'Choose an active station from your organization.')
        conn.execute('INSERT INTO request_job_plans VALUES(?,?,?) ON CONFLICT(request_id) DO UPDATE SET data=excluded.data', (tenant, rid, json.dumps(plan)))


def validate(conn, tenant, rid):
    plan = get(conn, tenant, rid)
    if plan is None:
        return None  # Existing requests retain their conversion workflow.
    from .request_outside import get_plan
    outside = get_plan(conn, tenant, rid)
    outside_steps = [s for s in plan['steps'] if s['outside']]
    if not plan['steps'] or len(outside_steps) != (1 if outside else 0):
        abort(409, 'Complete the Job Plan route, including exactly one outside step when outside processing is requested.')
    if outside_steps and not plan['steps'][-1]['outside']:
        abort(409, 'Outside processing must follow all internal production steps, before inbound inspection, packing and shipping.')
    for step in plan['steps'] + [plan[k] for k in STAGES]:
        if not step['criteria']:
            abort(409, 'Enter acceptance checks for every Job Plan step, inspection, packing and shipping.')
        if step.get('outside'):
            if step['first_piece'] or step['station_id']:
                abort(409, 'Outside work uses the vendor and receiving inspection, not an internal station or first-piece check.')
        elif not conn.execute('SELECT 1 FROM stations WHERE tenant_id=? AND id=? AND active=1', (tenant, step['station_id'])).fetchone():
            abort(409, 'Assign an active station to each internal Job Plan step and final stage.')
    if any(not s['name'] or not s['instructions'] for s in plan['steps']):
        abort(409, 'Name each Job Plan step and provide work instructions.')
    from .materials import request_material
    material = request_material(conn, tenant, rid)
    if material and material['spec']['required_service'] and not plan['steps'][-1]['outside']:
        abort(409, 'The material-required outside service must be the final production operation, before inspection and packing.')
    if plan['dxf_required'] and not conn.execute('SELECT 1 FROM intake_requests r JOIN part_revisions p ON p.tenant_id=r.tenant_id AND p.pn=r.pn AND p.revision=r.revision JOIN part_dxf d ON d.tenant_id=p.tenant_id AND d.part_revision_id=p.id WHERE r.tenant_id=? AND r.id=?', (tenant, rid)).fetchone():
        abort(409, 'Upload the required DXF before approving this Job Plan.')
    return plan


def route(plan):
    return [s['name'] + ' | ' + s['instructions'] for s in plan['steps']]


def outside_sequence(plan, fallback=None):
    return next((i for i, s in enumerate(plan['steps'], 1) if s['outside']), None) if plan else fallback


def apply(conn, tenant, rid, tid, user, stamp, event):
    plan = validate(conn, tenant, rid)
    if not plan:
        return
    ops = conn.execute('SELECT id FROM operations WHERE tenant_id=? AND ticket_id=? ORDER BY sequence', (tenant, tid)).fetchall()
    data = dict(operations={}, dxf_required=plan['dxf_required'])
    for op, step in zip(ops, plan['steps'], strict=True):
        data['operations'][str(op['id'])] = dict(criteria=step['criteria'], first_piece=step['first_piece'])
        if not step['outside']:
            conn.execute('INSERT INTO operation_assignments VALUES(?,?,?,?,NULL) ON CONFLICT(operation_id) DO UPDATE SET station_id=excluded.station_id', (tenant, tid, op['id'], step['station_id']))
    for stage in STAGES:
        station = conn.execute('SELECT name FROM stations WHERE tenant_id=? AND id=?', (tenant, plan[stage]['station_id'])).fetchone()
        data[stage] = dict(plan[stage], station_name=station['name'])
    cursor = conn.execute('INSERT INTO job_plans(tenant_id,ticket_id,data,actor_id,reason,created_at) VALUES(?,?,?,?,?,?)', (tenant, tid, json.dumps(data), user, 'Copied from approved request ' + rid, stamp))
    event(tid, 'job_plan_from_request', json.dumps(dict(request_id=rid, plan_id=cursor.lastrowid, plan=data, route=plan['steps'])))
