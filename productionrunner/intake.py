"""Tenant request intake and explicit production-document readiness approval."""
from . import delivery_schedule, erp, production_calendar
import json
import re
import secrets
from datetime import date
from flask import abort, g, redirect, render_template, request, session, url_for
from .admin import epoch
from .request_outside import get_plan, parse as outside_fields, save as save_outside

from . import request_job_plan as request_plan
from . import custom_fields, request_documents

SOURCES = ('manual', 'po', 'erp', 'email', 'smartsheet', 'other')
EDIT_FIELDS = ('item_type', 'source', 'source_reference', 'requester', 'destination', 'required_date', 'pn', 'revision', 'quantity', 'description')


def actor(conn, tenant, user, allowed=('planner', 'admin')):
    member = conn.execute('SELECT roles FROM memberships WHERE tenant_id=? AND user_id=?', (tenant, user)).fetchone()
    if not member or not set(member['roles'].split(',')) & set(allowed) or epoch(conn, tenant, user) != session.get('membership_epoch', 0):
        abort(403, 'Your access changed. Sign in again.')


def get_request(conn, tenant, rid):
    row = conn.execute('SELECT r.*,u.username AS approver FROM intake_requests r LEFT JOIN users u ON u.id=r.approved_by WHERE r.tenant_id=? AND r.id=?', (tenant, rid)).fetchone()
    if not row:
        abort(404, 'Unknown manufacturing request.')
    return row


def approved_request(conn, tenant, rid, version=None):
    row = get_request(conn, tenant, rid)
    if row['status'] != 'approved' or not row['part_id']:
        abort(409, 'This request is not approved for ticket creation, or already has a ticket.')
    if version is not None and str(row['version']) != version:
        abort(409, 'Request changed. Reopen the approved request before creating its ticket.')
    from .materials import validate_request as validate_material
    validate_material(conn,tenant,rid)
    from .vendors import validate_request as validate_vendor
    validate_vendor(conn,tenant,rid)
    delivery_schedule.validate_request(conn, tenant, row)
    request_plan.validate(conn, tenant, rid)
    custom_fields.validate(conn, tenant, rid)
    erp.validate(conn,tenant,row)
    approval = conn.execute("SELECT detail FROM intake_events WHERE tenant_id=? AND request_id=? AND action='documents_approved' ORDER BY id DESC LIMIT 1", (tenant, rid)).fetchone()
    dxf = conn.execute('SELECT sha256 FROM part_dxf WHERE tenant_id=? AND part_revision_id=?', (tenant, row['part_id'])).fetchone()
    if not approval or json.loads(approval['detail']).get('dxf_sha256') != (dxf['sha256'] if dxf else None):
        abort(409, 'Document package changed after approval. Reopen preparation and approve the package again.')
    return row


def log(conn, tenant, user, rid, action, detail, stamp):
    conn.execute('INSERT INTO intake_events(tenant_id,request_id,actor_id,action,detail,created_at) VALUES(?,?,?,?,?,?)', (tenant, rid, user, action, json.dumps(detail), stamp))


def link_ticket(conn, tenant, user, req, tid, stamp):
    start=production_calendar.request_start(conn,tenant,req['id'])
    if start:
        conn.execute("INSERT INTO events(tenant_id,ticket_id,actor_id,action,detail,created_at) VALUES(?,?,?,'production_day_planned',?,?)",(tenant,tid,user,json.dumps(dict(unit_id=None,day=start,reason='Scheduled start from production request')),stamp))
    erp.snapshot(conn,tenant,user,req,tid,stamp)
    delivery_schedule.snapshot(conn,tenant,user,req,tid,stamp)
    custom_fields.snapshot(conn,tenant,req["id"],tid)
    conn.execute("UPDATE intake_requests SET status='ticketed',ticket_id=?,version=version+1 WHERE tenant_id=? AND id=?", (tid, tenant, req['id']))
    log(conn, tenant, user, req['id'], 'ticket_created', dict(ticket_id=tid, approved_part=req['part_id']), stamp)


def document_state(conn, tenant, row):
    part = conn.execute('SELECT id,pn,revision,description,drawing_url,sha256,(drawing_pdf IS NOT NULL OR object_key IS NOT NULL) AS has_pdf FROM part_revisions WHERE tenant_id=? AND pn=? AND revision=?', (tenant, row['pn'], row['revision'])).fetchone()
    dxf = conn.execute('SELECT sha256 FROM part_dxf WHERE tenant_id=? AND part_revision_id=?', (tenant, part['id'])).fetchone() if part else None
    missing = []
    if not part:
        missing.append('Matching PN/revision drawing is not in the catalog.')
    if row['item_type'] == 'custom':
        if part and not part['has_pdf']:
            missing.append('Custom work requires an uploaded PDF, not an external drawing link.')
        if not dxf:
            missing.append('Custom work requires an uploaded DXF.')
    return dict(part=part, dxf=dxf, missing=missing, ready=not missing)


def register_intake(app, db, roles_required, now):
    from .repeat_jobs import register as register_repeat
    register_repeat(app, db, roles_required, now)
    from .release_flow import register as register_release_flow
    register_release_flow(app, db, roles_required)
    @app.context_processor
    def intake_context():
        return dict(request_delivery_schedule=lambda rid:delivery_schedule.request_schedule(db(),g.tenant["id"],rid), request_job_plan=lambda rid:request_plan.get(db(),g.tenant["id"],rid), request_outside=lambda rid:get_plan(db(),g.tenant['id'],rid), request_for_ticket=lambda ticket: db().execute('SELECT * FROM intake_requests WHERE tenant_id=? AND ticket_id=?', (g.tenant['id'], ticket['id'])).fetchone())

    def fields():
        data = {key: request.form.get(key, '').strip() for key in EDIT_FIELDS}
        if data['item_type'] not in {'standard', 'custom'} or data['source'] not in SOURCES:
            abort(400, 'Choose standard/custom and a request source.')
        erp_origin=data['source'] in erp.settings(db(),g.tenant['id'])['required_sources'] or bool(request.form.get('erp_path'))
        for key, limit in [('source_reference', 500), ('requester', 200), ('destination', 500), ('description', 4000)]:
            if len(data[key]) > limit or (not data[key] and (key != 'source_reference' or data['source'] != 'manual' and not erp_origin)):
                abort(400, f'Provide {key} (up to {limit} characters).')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,63}', data['pn']) or not data['revision'] or len(data['revision']) > 40:
            abort(400, 'Provide one valid PN and a revision of up to 40 characters.')
        try:
            data['quantity'] = int(data['quantity'])
        except ValueError:
            abort(400, 'Quantity must be a whole number.')
        if not 1 <= data['quantity'] <= 10000:
            abort(400, 'Quantity must be 1–10000.')
        if data['required_date']:
            try:
                if date.fromisoformat(data['required_date']).isoformat() != data['required_date']:
                    raise ValueError
            except ValueError:
                abort(400, 'Required date must be YYYY-MM-DD.')
        return data

    def reason():
        value = request.form.get('reason', '').strip()
        if not value or len(value) > 500:
            abort(400, 'Record a reason or approval evidence (1–500 characters).')
        return value

    def choices():
        return db().execute('SELECT id,pn,revision,description FROM part_revisions WHERE tenant_id=? AND id NOT IN (SELECT part_id FROM part_catalog_controls WHERE archived=1) ORDER BY pn,revision', (g.tenant['id'],)).fetchall()

    @app.get('/requests')
    @roles_required()
    def intake_queue():
        view = request.args.get('view', 'open')
        if view not in {'open', 'all'}:
            abort(400)
        entries = db().execute('SELECT * FROM intake_requests WHERE tenant_id=? ORDER BY CASE status WHEN \'preparing\' THEN 0 WHEN \'approved\' THEN 1 ELSE 2 END,required_date=\'\',required_date,created_at,id', (g.tenant['id'],)).fetchall()
        entries = [row for row in entries if view == 'all' or row['status'] in {'preparing', 'approved'}]
        return render_template('intake_queue.html', entries=entries, view=view)

    @app.route('/requests/new', methods=['GET', 'POST'])
    @roles_required('planner', 'admin')
    def intake_new():
        if request.method == 'GET':
            return render_template('intake_form.html', entry=None, parts=choices(), sources=SOURCES)
        data = fields()
        schedule = delivery_schedule.parse(request.form,data['quantity'],data['required_date'])
        outside = outside_fields(request.form)
        rid = 'REQ-' + secrets.token_hex(6).upper()
        with db():
            db().execute('BEGIN IMMEDIATE')
            actor(db(), g.tenant['id'], g.user['id'])
            from .parts import matching_part
            from .catalog import active
            existing=matching_part(db(),g.tenant['id'],data['pn'],data['revision'])
            if existing: active(db(),g.tenant['id'],existing['id'])
            db().execute('INSERT INTO intake_requests(id,tenant_id,' + ','.join(EDIT_FIELDS) + ',created_by,created_at) VALUES(' + ','.join('?' for _ in range(len(EDIT_FIELDS)+4)) + ')', (rid, g.tenant['id'], *(data[k] for k in EDIT_FIELDS), g.user['id'], now()))
            start=production_calendar.validate_start(request.form.get('scheduled_start','').strip(),data['required_date'])
            production_calendar.save_request_start(db(),g.tenant['id'],g.user['id'],rid,start,now())
            erp_data=erp.collect(request.form)
            erp.path_for(erp.settings(db(),g.tenant['id']),erp_data,data['source'])
            erp.save(db(),g.tenant['id'],g.user['id'],rid,erp_data,now())
            from .materials import select_material,save_request
            material=select_material(db(),g.tenant['id'],request.form)
            save_request(db(),g.tenant['id'],rid,material)
            save_outside(db(), g.tenant['id'], rid, outside)
            custom=custom_fields.collect(db(),g.tenant['id'],request.form)
            custom_fields.save(db(),g.tenant['id'],rid,custom)
            plan = request_plan.parse(request.form)
            request_plan.save(db(),g.tenant['id'],rid,plan)
            uploaded = request_documents.save(db(),g.tenant['id'],g.user['id'],data,now())
            if uploaded: log(db(),g.tenant['id'],g.user['id'],rid,'documents_uploaded',uploaded,now())
            delivery_schedule.save_request(db(),g.tenant['id'],g.user['id'],rid,schedule,now())
            log(db(), g.tenant['id'], g.user['id'], rid, 'request_received', dict(data, outside_processing=outside,material=material,job_plan=plan,custom_fields=custom), now())
        if request.form.get('next_action')=='release' and 'planner' in g.roles:
            return redirect(url_for('release_review',rid=rid,template=request.form.get('save_template','')))
        return redirect(url_for('intake_detail', rid=rid))

    @app.get('/requests/<rid>')
    @roles_required()
    def intake_detail(rid):
        row = get_request(db(), g.tenant['id'], rid)
        docs = document_state(db(), g.tenant['id'], row)
        history = db().execute('SELECT e.*,u.username FROM intake_events e JOIN users u ON u.id=e.actor_id WHERE e.tenant_id=? AND e.request_id=? ORDER BY e.id', (g.tenant['id'], rid)).fetchall()
        return render_template('intake_detail.html', entry=row, docs=docs, history=history,erp_duplicates=erp.duplicates(db(),g.tenant['id'],erp.saved(db(),g.tenant['id'],rid)['values'],rid))

    @app.route('/requests/<rid>/edit', methods=['GET', 'POST'])
    @roles_required('planner', 'admin')
    def intake_edit(rid):
        if request.method == 'GET':
            row = get_request(db(), g.tenant['id'], rid)
            if row['status'] in {'ticketed', 'cancelled'}:
                abort(409, 'Converted or cancelled requests cannot be edited.')
            return render_template('intake_form.html', entry=row, parts=choices(), sources=SOURCES)
        data, why = fields(), reason()
        outside = outside_fields(request.form)
        with db():
            db().execute('BEGIN IMMEDIATE')
            actor(db(), g.tenant['id'], g.user['id'])
            row = get_request(db(), g.tenant['id'], rid)
            if row['status'] in {'ticketed', 'cancelled'} or str(row['version']) != request.form.get('version'):
                abort(409, 'Request changed or cannot be edited. Reload it.')
            if (data['pn'],data['revision']) != (row['pn'],row['revision']):
                from .parts import matching_part
                from .catalog import active
                existing=matching_part(db(),g.tenant['id'],data['pn'],data['revision'])
                if existing: active(db(),g.tenant['id'],existing['id'])
            previous_start=production_calendar.request_start(db(),g.tenant['id'],rid)
            start=production_calendar.validate_start(request.form.get('scheduled_start',previous_start).strip(),data['required_date'])
            previous_erp=erp.saved(db(),g.tenant['id'],rid)
            erp_data=erp.collect(request.form,previous_erp)
            erp.path_for(erp.settings(db(),g.tenant['id']),erp_data,data['source'])
            previous_schedule=delivery_schedule.request_schedule(db(),g.tenant['id'],rid)
            schedule=delivery_schedule.parse(request.form,data['quantity'],data['required_date'],previous_schedule)
            before = {key: row[key] for key in EDIT_FIELDS}
            from .materials import select_material,save_request,request_material
            material=select_material(db(),g.tenant['id'],request.form)
            previous_material=request_material(db(),g.tenant['id'],rid)
            if previous_material:previous_material={k:previous_material[k] for k in ('material_id','material_version','spec','planned_sheets','notes')}
            previous_outside=get_plan(db(),g.tenant['id'],rid)
            previous_plan=request_plan.get(db(),g.tenant['id'],rid)
            plan=request_plan.parse(request.form,previous_plan)
            previous_custom=custom_fields.saved(db(),g.tenant['id'],rid)
            custom=custom_fields.collect(db(),g.tenant['id'],request.form,previous_custom)
            uploaded = request_documents.save(db(),g.tenant['id'],g.user['id'],data,now())
            if uploaded: log(db(),g.tenant['id'],g.user['id'],rid,'documents_uploaded',uploaded,now())
            if start != previous_start or erp_data != previous_erp or schedule != previous_schedule or uploaded or previous_custom != custom or previous_plan != plan or before != data or previous_outside != outside or previous_material != material:
                db().execute('UPDATE intake_requests SET ' + ','.join(key+'=?' for key in EDIT_FIELDS) + ",status='preparing',part_id=NULL,approved_by=NULL,approved_at=NULL,version=version+1 WHERE tenant_id=? AND id=?", (*(data[k] for k in EDIT_FIELDS), g.tenant['id'], rid))
                production_calendar.save_request_start(db(),g.tenant['id'],g.user['id'],rid,start,now())
                erp.save(db(),g.tenant['id'],g.user['id'],rid,erp_data,now())
                save_request(db(),g.tenant['id'],rid,material)
                save_outside(db(),g.tenant['id'],rid,outside)
                request_plan.save(db(),g.tenant['id'],rid,plan)
                custom_fields.save(db(),g.tenant['id'],rid,custom)
                delivery_schedule.save_request(db(),g.tenant['id'],g.user['id'],rid,schedule,now(),why)
                log(db(), g.tenant['id'], g.user['id'], rid, 'request_edited', dict(before=dict(before,outside_processing=previous_outside,material=previous_material,job_plan=previous_plan,custom_fields=previous_custom), after=dict(data,outside_processing=outside,material=material,job_plan=plan,custom_fields=custom), reason=why, approval_invalidated=row['status']=='approved'), now())
        if request.form.get('next_action')=='release' and 'planner' in g.roles:
            return redirect(url_for('release_review',rid=rid,template=request.form.get('save_template','')))
        return redirect(url_for('intake_detail', rid=rid))

    @app.post('/requests/<rid>/documents')
    @roles_required('planner', 'admin')
    def intake_documents(rid):
        with db():
            db().execute('BEGIN IMMEDIATE')
            actor(db(),g.tenant['id'],g.user['id'])
            row=get_request(db(),g.tenant['id'],rid)
            if row['status'] not in {'preparing','approved'} or str(row['version']) != request.form.get('version'):
                abort(409,'Request changed or is already in production. Reload it.')
            uploaded=request_documents.save(db(),g.tenant['id'],g.user['id'],row,now())
            if not uploaded: abort(400,'Choose a drawing PDF or DXF to upload.')
            db().execute("UPDATE intake_requests SET status='preparing',part_id=NULL,approved_by=NULL,approved_at=NULL,version=version+1 WHERE tenant_id=? AND id=?",(g.tenant['id'],rid))
            log(db(),g.tenant['id'],g.user['id'],rid,'documents_uploaded',dict(uploaded,approval_invalidated=row['status']=='approved'),now())
        return redirect(url_for('intake_detail',rid=rid))

    @app.post('/requests/<rid>/decision')
    @roles_required('planner', 'admin')
    def intake_decision(rid):
        why = reason()
        action = request.form.get('action')
        with db():
            db().execute('BEGIN IMMEDIATE')
            actor(db(), g.tenant['id'], g.user['id'])
            row = get_request(db(), g.tenant['id'], rid)
            if row['status'] in {'ticketed', 'cancelled'} or str(row['version']) != request.form.get('version'):
                abort(409, 'Request changed or cannot be decided. Reload it.')
            if action == 'approve':
                if not row['requester'] or not row['destination']:
                    abort(409,'Edit the request to enter the customer and destination before approval.')
                outside_review=get_plan(db(),g.tenant['id'],rid)
                if outside_review and not outside_review['due_date']:
                    abort(409,'Enter a new outside processing return date before approval.')
                if row['status'] == 'approved':
                    abort(409, 'Request already approved.')
                from .materials import validate_request as validate_material
                validate_material(db(),g.tenant['id'],rid)
                from .vendors import validate_request as validate_vendor
                validate_vendor(db(),g.tenant['id'],rid)
                request_plan.validate(db(),g.tenant['id'],rid)
                custom_fields.validate(db(),g.tenant['id'],rid)
                delivery_schedule.validate_request(db(),g.tenant['id'],row)
                erp.validate(db(),g.tenant['id'],row)
                docs = document_state(db(), g.tenant['id'], row)
                if not docs['ready']:
                    abort(409, 'Upload the required matching production documents before approval.')
                if request.form.get('confirm') != 'yes':
                    abort(400, 'Confirm you reviewed the production documents.')
                part, dxf = docs['part'], docs['dxf']
                if part['drawing_url'] and request.form.get('external_ack') != 'yes':
                    abort(400, 'Acknowledge that an externally hosted drawing can change.')
                db().execute("UPDATE intake_requests SET status='approved',part_id=?,approved_by=?,approved_at=?,version=version+1 WHERE tenant_id=? AND id=?", (part['id'], g.user['id'], now(), g.tenant['id'], rid))
                log(db(), g.tenant['id'], g.user['id'], rid, 'documents_approved', dict(reason=why, part_id=part['id'], pdf_sha256=part['sha256'], drawing_url=part['drawing_url'], dxf_sha256=dxf['sha256'] if dxf else None, request_version=row['version']), now())
            elif action == 'reopen':
                if row['status'] != 'approved':
                    abort(409, 'Only approved requests can be reopened.')
                db().execute("UPDATE intake_requests SET status='preparing',part_id=NULL,approved_by=NULL,approved_at=NULL,version=version+1 WHERE tenant_id=? AND id=?", (g.tenant['id'], rid))
                log(db(), g.tenant['id'], g.user['id'], rid, 'preparation_reopened', dict(reason=why), now())
            elif action == 'cancel':
                db().execute("UPDATE intake_requests SET status='cancelled',version=version+1 WHERE tenant_id=? AND id=?", (g.tenant['id'], rid))
                log(db(), g.tenant['id'], g.user['id'], rid, 'request_cancelled', dict(reason=why), now())
            else:
                abort(400, 'Choose approve or cancel.')
        return redirect(url_for('intake_detail', rid=rid))
