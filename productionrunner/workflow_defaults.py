"""Versioned organization defaults copied immutably onto newly created jobs."""
import json
from flask import abort, g, redirect, render_template, request, url_for
from .intake import actor
from .pin_auth import audit


def settings(conn, tenant):
    row = conn.execute('SELECT * FROM workflow_defaults WHERE tenant_id=?', (tenant,)).fetchone()
    if not row:
        return dict(version=0, require_intake=False, checks=[])
    return dict(version=row['version'], require_intake=bool(row['require_intake']), checks=json.loads(row['checks']))


def validate_ticket(conn, tenant, submitted_version, intake_entry):
    policy = settings(conn, tenant)
    # Older clients remain compatible only while the organization has never configured defaults.
    if submitted_version != str(policy['version']) and not (policy['version']==0 and submitted_version is None):
        abort(409, 'Organization workflow changed. Reload ticket creation to review its requirements.')
    if policy['require_intake'] and not intake_entry:
        abort(409, 'This organization requires an approved manufacturing request. Start in Requests.')
    return policy


def snapshot(conn, tenant, ticket_id, user, policy, stamp, event):
    conn.execute('INSERT INTO ticket_workflow VALUES(?,?,?,?,?,?)', (tenant, ticket_id, policy['version'], int(policy['require_intake']), json.dumps(policy['checks']), stamp))
    for check in policy['checks']:
        conn.execute('INSERT INTO fulfillment_checks(tenant_id,ticket_id,name,stage,actor_id,created_at) VALUES(?,?,?,?,?,?)', (tenant, ticket_id, check['name'], check['stage'], user, stamp))
    if policy['version']:
        event(ticket_id, 'workflow_defaults_applied', json.dumps(policy))


def register_workflow_defaults(app, db, roles_required):
    @app.context_processor
    def context():
        return dict(workflow_for_ticket=lambda ticket: db().execute('SELECT * FROM ticket_workflow WHERE tenant_id=? AND ticket_id=?', (g.tenant['id'], ticket['id'])).fetchone())

    @app.route('/admin/workflow', methods=['GET', 'POST'])
    @roles_required('admin')
    def workflow_admin():
        policy = settings(db(), g.tenant['id'])
        preview = False
        if request.method == 'POST':
            action = request.form.get('action')
            if action not in {'save', 'preview', 'sap_example', 'no_erp_example'}:
                abort(400, 'Choose preview or save.')
            if action == 'sap_example':
                proposed = dict(require_intake=True, checks=[dict(name='ERP production confirmation',stage='pack'),dict(name='ERP delivery / transfer reference',stage='depart')])
            elif action == 'no_erp_example':
                proposed = dict(require_intake=True, checks=[])
            else:
                if request.form.get('require_intake', '0') not in {'0','1'}:
                    abort(400)
                checks=[]
                for stage in ('pack','depart'):
                    names=[line.strip() for line in request.form.get(stage+'_checks','').splitlines() if line.strip()]
                    if any(len(name)>120 or any(ord(ch)<32 for ch in name) for name in names):
                        abort(400,'Checkpoint names must be printable and at most 120 characters.')
                    checks.extend(dict(name=name,stage=stage) for name in names)
                if len(checks)>20 or len({c['name'].casefold() for c in checks})!=len(checks):
                    abort(400,'Use at most 20 distinct checkpoint names across both stages.')
                proposed=dict(require_intake=request.form.get('require_intake')=='1',checks=checks)
            if action == 'save':
                reason=request.form.get('reason','').strip()
                if not 1<=len(reason)<=500:
                    abort(400,'Record a reason of 1–500 characters.')
                with db():
                    db().execute('BEGIN IMMEDIATE')
                    actor(db(),g.tenant['id'],g.user['id'],('admin',))
                    old=settings(db(),g.tenant['id'])
                    if request.form.get('version')!=str(old['version']):
                        abort(409,'Workflow defaults changed. Reload before saving.')
                    before={key:old[key] for key in proposed}
                    if before!=proposed:
                        db().execute('INSERT INTO workflow_defaults VALUES(?,?,?,1) ON CONFLICT(tenant_id) DO UPDATE SET require_intake=excluded.require_intake,checks=excluded.checks,version=workflow_defaults.version+1',(g.tenant['id'],int(proposed['require_intake']),json.dumps(proposed['checks'])))
                        audit(db(),g.tenant['id'],g.user['id'],'workflow_defaults_updated',f"user:{g.user['id']}",app.config['AUTH_CLOCK'](),json.dumps(dict(reason=reason,before=before,after=proposed,version=old['version']+1)))
                return redirect(url_for('workflow_admin',saved='1'))
            policy=dict(proposed,version=request.form.get('version',''))
            preview=True
        return render_template('workflow_defaults.html',policy=policy,preview=preview,saved=request.args.get('saved')=='1')
